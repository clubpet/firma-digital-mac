#!/bin/sh
# Compila y arma "build/Firma Digital.app". Uso: sh app/build-app.sh
set -eu
APP_DIR=$(cd "$(dirname "$0")" && pwd)
REPO=$(dirname "$APP_DIR")
BUILD=$(mkdir -p "${BUILD_DIR:-$APP_DIR/build}" && cd "${BUILD_DIR:-$APP_DIR/build}" && pwd)  # BUILD_DIR=app/build-dev: no pisar una app en uso
APP="$BUILD/Firma Digital.app"
RES="$APP/Contents/Resources"
DRIVER_SRC="$REPO/vendor/liblm_cryptoide_pkcs11.dylib"
PKG_NAME=MSCryptoID-FIPS140-3_Mac_Spanish_V2.0.25.0912
PKG_ZIP="$REPO/vendor/$PKG_NAME.zip"
warn() { printf '\033[33mAVISO:\033[0m %s\n' "$*" >&2; }

cd "$APP_DIR"
ARCHS="--arch arm64 --arch x86_64"
swift build -c release $ARCHS || { warn "build universal fallo, compilo solo arm64"; ARCHS=""; swift build -c release; }
BIN="$(swift build -c release $ARCHS --show-bin-path)/FirmaDigital"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$RES/driver" "$RES/backend"
cp "$BIN" "$APP/Contents/MacOS/FirmaDigital"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>ar.rubino.firmadigital</string>
  <key>CFBundleName</key><string>Firma Digital</string>
  <key>CFBundleDisplayName</key><string>Firma Digital</string>
  <key>CFBundleExecutable</key><string>FirmaDigital</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleDevelopmentRegion</key><string>es</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>CFBundleDocumentTypes</key><array><dict>
    <key>CFBundleTypeName</key><string>Documento PDF</string>
    <key>CFBundleTypeRole</key><string>Editor</string>
    <key>LSHandlerRank</key><string>Alternate</string>
    <key>LSItemContentTypes</key><array><string>com.adobe.pdf</string></array>
  </dict></array>
</dict></plist>
PLIST

if [ ! -f "$BUILD/AppIcon.icns" ]; then
  rm -rf "$BUILD/AppIcon.iconset"
  swift "$APP_DIR/icon.swift" "$BUILD/AppIcon.iconset"
  iconutil -c icns "$BUILD/AppIcon.iconset" -o "$BUILD/AppIcon.icns"
fi
cp "$BUILD/AppIcon.icns" "$RES/AppIcon.icns"

# Driver PKCS#11 (universal, firmado por Longmai) + .pkg oficial
if [ -f "$DRIVER_SRC" ]; then cp "$DRIVER_SRC" "$RES/driver/"; else warn "no esta $DRIVER_SRC: la app no podra usar el token"; fi
if [ ! -f "$BUILD/$PKG_NAME.pkg" ] && [ -f "$PKG_ZIP" ]; then unzip -o -j -q "$PKG_ZIP" "*.pkg" -d "$BUILD"; fi
if [ -f "$BUILD/$PKG_NAME.pkg" ]; then cp "$BUILD/$PKG_NAME.pkg" "$RES/driver/"; else warn "no esta $PKG_ZIP: sin paquete oficial"; fi

cp -R "$REPO/trust" "$RES/trust"
cp "$REPO/README.md" "$RES/README.md"

# Motor de firma (PyInstaller onefile u onedir)
if [ -d "$REPO/dist/firma-backend" ]; then cp -R "$REPO/dist/firma-backend" "$RES/backend/firma-backend"
elif [ -f "$REPO/dist/firma-backend" ]; then cp "$REPO/dist/firma-backend" "$RES/backend/firma-backend"
else warn "no esta dist/firma-backend: la app arranca pero sin motor (en desarrollo usar FIRMA_BACKEND=$REPO/firma.py)"; fi

# Ad-hoc, sin hardened runtime: el motor hace dlopen de una dylib firmada por otro equipo (Longmai).
codesign --force --deep -s - "$APP"
echo "Listo: $APP"
