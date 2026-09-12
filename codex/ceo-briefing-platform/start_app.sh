#!/bin/bash
echo "=============================================="
echo "  CEO Briefing Platform - Start Script (Mac)"
echo "=============================================="
echo ""

# Get absolute path of project and backend directory
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"

echo "[1/2] Starting backend server (FastAPI) in a new Terminal window..."
osascript -e "tell app \"Terminal\" to do script \"cd '$BACKEND_DIR' && ./.venv/bin/uvicorn main:app --port 8011 --reload\""

# Wait 3 seconds for server to start
sleep 3

echo "[2/2] Opening frontend in browser..."
open "$PROJECT_DIR/frontend/index.html"

echo ""
echo "Execution completed!"
echo "To stop the backend, close the newly opened Terminal window or press Ctrl+C inside it."
