#!/bin/bash
cd /Users/brainlee/Downloads/codex/ceo-briefing-platform/backend
exec /Users/brainlee/Downloads/codex/ceo-briefing-platform/backend/.venv/bin/python3 \
    -m uvicorn main:app \
    --port 8011 \
    --host 0.0.0.0 \
    --log-level warning
