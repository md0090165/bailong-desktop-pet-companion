@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "BAILONG_EXE=%~dp0BailongPet.exe"
if not exist "%BAILONG_EXE%" set "BAILONG_EXE=%~dp0release-work\google-drive-sync\dist\BailongPet\BailongPet.exe"
"%BAILONG_EXE%" --self-test
if errorlevel 1 exit /b 1
for %%I in ("%BAILONG_EXE%") do set "BAILONG_DIR=%%~dpI"
start "" notepad.exe "%BAILONG_DIR%检查报告.json"
