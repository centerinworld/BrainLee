import json, subprocess, tempfile, time, unittest
from pathlib import Path
from unittest.mock import patch, Mock
from services import agentic_apply_worker as p
from services.approved_code_jobs import CodeJobs, PROVIDERS, run_tests, generate_code

class JobsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  root=Path(self.tmp.name);self.repo=root/'repo';self.repo.mkdir()
  self.git('init','-b','main');self.git('config','user.name','Fixture');self.git('config','user.email','fixture@example.invalid')
  (self.repo/'calc.py').write_text('def add(a, b):\n    return a - b\n')
  (self.repo/'test_calc.py').write_text('import unittest\nfrom calc import add\nclass Test(unittest.TestCase):\n def test_add(self):\n  self.assertEqual(add(2, 3), 5)\n')
  self.git('add','.');self.git('-c','core.hooksPath=/dev/null','commit','-m','fixture');self.base=self.git('rev-parse','HEAD')
  # 2026-09-17 handoff P1-1: 전역 REPO_ROOT/WORKTREE_BASE 대신 REPO_REGISTRY의
  # 'ai-system' 항목을 이 테스트의 임시 저장소로 바꿔치기한다(CodeJobs.create()
  # 기본 repo_id가 'ai-system'이라 다른 테스트 코드는 그대로 둘 수 있다).
  self.ctx=p.RepoContext('ai-system',self.repo,root/'proposals')
  m=patch.dict(p.REPO_REGISTRY,{'ai-system':self.ctx});m.start();self.addCleanup(m.stop)
  self.generator=Mock(return_value={'provider':'fixture','content':json.dumps({'files':[{'path':'calc.py','content':'def add(a, b):\n    return a + b\n'}]})})
  self.tester=Mock(return_value={'passed':True,'exit_code':0,'stdout':'fixture','stderr':''})
  self.jobs=CodeJobs(root/'state',self.generator,self.tester)
 def git(self,*args):
  return subprocess.check_output(['git',*args],cwd=self.repo,text=True,stderr=subprocess.DEVNULL).strip()
 def create(self,provider='claude'):
  return self.jobs.create(instruction='Fix add',provider=provider,paths=['calc.py'],test_module='test_calc',requested_by='owner')
 def run_job(self,provider='claude'):
  j=self.create(provider);self.jobs.approve(j['id'],j['approval_hash'],approved_by='owner');return self.jobs.execute(j['id'])
 def test_approval_and_single_execution(self):
  j=self.create();self.generator.assert_not_called()
  with self.assertRaises(ValueError):self.jobs.execute(j['id'])
  self.jobs.approve(j['id'],j['approval_hash'],approved_by='owner')
  with self.assertRaises(ValueError):self.jobs.approve(j['id'],j['approval_hash'],approved_by='owner')
  self.assertEqual(self.jobs.execute(j['id'])['status'],'READY_FOR_REVIEW')
  with self.assertRaises(ValueError):self.jobs.execute(j['id'])
  self.assertEqual(self.generator.call_count,1)
 def test_all_providers_write_files(self):
  for provider in PROVIDERS:
   with self.subTest(provider=provider):
    r=self.run_job(provider);self.assertEqual(r['status'],'READY_FOR_REVIEW',r)
    self.assertIn('a + b',(Path(r['checkout'])/'calc.py').read_text())
    self.assertIn('a - b',(self.repo/'calc.py').read_text())
    self.assertEqual(self.generator.call_args.args[0],provider)
 def test_apply_rollback_other_session_preserved(self):
  r=self.run_job();(self.repo/'other.txt').write_text('other session')
  self.assertEqual(self.jobs.apply_to_workspace(r['id'],r['diff_hash'],approved_by='owner')['status'],'APPLIED_TO_WORKSPACE')
  self.assertIn('a + b',(self.repo/'calc.py').read_text());self.assertEqual(self.git('rev-parse','HEAD'),self.base)
  self.assertEqual(self.jobs.rollback(r['id'],r['diff_hash'],approved_by='owner')['status'],'ROLLED_BACK')
  self.assertIn('a - b',(self.repo/'calc.py').read_text());self.assertEqual((self.repo/'other.txt').read_text(),'other session')
 def test_hash_expiry_rejection(self):
  j=self.create()
  with self.assertRaises(ValueError):self.jobs.approve(j['id'],'0'*64,approved_by='owner')
  with patch('services.approved_code_jobs.time.time',return_value=j['approval_expires_at']+1):
   with self.assertRaises(ValueError):self.jobs.approve(j['id'],j['approval_hash'],approved_by='owner')
  self.jobs.reject(j['id'],j['approval_hash'],rejected_by='owner')
  with self.assertRaises(ValueError):self.jobs.execute(j['id'])
  self.generator.assert_not_called()
 def test_scope_and_invalid_python(self):
  for files in [[{'path':'evil.py','content':'x=1'}],[{'path':'calc.py','content':'not valid !!!'}]]:
   self.generator.return_value={'content':json.dumps({'files':files})}
   self.assertEqual(self.run_job()['status'],'FAILED');self.tester.assert_not_called()
 def test_model_failure_and_test_failure(self):
  self.generator.side_effect=RuntimeError('quota');self.assertEqual(self.run_job()['status'],'FAILED')
  self.generator.side_effect=None;self.tester.return_value={'passed':False,'exit_code':1}
  r=self.run_job();self.assertEqual(r['status'],'TEST_FAILED')
  with self.assertRaises(ValueError):self.jobs.apply_to_workspace(r['id'],r['diff_hash'],approved_by='owner')
 def test_concurrent_source_change(self):
  r=self.run_job();(self.repo/'calc.py').write_text('other session')
  with self.assertRaises(ValueError):self.jobs.apply_to_workspace(r['id'],r['diff_hash'],approved_by='owner')
 def test_command_and_sensitive_path(self):
  for paths,module in [(['.env'],'test_calc'),(['calc.py'],'test_calc;rm -rf /')]:
   with self.assertRaises(ValueError):self.jobs.create(instruction='fix',provider='claude',paths=paths,test_module=module,requested_by='owner')
 def test_unregistered_repo_id_rejected(self):
  """2026-09-17 handoff P1-1: repo_id는 서버 측 등록 목록으로만 해석돼야 한다 -
  임의 문자열로 다른 경로를 가리킬 수 없다."""
  with self.assertRaises(ValueError):
   self.jobs.create(instruction='fix',provider='claude',paths=['calc.py'],test_module='test_calc',requested_by='owner',repo_id='not-a-real-repo')
  self.generator.assert_not_called()
 def test_default_repo_id_is_recorded_on_the_job_spec(self):
  j=self.create();self.assertEqual(j['spec']['repo_id'],'ai-system')
 def test_claim_next_approved_picks_unleased_job(self):
  """2026-09-17 handoff P1-2: approve() 직후 BackgroundTasks가 유실돼도(서버
  재시작 등) 이 메서드로 다른 워커가 나중에 다시 집어갈 수 있어야 한다."""
  j=self.create();self.jobs.approve(j['id'],j['approval_hash'],approved_by='owner')
  claimed=self.jobs.claim_next_approved('worker-1',lease_seconds=100)
  self.assertEqual(claimed['id'],j['id']);self.assertEqual(claimed['status'],'APPROVED')
  self.assertEqual(claimed['lease_owner'],'worker-1');self.assertEqual(claimed['attempt'],1)
 def test_claim_next_approved_skips_actively_leased_job(self):
  j=self.create();self.jobs.approve(j['id'],j['approval_hash'],approved_by='owner')
  self.jobs.claim_next_approved('worker-1',lease_seconds=100)
  self.assertIsNone(self.jobs.claim_next_approved('worker-2',lease_seconds=100))
 def test_claim_next_approved_reclaims_after_lease_expires(self):
  j=self.create();self.jobs.approve(j['id'],j['approval_hash'],approved_by='owner')
  self.jobs.claim_next_approved('worker-1',lease_seconds=100)
  with patch('services.approved_code_jobs.time.time',return_value=time.time()+101):
   claimed=self.jobs.claim_next_approved('worker-2',lease_seconds=100)
  self.assertEqual(claimed['lease_owner'],'worker-2');self.assertEqual(claimed['attempt'],2)
 def test_reconcile_stale_running_marks_needs_reconciliation(self):
  """워커가 실행 도중 죽으면(리스 만료) 자동 재실행하지 않고 사람이 볼 수 있게
  표시만 한다 - 모델 호출 중복이나 이미 존재하는 격리 디렉터리 충돌을 피한다."""
  j=self.create();self.jobs.approve(j['id'],j['approval_hash'],approved_by='owner')
  self.jobs.claim_next_approved('worker-1',lease_seconds=1)
  self.jobs._transition(j['id'],'APPROVED','RUNNING')
  with patch('services.approved_code_jobs.time.time',return_value=time.time()+5):
   self.jobs.reconcile_stale_running(stale_after_seconds=1)
  self.assertEqual(self.jobs.get(j['id'])['status'],'NEEDS_RECONCILIATION')
 def test_reconcile_stale_running_leaves_active_lease_alone(self):
  j=self.create();self.jobs.approve(j['id'],j['approval_hash'],approved_by='owner')
  self.jobs.claim_next_approved('worker-1',lease_seconds=300)
  self.jobs._transition(j['id'],'APPROVED','RUNNING')
  self.jobs.reconcile_stale_running(stale_after_seconds=300)
  self.assertEqual(self.jobs.get(j['id'])['status'],'RUNNING')
 def test_execute_sets_lease_so_concurrent_reconcile_tick_does_not_misfire(self):
  """2026-09-18 실사용 중 재현: approve() 직후 FastAPI BackgroundTasks가 부르는
  빠른 경로(execute() 직접 호출)는 claim_next_approved()를 거치지 않아 리스가
  전혀 없었다. 그 사이 CodeJobWorker의 주기적 reconcile_stale_running()이 돌면
  '리스 없음'을 '리스 만료됨'으로 오판해, 방금 정상적으로 RUNNING된 작업을 즉시
  NEEDS_RECONCILIATION으로 잘못 표시했다(실측: 첫 실제 code-job이 8초 만에
  이렇게 됨 - 아직 모델 호출도 안 끝났는데). execute()가 스스로 리스를 찍어두면
  이런 오판이 없어야 한다."""
  j=self.create();self.jobs.approve(j['id'],j['approval_hash'],approved_by='owner')
  fixture_result=self.generator.return_value
  def _mid_flight(*a,**k):
   self.jobs.reconcile_stale_running(stale_after_seconds=1)  # 실행 도중 워커 틱이 끼어든 상황 재현
   return fixture_result
  self.generator.side_effect=_mid_flight
  r=self.jobs.execute(j['id'])
  self.assertEqual(r['status'],'READY_FOR_REVIEW',r)
 def test_claude_code_route_not_plan(self):
  with patch('services.approved_code_jobs.bounded_process',return_value=(0,json.dumps({'result':'{}','is_error':False}),'')) as call:
   generate_code('claude','implement',self.repo);argv=call.call_args.args[0]
   self.assertEqual(argv[argv.index('--permission-mode')+1],'default');self.assertNotIn('plan',argv)
   self.assertFalse(any('bypass' in a for a in argv))
 def test_real_sandboxed_unittest(self):
  self.jobs.tester=run_tests;r=self.run_job()
  self.assertEqual(r['status'],'READY_FOR_REVIEW',r.get('error') or r.get('tests'))
  self.assertIn('Ran 1 test',r['tests']['stderr'])
 def test_sandbox_denies_network_and_writes_outside_clone(self):
  forbidden=str(Path(self.tmp.name)/'must-not-exist')
  (self.repo/'test_guard.py').write_text(
   'import unittest,socket\nclass Guard(unittest.TestCase):\n'
   ' def test_write(self):\n  with self.assertRaises(PermissionError):\n'
   f'   open({forbidden!r}, "w")\n'
   ' def test_network(self):\n  with socket.socket() as sock:\n'
   '   with self.assertRaises(PermissionError):\n    sock.connect(("127.0.0.1",9))\n')
  self.git('add','.');self.git('-c','core.hooksPath=/dev/null','commit','-m','guard fixture')
  self.jobs.tester=run_tests
  j=self.jobs.create(instruction='Fix add',provider='claude',paths=['calc.py'],test_module='test_guard',requested_by='owner')
  self.jobs.approve(j['id'],j['approval_hash'],approved_by='owner');r=self.jobs.execute(j['id'])
  self.assertEqual(r['status'],'READY_FOR_REVIEW',r.get('tests') or r.get('error'))
  self.assertFalse(Path(forbidden).exists())
 def test_zero_tests_cannot_pass(self):
  (self.repo/'test_empty.py').write_text('# no tests\n')
  self.git('add','.');self.git('-c','core.hooksPath=/dev/null','commit','-m','empty test fixture')
  self.jobs.tester=run_tests
  j=self.jobs.create(instruction='Fix add',provider='claude',paths=['calc.py'],test_module='test_empty',requested_by='owner')
  self.jobs.approve(j['id'],j['approval_hash'],approved_by='owner');r=self.jobs.execute(j['id'])
  self.assertEqual(r['status'],'TEST_FAILED',r)
 def test_authenticated_http_approval_apply_rollback(self):
  # 2026-09-18 발견(소유자가 실제 텔레그램에서 목격): 이 테스트가 notify_telegram/
  # send_final_approval_prompt를 mock하지 않아서, 테스트를 돌릴 때마다 code_jobs만
  # 격리된 임시 저장소를 쓸 뿐 알림은 실제 GOAL_INTAKE_BOT_TOKEN으로 실제 전송되고
  # 있었다("등록/검토대기/버튼/적용됨/롤백됨" 5통이 매 테스트 실행마다 실제 폰에
  # 감. FastAPI TestClient는 BackgroundTasks를 응답 직후 실제로 실행한다) - 실제
  # 파일 변경은 격리돼 있어 안전했지만 실제 알림 스팸은 진짜였다. 반드시 mock한다.
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  import approved_code_routes as routes
  from session_deps import require_any_session
  app=FastAPI();app.include_router(routes.router)
  app.dependency_overrides[require_any_session]=lambda:{'username':'staff','role':'staff'}
  client=TestClient(app)
  with patch.object(routes,'code_jobs',self.jobs), \
       patch.object(routes,'notify_telegram') as mock_notify, \
       patch.object(routes,'send_final_approval_prompt') as mock_prompt:
   self.assertEqual(client.get('/api/agi/code-jobs').status_code,403)
   app.dependency_overrides[require_any_session]=lambda:{'username':'owner','role':'admin'}
   r=client.post('/api/agi/code-jobs',json={'instruction':'Fix add','provider':'claude','paths':['calc.py'],'test_module':'test_calc'})
   self.assertEqual(r.status_code,200,r.text);j=r.json();self.generator.assert_not_called()
   path='/api/agi/code-jobs/'+j['id']
   self.assertEqual(client.post(path+'/approve',json={'fingerprint':'0'*64}).status_code,409)
   self.assertEqual(client.post(path+'/approve',json={'fingerprint':j['approval_hash']}).status_code,200)
   r=client.get(path).json();self.assertEqual(r['status'],'READY_FOR_REVIEW')
   self.assertEqual(client.post(path+'/apply',json={'fingerprint':r['diff_hash']}).json()['status'],'APPLIED_TO_WORKSPACE')
   self.assertEqual(client.post(path+'/rollback',json={'fingerprint':r['diff_hash']}).json()['status'],'ROLLED_BACK')
  # 실제 텔레그램으로 나가면 안 된다 - mock이 실제로 가로챘는지 확인한다(스파이가
  # 있다는 사실 자체가 이 회귀의 재발을 막는 잠금장치).
  self.assertTrue(mock_notify.called or mock_prompt.called)

class RoutesTests(unittest.TestCase):
 def test_requires_admin(self):
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  from approved_code_routes import router
  app=FastAPI();app.include_router(router);client=TestClient(app)
  for method,url in [('get','/api/agi/code-jobs'),('post','/api/agi/code-jobs'),('get','/api/agi/code-jobs/x'),('post','/api/agi/code-jobs/x/approve'),('post','/api/agi/code-jobs/x/reject'),('post','/api/agi/code-jobs/x/apply'),('post','/api/agi/code-jobs/x/rollback')]:
   with self.subTest(url=url):self.assertEqual(getattr(client,method)(url).status_code,401)
if __name__=='__main__':unittest.main()
