@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 소스에서 exe를 다시 만들 때만 사용 (일반 사용자는 필요 없음)
if not exist .venv python -m venv .venv
.venv\Scripts\python -m pip install -q -U -r requirements.txt pyinstaller
.venv\Scripts\python -m PyInstaller --noconfirm --onefile --windowed --name YouTubeDownloader --icon icon.ico --add-data "icon.ico;." --collect-data customtkinter --collect-data yt_dlp_ejs --copy-metadata yt-dlp app.py
echo.
echo 완료: dist\YouTubeDownloader.exe
pause
