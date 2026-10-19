@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "BAILONG_EXE=%~dp0BailongPet.exe"
if not exist "%BAILONG_EXE%" set "BAILONG_EXE=%~dp0release-work\google-drive-sync\dist\BailongPet\BailongPet.exe"
start "" "%BAILONG_EXE%" --disable-autostart
