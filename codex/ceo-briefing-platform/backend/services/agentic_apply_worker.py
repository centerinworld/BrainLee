"""Reviewable, one-shot patch staging; does not launch agents or merge production.

The former prompt-to-Codex entry point is deliberately unavailable. A trusted
caller supplies exact patch bytes and an exact file allowlist. Each proposal has
an independent Git database, preserved patch and immutable review fingerprint.
Approval records consent to that fingerprint; deployment is a separate operation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Optional

REPO_ROOT = Path('/Volumes/Realtek_NVME/AI System')
WORKTREE_BASE = Path(__file__).resolve().parents[1] / 'data' / 'agentic_proposals'
MAX_PATCH_BYTES = 256_000
MAX_FILES = 20
BLOCKED_PARTS = {'.git', '.codex', '.agents', '.claude', '.gemini'}


def _run(cmd, cwd=None, timeout=60, input=None):
    # No shell, hooks, user aliases, external diff or filters in the child repo.
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0', GIT_LFS_SKIP_SMUDGE='1')
    return subprocess.run(cmd, cwd=cwd, input=input, capture_output=True,
                          text=True, timeout=timeout, env=env)


def _git(repo, *args, input=None):
    result = _run(['git', '-c', 'core.hooksPath=/dev/null', *args],
                  cwd=str(repo), input=input)
    if result.returncode:
        raise RuntimeError(f'git {args[0]} failed: {(result.stderr or result.stdout)[:800]}')
    return result.stdout


def _now():
    return datetime.now(timezone.utc).isoformat()


def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _task_dir(task_id):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', task_id):
        raise ValueError('Invalid task_id')
    base = WORKTREE_BASE.resolve()
    path = base / task_id
    if path.is_symlink():
        raise ValueError('Symlink proposal directory')
    return path


def _safe_paths(paths):
    if isinstance(paths, (str, bytes)):
        raise ValueError('allowed_paths must be a sequence of exact paths')
    paths = tuple(paths)
    if not paths or len(paths) > MAX_FILES or len(set(paths)) != len(paths):
        raise ValueError('Provide 1..20 distinct exact allowed paths')
    for value in paths:
        p = PurePosixPath(value)
        if (not value or p.is_absolute() or str(p) != value or
                '..' in p.parts or '\\' in value or any(ord(c) < 32 for c in value) or
                any(part in BLOCKED_PARTS or part.startswith('.env') for part in p.parts)):
            raise ValueError(f'Forbidden patch path: {value!r}')
    return paths


def _write_json(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


@dataclass
class ApplyResult:
    task_id: str
    branch: str
    worktree_dir: str
    has_changes: bool
    files_changed: list
    diff_text: str
    commit_sha: Optional[str]
    codex_message: str
    error: Optional[str] = None
    review_sha256: Optional[str] = None
    status: str = 'NEEDS_REVIEW'


def create_isolated_worktree(task_id: str):
    """Compatibility name: creates an independent clone, NOT a Git worktree.

    Captures committed HEAD; local uncommitted changes are explicitly excluded.
    No operation writes to the source repository's files, refs or shared metadata.
    """
    root = REPO_ROOT.resolve()
    base_sha = _git(root, 'rev-parse', '--verify', 'HEAD').strip()
    directory = _task_dir(task_id)
    directory.mkdir(parents=True, exist_ok=False)
    repo = directory / 'checkout'
    try:
        _git(directory, 'clone', '--no-local', '--no-hardlinks', '--no-checkout',
             '--', str(root), str(repo))
        _git(repo, 'remote', 'remove', 'origin')
        _git(repo, 'checkout', '--detach', base_sha)
        return {'branch': '', 'worktree_dir': str(repo), 'base_sha': base_sha}
    except Exception as exc:
        _write_json(directory / 'failure.json', {'status': 'FAILED', 'error': str(exc), 'at': _now()})
        raise


def apply_change_in_isolated_worktree(task_id: str, prompt: str) -> ApplyResult:
    """Old autonomous-agent path: never starts a process or creates a checkout."""
    raise RuntimeError('Autonomous agent execution is unavailable. Supply an explicit '
                       'reviewable patch to stage_patch(task_id, patch_text, allowed_paths=...).')


def stage_patch(task_id: str, patch_text: str, *, allowed_paths) -> ApplyResult:
    """Apply a bounded patch to committed HEAD and preserve the complete diff.

    This is deterministic Git patch application. It runs no generated code,
    tests, models, shell snippets or callbacks. Tests must be reviewed and run
    independently before a proposal is integrated into a live service.
    """
    allowed = _safe_paths(allowed_paths)
    if not patch_text.strip() or len(patch_text.encode('utf-8')) > MAX_PATCH_BYTES:
        raise ValueError('Empty or oversized patch')
    info = create_isolated_worktree(task_id)
    repo = Path(info['worktree_dir'])
    directory = repo.parent
    try:
        # --index enforces agreement of the index and worktree; git apply's
        # default path checks prohibit traversal and writes through symlinks.
        _git(repo, 'apply', '--check', '--index', '-', input=patch_text)
        _git(repo, 'apply', '--index', '-', input=patch_text)
        names = [p for p in _git(repo, 'diff', '--cached', '--name-only',
                                '--no-renames', '-z').split('\0') if p]
        _safe_paths(names)
        if not set(names).issubset(allowed):
            raise ValueError('Patch changed files outside the exact allowlist')
        # Refuse symlinks/submodules and executable files as proposal output.
        for name in names:
            entries = _git(repo, 'ls-files', '--stage', '--', name).splitlines()
            if any(line.split()[0] != '100644' for line in entries):
                raise ValueError('Only regular non-executable file output is supported')
        diff = _git(repo, 'diff', '--cached', '--binary', '--no-ext-diff',
                    '--no-textconv', '--no-renames', info['base_sha'], '--')
        if not diff or len(diff.encode('utf-8')) > MAX_PATCH_BYTES:
            raise ValueError('No changes or review diff exceeds the size limit')
        record = {'task_id': task_id, 'base_sha': info['base_sha'],
                  'source_repo': str(REPO_ROOT.resolve()), 'allowed_paths': list(allowed),
                  'files_changed': names, 'diff_sha256': _digest(diff),
                  'created_at': _now(), 'status': 'NEEDS_REVIEW',
                  'validation': 'patch applicability only; code tests not run',
                  'source_uncommitted_changes_included': False}
        (directory / 'proposal.patch').write_text(diff, encoding='utf-8')
        _write_json(directory / 'proposal.json', record)
        return ApplyResult(task_id, '', str(repo), True, names, diff, None, '',
                           review_sha256=record['diff_sha256'])
    except Exception as exc:
        _write_json(directory / 'failure.json', {'status': 'FAILED', 'error': str(exc), 'at': _now()})
        raise


def _load_review(task_id, expected_sha256):
    directory = _task_dir(task_id)
    if (directory / 'failure.json').exists():
        raise ValueError('Failed proposal cannot be reviewed')
    record = json.loads((directory / 'proposal.json').read_text(encoding='utf-8'))
    if record['task_id'] != task_id or record['source_repo'] != str(REPO_ROOT.resolve()):
        raise ValueError('Proposal identity mismatch')
    patch = (directory / 'proposal.patch').read_text(encoding='utf-8')
    if not expected_sha256 or expected_sha256 != record['diff_sha256'] or _digest(patch) != expected_sha256:
        raise ValueError('Approval must match the full reviewed diff hash')
    return directory, record


def approve_change(task_id: str, *, expected_sha256: str):
    """Trusted local caller only; records approval, never merges or deploys.

    This function is not an authentication boundary. A future HTTP/Telegram
    adapter must authenticate the owner and bind consent to task/base/diff hash.
    """
    directory, record = _load_review(task_id, expected_sha256)
    repo = directory / 'checkout'
    if _git(REPO_ROOT, 'rev-parse', 'HEAD').strip() != record['base_sha']:
        raise ValueError('Source HEAD moved; restage and review again')
    if _git(repo, 'rev-parse', 'HEAD').strip() != record['base_sha']:
        raise ValueError('Proposal HEAD changed')
    if _git(repo, 'diff', '--no-ext-diff', '--no-textconv') or _git(repo, 'ls-files', '--others', '--exclude-standard'):
        raise ValueError('Proposal contains unreviewed worktree changes')
    diff = _git(repo, 'diff', '--cached', '--binary', '--no-ext-diff',
                '--no-textconv', '--no-renames', record['base_sha'], '--')
    if _digest(diff) != expected_sha256:
        raise ValueError('Staged diff changed after review')
    decision = {'status': 'APPROVED_FOR_MANUAL_INTEGRATION', 'task_id': task_id,
                'base_sha': record['base_sha'], 'diff_sha256': expected_sha256,
                'at': _now(), 'merged': False}
    _write_json(directory / 'decision.json', decision)
    return decision


def reject_change(task_id: str, *, expected_sha256: str):
    """Record a rejection while preserving evidence; never force-delete work."""
    directory, record = _load_review(task_id, expected_sha256)
    decision = {'status': 'REJECTED', 'task_id': task_id,
                'diff_sha256': record['diff_sha256'], 'at': _now(), 'deleted': False}
    _write_json(directory / 'decision.json', decision)
    return decision
