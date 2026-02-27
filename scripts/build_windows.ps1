$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

python -m pip install --upgrade pip
python -m pip install "." ".[ui]" pyinstaller build

if (Test-Path build) { Remove-Item build -Recurse -Force }
if (Test-Path dist) { Remove-Item dist -Recurse -Force }

pyinstaller --clean --noconfirm openbrep.spec

New-Item -ItemType Directory -Path release -Force | Out-Null
if (Test-Path release/OpenBrep-Windows.zip) { Remove-Item release/OpenBrep-Windows.zip -Force }
Compress-Archive -Path dist/OpenBrep -DestinationPath release/OpenBrep-Windows.zip
Write-Host "Built: release/OpenBrep-Windows.zip"
