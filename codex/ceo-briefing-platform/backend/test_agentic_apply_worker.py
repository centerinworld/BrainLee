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
        # 2026-09-17 handoff P1-1: 전역 REPO_ROOT/WORKTREE_BASE 대신 RepoContext를
        # 명시적으로 넘긴다 - REPO_REGISTRY에 등록해 get_repo()로도 조회 가능하게 한다.
        self.ctx = w.RepoContext('test-repo', self.root, Path(self.tmp.name)/'proposals')
        registry_patch = patch.dict(w.REPO_REGISTRY, {'test-repo': self.ctx})
        registry_patch.start(); self.addCleanup(registry_patch.stop)

    def git(self,*args):
        return subprocess.check_output(['git',*args],cwd=self.root,text=True,stderr=subprocess.DEVNULL).strip()

    def stage(self,task='task-1',text=PATCH,paths=('value.txt',)):
        return w.stage_patch(self.ctx,task,text,allowed_paths=paths)

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
        r=self.stage(); result=w.approve_change(self.ctx,r.task_id,expected_sha256=r.review_sha256)
        self.assertFalse(result['merged'])
        self.assertEqual(self.git('rev-parse','HEAD'),self.head)
        self.assertEqual((self.root/'value.txt').read_text(),'before\n')
        with self.assertRaises(FileExistsError): w.reject_change(self.ctx,r.task_id,expected_sha256=r.review_sha256)

    def test_rejection_preserves_evidence(self):
        r=self.stage(); result=w.reject_change(self.ctx,r.task_id,expected_sha256=r.review_sha256)
        self.assertFalse(result['deleted']); self.assertTrue(Path(r.worktree_dir).exists())
        with self.assertRaises(FileExistsError): w.approve_change(self.ctx,r.task_id,expected_sha256=r.review_sha256)

    def test_bad_hash_and_altered_artifact(self):
        r=self.stage()
        with self.assertRaises(ValueError): w.approve_change(self.ctx,r.task_id,expected_sha256='0'*64)
        (Path(r.worktree_dir).parent/'proposal.patch').write_text('different')
        with self.assertRaises(ValueError): w.approve_change(self.ctx,r.task_id,expected_sha256=r.review_sha256)

    def test_unstaged_and_staged_tampering(self):
        r=self.stage(); checkout=Path(r.worktree_dir)
        (checkout/'value.txt').write_text('unreviewed\n')
        with self.assertRaises(ValueError): w.approve_change(self.ctx,r.task_id,expected_sha256=r.review_sha256)
        subprocess.run(['git','add','value.txt'],cwd=checkout,check=True)
        with self.assertRaises(ValueError): w.approve_change(self.ctx,r.task_id,expected_sha256=r.review_sha256)

    def test_source_head_moved(self):
        r=self.stage(); (self.root/'new.txt').write_text('new')
        self.git('add','.'); self.git('-c','core.hooksPath=/dev/null','commit','-m','next')
        with self.assertRaises(ValueError): w.approve_change(self.ctx,r.task_id,expected_sha256=r.review_sha256)

    def test_failed_patch_not_reviewable(self):
        with self.assertRaises(RuntimeError): self.stage(text='not a diff')
        self.assertTrue((self.ctx.worktree_base/'task-1'/'failure.json').exists())
        self.assertFalse((self.ctx.worktree_base/'task-1'/'proposal.json').exists())
        with self.assertRaises(ValueError): w.approve_change(self.ctx,'task-1',expected_sha256='0'*64)

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
            with self.assertRaises(RuntimeError): w.apply_change_in_isolated_worktree(self.ctx,'task','change code')
            run.assert_not_called()

    def test_oversized_patch_before_clone(self):
        with patch.object(w,'create_isolated_worktree') as clone:
            with self.assertRaises(ValueError): self.stage(text='x'*(w.MAX_PATCH_BYTES+1))
            clone.assert_not_called()


class RepoRegistryTests(unittest.TestCase):
    """2026-09-17 handoff P1-1 통과 기준: 두 임시 저장소 병렬 실행 시 상호 오염 없음;
    등록되지 않은 repo_id는 거부; symlink로 등록 루트를 바꿔치기해도 안전."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _make_repo(self, name, content):
        root = Path(self.tmp.name)/name
        root.mkdir()
        subprocess.check_output(['git','init','-b','main'],cwd=root,stderr=subprocess.DEVNULL)
        subprocess.check_output(['git','config','user.name','Fixture'],cwd=root)
        subprocess.check_output(['git','config','user.email','fixture@example.invalid'],cwd=root)
        (root/'value.txt').write_text(content)
        subprocess.check_output(['git','add','.'],cwd=root)
        subprocess.check_output(['git','-c','core.hooksPath=/dev/null','commit','-m','fixture'],cwd=root)
        return root

    def test_unknown_repo_id_rejected(self):
        with self.assertRaises(ValueError):
            w.get_repo('no-such-repo')

    def test_two_repos_do_not_cross_contaminate(self):
        root_a = self._make_repo('repo-a', 'A\n')
        root_b = self._make_repo('repo-b', 'B\n')
        ctx_a = w.RepoContext('repo-a', root_a, Path(self.tmp.name)/'proposals-a')
        ctx_b = w.RepoContext('repo-b', root_b, Path(self.tmp.name)/'proposals-b')
        patcher = patch.dict(w.REPO_REGISTRY, {'repo-a': ctx_a, 'repo-b': ctx_b})
        patcher.start(); self.addCleanup(patcher.stop)

        patch_a = PATCH.replace('before', 'A').replace('after', 'A2')
        patch_b = PATCH.replace('before', 'B').replace('after', 'B2')
        result_a = w.stage_patch(w.get_repo('repo-a'), 'same-task-id', patch_a, allowed_paths=('value.txt',))
        result_b = w.stage_patch(w.get_repo('repo-b'), 'same-task-id', patch_b, allowed_paths=('value.txt',))

        # 같은 task_id를 써도 서로 다른 worktree_base 아래로 격리돼 충돌하지 않는다.
        self.assertNotEqual(Path(result_a.worktree_dir).parent, Path(result_b.worktree_dir).parent)
        self.assertEqual((Path(result_a.worktree_dir)/'value.txt').read_text(), 'A2\n')
        self.assertEqual((Path(result_b.worktree_dir)/'value.txt').read_text(), 'B2\n')
        # 원본 저장소는 서로 영향받지 않는다.
        self.assertEqual((root_a/'value.txt').read_text(), 'A\n')
        self.assertEqual((root_b/'value.txt').read_text(), 'B\n')

    def test_approve_rejects_mismatched_repo_id_even_with_shared_worktree_base(self):
        """proposal.json에 기록된 repo_id가 현재 ctx.repo_id와 다르면 거부돼야 한다 -
        (설정 실수로) 두 저장소가 같은 worktree_base를 공유하게 되더라도 다른
        저장소의 승인 절차로 잘못 흘러들어가지 않게 하는 마지막 방어선이다."""
        root_a = self._make_repo('repo-a', 'A\n')
        shared_base = Path(self.tmp.name)/'shared-proposals'
        ctx_a = w.RepoContext('repo-a', root_a, shared_base)
        ctx_evil = w.RepoContext('repo-evil', root_a, shared_base)
        patch_a = PATCH.replace('before', 'A').replace('after', 'A2')
        r = w.stage_patch(ctx_a, 'task-x', patch_a, allowed_paths=('value.txt',))
        with self.assertRaises(ValueError):
            w.approve_change(ctx_evil, r.task_id, expected_sha256=r.review_sha256)


if __name__=='__main__': unittest.main()
