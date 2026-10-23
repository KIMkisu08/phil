@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/4] Python 확인
python -c "import sys" >nul 2>nul
if errorlevel 1 (
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  echo.
  echo Python 설치가 끝났습니다. 이 창을 닫고 설치.bat을 한 번 더 실행하세요.
  pause
  exit /b
)
echo [2/4] FFmpeg 확인
where ffmpeg >nul 2>nul || winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
echo [3/4] Deno(유튜브 해석용) 확인
where deno >nul 2>nul || winget install -e --id DenoLand.Deno --accept-source-agreements --accept-package-agreements
echo [4/4] 프로그램 구성요소 설치
if not exist .venv python -m venv .venv
.venv\Scripts\python -m pip install -q --upgrade pip
.venv\Scripts\python -m pip install -q -U -r requirements.txt
echo.
echo 설치 완료! 실행.bat으로 프로그램을 여세요. (FFmpeg/Deno를 방금 설치했다면 PC 재로그인 후 실행)
pause
