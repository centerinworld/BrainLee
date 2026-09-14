"""Real Git in disposable repos. No model execution or production writes."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from services import agentic_apply_worker as w

PATCH = 'diff --git a/value.txt b/value.txt\n--- a/value.txt\n+++ b/value.txt\n@@ -1 +1 @@\n-before\n+after\n'

class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'source'
        self.root.mkdir()
        self.git('init', '-b', 'feature-current')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.root/'value.txt').write_text('before\n')
        self.git('add', '.')
        self.git('-c','core.hooksPath=/dev/null','commit','-m','fixture')
        self.head = self.git('rev-parse','HEAD')
        for key,value in [('REPO_ROOT',self.root),('WORKTREE_BASE',Path(self.tmp.name)/'proposals')]:
            p=patch.object(w,key,value); p.start(); self.addCleanup(p.stop)

    def git(self,*args):
        return subprocess.check_output(['git',*args],cwd=self.root,text=True,stderr=subprocess.DEVNULL).strip()

    def stage(self,task='task-1',text=PATCH,paths=('value.txt',)):
        return w.stage_patch(task,text,allowed_paths=paths)

    def test_independent_git_source_and_other_session_unchanged(self):
        (self.root/'local.txt').write_text('other session')
        status=self.git('status','--porcelain')
        r=self.stage(); checkout=Path(r.worktree_dir)
        self.assertEqual((checkout/'value.txt').read_text(),'after\n')
        self.assertTrue((checkout/'.git').is_dir())
        self.assertFalse((checkout/'local.txt').exists())
        self.assertEqual((self.root/'value.txt').read_text(),'before\n')
        self.assertEqual(self.git('status','--porcelain'),status)
        self.assertEqual(self.git('rev-parse','HEAD'),self.head)
        self.assertEqual(self.git('branch','--show-current'),'feature-current')
        self.assertIsNone(r.commit_sha)
        self.assertIn('+after',r.diff_text)

    def test_approval_never_merges_and_cannot_reverse_decision(self):
        r=self.stage(); result=w.approve_change(r.task_id,expected_sha256=r.review_sha256)
        self.assertFalse(result['merged'])
        self.assertEqual(self.git('rev-parse','HEAD'),self.head)
        self.assertEqual((self.root/'value.txt').read_text(),'before\n')
        with self.assertRaises(FileExistsError): w.reject_change(r.task_id,expected_sha256=r.review_sha256)

    def test_rejection_preserves_evidence(self):
        r=self.stage(); result=w.reject_change(r.task_id,expected_sha256=r.review_sha256)
        self.assertFalse(result['deleted']); self.assertTrue(Path(r.worktree_dir).exists())
        with self.assertRaises(FileExistsError): w.approve_change(r.task_id,expected_sha256=r.review_sha256)

    def test_bad_hash_and_altered_artifact(self):
        r=self.stage()
        with self.assertRaises(ValueError): w.approve_change(r.task_id,expected_sha256='0'*64)
        (Path(r.worktree_dir).parent/'proposal.patch').write_text('different')
        with self.assertRaises(ValueError): w.approve_change(r.task_id,expected_sha256=r.review_sha256)

    def test_unstaged_and_staged_tampering(self):
        r=self.stage(); checkout=Path(r.worktree_dir)
        (checkout/'value.txt').write_text('unreviewed\n')
        with self.assertRaises(ValueError): w.approve_change(r.task_id,expected_sha256=r.review_sha256)
        subprocess.run(['git','add','value.txt'],cwd=checkout,check=True)
        with self.assertRaises(ValueError): w.approve_change(r.task_id,expected_sha256=r.review_sha256)

    def test_source_head_moved(self):
        r=self.stage(); (self.root/'new.txt').write_text('new')
        self.git('add','.'); self.git('-c','core.hooksPath=/dev/null','commit','-m','next')
        with self.assertRaises(ValueError): w.approve_change(r.task_id,expected_sha256=r.review_sha256)

    def test_failed_patch_not_reviewable(self):
        with self.assertRaises(RuntimeError): self.stage(text='not a diff')
        self.assertTrue((w.WORKTREE_BASE/'task-1'/'failure.json').exists())
        self.assertFalse((w.WORKTREE_BASE/'task-1'/'proposal.json').exists())
        with self.assertRaises(ValueError): w.approve_change('task-1',expected_sha256='0'*64)

    def test_allowlist(self):
        with self.assertRaises(ValueError): self.stage(paths=('elsewhere.txt',))
        for value in ('../escape','/absolute','.env','.codex/config.toml','a/../b','a//b'):
            with self.subTest(value=value),self.assertRaises(ValueError): self.stage('other',paths=(value,))

    def test_task_traversal_and_collision(self):
        for value in ('../escape','/tmp/escape','--option','a/b'):
            with self.subTest(value=value),self.assertRaises(ValueError): self.stage(value)
        self.stage()
        with self.assertRaises(FileExistsError): self.stage()

    def test_agent_entry_point_starts_no_process(self):
        with patch.object(w,'_run') as run:
            with self.assertRaises(RuntimeError): w.apply_change_in_isolated_worktree('task','change code')
            run.assert_not_called()

    def test_oversized_patch_before_clone(self):
        with patch.object(w,'create_isolated_worktree') as clone:
            with self.assertRaises(ValueError): self.stage(text='x'*(w.MAX_PATCH_BYTES+1))
            clone.assert_not_called()

if __name__=='__main__': unittest.main()
