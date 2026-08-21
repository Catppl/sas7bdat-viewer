param(
    [string]$PythonExe = "",
    [switch]$RecreateVenv
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if ($RecreateVenv -and (Test-Path ".venv")) {
    Remove-Item -Recurse -Force ".venv"
}

if (-not (Test-Path ".venv")) {
    if ($PythonExe) {
        $PythonCommand = $PythonExe
    }
    elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $PythonCommand = "python"
    }
    elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $PythonCommand = "py"
    }
    else {
        throw "Python 3.11 or newer was not found. Install 64-bit Python and try again."
    }
    & $PythonCommand -m venv .venv
}

$VenvPython = (Resolve-Path ".venv\Scripts\python.exe").Path
& $VenvPython -c "import platform, sys; assert sys.version_info >= (3, 11), 'Python 3.11 or newer is required'; assert platform.architecture()[0] == '64bit', '64-bit Python is required'; print('Build Python:', sys.executable); print('Version:', sys.version); print('Architecture:', platform.architecture()[0])"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r requirements-build.txt
& .\.venv\Scripts\ruff.exe check clinical_data_viewer tests run.py
& .\.venv\Scripts\ruff.exe format --check clinical_data_viewer tests run.py
& $VenvPython -m compileall -q clinical_data_viewer tests run.py
& $VenvPython -m unittest discover -s tests -v
& .\.venv\Scripts\pyinstaller.exe --noconfirm --clean SASDataViewer.spec

$Executable = Resolve-Path "dist\SASDataViewer.exe"
$ExecutableInfo = Get-Item $Executable
$ExecutableHash = Get-FileHash $Executable -Algorithm SHA256

Write-Host "Build complete"
Write-Host "EXE:  $($ExecutableInfo.FullName)"
Write-Host "Size: $([math]::Round($ExecutableInfo.Length / 1MB, 2)) MB"
Write-Host "SHA256: $($ExecutableHash.Hash)"
