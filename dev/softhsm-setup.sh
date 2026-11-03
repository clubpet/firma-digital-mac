#!/bin/sh
# Crea un token SoftHSM2 de desarrollo con una clave RSA y un cert autofirmado.
# Uso: sh dev/softhsm-setup.sh   (idempotente: borra y recrea el token)
# Exporta: dev/softhsm2.conf (usar via SOFTHSM2_CONF), dev/test-cert.pem (raiz de confianza para el test).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
TOKENS="$HERE/tokens"
PIN=${PIN:-1234}
SOPIN=${SOPIN:-123456}
LABEL=${LABEL:-FirmaDev}
LIB=${FIRMA_PKCS11_LIB:-/opt/homebrew/lib/softhsm/libsofthsm2.so}

rm -rf "$TOKENS"; mkdir -p "$TOKENS"
printf 'directories.tokendir = %s\nobjectstore.backend = file\nlog.level = ERROR\n' "$TOKENS" > "$HERE/softhsm2.conf"
export SOFTHSM2_CONF="$HERE/softhsm2.conf"

softhsm2-util --init-token --free --label "$LABEL" --pin "$PIN" --so-pin "$SOPIN" >/dev/null

# Clave + cert autofirmado (sujeto imita un cert de Firma Digital AR con CUIL en serialNumber)
openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 3650 \
  -keyout "$HERE/test-key.pem" -out "$HERE/test-cert.pem" \
  -subj "/C=AR/O=Prueba/CN=Juan Perez/serialNumber=CUIL 20123456785" \
  -addext "keyUsage=critical,digitalSignature,nonRepudiation" 2>/dev/null
openssl x509 -in "$HERE/test-cert.pem" -outform DER -out "$HERE/test-cert.der"

softhsm2-util --import "$HERE/test-key.pem" --token "$LABEL" --pin "$PIN" --label "firma" --id 01 >/dev/null
pkcs11-tool --module "$LIB" --token-label "$LABEL" --login --pin "$PIN" \
  --write-object "$HERE/test-cert.der" --type cert --label "firma" --id 01 >/dev/null
rm -f "$HERE/test-key.pem" "$HERE/test-cert.der"

echo "Token '$LABEL' listo (PIN $PIN). export SOFTHSM2_CONF=$SOFTHSM2_CONF FIRMA_PKCS11_LIB=$LIB"
