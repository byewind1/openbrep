#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${1:-dist/OpenBrep}"
IDENTITY="${MACOS_DEVELOPER_ID_APPLICATION:-}"

if [[ -z "$IDENTITY" ]]; then
  echo "Usage: MACOS_DEVELOPER_ID_APPLICATION='Developer ID Application: Name (TEAMID)' $0 dist/OpenBrep"
  exit 2
fi

if [[ ! -d "$APP_DIR" ]]; then
  echo "❌ macOS package directory not found: $APP_DIR"
  exit 2
fi

echo "Signing Mach-O files in $APP_DIR"

while IFS= read -r -d '' path; do
  if [[ "$path" == "$APP_DIR/OpenBrep" ]]; then
    continue
  fi
  if file "$path" | grep -q "Mach-O"; then
    codesign --force --timestamp --options runtime --sign "$IDENTITY" "$path"
  fi
done < <(find "$APP_DIR" -type f -print0)

codesign --force --timestamp --options runtime --sign "$IDENTITY" "$APP_DIR/OpenBrep"
codesign --verify --deep --strict --verbose=2 "$APP_DIR/OpenBrep"
codesign -dv --verbose=2 "$APP_DIR/OpenBrep"

echo "Signed: $APP_DIR/OpenBrep"
