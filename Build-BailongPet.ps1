param([switch] $Archive, [ValidatePattern('^[A-Za-z0-9_-]*$')][string] $OutputSubdirectory = 'gemini-drive')
$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Project Python environment is missing.' }
$buildRoot = Join-Path $repoRoot 'release-work'
if ($OutputSubdirectory) { $buildRoot = Join-Path $buildRoot $OutputSubdirectory }
$distRoot = Join-Path $buildRoot 'dist'
$packageRoot = Join-Path $distRoot 'BailongPet'
$originalPath = $env:PATH
Push-Location $repoRoot
try {
    $pythonBase = & $pythonPath -c 'import sys; print(sys.base_prefix)'
    if ($LASTEXITCODE -ne 0) { throw 'Cannot locate the Python runtime.' }
    $env:PATH = @((Split-Path $pythonPath), $pythonBase, "$env:SystemRoot\System32", $env:SystemRoot, "$env:SystemRoot\System32\Wbem", $PSHOME) -join ';'
    & $pythonPath -m PyInstaller --clean --noconfirm --onedir --windowed --name BailongPet --paths (Join-Path $repoRoot 'app') --hidden-import autostart --hidden-import selftest --hidden-import version --add-data ((Join-Path $repoRoot 'app\privacy_ocr.ps1') + ';.') --distpath $distRoot --workpath (Join-Path $buildRoot 'build') --specpath $buildRoot (Join-Path $repoRoot 'app\pet.py')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    & $pythonPath -c 'import pathlib, shutil, sys; r=pathlib.Path.cwd(); d=pathlib.Path(sys.argv[1]); [shutil.copytree(r/p,d/p,dirs_exist_ok=True) for p in ("outputs","app/sounds","app/cache","app/translations","licenses")]; [shutil.copy2(r/p,d/p) for p in ("Character.md","Animations.json","RelationshipRules.json","RandomEvents.json","使用說明-繁體中文.txt","ASSETS.md","VERSION.txt","開啟隨GPT自動啟動.cmd","關閉隨GPT自動啟動.cmd","檢查桌寵.cmd")]' $packageRoot
    if ($LASTEXITCODE -ne 0) { throw 'Runtime resource copy failed.' }
    & $pythonPath -m PyInstaller --clean --noconfirm --onefile --windowed --paths (Join-Path $repoRoot 'app') --name CodexNotify --distpath $packageRoot --workpath (Join-Path $buildRoot 'notify-build') --specpath $buildRoot (Join-Path $repoRoot 'app\codex_notify.py')
    if ($LASTEXITCODE -ne 0) { throw 'Codex notification bridge build failed.' }
    if ($Archive) {
        $archivePath = Join-Path $buildRoot 'BailongPet-v0.3.3-zh-TW-windows-x64.zip'
        if (Test-Path -LiteralPath $archivePath) { Remove-Item -LiteralPath $archivePath -Force }
        Compress-Archive -LiteralPath $packageRoot -DestinationPath $archivePath
    }
    Write-Output "Traditional Chinese build: $packageRoot\BailongPet.exe"
}
finally {
    $env:PATH = $originalPath
    Pop-Location
}
