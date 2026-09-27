"""Durable five-stage orchestrator with hard provider gates."""
from __future__ import annotations
from datetime import datetime, timedelta
import hashlib, json, os, re, subprocess, threading, time, uuid
from pathlib import Path
from typing import Any, Optional
from urllib.request import Request, urlopen

ROOT=Path("/Volumes/Realtek_NVME/AI System"); HERE=Path(__file__).resolve().parent
STATE=HERE/"data/strict_agi_state.json"; ART=HERE/"data/agi_task_artifacts"; ENV=HERE.parent/".env"
BINS={"codex":Path("/Applications/ChatGPT.app/Contents/Resources/codex"),"claude":Path("/opt/homebrew/bin/claude"),"gemini":Path("/opt/homebrew/bin/gemini")}
# 2026-09-17 소유자 지시("AI의 우선순위를 변경하자. 1번 계획을 Claude가 · GPT Astra
# 모델이 사용 불가능한 상태라면 Claude가 대체"): GPT 토큰 소진 상태이므로 1단계
# 계획의 주 담당을 Gemini에서 Claude로 올린다(Gemini는 대체 경로로 남김). 2단계는
# 대용량 컨텍스트 근거 수집이라 Gemini 그대로 유지 - 이건 명시적으로 바뀌라고 하신
# 부분이 아니다.
PIPELINE=[(1,"claude","Claude 계획"),(2,"gemini","Gemini 근거 수집"),(3,"deepseek","DeepSeek 분석·초안"),(4,"deepseek","일반 보강 / 핵심만 Claude"),(5,"policy","일반 산출물 보존 / 핵심만 Sol 검토")]
FINAL={"COMPLETED","FAILED","CANCELLED","INVALIDATED","DRAFT_READY"}; LIMIT=("rate limit","usage limit","quota","too many requests","limit reached","resource exhausted","429","try again")

def now(): return datetime.now().astimezone()
def iso(v=None): return (v or now()).isoformat(timespec="seconds")
def secret(name):
    value=os.getenv(name,"").strip().strip('"').strip("'")
    if value:return value
    try:
        for raw in ENV.read_text().splitlines():
            if "=" in raw and not raw.lstrip().startswith("#"):
                k,v=raw.split("=",1)
                if k.strip()==name:return v.strip().strip('"').strip("'")
    except OSError:pass
    return ""
def provider(label):return {"display_name":label,"status":"UNKNOWN_UNTIL_REQUEST","auth_ready":False,"quota_observed":False,"reset_at":None,"last_checked_at":None,"note":"실제 단계 요청으로 한도 확인"}
def blank():return {"schema_version":1,"updated_at":iso(),"providers":{"codex":provider("GPT / Codex"),"gemini":provider("Gemini CLI"),"qwen":provider("Qwen 로컬"),"deepseek":provider("DeepSeek API"),"claude":provider("Claude")},"tasks":[],"goals":[]}

