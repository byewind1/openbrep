#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python3 -m pip install --upgrade pip
python3 -m pip install "." ".[ui]" pyinstaller build

rm -rf build dist
pyinstaller --clean --noconfirm openbrep.spec

mkdir -p release
if command -v ditto >/dev/null 2>&1; then
  ditto -c -k --sequesterRsrc --keepParent "dist/OpenBrep" "release/OpenBrep-macOS.zip"
else
  (cd dist && zip -r "../release/OpenBrep-macOS.zip" OpenBrep)
fi

echo "Built: release/OpenBrep-macOS.zip"
