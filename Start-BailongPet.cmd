@echo off
setlocal
cd /d "%~dp0"
if exist "release-work\gemini-drive\dist\BailongPet\BailongPet.exe" (
  start "" "release-work\gemini-drive\dist\BailongPet\BailongPet.exe" %*
  exit /b 0
)
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "app\pet.py" %*
  exit /b 0
)
echo See docs/DEVELOPMENT.md to prepare the environment and animation assets.
pause
exit /b 1
