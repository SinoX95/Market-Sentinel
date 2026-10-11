#!/bin/sh
cd "$(dirname "$0")"
docker compose up -d --build && (xdg-open http://localhost:8000 2>/dev/null || open http://localhost:8000 2>/dev/null || echo "Ouvre http://localhost:8000")
