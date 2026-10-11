@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Market Sentinel - laisse cette fenetre ouverte

netstat -ano | findstr ":8000" | findstr "LISTENING" >nul
if not errorlevel 1 (
  echo Market Sentinel tourne deja, ou le port 8000 est pris par autre chose.
  echo Ferme l'ancienne fenetre noire de Market Sentinel, puis relance start.bat.
  pause
  exit /b 1
)

set PY=
where py >nul 2>&1 && set PY=py -3
if not defined PY (
  echo Python est introuvable. Installation automatique via winget...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  echo.
  echo Python est installe. Ferme cette fenetre puis relance start.bat.
  pause
  exit /b 1
)

if not exist .venv\Scripts\python.exe (
  echo Premiere installation, quelques minutes...
  %PY% -m venv .venv
  if errorlevel 1 goto erreur
)
call .venv\Scripts\activate.bat
python -m pip install -q -r backend\requirements-local.txt
if errorlevel 1 goto erreur

python backend\configure.py
echo Market Sentinel demarre sur http://localhost:8000
start "" /b powershell -NoProfile -Command "Start-Sleep 5; Start-Process http://localhost:8000"
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
goto :eof

:erreur
echo.
echo Une erreur est survenue (voir ci-dessus).
pause
exit /b 1
