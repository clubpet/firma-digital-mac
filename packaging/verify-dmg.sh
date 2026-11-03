#!/bin/bash
# Uso: packaging/verify-dmg.sh "packaging/dist/Firma Digital-<version>.dmg"
# Monta read-only, verifica firma, Gatekeeper, backend y dylib, y desmonta. Sale != 0 si algo esencial falla.
set -uo pipefail

DMG="${1:?uso: verify-dmg.sh <dmg>}"
MNT="$(mktemp -d)"
FAIL=0
ok()   { echo "OK    $*"; }
bad()  { echo "FALLA $*"; FAIL=1; }
note() { echo "AVISO $*"; }

cleanup() {
  [ -n "${BPID:-}" ] && kill "$BPID" 2>/dev/null
  hdiutil detach -quiet "$MNT" 2>/dev/null || hdiutil detach -force -quiet "$MNT"
  rmdir "$MNT" 2>/dev/null
}
trap cleanup EXIT

echo "== DMG"
codesign --verify "$DMG" && ok "firma del dmg" || bad "firma del dmg"
hdiutil verify -quiet "$DMG" && ok "checksum del dmg" || bad "checksum del dmg"
hdiutil attach -readonly -nobrowse -noautoopen -quiet -mountpoint "$MNT" "$DMG" || { bad "no monta"; exit 1; }
ls -la "$MNT"

APP="$(find "$MNT" -maxdepth 1 -name '*.app' | head -1)"
[ -n "$APP" ] && ok "app: $(basename "$APP")" || { bad "no hay .app"; exit 1; }
[ -L "$MNT/Aplicaciones" ] && ok "link a /Applications" || bad "falta link a /Applications"
[ -f "$MNT/LEEME.txt" ] && ok "LEEME.txt" || bad "falta LEEME.txt"

echo "== Firma de la app"
codesign --verify --deep --strict --verbose=2 "$APP" && ok "codesign --verify --deep --strict" || bad "codesign verify"
codesign -dvv "$APP" 2>&1 | grep -E '^(Identifier|Authority|Signature|TeamIdentifier|Runtime|flags|CodeDirectory)' | sed 's/^/      /'

echo "== Gatekeeper"
if spctl -a -t exec -vv "$APP" 2>&1 | sed 's/^/      /'; [ "${PIPESTATUS[0]}" -eq 0 ]; then
  ok "spctl acepta la app"
else
  note "spctl rechaza la app (esperado si es ad-hoc / no notarizada; ver LEEME.txt)"
fi
xcrun stapler validate -q "$APP" 2>/dev/null && ok "ticket de notarizacion stapleado" || note "sin ticket de notarizacion"

echo "== Backend"
RES="$APP/Contents/Resources"
BE="$RES/backend/firma-backend"
[ -d "$BE" ] && BE="$BE/firma-backend" # PyInstaller onedir
if [ -x "$BE" ]; then
  LOG="$(mktemp)"
  # Mismos args que usa la app (Backend.swift); el dylib se pasa si esta.
  LIB="$(find "$RES/driver" -name '*.dylib' 2>/dev/null | head -1)"
  "$BE" --port 0 ${LIB:+--lib "$LIB"} --trust "$RES/trust" --token verify-dmg >"$LOG" 2>&1 & BPID=$!
  for _ in $(seq 1 60); do grep -q READY "$LOG" && break; kill -0 "$BPID" 2>/dev/null || break; sleep 0.5; done
  if grep -q READY "$LOG"; then ok "backend arranca: $(grep -m1 READY "$LOG")"; else bad "backend no imprimio READY"; sed 's/^/      /' "$LOG" | tail -20; fi
  kill "$BPID" 2>/dev/null; wait "$BPID" 2>/dev/null; BPID=
  rm -f "$LOG"
else
  bad "falta $BE"
fi

echo "== Driver"
DRV="$APP/Contents/Resources/driver"
ls -la "$DRV" 2>/dev/null | sed 's/^/      /'
[ -n "$(find "$DRV" -name '*.dylib' 2>/dev/null)" ] || bad "no hay dylib en $DRV"
while IFS= read -r -d '' d; do
  echo "   $d"
  otool -L "$d" | sed 's/^/      /'
  codesign -dvv "$d" 2>&1 | grep -E '^(Identifier|Authority|TeamIdentifier|Signature)' | sed 's/^/      /'
  codesign --verify "$d" && ok "firma del dylib" || bad "firma del dylib"
done < <(find "$DRV" -name '*.dylib' -print0 2>/dev/null)
find "$DRV" -name '*.pkg' -print0 2>/dev/null | while IFS= read -r -d '' p; do
  echo "   $p"; pkgutil --check-signature "$p" | sed 's/^/      /' | head -6
done

echo "== Resultado"
[ "$FAIL" -eq 0 ] && echo "VERIFICACION OK" || echo "VERIFICACION CON FALLAS"
exit "$FAIL"
