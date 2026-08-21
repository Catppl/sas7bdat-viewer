$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".venv")) {
    py -3.12 -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
& .\.venv\Scripts\ruff.exe check clinical_data_viewer tests run.py
& .\.venv\Scripts\ruff.exe format --check clinical_data_viewer tests run.py
& .\.venv\Scripts\python.exe -m compileall -q clinical_data_viewer tests run.py
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
& .\.venv\Scripts\pyinstaller.exe --noconfirm --clean SASDataViewer.spec

$Executable = Resolve-Path "dist\SASDataViewer.exe"
$ExecutableInfo = Get-Item $Executable
$ExecutableHash = Get-FileHash $Executable -Algorithm SHA256

Write-Host "Build complete"
Write-Host "EXE:  $($ExecutableInfo.FullName)"
Write-Host "Size: $([math]::Round($ExecutableInfo.Length / 1MB, 2)) MB"
Write-Host "SHA256: $($ExecutableHash.Hash)"
