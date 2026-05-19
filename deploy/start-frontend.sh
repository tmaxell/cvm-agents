#!/bin/sh
set -eu

if [ ! -f /app/frontend/node_modules/react-router-dom/package.json ]; then
  echo "[frontend-preflight] Missing dependency: /app/frontend/node_modules/react-router-dom/package.json"
  echo "[frontend-preflight] node_modules is unavailable in /app/frontend."
  echo "[frontend-preflight] Ensure dependencies are installed in the image and that docker-compose keeps /app/frontend/node_modules on a dedicated volume."
  exit 1
fi

exec npx vite --port 5173 --host 0.0.0.0
