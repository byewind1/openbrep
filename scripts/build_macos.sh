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

cat > dist/OpenBrep/OpenBrep.command <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
./OpenBrep
EOF
chmod +x dist/OpenBrep/OpenBrep.command

cat > dist/OpenBrep/README-macOS.txt <<'EOF'
OpenBrep macOS package

How to start:
1. Unzip OpenBrep-free-macOS.zip.
2. Open the OpenBrep folder.
3. Double-click OpenBrep.command.
4. Your browser should open OpenBrep automatically.

If macOS blocks the app:
1. Right-click OpenBrep.command.
2. Choose Open.
3. Confirm Open in the system prompt.

If the browser does not open automatically, visit:
http://127.0.0.1:8501

Keep the OpenBrep file and _internal folder together. Do not move only the
OpenBrep executable out of this folder.
EOF

(
  cd dist
  zip -r -X "../$OUT" OpenBrep >/dev/null
)

echo "Built: $OUT"