class StrictAGIOrchestrator:
 def __init__(self,state_file=STATE,monitor_interval=60):
  self.state_file=Path(state_file);self.artifact_dir=self.state_file.parent/"agi_task_artifacts";self.monitor_interval=max(10,monitor_interval);self.lock=threading.RLock();self.active=set();self.stop_event=threading.Event();self.monitor=None;self._recover()
 def _load(self):
  with self.lock:
   if not self.state_file.exists():s=blank();self._save(s);return s
   try:
    s=json.loads(self.state_file.read_text());s.setdefault("goals",[]);return s
   except Exception:s=blank();self._save(s);return s
 def _save(self,s):
  with self.lock:
   self.state_file.parent.mkdir(parents=True,exist_ok=True);s["updated_at"]=iso();tmp=self.state_file.with_name(f".{self.state_file.name}.{uuid.uuid4().hex}.tmp");tmp.write_text(json.dumps(s,ensure_ascii=False,indent=2));os.replace(tmp,self.state_file)
 def _recover(self):
  s=self._load();changed=False
  for t in s["tasks"]:
   if str(t.get("status","")).startswith("RUNNING_"):t.update(status="WAITING_RETRY",stage_label="재시작 감지 · 현재 단계 재개 대기",next_retry_at=iso(now()+timedelta(minutes=1)));changed=True
   if t.get("status")=="COMPLETED":
    stages={a.get("stage") for a in t.get("artifacts",[]) if isinstance(a,dict)}
    outputs=all(str(t.get(f"stage{n}_output","")).strip() for n in range(1,6))
    if stages!={1,2,3,4,5} or not outputs:
     t.update(status="INVALIDATED",verdict="EVIDENCE_INVALID",progress_pct=0,stage_label="완료 무효 · 5단계 산출물/지문 불충분",invalidated_at=iso());changed=True
     gid=t.get("goal_id")
     if gid:
      goal=next((g for g in s.get("goals",[]) if g.get("goal_id")==gid),None)
      if goal:goal["next_run_at"]=None
   elif t.get("status") not in FINAL and int(t.get("current_stage",1) or 1)>1:
    current=int(t.get("current_stage",1));required=set(range(1,current))
    stages={a.get("stage") for a in t.get("artifacts",[]) if isinstance(a,dict)}
    outputs=all(str(t.get(f"stage{n}_output","")).strip() for n in required)
    if not required.issubset(stages) or not outputs:
     t.update(status="INVALIDATED",verdict="CHECKPOINT_EVIDENCE_INVALID",progress_pct=0,stage_label="재개 무효 · 이전 단계 산출물/지문 불충분",invalidated_at=iso());changed=True
     gid=t.get("goal_id")
     if gid:
      goal=next((g for g in s.get("goals",[]) if g.get("goal_id")==gid),None)
      if goal:goal["next_run_at"]=None
  if changed:self._save(s)
 def start(self):
  if self.monitor and self.monitor.is_alive():return
  self.monitor=threading.Thread(target=self._loop,daemon=True,name="strict-agi-monitor");self.monitor.start()
 def _loop(self):
  while not self.stop_event.wait(self.monitor_interval):
   try:self.check_and_resume()
   except Exception:pass
 def _cli_probe(self,name):
  if not BINS[name].exists():return {"auth_ready":False,"status":"UNAVAILABLE","note":"CLI 없음"}
  if name=="gemini":return {"auth_ready":True,"status":"AVAILABLE_QUOTA_UNKNOWN","note":"CLI 설치 확인 · 인증/한도는 단계 요청에서 확인"}
  try:
   args=[str(BINS[name]),"login","status"] if name=="codex" else [str(BINS[name]),"auth","status","--json"]
   p=subprocess.run(args,capture_output=True,text=True,timeout=10);ready=p.returncode==0 and (("Logged in" in p.stdout+p.stderr) if name=="codex" else bool(json.loads(p.stdout or "{}").get("loggedIn")))
  except Exception:ready=False
  return {"auth_ready":ready,"status":"AVAILABLE_QUOTA_UNKNOWN" if ready else "WAITING_AUTH","note":"로그인 확인 · 잔여량은 단계 요청에서 확인" if ready else "로그인 필요"}
 def refresh_providers(self):
  try:
   with urlopen("http://127.0.0.1:11434/api/tags",timeout=3) as r:names=[x.get("name","") for x in json.loads(r.read()).get("models",[])]
   q=any(x.startswith("qwen") for x in names)
  except Exception:q=False
  # 2026-09-22 소유자 지시: 유료 모델(DeepSeek) 자동 사용 금지 - auth_ready를 항상
  # False로 고정해 3/4단계가 아래 _run()의 기존 alternate 로직(deepseek 미준비 시
  # gemini로 대체)을 타게 한다. 재활성화하려면 이 한 줄만 원복하면 된다.
  probes={"codex":self._cli_probe("codex"),"gemini":self._cli_probe("gemini"),"claude":self._cli_probe("claude"),"qwen":{"auth_ready":q,"status":"AVAILABLE_LOCAL" if q else "UNAVAILABLE","note":"로컬 모델 확인" if q else "Ollama/Qwen 없음"},"deepseek":{"auth_ready":False,"status":"DISABLED_PAID_MODEL_BY_OWNER","note":"유료 API 자동 사용 금지(2026-09-22 소유자 지시) - 3/4단계는 Gemini로 대체됨"}}
  with self.lock:
   s=self._load()
   for n,p in probes.items():
    item=s["providers"][n]
    try:locked=item.get("reset_at") and datetime.fromisoformat(item["reset_at"])>now()
    except ValueError:locked=False
    if locked:item.update(last_checked_at=iso(),auth_ready=p["auth_ready"])
    else:item.update(p,quota_observed=False,reset_at=None,last_checked_at=iso())
   self._save(s);return s["providers"]
 @staticmethod
 def _retry(msg):
  # 2026-09-14 진단(D03, AGENTIC_RUNTIME_DIAGNOSIS_SOL_ONLY): 파싱 실패 시 "현재
  # 시각+5시간"을 실제 리셋 시각처럼 note에 표시하고 있었다 - Claude가 실제로 보낸
  # "resets 9:40am (Asia/Seoul)"처럼 절대 시각 형식은 이 정규식이 못 잡아 전부 이
  # 임의의 5시간 기본값으로 떨어졌다(실측: Claude가 이미 사용 가능해진 뒤에도 그
  # 임의의 5시간짜리 next_retry_at 때문에 계속 대기 상태로 남아 있었음). 이제
  # 상대시간뿐 아니라 "HH:MM am/pm" 절대시각도 파싱하고, 둘 다 실패하면 5시간이
  # 아니라 짧은 backoff(30분)로 재확인하되 "확인된 리셋 시각이 아니다"를 명시한다.
  low=msg.lower()
  m=re.search(r"(?:retry|try again)\s+(?:in|after)\s+(\d+)\s*(minute|min|hour|hr)",low)
  if m:
   return now()+(timedelta(hours=int(m.group(1))) if m.group(2) in {"hour","hr"} else timedelta(minutes=int(m.group(1)))),True
  m2=re.search(r"resets?\s+(?:at\s+)?(\d{1,2}):(\d{2})\s*(am|pm)?",low)
  if m2:
   hour=int(m2.group(1))%12;minute=int(m2.group(2));ampm=m2.group(3)
   if ampm=="pm":hour+=12
   candidate=now().replace(hour=hour,minute=minute,second=0,microsecond=0)
   if candidate<=now():candidate+=timedelta(days=1)
   return candidate,True
  return now()+timedelta(minutes=30),False
 def _failure(self,name,msg):
  with self.lock:
   s=self._load();p=s["providers"][name];limited=any(x in msg.lower() for x in LIMIT)
   if limited:
    r,confirmed=self._retry(msg)
    label="실제 한도 감지" if confirmed else "한도로 추정(정확한 리셋 시각 불명 · 짧은 간격으로 재확인)"
    p.update(status="WAITING_QUOTA",quota_observed=confirmed,reset_at=iso(r),note=f"{label} · {r.strftime('%m-%d %H:%M')} 이후 재시도")
   else:p.update(status="WAITING_AUTH" if any(x in msg.lower() for x in ("auth","login","credential")) else "EXECUTION_ERROR",note=msg[:240])
   p["last_checked_at"]=iso();self._save(s);return p
 def _task(self,s,tid):return next((t for t in s["tasks"] if t["task_id"]==tid),None)
 def _update(self,tid,event=None,**kw):
  with self.lock:
   s=self._load();t=self._task(s,tid);t.update(kw,updated_at=iso())
   if event:t.setdefault("timeline",[]).append({"at":iso(),"stage":t.get("current_stage"),"event":event})
   self._save(s);return dict(t)
 def dispatch(self,title,description="",strategy="STRICT_STAGE_GATE",goal_id=None,core_review_reason=None):
  if core_review_reason not in {None, "production_change", "security_change", "strategy_promotion", "unresolved_material_conflict"}:
   raise ValueError("Unknown core review reason")
  title=title.strip()
  if not title or len(title)>1000:raise ValueError("제목은 1~1000자여야 합니다")
  tid=f"qtask-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}";t={"task_id":tid,"goal_id":goal_id,"title":title,"description":(description or title)[:12000],"strategy":"STRICT_STAGE_GATE","status":"QUEUED","current_stage":1,"stage_label":"1/5 Gemini 계획 대기","core_review_reason":core_review_reason,"progress_pct":0,"artifacts":[],"timeline":[{"at":iso(),"stage":0,"event":"외부 과업 접수"}],"verdict":"PENDING","next_retry_at":None,"created_at":iso(),"updated_at":iso()}
  for n in range(1,6):t[f"stage{n}_output"]=""
  with self.lock:
   s=self._load()
   if len([x for x in s["tasks"] if x["status"] not in FINAL])>=20:raise RuntimeError("대기 과업 20개 초과")
   s["tasks"].insert(0,t);s["tasks"]=s["tasks"][:200];self._save(s)
  self._launch(tid);return t
 def upsert_goal(self,title,description="",success_criteria="",goal_id=None,auto_continue=True,cadence_hours=24):
  title=title.strip()
  if not title or len(title)>500:raise ValueError("목표 제목은 1~500자여야 합니다")
  with self.lock:
   s=self._load();g=next((x for x in s["goals"] if x["goal_id"]==goal_id),None) if goal_id else None
   if not g:
    if len(s["goals"])>=7:raise ValueError("AGI 핵심 목표는 최대 7개입니다")
    g={"goal_id":goal_id or f"goal-{uuid.uuid4().hex[:10]}","created_at":iso(),"iterations":0};s["goals"].append(g)
   g.update(title=title,description=description[:6000],success_criteria=success_criteria[:4000],status="ACTIVE",auto_continue=bool(auto_continue),cadence_hours=max(1,min(int(cadence_hours),168)),updated_at=iso(),next_run_at=g.get("next_run_at"))
   self._save(s);gid=g["goal_id"]
  self.maintain_goals();return self.get_goal(gid)
 def set_goal_auto(self,gid,enabled):
  with self.lock:
   s=self._load();g=next((x for x in s["goals"] if x["goal_id"]==gid),None)
   if not g:raise ValueError("Goal not found")
   g["auto_continue"]=bool(enabled);g["updated_at"]=iso()
   if enabled and not g.get("next_run_at"):g["next_run_at"]=None
   self._save(s)
  launched=self.maintain_goals() if enabled else []
  return {"goal":self.get_goal(gid),"launched_task_ids":launched}
 def set_all_goals_auto(self,enabled):
  with self.lock:
   s=self._load()
   for g in s["goals"]:g.update(auto_continue=bool(enabled),updated_at=iso())
   self._save(s)
  launched=self.maintain_goals() if enabled else []
  return {"goals":self.list_goals(),"launched_task_ids":launched}
 def list_goals(self):
  s=self._load();tasks=s["tasks"];out=[]
  for raw in s["goals"]:
   g=dict(raw);linked=[t for t in tasks if t.get("goal_id")==g["goal_id"]];completed=[t for t in linked if t["status"]=="COMPLETED"]
   active=next((t for t in linked if t["status"] not in FINAL),None);g.update(task_count=len(linked),completed_task_count=len(completed),progress_pct=(active.get("progress_pct",0) if active else (100 if completed else 0)),current_task_id=active.get("task_id") if active else None,last_activity_at=(linked[0].get("updated_at") if linked else g.get("updated_at")))
   out.append(g)
  return out
 def get_goal(self,gid):
  g=next((x for x in self.list_goals() if x["goal_id"]==gid),None)
  if g:g["tasks"]=[t for t in self.list_tasks() if t.get("goal_id")==gid]
  return g
 def _latest_task_for_goal(self,gid):
  # dispatch()가 새 과업을 tasks 리스트 맨 앞(index 0)에 넣으므로, list_tasks()의
  # 첫 매치가 이 목표의 가장 최근 시도다.
  return next((t for t in self.list_tasks() if t.get("goal_id")==gid),None)
 def _feedback_brief(self,gid):
  # 2026-09-13 소유자 지적: 자율 반복이 매번 목표 원문(title/description/success_criteria)만
  # 반복해 사실상 동일한 계획을 계속 만들어냈다("동일한 목표를 계속 주는 게 무슨 AGI야?").
  # 직전 시도의 실제 결과(5단계 최종 검수 텍스트 또는 정지 사유)를 다음 프롬프트에 반드시
  # 포함시켜, 이미 충족된 부분을 반복하지 않고 지적된 격차를 우선 해소하도록 유도한다.
  prior=self._latest_task_for_goal(gid)
  if not prior:return ""
  final_review=str(prior.get("stage5_output","")).strip()
  if final_review:
   return (f"\n\n## 직전 시도({prior.get('task_id')}) 최종 검수 결과 - 반드시 반영, 이미 충족된 부분은 반복 금지\n"
           f"상태: {prior.get('status')} · 판정: {prior.get('verdict')}\n{final_review[:4000]}\n\n"
           "위에서 지적된 격차·미흡·미검증 사항을 이번 실행의 최우선 과제로 삼아라. "
           "직전과 실질적으로 동일한 계획을 다시 제시하지 마라.")
  reached=int(prior.get("current_stage",1) or 1)
  return (f"\n\n## 직전 시도({prior.get('task_id')}) 상태 - 참고해 원인부터 해결할 것\n"
          f"{prior.get('status')} · {reached}/5 단계에서 정지({prior.get('stage_label','')}). "
          "이 정지 원인을 먼저 해결하는 계획을 세워라.")
 def _real_evidence_for_goal(self,gid):
  # 2026-09-15 소유자 지시("실제로 DB를 스캔하는 실행 도구를 연결할까요?" → "실제 실행
  # 도구 연결"): goal_1(수집 데이터 무결점)이 실제 DB를 한 번도 열어보지 않고
  # "검증 방법론" 텍스트만 반복 생산하고 있었다(5단계 전부 순수 LLM 텍스트 생성이라
  # 실행 도구가 없었음). 신뢰된 컨트롤러(이 코드)가 읽기 전용 스캐너를 직접 실행해
  # 실측값을 프롬프트에 못박아, 모델이 가상의 프로토콜 대신 실제 숫자를 놓고
  # 판단하게 한다. 지금은 goal_1 전용 - 다른 목표로 일반화하려면 별도 스캐너가 필요.
  if gid!="goal_1_zero_defect_data":return ""
  try:
   from services.data_integrity_scan import run_scan
   result=run_scan()
  except Exception as exc:
   return (f"\n\n## 실측 데이터 스캔 실패 - 이 사실 자체를 다뤄라\n"
           f"읽기 전용 스캐너(services/data_integrity_scan.py) 실행이 실패했다: {str(exc)[:500]}\n"
           "이 오류를 원인까지 파악하는 것이 이번 실행의 최우선 과제다. 스캔 실패를 "
           "무시하고 가상의 데이터를 가정한 계획을 세우지 마라.")
  lines=["\n\n## 실측 데이터 스캔 결과 (지금 막 읽기 전용으로 직접 조회함 · 아래 숫자만 사실)"]
  for t in result["tables"]:
   lines.append(f"- {t['label']}: 총 {t['row_count']:,}행 · 최신 날짜 {t['latest_date']}"
                f"(오늘 대비 {t['staleness_days']}일 지연) · 비정상 시가/종가 {t['invalid_ohlc_count']:,}건"
                f" · high<low {t['high_less_than_low_count']:,}건 · 미래 날짜 {t['future_dated_count']:,}건")
  lines.append(f"- 추적 중인 미해결 품질 이슈: {result['open_quality_issues_tracked']}건"
              f"(가장 오래된 건 감지일 {result['oldest_open_quality_issue_detected_at']})")
  lines.append("이 실측값에 근거해 우선순위를 정하고 구체적 원인 가설을 세워라. "
              "스캐너가 다루지 않는 항목에 대해서만 추가 명세를 작성하고, "
              "이미 스캔된 항목을 다시 '검증 방법을 설계하겠다'는 식으로 반복하지 마라.")
  return "\n".join(lines)
 def _collect_integrity_findings(self,g):
  # Persist a bounded scan checkpoint instead of another five-model prose task.
  with self.lock:
   s=self._load();target=next(x for x in s["goals"] if x["goal_id"]==g["goal_id"])
   due=target.get("integrity_next_scan_at")
   if due and datetime.fromisoformat(due)>now():return []
   target["integrity_next_scan_at"]=iso(now()+timedelta(hours=int(g.get("cadence_hours",24))))
   target["integrity_status"]="SCANNING";self._save(s)
  try:
   from services.data_integrity_scan import run_scan
   from services.integrity_findings import build_findings
   record=build_findings(run_scan())
  except Exception as exc:
   # Do not turn scanner failure into an LLM prompt or serialize possible credentials.
   record={"status":"SCAN_FAILED","error_type":type(exc).__name__,"findings":[],"code_job_eligible":False,"goal_verified":False}
  self.artifact_dir.mkdir(parents=True,exist_ok=True)
  artifact=self.artifact_dir/f"integrity-{uuid.uuid4().hex}.json"
  artifact.write_text(json.dumps(record,ensure_ascii=False,indent=2,default=str))
  with self.lock:
   s=self._load();target=next(x for x in s["goals"] if x["goal_id"]==g["goal_id"])
   target.update(integrity_status=record["status"],integrity_artifact=str(artifact),integrity_checked_at=iso(),
                 integrity_finding_count=len(record["findings"]),updated_at=iso())
   target["next_run_at"]=target["integrity_next_scan_at"];self._save(s)
  return []
 def maintain_goals(self):
  # 대기·실행·오류 재시도를 포함해 전역 미완료 과업은 항상 하나만 둔다.
  if self.active or any(t.get("status")=="QUEUED" or str(t.get("status","")).startswith("RUNNING_") for t in self.list_tasks()):return []
  launched=[]
  # 2026-09-15 소유자 지시("1번 돌고 100%가 안되면 계속 검토를 해야지 · Agentic AI
  # 개념으로 적용해줘"): 이전엔 디스패치 직후 결과와 무관하게 무조건 next_run_at을
  # +cadence_hours로 박아뒀다 - 초안 저장(DRAFT_READY)/무효(INVALIDATED)처럼 실제로는
  # 아무것도 검증되지 않았는데도 다음 날까지 24시간을 그냥 기다리는 구조였다. 이제
  # "검증된 완료(COMPLETED·verdict=PASS, 핵심 검토를 통과한 경우만 가능)"에 도달했을
  # 때만 그 시점부터 cadence_hours를 쉬고, 그 외 모든 결과(초안/무효/실패 등 - 아직
  # 목표가 완수되지 않았다는 뜻)는 대기 없이 다음 감시 주기(60초)에 곧바로 이어서
  # 계속 시도한다. next_run_at 필드는 더 이상 게이트가 아니라 화면 표시용 힌트다.
  #
  # 같은 이유로 "목표 리스트 순서상 앞선 목표가 항상 이긴다"도 더 이상 안전하지
  # 않다 - 어차피 못 끝나는 목표(진짜 COMPLETED+PASS에 도달 못 함)가 항상 먼저면
  # 뒤 목표는 영원히 차례가 안 온다(실측: goal_6이 이 수정 배포 후에도 계속 굶음).
  # 지금 실행 가능한(due) 목표들 중 "가장 오래 전에 마지막으로 시도한" 목표부터
  # 공정하게 순번을 준다.
  candidates=[]
  for g in self.list_goals():
   if g.get("status")!="ACTIVE" or not g.get("auto_continue") or g.get("current_task_id"):continue
   if g["goal_id"]=="goal_1_zero_defect_data" and g.get("integrity_next_scan_at"):
    if datetime.fromisoformat(g["integrity_next_scan_at"])>now():continue
   prior=self._latest_task_for_goal(g["goal_id"])
   verified_pass=bool(prior) and prior.get("status")=="COMPLETED" and prior.get("verdict")=="PASS"
   if verified_pass:
    try:cooldown_end=datetime.fromisoformat(prior["updated_at"])+timedelta(hours=int(g.get("cadence_hours",24)))
    except (ValueError,KeyError,TypeError):cooldown_end=now()
    if cooldown_end>now():
     with self.lock:
      s=self._load();target=next((x for x in s["goals"] if x["goal_id"]==g["goal_id"]),None)
      if target and target.get("next_run_at")!=iso(cooldown_end):target["next_run_at"]=iso(cooldown_end);self._save(s)
     continue
   candidates.append((g,str((prior or {}).get("updated_at") or "")))
  if not candidates:return []
  g=min(candidates,key=lambda item:item[1])[0]
  if g["goal_id"]=="goal_1_zero_defect_data":return self._collect_integrity_findings(g)
  description=(f"목표: {g['title']}\n설명: {g.get('description','')}\n성공 기준: {g.get('success_criteria','')}\n"
               f"이 목표를 향해 현재 검증 가능한 다음 과업 한 단위를 계획하고 수행하라."
               f"{self._real_evidence_for_goal(g['goal_id'])}"
               f"{self._feedback_brief(g['goal_id'])}")
  t=self.dispatch(f"[{g['title']}] 자율 실행 #{int(g.get('iterations',0))+1}",description,goal_id=g["goal_id"]);launched.append(t["task_id"])
  with self.lock:
   s=self._load();target=next(x for x in s["goals"] if x["goal_id"]==g["goal_id"]);target["iterations"]=int(target.get("iterations",0))+1;target["next_run_at"]=None;self._save(s)
  return launched
 def _launch(self,tid):
  with self.lock:
   # 한 작업만 실행해 공급자 구독 한도를 동시에 소진하지 않는다.
   if tid in self.active or self.active:return
   self.active.add(tid)
  threading.Thread(target=self._guard,args=(tid,),daemon=True).start()
 def _guard(self,tid):
  try:self._run(tid)
  except Exception as e:self._update(tid,event="예외 · 재시도",status="WAITING_RETRY",stage_label=f"현재 단계 오류: {str(e)[:160]}",next_retry_at=iso(now()+timedelta(minutes=5)))
  finally:
   with self.lock:self.active.discard(tid)
 def _prior(self,t):
  p=[f"과업: {t['title']}\n상세: {t['description']}"]
  for n in range(1,6):
   if t.get(f"stage{n}_output"):p.append(f"\n## 단계 {n}\n{t[f'stage{n}_output']}")
  return "\n".join(p)
 def _review_context(self,t):
  # Character bound is not a token quota. Include provenance and mark partial excerpts.
  bundle={"task_id":t["task_id"],"title":t["title"],"description":t["description"][:1600],
          "core_review_reason":t.get("core_review_reason"),
          "artifacts":[{"stage":a.get("stage"),"path":a.get("path"),"hash":a.get("hash")} for a in t.get("artifacts",[])],
          "latest_excerpt":str(t.get("stage4_output") or t.get("stage3_output") or "")[:2600],
          "scope":"부분 요약. 필요한 증거가 없으면 REVISE. 실행 완료로 인증하지 말 것."}
  return json.dumps(bundle,ensure_ascii=False)[:6000]
 def _reserve_core_review(self,t,provider_name):
  if not t.get("core_review_reason"):return False
  digest=hashlib.sha256(self._review_context(t).encode()).hexdigest()
  key=f"{provider_name}:{t['task_id']}:{t['current_stage']}:{digest}"
  with self.lock:
   state=self._load();records=state.setdefault("core_review_reservations",{})
   if key in records:return False
   today=now().date().isoformat()
   # Attempts, including failures, consume a slot; no silent retry spending.
   if sum(r.get("date")==today and r.get("provider")==provider_name for r in records.values())>=4:return False
   records[key]={"date":today,"provider":provider_name,"at":iso(),"task_id":t["task_id"],"evidence_hash":digest}
   self._save(state);return True
 def _plan_prompt(self,t):
  return "최고 수준 사고로 실행 계획과 완료 기준을 수립하라. Gemini는 근거 수집, Qwen은 분류·중복 제거·필드 추출·형식 변환만, DeepSeek는 그 전처리 점검, Claude는 독립 검증·보강을 담당하도록 지시하라. 사용자에게 질문하거나 승인을 기다리지 말고, 미정값은 명시적 가정과 추후 검증 항목으로 기록하라. 직접 완료라 하지 마라.\n\n"+t["description"]
 def _codex(self,t,final=False):
  prompt=("최고 수준 사고로 전체 산출물을 최종 검수하라. 첫 줄에 PASS 또는 REVISE. 요구 충족·근거·실행검증·위험을 확인하고 미실행을 완료라 하지 마라. 사용자에게 질문하지 말고 미정값은 가정과 한계로 기록하라.\n\n"+self._review_context(t)) if final else self._plan_prompt(t)
  import sys
  ag=ROOT/"antigravity_workspace"
  if str(ag) not in sys.path:sys.path.insert(0,str(ag))
  from execution.codex_sdk import CodexSDKAdapter
  from execution.contracts import ExecutionRequest
  stage="final" if final else "plan";run_id=f"agi_{t['task_id']}_{stage}_{int(time.time())}"
  result=CodexSDKAdapter().submit(ExecutionRequest(prompt=prompt,workspace=str(ROOT),role="final_verifier" if final else "planner",run_id=run_id,task_id=t["task_id"],model="gpt-5.6-sol",sandbox="read-only"))
  if result.get("status")!="SUCCEEDED":raise RuntimeError(str(result.get("error") or result.get("error_type") or result.get("status")))
  out=Path(result["artifact_path"]);c=out.read_text().strip() if out.exists() else ""
  if not c:raise RuntimeError("Codex 결과 없음")
  return c
 def _gemini_call(self,prompt,timeout=300):
  # 2026-09-14 소유자 지적("Gemini 한도에 왜 걸렸지? 거의 안 썼는데") 조사 결과: API 키를
  # 서브프로세스 환경에 넘기지 않았고 모델도 지정하지 않아, gemini CLI가 기본값
  # gemini-3.6-flash로 익명 무료 등급(하루 20회 한도)에 걸려 실제로는 API 키의 정식
  # 쿼터(1,500 RPD)를 전혀 못 쓰고 있었다. RSS 파이프라인이 실제로 문제없이 쓰고 있는
  # gemini-3.7-flash + 키 전달로 맞춘다. _gemini()/_gemini_qc() 공용 호출부.
  #
  # 2026-09-14 추가 소유자 지적("CLI로 어제 연결 다 했는데"): gemini CLI는 API 키 외에
  # "Login with Google" OAuth 방식도 있고(조직 유료 Code Assist 라이선스가 있으면 그
  # 쪽이 진짜 유료 쿼터), 이 저장소 실측 결과 ~/.gemini/settings.json이 아예 없어
  # 지금은 OAuth가 설정돼 있지 않았다. 다만 소유자가 그 OAuth 로그인을 나중에 완료할
  # 경우를 대비해, 그 설정 파일이 있으면 API 키를 강제로 덮어쓰지 않고 CLI 자체의
  # 인증 우선순위(보통 이미 로그인된 세션 우선)를 그대로 따르게 한다.
  gemini_settings_exists=(Path.home()/".gemini"/"settings.json").exists()
  gemini_env=dict(os.environ)
  if not gemini_settings_exists:
   gemini_key=secret("GEMINI_API_KEY")
   if gemini_key:gemini_env["GEMINI_API_KEY"]=gemini_key
  model=secret("AGI_GEMINI_MODEL") or "gemini-3.7-flash"
  p=subprocess.run([str(BINS['gemini']),"--prompt",prompt,"--model",model,"--approval-mode","plan","--output-format","json"],cwd=ROOT,capture_output=True,text=True,timeout=timeout,env=gemini_env)
  if p.returncode:raise RuntimeError((p.stderr or p.stdout)[:3000])
  try:d=json.loads(p.stdout);c=str(d.get("response") or d.get("result") or d.get("content") or "")
  except json.JSONDecodeError:c=p.stdout
  return c.strip()
 def _gemini(self,t):
  prompt="GPT 계획에 필요한 대용량 근거와 컨텍스트를 정리하라. 사실/추정/미확인을 나누고 다음 실행용 입력을 작성하라.\n\n"+self._prior(t)
  c=self._gemini_call(prompt)
  if not c:raise RuntimeError("Gemini 결과 없음")
  return c
 def _qwen(self,t):
  body=json.dumps({"model":"qwen2.5-coder:7b","prompt":"다음 자료에서 항목 분류, 중복 제거, 필드 추출, 체크리스트 변환만 수행하라. 설계·판단·코딩·완료 판정·후속 승계를 하지 마라.\n\n"+self._prior(t),"stream":False,"options":{"temperature":.1,"num_predict":900}}).encode()
  with urlopen(Request("http://127.0.0.1:11434/api/generate",data=body,headers={"Content-Type":"application/json"}),timeout=300) as r:c=json.loads(r.read()).get("response","")
  if not c.strip():raise RuntimeError("Qwen 결과 없음")
  return c.strip()
 def _deepseek(self,q):
  key=secret("DEEPSEEK_API_KEY")
  if not key:raise RuntimeError("DeepSeek API 키 없음")
  import sys
  ag=ROOT/"antigravity_workspace"
  if str(ag) not in sys.path:sys.path.insert(0,str(ag))
  from memory.llm_usage_ledger import LLMUsageLedger
  ledger=LLMUsageLedger();limit_krw=int(secret("DEEPSEEK_MONTHLY_BUDGET_KRW") or "10000");limit_usd=limit_krw/1400
  reservation=ledger.reserve_monthly_budget("deepseek",.015,limit_usd)
  if not reservation:raise RuntimeError(f"DeepSeek 월 {limit_krw:,}원 예산 상한 도달")
  model=secret("DEEPSEEK_MODEL") or "deepseek-flash"
  # 2026-09-14 발견: deepseek-flash는 추론 모델이라 reasoning_content에 사고 과정을 먼저
  # 쓰는데, max_tokens=1200으로는 그 추론만으로 예산이 소진돼 정작 content(최종 답변)가
  # 빈 문자열로 오는 경우가 실제로 있었다(계정 잔액 문제와는 별개 - 200 응답이지만
  # 내용이 비어 있었음). 실측 결과 이 검토 프롬프트 하나에 reasoning_tokens만 6,800개
  # 넘게 쓰는 경우가 있어(내용 3,394자 기준 총 8,680 completion 토큰) 16000으로 올린다.
  # 그래도 비면 명시적으로 실패 처리해 위(3단계 호출부)의 Gemini 대체가 발동하게 한다.
  # 2026-09-17 소유자 지시("DeepSeek는 추론모델이 아니라 Coding으로"): 계정에 실제
  # 사용 가능한 모델은 deepseek-flash/deepseek-v4-pro 둘뿐이고 실측 확인 결과 둘 다
  # 추론 모델이다(트리비얼한 프롬프트에도 reasoning_tokens 65개 vs 실답 12개 - 이게
  # 9/15~16 예산 급속 소진의 실제 원인이기도 하다). 모델 자체를 코딩 전용으로 바꿀 순
  # 없지만, `reasoning_effort:"none"`을 실제로 호출해보니 reasoning_content가 완전히
  # 사라지고 completion_tokens가 77→11로 줄었다(같은 프롬프트 실측) - 이걸로 "추론 없이
  # 바로 코딩 답변만" 요청을 실질적으로 충족한다.
  body=json.dumps({"model":model,"messages":[{"role":"user","content":"주어진 업무를 분석하고 계획·초안·검증 항목을 작성하라. 사실과 추정을 나누고 실제 실행하지 않은 작업을 완료로 선언하지 마라.\n\n"+q[:24000]}],"temperature":.1,"max_tokens":16000,"reasoning_effort":"none"}).encode()
  try:
   with urlopen(Request("https://api.deepseek.com/chat/completions",data=body,headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"}),timeout=120) as r:d=json.loads(r.read())
   content=(d["choices"][0]["message"].get("content") or "").strip()
   if not content:raise RuntimeError(f"DeepSeek 결과 없음(finish_reason={d['choices'][0].get('finish_reason')} - 추론 토큰 소진 가능성)")
   ledger.finalize_reservation(reservation,"strict_agi_orchestrator",{"provider":"deepseek","model":model,"usage":d.get("usage") or {},"is_fallback":False});reservation=None
   return content
  finally:
   if reservation:ledger.release_reservation(reservation)
 def _gemini_qc(self,q):
  # 소유자 지시(2026-09-14): "DeepSeek는 중요한 AI가 아니니 Gemini가 대체하도록 해."
  # DeepSeek 계정 잔액 소진(402) 등으로 사용 불가할 때, 3단계의 DeepSeek 점검 역할을
  # Gemini가 대신한다(핵심 판단자인 Codex/Claude 자리는 대체 대상이 아님 - 이건 그
  # 아래 draft 등급 전처리 점검 역할만 이관하는 것).
  prompt="주어진 업무를 분석하고 계획·초안·검증 항목을 작성하라. 사실과 추정을 나누고 실제 실행하지 않은 작업을 완료로 선언하지 마라. (DeepSeek 계정/한도 문제로 Gemini가 이 점검을 대신 수행합니다.)\n\n"+q[:24000]
  c=self._gemini_call(prompt,timeout=120)
  if not c:raise RuntimeError("Gemini(DeepSeek 대체) 결과 없음")
  return c
 def deepseek_budget_status(self):
  import sys
  ag=ROOT/"antigravity_workspace"
  if str(ag) not in sys.path:sys.path.insert(0,str(ag))
  from memory.llm_usage_ledger import LLMUsageLedger
  ledger=LLMUsageLedger();limit_krw=int(secret("DEEPSEEK_MONTHLY_BUDGET_KRW") or "10000")
  spent_usd=ledger.cost_this_month_usd("deepseek");spent_krw=round(spent_usd*1400,2)
  provider_summary=ledger.summary().get("by_provider",{}).get("deepseek",{})
  return {"provider":"deepseek","model":secret("DEEPSEEK_MODEL") or "deepseek-flash","month":now().strftime("%Y-%m"),"limit_krw":limit_krw,"spent_krw":spent_krw,"remaining_krw":max(0,round(limit_krw-spent_krw,2)),"spent_usd_estimate":round(spent_usd,8),"call_count":int(provider_summary.get("call_count") or 0),"guard_status":"AVAILABLE" if spent_krw<limit_krw else "BUDGET_EXHAUSTED","fx_assumption_krw_per_usd":1400}
 def _claude(self,t):
  p=subprocess.run([str(BINS['claude']),"-p","GPT 계획, Gemini 근거, Qwen+DeepSeek 실행물을 확인·보강해 적용 가능한 최종 산출물을 완성하라. 실제 적용·테스트 증거와 미실행을 구분하라.\n\n"+self._review_context(t),"--output-format","json","--tools","","--strict-mcp-config","--max-turns","1","--effort","medium","--permission-mode","plan"],cwd=ROOT,capture_output=True,text=True,timeout=600)
  if p.returncode:raise RuntimeError((p.stderr or p.stdout)[:3000])
  c=str(json.loads(p.stdout or "{}").get("result","")).strip()
  if not c:raise RuntimeError("Claude 결과 없음")
  return c
 def _claude_generic(self,prompt):
  # 2026-09-17 소유자 지시로 1단계 계획의 주 담당이 Claude가 됨에 따라 추가한 범용
  # 호출부 - _claude(t)는 core 검토(§4단계) 전용 프롬프트(리뷰·보강 역할)라 그대로 못
  # 쓴다. 이전엔 이 자리가 "일반 계획의 Claude 대체 호출은 사용량 정책상 비활성화됨"
  # 이라는 고정 예외를 던지는 스텁이었다 - 이제 실제로 호출한다.
  # 2026-09-17/18 실측 발견: --tools ''만으로는 안 막힌다 - 프로젝트에 등록된 앰비언트
  # MCP 서버(예: docs 스킬의 guide 툴)를 이 헤드리스 호출이 그대로 물려받아서, 프롬프트
  # 내용에 따라 그 MCP 툴을 first turn에 호출 시도하고(권한 거부로 실패), max_turns=1
  # 예산을 통째로 날려 최종 텍스트 응답(result) 자체가 없는 상태로 끝난다(returncode=0
  # · subtype="error_max_turns" · result 필드 없음 - "Claude 결과 없음"으로 이어짐).
  # --strict-mcp-config(빈 --mcp-config 암시)로 앰비언트 MCP 서버 자체를 안 물려받게
  # 해야 실제로 1턴 안에 텍스트 답이 나온다(직접 재현·수정 확인함).
  p=subprocess.run([str(BINS['claude']),"-p",prompt,"--output-format","json","--tools","","--strict-mcp-config","--max-turns","1","--effort","medium","--permission-mode","plan"],cwd=ROOT,capture_output=True,text=True,timeout=600)
  if p.returncode:raise RuntimeError((p.stderr or p.stdout)[:3000])
  c=str(json.loads(p.stdout or "{}").get("result","")).strip()
  if not c:raise RuntimeError("Claude 결과 없음")
  return c
 def _ready(self,ps,name):
  return bool(ps.get(name,{}).get("auth_ready")) and ps.get(name,{}).get("status") not in {"WAITING_QUOTA","WAITING_AUTH","UNAVAILABLE"}
 def _finish_stage(self,t,stage,provider,content):
  self.artifact_dir.mkdir(parents=True,exist_ok=True);path=self.artifact_dir/f"{t['task_id']}_s{stage}_{provider}.md";path.write_text(content);a={"stage":stage,"provider":provider,"path":str(path),"hash":hashlib.sha256(content.encode()).hexdigest(),"created_at":iso(),"preview":content[:1000]};arts=list(t["artifacts"])+[a]
  if stage==5 and provider=="policy":
   self._update(t["task_id"],event="일반 분석 초안 저장 · 목표 완료 인증 아님",status="DRAFT_READY",progress_pct=80,stage_label="분석 초안 준비 · 실제 적용/검증 별도",stage5_output=content,artifacts=arts,artifact_path=str(path),artifact_hash=a["hash"],verdict="NOT_VERIFIED",next_retry_at=None)
   return
  if stage==5:
   prior_outputs=all(str(t.get(f"stage{n}_output","")).strip() for n in range(1,5));stages={x.get("stage") for x in arts if isinstance(x,dict)}
   if stages!={1,2,3,4,5} or not prior_outputs:
    self._update(t["task_id"],event="GPT 완료 판정 거부 · 증거 불충분",status="INVALIDATED",progress_pct=0,stage_label="완료 무효 · 5단계 산출물/지문 불충분",stage5_output=content,artifacts=arts,verdict="EVIDENCE_INVALID",invalidated_at=iso(),next_retry_at=None)
   else:
    first_line=content.strip().splitlines()[0].strip().upper() if content.strip() else ""
    verdict=first_line if first_line in {"PASS","REVISE"} else "INVALID_VERDICT"
    # Text artifacts do not establish that the requested work was applied and tested.
    self._update(t["task_id"],event="최종 검토 저장 · 실행 증거 검증 필요",status="NEEDS_RECONCILIATION",progress_pct=80,stage_label="최종 검토 수신 · 실행 증거 게이트 필요",stage5_output=content,artifacts=arts,artifact_path=str(path),artifact_hash=a["hash"],verdict=verdict,next_retry_at=None)
  else:self._update(t["task_id"],event=f"{stage}단계 산출물 저장",status="QUEUED",current_stage=stage+1,progress_pct=stage*20,stage_label=f"{stage+1}/5 {PIPELINE[stage][2]} 대기",artifacts=arts,next_retry_at=None,**{f"stage{stage}_output":content})
 def _run(self,tid):
  t=self.get_task(tid)
  if not t or t["status"] in FINAL or t["status"] in {"NEEDS_RECONCILIATION","WAITING_CORE_BUDGET","WAITING_REPAIR"}:return
  ps=self.refresh_providers();st=int(t["current_stage"]);core=bool(t.get("core_review_reason"))
  # 2026-09-17 소유자 지시: 1단계는 Claude가 주 담당(GPT 토큰 소진 상태라 GPT/Astra는
  # 이 표에 아예 없다 - "GPT Astra 불가 시 Claude 대체"는 Claude를 1단계 기본값으로
  # 승격하는 것으로 반영했다). 2단계는 Gemini 그대로(대용량 컨텍스트 근거 수집).
  name="claude" if st==1 else ("gemini" if st==2 else ("claude" if st==4 and core else ("codex" if st==5 and core else ("policy" if st==5 else "deepseek"))))
  if name in {"gemini","deepseek"} and not self._ready(ps,name):
   alternate="deepseek" if name=="gemini" else "gemini"
   if self._ready(ps,alternate):name=alternate
  elif name=="claude" and not core and not self._ready(ps,name) and self._ready(ps,"gemini"):
   # 1단계 일반 계획에서만 - core 검토(4/5단계)는 조용히 다른 공급자로 안 바꾼다(기존 설계 그대로).
   name="gemini"
  if name!="policy" and not self._ready(ps,name):
   self._update(tid,status="WAITING_QUOTA",stage_label=f"{name} 대기 · 다른 목표 진행 가능",next_retry_at=ps.get(name,{}).get("reset_at") or iso(now()+timedelta(minutes=10)))
   return
  if core and name in {"claude","codex"} and not self._reserve_core_review(t,name):
   self._update(tid,status="WAITING_CORE_BUDGET",stage_label="핵심 검토 중복 또는 일일 4회 상한 · 추가 호출 보류",next_retry_at=None)
   return
  self._update(tid,status=f"RUNNING_STAGE_{st}",stage_label=f"{st}/5 {name} 실행",event=f"{name} 단계 시작")
  try:
   if name=="policy":content="일반 분석 산출물 저장. 핵심 모델 호출 생략. 코드 적용·검사·목표 완료는 인증하지 않음."
   elif name=="codex":content=self._codex(t,True)
   elif name=="claude" and core:content=self._claude(t)
   else:
    role={1:"계획과 수락 기준",2:"원문 근거 수집 및 사실/추정 구분",3:"분석·구현 초안과 검증 항목",4:"오류 보강 및 간결한 인계"}[st]
    prompt=f"담당 업무: {role}. Gemini/DeepSeek/Claude가 일반 업무를 수행한다. 실행하지 않은 변경을 완료라고 쓰지 말라.\n"+self._prior(t)[-16000:]
    def _call(n):return self._claude_generic(prompt) if n=="claude" else (self._gemini_call(prompt) if n=="gemini" else self._deepseek(prompt))
    try:
     content=_call(name)
    except Exception as primary_error:
     alternate={"claude":"gemini","gemini":"deepseek","deepseek":"gemini"}.get(name,"gemini")
     if not self._ready(ps,alternate):raise
     self._failure(name,str(primary_error))
     name=alternate
     content=_call(name)
   if not content.strip():raise RuntimeError("빈 결과")
  except Exception as exc:
   failure=self._failure(name,str(exc));fresh=self.get_task(tid);counts=dict(fresh.get("stage_failures",{}));key=str(st);counts[key]=counts.get(key,0)+1
   # core 검토(4/5단계 claude/codex)만 1회 실패로 즉시 파킹한다 - 1단계 일반 Claude
   # 계획은 Gemini/DeepSeek처럼 3회까지 재시도한다(2026-09-17 우선순위 변경 반영).
   parked=counts[key]>=3 or (core and name in {"claude","codex"})
   self._update(tid,event=f"{name} 실패",status="WAITING_REPAIR" if parked else "EXECUTION_ERROR",stage_failures=counts,last_error=str(exc)[:1000],last_failed_provider=name,next_retry_at=None if parked else failure.get("reset_at") or iso(now()+timedelta(minutes=10)))
   return
  self._finish_stage(t,st,name,content)
 def check_and_resume(self):
  # Refresh occurs within the selected run. Select oldest runnable work, not newest blocked work.
  launched=[]
  if self.active:return {"checked_at":iso(),"resumed_task_ids":launched}
  parked={"NEEDS_RECONCILIATION","WAITING_CORE_BUDGET","WAITING_REPAIR"}
  for t in sorted(self.list_tasks(),key=lambda item:item.get("updated_at",item.get("created_at",""))):
   if t["status"] in FINAL or t["status"] in parked:continue
   retry=t.get("next_retry_at")
   try:due=not retry or datetime.fromisoformat(retry)<=now()
   except ValueError:due=False
   if due:
    self._launch(t["task_id"]);launched.append(t["task_id"]);break
  if not launched:launched=self.maintain_goals()
  return {"checked_at":iso(),"resumed_task_ids":launched}
 def status(self):return {"status":"success","monitor_status":"RUNNING" if self.monitor and self.monitor.is_alive() else "STOPPED","monitor_interval_seconds":self.monitor_interval,"providers":self.refresh_providers(),"deepseek_budget":self.deepseek_budget_status(),"waiting_tasks":len([t for t in self.list_tasks() if t["status"] not in FINAL]),"checked_at":iso(),"pipeline":[{"stage":n,"provider":p,"role":r} for n,p,r in PIPELINE],"measurement_note":"구독 잔여 카운터는 공개되지 않아 실제 단계 요청의 한도 오류와 리셋 시각을 기록합니다."}
 def dashboard_status(self):
  t=next((x for x in self.list_tasks() if x["status"] not in FINAL),None);return {"timestamp":iso(),"current_stage":t["status"] if t else "IDLE","status_message":t["stage_label"] if t else "대기 작업 없음 · 60초 감시 중","target_unlock_time":t.get("next_retry_at") if t else None,"target_task":t["task_id"] if t else None,"pipeline_architecture":"Gemini 계획·근거 → DeepSeek 분석·보강 → 핵심만 Claude/Sol"}
 def list_tasks(self):return [dict(t) for t in self._load()["tasks"]]
 def get_task(self,tid):t=self._task(self._load(),tid);return dict(t) if t else None
 def active_sessions(self):
  s=self.status();purpose={"codex":"계획·최종검수","gemini":"근거·대용량 컨텍스트","qwen":"로컬 실행","deepseek":"독립 검증","claude":"확인·보강·마무리"};return [{"session_id":f"monitor-{n}","agent":p["display_name"],"timestamp":p["last_checked_at"],"status":p["status"],"status_label":p["note"],"traffic_light":"RED" if p["status"] in {"WAITING_QUOTA","WAITING_AUTH","UNAVAILABLE"} else "GREEN","window_desc":"실제 요청 기반 감지","exhaustion_pct":None,"reset_at":p.get("reset_at"),"quota_purpose":purpose[n],"work_status":"단계 감시","current_action":purpose[n],"tokens_today":None,"tokens_month":None,"tokens_share_pct":None,"cost_str":"실측 없음","last_worked_file":"","active_topic":"5단계 하드 게이트","pending_tasks":[]} for n,p in s["providers"].items()]

strict_agi_orchestrator=StrictAGIOrchestrator();strict_agi_orchestrator.start() if os.getenv("CEO_BACKGROUND_JOBS", "1") != "0" else None
