"""Explicitly authorized tiny code-edit validation. Never edits operational files."""
import sys,json,subprocess
from pathlib import Path
ROOT=Path('/Volumes/Realtek_NVME/AI System')
sys.path.insert(0,str(ROOT/'codex/ceo-briefing-platform/backend'))
from services import agentic_apply_worker as patches
from services.approved_code_jobs import CodeJobs,generate_code,run_tests
OUT=Path(__file__).resolve().parent
provider=sys.argv[1]
repo=OUT/('fixture_'+provider)
repo.mkdir(exist_ok=False)
def git(*args):subprocess.run(['git',*args],cwd=repo,check=True,capture_output=True)
git('init','-b','main');git('config','user.name','Local validation');git('config','user.email','validation@example.invalid')
(repo/'calc.py').write_text('def add(a, b):\n    return a - b\n')
(repo/'test_calc.py').write_text('import unittest\nfrom calc import add\nclass Test(unittest.TestCase):\n def test_add(self):\n  self.assertEqual(add(2,3),5)\n  self.assertEqual(add(-2,3),1)\n')
git('add','.');git('-c','core.hooksPath=/dev/null','commit','-m','local validation fixture')
patches.REPO_ROOT=repo;patches.WORKTREE_BASE=OUT/('proposals_'+provider)
jobs=CodeJobs(OUT/('state_'+provider))
j=jobs.create(instruction='Fix calc.py add(a,b) so it returns the sum of a and b. Only change calc.py. Output complete file content in the required JSON.',provider=provider,paths=['calc.py'],test_module='test_calc',requested_by='explicit-user-request-validation')
jobs.approve(j['id'],j['approval_hash'],approved_by='explicit-user-request-validation')
r=jobs.execute(j['id'])
if r['status']=='READY_FOR_REVIEW':
 r=jobs.apply_to_workspace(j['id'],r['diff_hash'],approved_by='explicit-user-request-validation')
 r['source_after_apply']=(repo/'calc.py').read_text()
 # Additional independent run after application, within same restrictive sandbox.
 r['post_apply_tests']=run_tests(repo,'test_calc')
 r['rollback_result']=jobs.rollback(j['id'],r['diff_hash'],approved_by='explicit-user-request-validation')
 r['source_after_rollback']=(repo/'calc.py').read_text()
(OUT/(provider+'_result.json')).write_text(json.dumps(r,ensure_ascii=False,indent=2))
print(json.dumps({'provider':provider,'status':r['status'],'error':r.get('error'),'models':r.get('provider_result'),'post_apply_tests':r.get('post_apply_tests'),'rollback':r.get('rollback_result',{}).get('status')},ensure_ascii=False),flush=True)
