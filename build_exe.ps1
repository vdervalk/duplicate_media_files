<#
    Bouwt DuplicateMediaFinder.exe op Windows 11.

    Gebruik (PowerShell, in de projectmap):
        .\build_exe.ps1

    Resultaat: dist\DuplicateMediaFinder.exe  (een los bestand, geen installatie nodig)
#>

$ErrorActionPreference = "Stop"

$python = "py"
if (-not (Get-Command $python -ErrorAction SilentlyContinue)) { $python = "python" }

Write-Host "==> Virtuele omgeving aanmaken (.venv)" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) { & $python -m venv .venv }

$venvPython = ".\.venv\Scripts\python.exe"

Write-Host "==> Afhankelijkheden installeren" -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements-dev.txt

Write-Host "==> Tests draaien" -ForegroundColor Cyan
& $venvPython -m pytest
if ($LASTEXITCODE -ne 0) { throw "Tests zijn niet geslaagd; build gestopt." }

Write-Host "==> Executable bouwen" -ForegroundColor Cyan
& $venvPython -m PyInstaller --noconfirm --clean DuplicateMediaFinder.spec

Write-Host ""
Write-Host "Klaar: dist\DuplicateMediaFinder.exe" -ForegroundColor Green
