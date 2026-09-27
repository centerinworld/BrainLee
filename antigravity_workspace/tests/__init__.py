# Tests package
import os

# 테스트가 실제 ChatGPT 구독(Codex CLI)을 호출해 한도를 쓰지 않도록 기본 비활성화한다.
os.environ.setdefault("CODEX_LEAN_ENABLED", "0")
