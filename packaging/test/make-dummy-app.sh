#!/bin/bash
# Arma packaging/test/build/Firma Digital.app (v0.0.1) para probar make-dmg.sh / verify-dmg.sh sin la app real.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HERE/build/Firma Digital.app"
rm -rf "$HERE/build"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/backend" "$APP/Contents/Resources/driver" "$APP/Contents/Resources/trust"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>ar.rubino.firmadigital</string>
<key>CFBundleName</key><string>Firma Digital</string>
<key>CFBundleExecutable</key><string>FirmaDigital</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.0.1</string>
<key>CFBundleVersion</key><string>1</string>
</dict></plist>
PLIST
printf '#!/bin/sh\necho dummy\n' > "$APP/Contents/MacOS/FirmaDigital"
printf '#!/bin/sh\necho "READY port=1"\nexec sleep 30\n' > "$APP/Contents/Resources/backend/firma-backend"
chmod +x "$APP/Contents/MacOS/FirmaDigital" "$APP/Contents/Resources/backend/firma-backend"
echo 'int dummy(void){return 0;}' | clang -x c -dynamiclib -o "$APP/Contents/Resources/driver/libdummy.dylib" -
codesign -s - "$APP/Contents/Resources/driver/libdummy.dylib"
echo dummy > "$APP/Contents/Resources/trust/root.pem"
codesign --force --deep -s - "$APP"
echo "$APP"
