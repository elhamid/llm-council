#!/bin/bash

# LLM Council - Start script

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
echo "Starting LLM Council..."
echo ""

# Port checks (avoid double-start + misleading status)
is_listening() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }


# Deterministic defaults (can be overridden by environment)
: "${PERSIST_STORAGE:=1}"
: "${CONVERSATIONS_FILE:=$ROOT/backend/data/conversations.json}"
: "${CORS_ALLOW_ORIGINS:=http://localhost:5173,http://127.0.0.1:5173}"

export PERSIST_STORAGE
export CONVERSATIONS_FILE
export CORS_ALLOW_ORIGINS


# Start backend
echo "Starting backend on http://localhost:8001..."
if is_listening 8001; then
  echo "Backend already running on :8001 (skipping start)."
else
  uv run uvicorn backend.main:app --host 127.0.0.1 --port 8001 --log-level info &
  BACKEND_PID=$!
  sleep 1
  if ! is_listening 8001; then
    echo "ERROR: backend failed to start on :8001"
    exit 1
  fi
fi
BACKEND_PID=$!

# Wait a bit for backend to start
sleep 2

# Start frontend
echo "Starting frontend on http://localhost:5173..."
cd frontend
if is_listening 5173; then
  echo "Frontend already running on :5173 (skipping start)."
else
  npm run dev &
  FRONTEND_PID=$!
fi

echo ""
echo "✓ LLM Council is running!"
echo "  Backend:  http://localhost:8001"
echo "  Frontend: http://localhost:5173"
echo ""
echo "Press Ctrl+C to stop both servers"

# Wait for Ctrl+C
trap "kill ${BACKEND_PID:-} ${FRONTEND_PID:-} 2>/dev/null || true; exit" SIGINT SIGTERM
wait
