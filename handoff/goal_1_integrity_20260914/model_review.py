import sys,json,hashlib,subprocess
from pathlib import Path
from datetime import datetime,timezone
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(Path.cwd()/'antigravity_workspace'))
from llm_client import AntigravityLLMClient
client=AntigravityLLMClient()
roles={
'gemini':'근거 수집만 담당하라. 아래 공식 문서 근거의 적용 범위와 검증 가능한 추가 성공 기준을 추출하라. 확인하지 않은 URL/원문은 미확인으로 표시. 데이터 수정·완료 판정 금지.',
'qwen':'분류·중복 제거·필드 추출·형식 변환만 담당하라. 아래 DB 검사 결과를 JSON records(check,value,evidence_path)로 변환하라. 원문 수치 보존. 설계·코드·원천 데이터 수정·완료 판정 금지.',
'deepseek':'Qwen 전처리 점검만 담당하라. DB 원문과 Qwen 출력의 수치 손실·필드 누락·중복 제거 오류·추론 혼입을 대조하라. Qwen 결과가 없으면 검증 불가로 기록. 완료 판정 금지.',
'claude':'독립 검증·보강을 담당하라. 원 SQL과 DB 결과를 우선 검토하고 기존 요약을 무비판적으로 따르지 말라. 반례·오탐·누락 검수·성공 기준 보완을 제시하라. 데이터 직접 수정 및 목표 완료 선언 금지. 제공 증거에 한정되며 직접 DB 재실행 여부를 명시하라.'}
sources=[{'url':'https://www.postgresql.org/docs/18/sql-set-transaction.html','observed':'REPEATABLE READ uses one snapshot for transaction queries; READ ONLY prohibits ordinary data writes.','adopted':'Record and verify read_only/isolation/snapshot for consistent audit.'},{'url':'https://engopendart.fss.or.kr/guide/detail.do?apiGrpCd=DE003&apiId=AE00036','observed':'DART documents fs_div CFS/OFS, rcept_no, account_id, currency, three-month versus accumulated amounts.','adopted':'Preserve filing version, consolidation, account identity, currency and period basis; missing is unknown, not zero.'}]
(OUT/'external_sources.json').write_text(json.dumps(sources,ensure_ascii=False,indent=2))
(OUT/'role_instructions.json').write_text(json.dumps(roles,ensure_ascii=False,indent=2))
def call(role,payload):
 prompt=roles[role]+'\n한국어로 간결하게 응답.\n'+json.dumps(payload,ensure_ascii=False,default=str)
 (OUT/(role+'_input.txt')).write_text(prompt)
 result={'role':role,'started_utc':datetime.now(timezone.utc).isoformat(),'input_sha256':hashlib.sha256(prompt.encode()).hexdigest()}
 if role=='qwen' and 'qwen' not in client.grok_model.lower():
  result['response']={'provider':'none','content':'Configured model is not Qwen; no substitution.'}
 elif role=='claude':
  try:
   r=subprocess.run([client.claude_cli_path,'-p',prompt,'--output-format','json','--tools','','--no-session-persistence'],capture_output=True,text=True,timeout=120)
   result['exit_code']=r.returncode
   result['response']=json.loads(r.stdout) if r.returncode==0 else {'error':r.stderr[:600]}
  except Exception as e: result['response']={'error':type(e).__name__}
 else:
  result['response']=client.chat_completion_with_meta([{'role':'user','content':prompt}],provider={'gemini':'gemini','qwen':'grok','deepseek':'deepseek'}[role],max_tokens=1600)
 result['ended_utc']=datetime.now(timezone.utc).isoformat()
 (OUT/(role+'_response.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
 print(role,json.dumps({k:v for k,v in result['response'].items() if k not in ('content','result','usage','modelUsage')},ensure_ascii=False)[:400],flush=True)
 return result
if sys.argv[1]=='gemini': call('gemini',sources)
else:
 db=json.loads((OUT/'db_evidence.json').read_text())
 compact={'transaction':db['transaction'],'profile':db['results'].get('profile'),'duplicates':db['results'].get('duplicates'),'schema':db['results']['schema'],'scope':db['scope']}
 q=call('qwen',compact)
 d=call('deepseek',{'original':compact,'qwen':q})
 call('claude',{'db':compact,'queries':db['queries'],'sources':sources,'qwen':q,'deepseek':d,'gemini':json.loads((OUT/'gemini_response.json').read_text())})
