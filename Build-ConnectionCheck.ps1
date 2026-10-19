param()
$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
$buildRoot = Join-Path $repoRoot 'release-work\connection-stage1'
$previousPath = $env:PATH
Push-Location $repoRoot
try {
    $pythonBase = & $pythonPath -c 'import sys; print(sys.base_prefix)'
    if ($LASTEXITCODE -ne 0) { throw 'Cannot locate Python runtime.' }
    $env:PATH = @((Split-Path $pythonPath), $pythonBase, "$env:SystemRoot\System32", $env:SystemRoot, "$env:SystemRoot\System32\Wbem", $PSHOME) -join ';'
    & $pythonPath -m PyInstaller --clean --noconfirm --onedir --windowed --name BailongConnectionCheck --paths (Join-Path $repoRoot 'app') --add-data ((Join-Path $repoRoot 'app\translations\qtbase_zh_TW.qm') + ';.') --distpath (Join-Path $buildRoot 'dist') --workpath (Join-Path $buildRoot 'build') --specpath $buildRoot (Join-Path $repoRoot 'app\connection_check.py')
    if ($LASTEXITCODE -ne 0) { throw 'Connection check build failed.' }
    Write-Output (Join-Path $buildRoot 'dist\BailongConnectionCheck\BailongConnectionCheck.exe')
}
finally {
    $env:PATH = $previousPath
    Pop-Location
}
