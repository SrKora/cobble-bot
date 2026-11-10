@echo off
REM Para servidores en Windows: arranca el bot (doble clic).
cd /d "%~dp0"
if not exist venv (
  python -m venv venv
  venv\Scripts\pip install -r requirements.txt
)
venv\Scripts\python run.py config.toml
pause
