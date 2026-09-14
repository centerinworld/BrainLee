import json, subprocess, tempfile, unittest
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
  for key,value in [('REPO_ROOT',self.repo),('WORKTREE_BASE',root/'proposals')]:
   m=patch.object(p,key,value);m.start();self.addCleanup(m.stop)
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
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  import approved_code_routes as routes
  from session_deps import require_any_session
  app=FastAPI();app.include_router(routes.router)
  app.dependency_overrides[require_any_session]=lambda:{'username':'staff','role':'staff'}
  client=TestClient(app)
  with patch.object(routes,'code_jobs',self.jobs):
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

class RoutesTests(unittest.TestCase):
 def test_requires_admin(self):
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  from approved_code_routes import router
  app=FastAPI();app.include_router(router);client=TestClient(app)
  for method,url in [('get','/api/agi/code-jobs'),('post','/api/agi/code-jobs'),('get','/api/agi/code-jobs/x'),('post','/api/agi/code-jobs/x/approve'),('post','/api/agi/code-jobs/x/reject'),('post','/api/agi/code-jobs/x/apply'),('post','/api/agi/code-jobs/x/rollback')]:
   with self.subTest(url=url):self.assertEqual(getattr(client,method)(url).status_code,401)
if __name__=='__main__':unittest.main()
