#!/bin/sh
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r backend/requirements-local.txt
python backend/configure.py
(sleep 4; xdg-open http://localhost:8000 2>/dev/null || open http://localhost:8000 2>/dev/null) &
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
