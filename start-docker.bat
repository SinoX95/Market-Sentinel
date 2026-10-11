@echo off
cd /d "%~dp0"
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker Desktop n'est pas lance. Lance-le, attends qu'il soit pret, puis relance ce fichier.
  pause
  exit /b 1
)
docker compose up -d --build
if errorlevel 1 (
  echo Erreur au demarrage.
  pause
  exit /b 1
)
start "" http://localhost:8000
