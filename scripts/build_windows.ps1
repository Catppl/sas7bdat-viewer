param(
    [string]$PythonExe = "",
    [switch]$RecreateVenv,
    [switch]$IncludeP21
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
& .\.venv\Scripts\ruff.exe check clinical_data_viewer tests run.py scripts\p21_bundle.py
& .\.venv\Scripts\ruff.exe format --check clinical_data_viewer\integrations\p21 tests\test_p21_integration.py tests\test_p21_packaging.py run.py scripts\p21_bundle.py
& $VenvPython -m compileall -q clinical_data_viewer tests run.py scripts\p21_bundle.py
$PreviousQtPlatform = $env:QT_QPA_PLATFORM
$env:QT_QPA_PLATFORM = "offscreen"
try {
    & $VenvPython -m unittest discover -s tests -v
}
finally {
    $env:QT_QPA_PLATFORM = $PreviousQtPlatform
}
& .\.venv\Scripts\pyinstaller.exe --noconfirm --clean SASDataViewer.spec

$BundleDirectory = (Resolve-Path "dist\SASDataViewer").Path
$Executable = Resolve-Path "dist\SASDataViewer\SASDataViewer.exe"
$BundleVerifier = (Resolve-Path "scripts\p21_bundle.py").Path
& $VenvPython $BundleVerifier verify --viewer-bundle $BundleDirectory --edition base
$ExecutableInfo = Get-Item $Executable
$ExecutableHash = Get-FileHash $Executable -Algorithm SHA256
$ArchivePath = Join-Path (Resolve-Path "dist").Path "SASDataViewer-Windows-x64.zip"
if (Test-Path $ArchivePath) {
    Remove-Item -Force $ArchivePath
}
Compress-Archive -Path $BundleDirectory -DestinationPath $ArchivePath -CompressionLevel Optimal
$ArchiveInfo = Get-Item $ArchivePath
$ArchiveHash = Get-FileHash $ArchivePath -Algorithm SHA256

Write-Host "Base build complete"
Write-Host "Bundle:     $BundleDirectory"
Write-Host "EXE:        $($ExecutableInfo.FullName)"
Write-Host "EXE size:   $([math]::Round($ExecutableInfo.Length / 1MB, 2)) MB"
Write-Host "EXE SHA256: $($ExecutableHash.Hash)"
Write-Host "ZIP:        $($ArchiveInfo.FullName)"
Write-Host "ZIP size:   $([math]::Round($ArchiveInfo.Length / 1MB, 2)) MB"
Write-Host "ZIP SHA256: $($ArchiveHash.Hash)"

if ($IncludeP21) {
    & $VenvPython -m pip install .\sidecars\p21-issue-explorer
    & .\.venv\Scripts\ruff.exe check sidecars\p21-issue-explorer\src sidecars\p21-issue-explorer\tests sidecars\p21-issue-explorer\run_p21.py
    & .\.venv\Scripts\ruff.exe format --check sidecars\p21-issue-explorer\src sidecars\p21-issue-explorer\tests sidecars\p21-issue-explorer\run_p21.py
    & $VenvPython -m compileall -q sidecars\p21-issue-explorer\src sidecars\p21-issue-explorer\tests sidecars\p21-issue-explorer\run_p21.py

    $PreviousPythonPath = $env:PYTHONPATH
    $PreviousSidecarQtPlatform = $env:QT_QPA_PLATFORM
    $env:PYTHONPATH = (Resolve-Path "sidecars\p21-issue-explorer\src").Path
    $env:QT_QPA_PLATFORM = "offscreen"
    try {
        & $VenvPython -m unittest discover -s sidecars\p21-issue-explorer\tests -v
    }
    finally {
        $env:PYTHONPATH = $PreviousPythonPath
        $env:QT_QPA_PLATFORM = $PreviousSidecarQtPlatform
    }

    & .\.venv\Scripts\pyinstaller.exe --noconfirm --clean sidecars\p21-issue-explorer\P21IssueExplorer.spec
    $SidecarBundle = (Resolve-Path "dist\P21IssueExplorer").Path
    $SidecarExecutable = (Resolve-Path "dist\P21IssueExplorer\P21IssueExplorer.exe").Path
    $SidecarSmoke = Start-Process -FilePath $SidecarExecutable -ArgumentList "--smoke-test" -Wait -PassThru
    if ($SidecarSmoke.ExitCode -ne 0) {
        throw "P21 side-car packaged smoke test failed with exit code $($SidecarSmoke.ExitCode)."
    }
    & $VenvPython $BundleVerifier stage --viewer-bundle $BundleDirectory --sidecar-bundle $SidecarBundle
    & $VenvPython $BundleVerifier verify --viewer-bundle $BundleDirectory --edition p21

    $P21ArchivePath = Join-Path (Resolve-Path "dist").Path "SASDataViewer-P21-Windows-x64.zip"
    if (Test-Path $P21ArchivePath) {
        Remove-Item -Force $P21ArchivePath
    }
    Compress-Archive -Path $BundleDirectory -DestinationPath $P21ArchivePath -CompressionLevel Optimal
    $P21ArchiveInfo = Get-Item $P21ArchivePath
    $P21ArchiveHash = Get-FileHash $P21ArchivePath -Algorithm SHA256
    $P21Executable = Resolve-Path "dist\SASDataViewer\_internal\p21-sidecar\P21IssueExplorer.exe"
    $P21ExecutableHash = Get-FileHash $P21Executable -Algorithm SHA256

    Write-Host "P21 build complete"
    Write-Host "Side-car:        $P21Executable"
    Write-Host "Side-car SHA256: $($P21ExecutableHash.Hash)"
    Write-Host "ZIP:             $($P21ArchiveInfo.FullName)"
    Write-Host "ZIP size:        $([math]::Round($P21ArchiveInfo.Length / 1MB, 2)) MB"
    Write-Host "ZIP SHA256:      $($P21ArchiveHash.Hash)"
}
