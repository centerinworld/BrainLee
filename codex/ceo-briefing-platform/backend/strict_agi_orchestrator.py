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
PIPELINE=[(1,"codex","GPT 최고 사고 계획"),(2,"gemini","Gemini 근거·대용량 컨텍스트"),(3,"qwen_deepseek","Qwen 단순 전처리 + DeepSeek 초안 점검"),(4,"claude","Claude 확인·보강·마무리"),(5,"codex","GPT 최고 사고 최종검수")]
FINAL={"COMPLETED","FAILED","CANCELLED","INVALIDATED"}; LIMIT=("rate limit","usage limit","quota","too many requests","limit reached","resource exhausted","429","try again")

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
  probes={"codex":self._cli_probe("codex"),"gemini":self._cli_probe("gemini"),"claude":self._cli_probe("claude"),"qwen":{"auth_ready":q,"status":"AVAILABLE_LOCAL" if q else "UNAVAILABLE","note":"로컬 모델 확인" if q else "Ollama/Qwen 없음"},"deepseek":{"auth_ready":bool(secret("DEEPSEEK_API_KEY")),"status":"AVAILABLE_BUDGET_GUARDED" if secret("DEEPSEEK_API_KEY") else "WAITING_AUTH","note":"API 키·월 예산 게이트 확인" if secret("DEEPSEEK_API_KEY") else "API 키 필요"}}
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
  m=re.search(r"(?:retry|try again)\s+(?:in|after)\s+(\d+)\s*(minute|min|hour|hr)",msg.lower())
  if m:return now()+(timedelta(hours=int(m.group(1))) if m.group(2) in {"hour","hr"} else timedelta(minutes=int(m.group(1))))
  return now()+timedelta(hours=5)
 def _failure(self,name,msg):
  with self.lock:
   s=self._load();p=s["providers"][name];limited=any(x in msg.lower() for x in LIMIT)
   if limited:r=self._retry(msg);p.update(status="WAITING_QUOTA",quota_observed=True,reset_at=iso(r),note=f"실제 한도 감지 · {r.strftime('%m-%d %H:%M')} 이후 재시도")
   else:p.update(status="WAITING_AUTH" if any(x in msg.lower() for x in ("auth","login","credential")) else "EXECUTION_ERROR",note=msg[:240])
   p["last_checked_at"]=iso();self._save(s);return p
 def _task(self,s,tid):return next((t for t in s["tasks"] if t["task_id"]==tid),None)
 def _update(self,tid,event=None,**kw):
  with self.lock:
   s=self._load();t=self._task(s,tid);t.update(kw,updated_at=iso())
   if event:t.setdefault("timeline",[]).append({"at":iso(),"stage":t.get("current_stage"),"event":event})
   self._save(s);return dict(t)
 def dispatch(self,title,description="",strategy="STRICT_STAGE_GATE",goal_id=None):
  title=title.strip()
  if not title or len(title)>1000:raise ValueError("제목은 1~1000자여야 합니다")
  tid=f"qtask-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}";t={"task_id":tid,"goal_id":goal_id,"title":title,"description":(description or title)[:12000],"strategy":"STRICT_STAGE_GATE","status":"QUEUED","current_stage":1,"stage_label":"1/5 GPT 계획 대기","progress_pct":0,"artifacts":[],"timeline":[{"at":iso(),"stage":0,"event":"외부 과업 접수"}],"verdict":"PENDING","next_retry_at":None,"created_at":iso(),"updated_at":iso()}
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
 def maintain_goals(self):
  # 대기·실행·오류 재시도를 포함해 전역 미완료 과업은 항상 하나만 둔다.
  if any(t.get("status") not in FINAL for t in self.list_tasks()):return []
  launched=[]
  for g in self.list_goals():
   if g.get("status")!="ACTIVE" or not g.get("auto_continue") or g.get("current_task_id"):continue
   try:due=not g.get("next_run_at") or datetime.fromisoformat(g["next_run_at"])<=now()
   except ValueError:due=True
   if due:
    description=(f"목표: {g['title']}\n설명: {g.get('description','')}\n성공 기준: {g.get('success_criteria','')}\n"
                 f"이 목표를 향해 현재 검증 가능한 다음 과업 한 단위를 계획하고 수행하라."
                 f"{self._feedback_brief(g['goal_id'])}")
    t=self.dispatch(f"[{g['title']}] 자율 실행 #{int(g.get('iterations',0))+1}",description,goal_id=g["goal_id"]);launched.append(t["task_id"])
    with self.lock:
     s=self._load();target=next(x for x in s["goals"] if x["goal_id"]==g["goal_id"]);target["iterations"]=int(target.get("iterations",0))+1;target["next_run_at"]=iso(now()+timedelta(hours=target["cadence_hours"]));self._save(s)
    break
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
 def _plan_prompt(self,t):
  return "최고 수준 사고로 실행 계획과 완료 기준을 수립하라. Gemini는 근거 수집, Qwen은 분류·중복 제거·필드 추출·형식 변환만, DeepSeek는 그 전처리 점검, Claude는 독립 검증·보강을 담당하도록 지시하라. 사용자에게 질문하거나 승인을 기다리지 말고, 미정값은 명시적 가정과 추후 검증 항목으로 기록하라. 직접 완료라 하지 마라.\n\n"+t["description"]
 def _codex(self,t,final=False):
  prompt=("최고 수준 사고로 전체 산출물을 최종 검수하라. 첫 줄에 PASS 또는 REVISE. 요구 충족·근거·실행검증·위험을 확인하고 미실행을 완료라 하지 마라. 사용자에게 질문하지 말고 미정값은 가정과 한계로 기록하라.\n\n"+self._prior(t)) if final else self._plan_prompt(t)
  import sys
  ag=ROOT/"antigravity_workspace"
  if str(ag) not in sys.path:sys.path.insert(0,str(ag))
  from execution.codex_sdk import CodexSDKAdapter
  from execution.contracts import ExecutionRequest
  stage="final" if final else "plan";run_id=f"agi_{t['task_id']}_{stage}_{int(time.time())}"
  result=CodexSDKAdapter().submit(ExecutionRequest(prompt=prompt,workspace=str(ROOT),role="final_verifier" if final else "planner",run_id=run_id,task_id=t["task_id"],model=os.getenv("AGI_CODEX_MODEL") or None,sandbox="read-only"))
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
  gemini_env=dict(os.environ);gemini_key=secret("GEMINI_API_KEY")
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
  body=json.dumps({"model":model,"messages":[{"role":"user","content":"Qwen의 단순 전처리 결과에서 누락·분류 오류·근거 없는 주장을 점검하고 Claude가 검토할 입력 묶음으로 정리하라. 최종 판단이나 완료 판정을 하지 마라.\n\n"+q[:24000]}],"temperature":.1,"max_tokens":16000}).encode()
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
  prompt="Qwen의 단순 전처리 결과에서 누락·분류 오류·근거 없는 주장을 점검하고 Claude가 검토할 입력 묶음으로 정리하라. 최종 판단이나 완료 판정을 하지 마라. (DeepSeek 계정/한도 문제로 Gemini가 이 점검을 대신 수행합니다.)\n\n"+q[:24000]
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
  p=subprocess.run([str(BINS['claude']),"-p","GPT 계획, Gemini 근거, Qwen+DeepSeek 실행물을 확인·보강해 적용 가능한 최종 산출물을 완성하라. 실제 적용·테스트 증거와 미실행을 구분하라.\n\n"+self._prior(t),"--output-format","json","--max-turns","4","--effort","high","--permission-mode","plan"],cwd=ROOT,capture_output=True,text=True,timeout=600)
  if p.returncode:raise RuntimeError((p.stderr or p.stdout)[:3000])
  c=str(json.loads(p.stdout or "{}").get("result","")).strip()
  if not c:raise RuntimeError("Claude 결과 없음")
  return c
 def _claude_plan_fallback(self,t):
  # 소유자 지시(2026-09-14): GPT(Codex/Astra) 한도가 크게 줄어 매 반복마다 쓰기 어려우니,
  # 1단계(계획)만큼은 Codex가 막혀 있어도 Claude가 대신 세워 자율 루프가 멈추지 않게
  # 한다. 다만 이건 "완료 판정"이 아니라 "계획 초안"이므로, 5단계 최종검수는 여전히
  # Codex 전용으로 남겨 Claude가 스스로 완료를 인증하지 못하게 한다(듀얼채널이되 완료는
  # 단일 채널). stage1_provider 기록으로 어느 채널이 계획을 세웠는지 항상 감사 가능하다.
  prompt=self._plan_prompt(t)+"\n\n(참고: GPT/Codex 한도 소진으로 Claude가 대신 계획을 수립합니다. 이 계획은 초안이며, 5단계 최종검수는 GPT/Codex 한도 복구 후에만 통과합니다.)"
  p=subprocess.run([str(BINS['claude']),"-p",prompt,"--output-format","json","--max-turns","4","--effort","high","--permission-mode","plan"],cwd=ROOT,capture_output=True,text=True,timeout=600)
  if p.returncode:raise RuntimeError((p.stderr or p.stdout)[:3000])
  c=str(json.loads(p.stdout or "{}").get("result","")).strip()
  if not c:raise RuntimeError("Claude 계획 대체 결과 없음")
  return c
 def _ready(self,ps,name):
  return bool(ps.get(name,{}).get("auth_ready")) and ps.get(name,{}).get("status") not in {"WAITING_QUOTA","WAITING_AUTH","UNAVAILABLE"}
 def _finish_stage(self,t,stage,provider,content):
  self.artifact_dir.mkdir(parents=True,exist_ok=True);path=self.artifact_dir/f"{t['task_id']}_s{stage}_{provider}.md";path.write_text(content);a={"stage":stage,"provider":provider,"path":str(path),"hash":hashlib.sha256(content.encode()).hexdigest(),"created_at":iso(),"preview":content[:1000]};arts=list(t["artifacts"])+[a]
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
  ps=self.refresh_providers();t=self.get_task(tid)
  if not t or t["status"] in FINAL or t["status"]=="NEEDS_RECONCILIATION":return
  st=int(t["current_stage"]);name=PIPELINE[st-1][1];need=["qwen","deepseek"] if name=="qwen_deepseek" else [name]
  # 소유자 지시(2026-09-14): GPT/Codex 한도가 자주 소진돼도 자율 루프가 멈추지 않도록,
  # 1단계(계획)만 Codex 불가 시 Claude로 대체한다("듀얼 채널"). 5단계(최종검수)는 완료
  # 판정 자체이므로 대체하지 않고 그대로 Codex 전용 게이트를 유지한다 - Claude가 스스로
  # 계획 세우고 스스로 완료 인증까지 하는 것은 금지(자기 인증 방지 원칙, R05/R09와 동일).
  use_claude_plan_fallback = st==1 and not self._ready(ps,"codex") and self._ready(ps,"claude")
  # 소유자 지시(2026-09-14): "DeepSeek는 중요한 AI가 아니니 Gemini가 대체하도록 해."
  # DeepSeek 계정 잔액 소진(402) 같은 실패는 키 존재 여부만 보는 사전 probe로는 안
  # 잡힌다(_failure()도 이런 비-한도성 오류엔 reset_at을 안 걸어 다음 refresh_providers가
  # 바로 "정상"으로 덮어써 버린다) - 그래서 게이트가 아니라 실제 호출 실패 시점에
  # Gemini로 대체한다. Qwen 자체는 대체 대상이 아니므로 게이트에 그대로 남긴다.
  if use_claude_plan_fallback:need=[]
  elif st==3:need=["qwen"]
  for n in need:
   if not self._ready(ps,n):self._update(tid,event=f"{n} 게이트 차단",status="WAITING_QUOTA" if ps[n]["status"]=="WAITING_QUOTA" else "WAITING_AUTH",stage_label=f"{st}/5 {n} 사용 불가 · 이 단계에서 정지",next_retry_at=ps[n].get("reset_at"));return
  fallback_note=" (Claude 계획 대체)" if use_claude_plan_fallback else ""
  self._update(tid,event=f"{st}단계 시작"+fallback_note,status=f"RUNNING_STAGE_{st}",stage_label=f"{st}/5 {PIPELINE[st-1][2]} 실행 중"+fallback_note);t=self.get_task(tid)
  used_gemini_for_deepseek=False
  try:
   if st==1:c=self._claude_plan_fallback(t) if use_claude_plan_fallback else self._codex(t)
   elif st==2:c=self._gemini(t)
   elif st==3:
    q=self._qwen(t)
    try:
     c=f"## Qwen 단순 전처리\n\n{q}\n\n## DeepSeek 전처리 점검\n\n{self._deepseek(q)}"
    except Exception as deepseek_err:
     if not self._ready(ps,"gemini"):raise
     self._failure("deepseek",str(deepseek_err))  # 실패 사유를 기록해두되, 이 스테이지는 대체로 계속 진행
     used_gemini_for_deepseek=True
     c=f"## Qwen 단순 전처리\n\n{q}\n\n## Gemini 전처리 점검(DeepSeek 대체 · 원인: {str(deepseek_err)[:200]})\n\n{self._gemini_qc(q)}"
   elif st==4:c=self._claude(t)
   else:c=self._codex(t,True)
  except Exception as e:
   if use_claude_plan_fallback:failed_provider="claude"
   elif st==3:failed_provider="gemini" if used_gemini_for_deepseek else "deepseek"
   else:failed_provider=name
   p=self._failure(failed_provider,str(e));self._update(tid,event=f"{name} 단계 정지",status=p["status"],stage_label=f"{st}/5 {name} {p['status']} · 다음 단계 진입 금지",next_retry_at=p.get("reset_at") or (iso(now()+timedelta(minutes=5)) if p["status"]=="EXECUTION_ERROR" else None));return
  provider_label="claude_plan_fallback" if use_claude_plan_fallback else ("qwen+gemini_qc_fallback" if used_gemini_for_deepseek else name)
  self._finish_stage(t,st,provider_label,c);self._launch(tid)
 def check_and_resume(self):
  self.refresh_providers();launched=self.maintain_goals()
  for t in self.list_tasks():
   if t["status"] in FINAL or t["status"]=="NEEDS_RECONCILIATION":continue
   retry=t.get("next_retry_at")
   try:due=not retry or datetime.fromisoformat(retry)<=now()
   except ValueError:due=True
   if due:
    launched.append(t["task_id"]);self._launch(t["task_id"]);break
  return {"checked_at":iso(),"resumed_task_ids":launched}
 def status(self):return {"status":"success","monitor_status":"RUNNING" if self.monitor and self.monitor.is_alive() else "STOPPED","monitor_interval_seconds":self.monitor_interval,"providers":self.refresh_providers(),"deepseek_budget":self.deepseek_budget_status(),"waiting_tasks":len([t for t in self.list_tasks() if t["status"] not in FINAL]),"checked_at":iso(),"pipeline":[{"stage":n,"provider":p,"role":r} for n,p,r in PIPELINE],"measurement_note":"구독 잔여 카운터는 공개되지 않아 실제 단계 요청의 한도 오류와 리셋 시각을 기록합니다."}
 def dashboard_status(self):
  t=next((x for x in self.list_tasks() if x["status"] not in FINAL),None);return {"timestamp":iso(),"current_stage":t["status"] if t else "IDLE","status_message":t["stage_label"] if t else "대기 작업 없음 · 60초 감시 중","target_unlock_time":t.get("next_retry_at") if t else None,"target_task":t["task_id"] if t else None,"pipeline_architecture":"GPT 계획 → Gemini 근거 → Qwen+DeepSeek 실행 → Claude 마무리 → GPT 최종검수"}
 def list_tasks(self):return [dict(t) for t in self._load()["tasks"]]
 def get_task(self,tid):t=self._task(self._load(),tid);return dict(t) if t else None
 def active_sessions(self):
  s=self.status();purpose={"codex":"계획·최종검수","gemini":"근거·대용량 컨텍스트","qwen":"로컬 실행","deepseek":"독립 검증","claude":"확인·보강·마무리"};return [{"session_id":f"monitor-{n}","agent":p["display_name"],"timestamp":p["last_checked_at"],"status":p["status"],"status_label":p["note"],"traffic_light":"RED" if p["status"] in {"WAITING_QUOTA","WAITING_AUTH","UNAVAILABLE"} else "GREEN","window_desc":"실제 요청 기반 감지","exhaustion_pct":None,"reset_at":p.get("reset_at"),"quota_purpose":purpose[n],"work_status":"단계 감시","current_action":purpose[n],"tokens_today":None,"tokens_month":None,"tokens_share_pct":None,"cost_str":"실측 없음","last_worked_file":"","active_topic":"5단계 하드 게이트","pending_tasks":[]} for n,p in s["providers"].items()]

strict_agi_orchestrator=StrictAGIOrchestrator();strict_agi_orchestrator.start()
