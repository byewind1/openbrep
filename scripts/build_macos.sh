#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

EDITION="${1:-free}"
EDITION_LOWER="$(echo "$EDITION" | tr '[:upper:]' '[:lower:]')"
if [[ "$EDITION_LOWER" != "free" && "$EDITION_LOWER" != "pro" ]]; then
  echo "Usage: $0 [free|pro]"
  exit 1
fi

python3 -m pip install --upgrade pip
python3 -m pip install "." ".[ui]" pyinstaller build

rm -rf build dist
OBR_EDITION="$EDITION_LOWER" pyinstaller --clean --noconfirm openbrep.spec

# Sensitive file guard (avoid false-positive on CA bundles like certifi/cacert.pem)
SENSITIVE_FILES=$(find dist -type f \
  \( -name 'config.toml' -o -name '.env' -o -name 'id_rsa*' -o -name '*.p12' -o -name '*.pfx' -o -name '*.key' \) \
  ! -path '*/certifi/*' || true)
if [[ -n "$SENSITIVE_FILES" ]]; then
  echo "❌ Sensitive file detected in dist/"
  echo "$SENSITIVE_FILES"
  exit 2
fi

mkdir -p release
OUT="release/OpenBrep-${EDITION_LOWER}-macOS.zip"
rm -f "$OUT"
(
  cd dist
  zip -r -X "../$OUT" OpenBrep >/dev/null
)

echo "Built: $OUT"
