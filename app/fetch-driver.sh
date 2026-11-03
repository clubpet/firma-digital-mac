#!/bin/sh
# Baja el driver macOS oficial del mToken CryptoID FIPS 140-3 (Macroseguridad/Longmai, Developer ID 4M7RX53G2N,
# notarizado) desde la copia publica del Colegio de Escribanos PBA y extrae la libreria PKCS#11. Verifica sha256.
set -eu
OUT="${1:-$(dirname "$0")/../vendor}"
PKG_NAME="MSCryptoID-FIPS140-3_Mac_Spanish_V2.0.25.0912"
URL="https://www.colescba.org.ar/static/descarga/fima-digital/2026/${PKG_NAME}%201.zip"
ZIP_SHA="0ebb810531a6716be9fe3957f803c836e5e91b1e77a944f6c04c3d22f41686da"
DYLIB_SHA="534209db0634f3ec217da28f42814f7115da2e5ecddf6ca8582f60f81a5dc9e4"
mkdir -p "$OUT"
if [ ! -f "$OUT/$PKG_NAME.zip" ]; then curl -fsSL -o "$OUT/$PKG_NAME.zip" "$URL"; fi
echo "$ZIP_SHA  $OUT/$PKG_NAME.zip" | shasum -a 256 -c - >/dev/null || { echo "sha256 del zip no coincide" >&2; exit 1; }
if [ ! -f "$OUT/liblm_cryptoide_pkcs11.dylib" ]; then
  T=$(mktemp -d); unzip -q -j "$OUT/$PKG_NAME.zip" "*.pkg" -d "$T"
  pkgutil --expand-full "$T"/*.pkg "$T/x"
  cp "$(find "$T/x" -path '*opt/CryptoIDE/lib/liblm_cryptoide_pkcs11.dylib' | head -1)" "$OUT/"
  rm -rf "$T"
fi
echo "$DYLIB_SHA  $OUT/liblm_cryptoide_pkcs11.dylib" | shasum -a 256 -c - >/dev/null || { echo "sha256 de la dylib no coincide" >&2; exit 1; }
echo "driver OK en $OUT"
