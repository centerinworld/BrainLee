#!/bin/bash
echo "=============================================="
echo "  CEO Briefing Platform - Cloudflare Tunnels"
echo "=============================================="
echo ""

# Get absolute path of project directory
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_DIR="$PROJECT_DIR/data/runtime"
mkdir -p "$RUNTIME_DIR"

# 1. Start frontend server if not already running on port 5500
if lsof -i :5500 >/dev/null 2>&1; then
  echo "[1/3] Frontend server is already running on port 5500."
else
  echo "[1/3] Starting frontend server on port 5500..."
  cd "$PROJECT_DIR/frontend"
  python3 -m http.server 5500 --bind 127.0.0.1 > /dev/null 2>&1 &
  cd "$PROJECT_DIR"
  sleep 1
fi

# 2. Check if backend is running on 8011
if lsof -i :8011 >/dev/null 2>&1; then
  echo "[2/3] Backend server is running on port 8011."
else
  echo "[2/3] Backend server is not running! Starting it..."
  cd "$PROJECT_DIR/backend"
  ./.venv/bin/uvicorn main:app --port 8011 --reload > /dev/null 2>&1 &
  cd "$PROJECT_DIR"
  sleep 2
fi

# 3. Start cloudflared tunnels
echo "[3/3] Requesting Cloudflare Quick Tunnels..."
rm -f "$RUNTIME_DIR/backend-tunnel.log" "$RUNTIME_DIR/frontend-tunnel.log"

cloudflared tunnel --url http://127.0.0.1:8011 > "$RUNTIME_DIR/backend-tunnel.log" 2>&1 &
BACKEND_TUNNEL_PID=$!

cloudflared tunnel --url http://127.0.0.1:5500 > "$RUNTIME_DIR/frontend-tunnel.log" 2>&1 &
FRONTEND_TUNNEL_PID=$!

# Wait for tunnel URLs to be generated
echo "Waiting for tunnel URLs (approx 5-7 seconds)..."
sleep 6

# Extract URLs from logs
BACKEND_URL=$(grep -o 'https://[a-zA-Z0-9-]\+\.trycloudflare\.com' "$RUNTIME_DIR/backend-tunnel.log" | head -n 1)
FRONTEND_URL=$(grep -o 'https://[a-zA-Z0-9-]\+\.trycloudflare\.com' "$RUNTIME_DIR/frontend-tunnel.log" | head -n 1)

if [ -z "$BACKEND_URL" ] || [ -z "$FRONTEND_URL" ]; then
  echo "Error: Failed to obtain Cloudflare Tunnel URLs."
  echo "Please check cloudflared logs in data/runtime/"
  exit 1
fi

echo ""
echo "=============================================="
echo "           CONNECTION URLS (EXTERNAL)         "
echo "=============================================="
echo "Frontend External URL: $FRONTEND_URL"
echo "Backend External URL : $BACKEND_URL"
echo ""
echo "👉 Admin Portal URL (Full Access):"
echo "   $FRONTEND_URL/?api=$BACKEND_URL"
echo ""
echo "👉 CEO Viewer URL (View Only):"
echo "   $FRONTEND_URL/?api=$BACKEND_URL&role=ceo"
echo "=============================================="
echo ""
echo "Tunnels are running in the background."
echo "To close tunnels, run: kill $BACKEND_TUNNEL_PID $FRONTEND_TUNNEL_PID"
echo "Or run: pkill cloudflared"
