#!/usr/bin/env bash
set -euo pipefail

ZIP_PATH="${1:-}"

if [[ -z "$ZIP_PATH" || ! -f "$ZIP_PATH" ]]; then
  echo "Usage: $0 release/OpenBrep-free-macOS.zip"
  exit 2
fi

missing=()
for name in APPLE_ID APPLE_TEAM_ID APPLE_APP_SPECIFIC_PASSWORD; do
  if [[ -z "${!name:-}" ]]; then
    missing+=("$name")
  fi
done

if [[ "${#missing[@]}" -gt 0 ]]; then
  echo "❌ Missing notarization environment variables: ${missing[*]}"
  exit 2
fi

echo "Submitting $ZIP_PATH to Apple notary service"
xcrun notarytool submit "$ZIP_PATH" \
  --apple-id "$APPLE_ID" \
  --team-id "$APPLE_TEAM_ID" \
  --password "$APPLE_APP_SPECIFIC_PASSWORD" \
  --wait

echo "Notarization accepted: $ZIP_PATH"
