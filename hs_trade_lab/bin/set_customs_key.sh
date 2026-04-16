#!/bin/zsh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"

printf "관세청 Itemtrade serviceKey를 입력하세요: "
stty -echo
read -r SERVICE_KEY
stty echo
printf "\n"

if [[ -z "${SERVICE_KEY}" ]]; then
  echo "serviceKey가 비어 있습니다."
  exit 1
fi

ROOT_DIR="$ROOT_DIR" SERVICE_KEY="$SERVICE_KEY" python3 - <<'PY'
from pathlib import Path
import os

root = Path(os.environ["ROOT_DIR"])
env_file = root / ".env"
key = os.environ["SERVICE_KEY"].strip()

lines = []
if env_file.exists():
    lines = env_file.read_text(encoding="utf-8").splitlines()

updated = False
for index, line in enumerate(lines):
    if line.startswith("CUSTOMS_ITEMTRADE_SERVICE_KEY="):
        lines[index] = f"CUSTOMS_ITEMTRADE_SERVICE_KEY={key}"
        updated = True
        break

if not updated:
    lines.append(f"CUSTOMS_ITEMTRADE_SERVICE_KEY={key}")

env_file.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
PY

echo "저장 완료: $ENV_FILE"
