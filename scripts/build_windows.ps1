$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$edition = if ($args.Length -gt 0) { $args[0].ToLower() } else { 'free' }
if ($edition -ne 'free' -and $edition -ne 'pro') {
  throw "Usage: ./scripts/build_windows.ps1 [free|pro]"
}

python -m pip install --upgrade pip
python -m pip install "." ".[ui]" pyinstaller build

if (Test-Path build) { Remove-Item build -Recurse -Force }
if (Test-Path dist) { Remove-Item dist -Recurse -Force }

$env:OBR_EDITION = $edition
pyinstaller --clean --noconfirm openbrep.spec
Remove-Item Env:\OBR_EDITION -ErrorAction SilentlyContinue

# Sensitive file guard (avoid false-positive on CA bundles like certifi\cacert.pem)
$sensitive = Get-ChildItem dist -Recurse -File -ErrorAction SilentlyContinue |
  Where-Object {
    $_.Name -in @('config.toml','.env') -or
    $_.Name -like 'id_rsa*' -or
    $_.Extension -in @('.p12','.pfx','.key')
  } |
  Where-Object { $_.FullName -notmatch 'certifi[\\/]'}
if ($sensitive) {
  $sensitive | ForEach-Object { Write-Host "❌ Sensitive file: $($_.FullName)" }
  throw "Sensitive files detected in dist/"
}

New-Item -ItemType Directory -Path release -Force | Out-Null
$out = "release/OpenBrep-$edition-Windows.zip"
if (Test-Path $out) { Remove-Item $out -Force }
Compress-Archive -Path dist/OpenBrep -DestinationPath $out
Write-Host "Built: $out"
