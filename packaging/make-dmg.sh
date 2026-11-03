#!/bin/bash
# Uso: packaging/make-dmg.sh "<ruta>/Firma Digital.app"
# Sale: packaging/dist/Firma Digital-<version>.dmg
# Por defecto firma ad-hoc. Con SIGN_IDENTITY="Developer ID Application: ..." firma con hardened runtime
# y, si ademas hay NOTARY_PROFILE, notariza y staplea.
set -euo pipefail

APP="${1:?uso: make-dmg.sh <ruta a .app>}"
APP="${APP%/}"
[ -d "$APP/Contents" ] || { echo "no es un .app: $APP" >&2; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
NAME="$(basename "$APP" .app)"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")"
OUT_DIR="$HERE/dist"
DMG="$OUT_DIR/$NAME-$VERSION.dmg"
SIGN="${SIGN_IDENTITY:--}"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP" "$STAGE/$NAME.app"

if [ "$SIGN" != "-" ]; then
  # Developer ID: hardened runtime + disable-library-validation (el backend carga el dylib de Longmai, team 4M7RX53G2N).
  # codesign --deep no firma bien binarios anidados con entitlements: firmamos de adentro hacia afuera.
  # Los binarios que ya traen firma Developer ID de terceros (el dylib de Longmai) se dejan como vienen.
  MAIN="$STAGE/$NAME.app/Contents/MacOS/$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Contents/Info.plist")"
  find "$STAGE/$NAME.app/Contents" -type f -print0 |
    while IFS= read -r -d '' f; do
      [ "$f" = "$MAIN" ] && continue
      file -b "$f" | grep -q 'Mach-O' || continue
      codesign -dvv "$f" 2>&1 | grep -q '^Authority=Developer ID Application' && continue
      codesign --force --options runtime --timestamp --entitlements "$HERE/entitlements.plist" -s "$SIGN" "$f"
    done
  codesign --force --options runtime --timestamp --entitlements "$HERE/entitlements.plist" -s "$SIGN" "$STAGE/$NAME.app"
  # El .pkg oficial del driver ya viene firmado por el fabricante: no se toca.
else
  # ponytail: la app ya viene firmada ad-hoc del build; solo re-firmamos si la firma no valida.
  codesign --verify --deep --strict "$STAGE/$NAME.app" 2>/dev/null || codesign --force --deep -s - "$STAGE/$NAME.app"
fi

ln -s /Applications "$STAGE/Aplicaciones"
cp "$HERE/LEEME.txt" "$STAGE/LEEME.txt"

mkdir -p "$OUT_DIR"
rm -f "$DMG"
hdiutil create -quiet -volname "$NAME $VERSION" -srcfolder "$STAGE" -fs APFS -format ULFO "$DMG"
codesign --force -s "$SIGN" ${SIGN_IDENTITY:+--timestamp} "$DMG"

if [ "$SIGN" != "-" ] && [ -n "${NOTARY_PROFILE:-}" ]; then
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
fi

echo "DMG: $DMG"
echo "Tamano: $(du -h "$DMG" | cut -f1)"
shasum -a 256 "$DMG"
