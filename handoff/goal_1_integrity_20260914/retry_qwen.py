import sys,json,hashlib,re
from pathlib import Path
from datetime import datetime,timezone
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(Path.cwd()/'antigravity_workspace'))
from llm_client import AntigravityLLMClient
client=AntigravityLLMClient()
assert 'qwen' in client.grok_model.lower()
db=json.loads((OUT/'db_evidence.json').read_text())
source={k:db['results'][k][0] for k in ['profile','duplicates']}
prompt='분류·중복 제거·필드 추출·형식 변환만 허용한다. 아래 두 객체의 각 키/값을 전부 JSON 배열로 변환하라. 각 원소는 path와 value만. path 예: profile.n. 설명, 마크다운, 설계, 심각도 판단, 완료 판단 금지. 값 변경 금지.\n'+json.dumps(source,ensure_ascii=False)
(OUT/'qwen_retry_input.txt').write_text(prompt)
r=client.chat_completion_with_meta([{'role':'user','content':prompt}],provider='grok',max_tokens=2200)
(OUT/'qwen_retry_response.json').write_text(json.dumps({'started_utc':datetime.now(timezone.utc).isoformat(),'input_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'response':r},ensure_ascii=False,indent=2))
expected={f'{group}.{key}':v for group,obj in source.items() for key,v in obj.items()}
try:
 text=r['content'].strip()
 if text.startswith('```'): text=re.sub(r'^```(?:json)?\s*|\s*```$','',text)
 records=json.loads(text)
 actual={x['path']:x['value'] for x in records}
 checks={'json_valid':True,'unique_paths':len(actual)==len(records),'exact_keys':set(actual)==set(expected),'exact_values':actual==expected,'records':len(records)}
except Exception as e: checks={'json_valid':False,'error':type(e).__name__}
checks['reviewer']='Codex deterministic comparison, NOT DeepSeek'
(OUT/'qwen_retry_validation.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
print(json.dumps(checks,ensure_ascii=False))
