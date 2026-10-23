@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
  echo 먼저 설치.bat을 실행하세요.
  pause
  exit /b
)
start "" .venv\Scripts\pythonw.exe app.py
