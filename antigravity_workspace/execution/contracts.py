"""로컬 에이전트 실행 요청의 최소 공통 계약."""

from dataclasses import dataclass, field
import hashlib
import os
import uuid
from typing import Optional


@dataclass(frozen=True)
class ExecutionRequest:
    prompt: str
    workspace: str
    role: str = "worker"
    run_id: str = field(default_factory=lambda: f"run_{uuid.uuid4().hex}")
    task_id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex}")
    attempt_id: str = field(default_factory=lambda: f"attempt_{uuid.uuid4().hex}")
    session_id: Optional[str] = None
    model: Optional[str] = None
    sandbox: str = "read-only"
    spec_hash: Optional[str] = None

    def __post_init__(self) -> None:
        if self.model not in {None, "gpt-5.6-sol"}:
            raise ValueError("GPT execution policy permits only gpt-5.6-sol")
        object.__setattr__(self, "model", "gpt-5.6-sol")
        if not self.prompt.strip():
            raise ValueError("prompt는 비어 있을 수 없습니다")
        workspace = os.path.abspath(self.workspace)
        if not os.path.isdir(workspace):
            raise ValueError(f"workspace가 존재하지 않습니다: {workspace}")
        if self.sandbox not in {"read-only", "workspace-write"}:
            raise ValueError("sandbox는 read-only 또는 workspace-write여야 합니다")
        object.__setattr__(self, "workspace", workspace)
        if self.spec_hash is None:
            material = f"{self.role}\0{self.sandbox}\0{self.model or ''}\0{self.prompt}"
            object.__setattr__(self, "spec_hash", hashlib.sha256(material.encode("utf-8")).hexdigest())

    @property
    def input_hash(self) -> str:
        return hashlib.sha256(self.prompt.encode("utf-8")).hexdigest()
