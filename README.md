# Firma Digital (Ley 25.506) con token PKCS#11 — macOS

App web local minima para **firmar PDFs (PAdES)** con un token USB PKCS#11 (ej. mToken CryptoID) y
**verificar firmas**. Un solo archivo Python (`firma.py`), stdlib + [pyHanko](https://pyhanko.readthedocs.io/).
Corre en `http://127.0.0.1:8780`, no sale a la red salvo para bajar CRL/OCSP al firmar/verificar con raices.

## Instalacion

```sh
cd firma-digital-mac
python3 -m venv .venv
.venv/bin/pip install 'pyHanko[pkcs11]'
```

**Produccion (token mToken CryptoID FIPS 140-3):** el driver macOS oficial es el .pkg de Macroseguridad
`MSCryptoID-FIPS140-3_Mac_Spanish_V2.0.25.0912` (Longmai, Developer ID 4M7RX53G2N, notarizado). Macroseguridad
solo lo entrega por ticket en https://soporte.macroseguridad.la, pero el Colegio de Escribanos PBA lo hostea:
https://www.colescba.org.ar/static/descarga/fima-digital/2026/MSCryptoID-FIPS140-3_Mac_Spanish_V2.0.25.0912%201.zip
(sha256 zip `0ebb810531a6716be9fe3957f803c836e5e91b1e77a944f6c04c3d22f41686da`). El .pkg instala
`/opt/CryptoIDE/lib/liblm_cryptoide_pkcs11.dylib` (universal x86_64+arm64, PKCS#11 sobre PC/SC; el token es
CCID, macOS no necesita driver USB) mas una app Qt x86_64 que pide Rosetta. Alternativa sin Rosetta ni sudo:
copiar solo la dylib a `~/Library/CryptoIDE/lib/`. `firma.py` busca ambas rutas solo; si no, `FIRMA_PKCS11_LIB`
o `--lib`. Si la ruta no existe, la UI y `/status` lo dicen claramente.
Chequeo rapido: `pkcs11-tool --module ~/Library/CryptoIDE/lib/liblm_cryptoide_pkcs11.dylib -L` (brew opensc).
PIN de usuario de fabrica: `12345678` (salvo que la Autoridad de Registro lo haya cambiado). 3 PIN malos bloquean.

**Desarrollo (sin token):** `brew install softhsm opensc` y `sh dev/softhsm-setup.sh` crea un token
SoftHSM2 `FirmaDev` (PIN `1234`) con clave RSA + cert autofirmado "Juan Perez, CUIL 20123456785".
El token vive en `dev/tokens/` y se usa con `SOFTHSM2_CONF=dev/softhsm2.conf`.

## Correr

```sh
# token real
FIRMA_PKCS11_LIB=/ruta/al/driver.dylib .venv/bin/python firma.py

# SoftHSM de desarrollo
SOFTHSM2_CONF=$PWD/dev/softhsm2.conf FIRMA_PKCS11_LIB=/opt/homebrew/lib/softhsm/libsofthsm2.so .venv/bin/python firma.py
```

Abrir http://127.0.0.1:8780. Flags: `--lib RUTA`, `--port N`, `--host`, `-v` (debug).

## Uso

- **Firmar:** PDF + PIN (+ pagina, posicion X/Y en puntos desde abajo-izquierda, motivo, lugar). Devuelve
  `<nombre>-firmado.pdf` con una firma PAdES visible (recuadro de texto con el CN del firmante y fecha/hora).
  Cada firma agrega un campo `FirmaN`; un PDF ya firmado se puede volver a firmar (revision incremental).
  El PIN se usa solo en esa peticion: no se loguea ni se guarda. Con varios certificados en el token se
  toma el primero; se puede forzar con el campo `cert_label` del formulario (POST).
- **Verificar:** PDF → por cada firma: firmante (CN), `serialNumber` del sujeto (en certificados argentinos
  es el `CUIL 20xxxxxxxx5`), emisor, fecha, integridad, si el documento fue modificado despues de firmar,
  si la cadena es confiable (y por que no), y si tiene sello de tiempo.
- **`GET /status`:** JSON con ruta de la libreria, si existe, tokens presentes y certificados (sin PIN).

## Raices de confianza (PAdES-B-LT y validacion de cadena)

`trust/` ya trae los 22 certificados oficiales de la PKI argentina (AC Raiz 2007 y 2016, AC ONTI, AC
Modernizacion-PFDR, Encode, Lakaut, Box Custodia, Digilogix, AFIP, ANSES, etc.), identicos byte a byte al zip
oficial `https://pki.jgm.gov.ar/docs/Certificados_AC_Manual.zip`; origen y huellas SHA-256 en `trust/README.md`.
Con `trust/` presente:

- al firmar se embeben CRL/OCSP de la cadena → **PAdES-B-LT**;
- al verificar se valida la cadena contra esas raices, con la misma semantica que el validador oficial
  (https://validadordefirmas.gob.ar): cadena evaluada a la fecha de firma declarada, SHA-1 aceptado,
  revocacion soft-fail (el OCSP de ONTI responde `unauthorized`, la CRL diaria si anda).

Sin `trust/` se firma **PAdES-B-B** (la UI lo avisa) y la verificacion reporta "sin raices de confianza".
`FIRMA_TRUST_DIR` cambia la carpeta (lo usa el test).

## Validador oficial

`python3 validador_oficial.py doc.pdf` sube el PDF a validadordefirmas.gob.ar (la API de la pagina
https://www.argentina.gob.ar/servicio/valida-los-documentos-electronicos-firmados-digitalmente) y devuelve
su JSON: integridad, AC licenciada, vencimientos, revocacion, sello de tiempo, certificado. Es el veredicto
que vale ante terceros; `/verify` reproduce el mismo criterio en local.

Adobe Acrobat en Mac muestra "validez desconocida" hasta importar las raices como identidades de confianza
(la raiz argentina no esta en la AATL de Adobe); la opcion "Integracion de Windows" de la guia oficial no
existe en macOS.

## Prueba

```sh
.venv/bin/python test_firma.py
```

Recrea el token SoftHSM, levanta el server en :8799 con `dev/test-cert.pem` como raiz, prueba PIN
incorrecto, firma un PDF de 1 pagina (PAdES-B-LT), verifica (integro + cadena confiable + CUIL), altera un
byte y comprueba que la firma queda invalida.

## Limitaciones

- Sin sello de tiempo (PAdES-B-T/LTA): se necesita una TSA; agregar `timestamper=HTTPTimeStamper(url)` en
  `sign_pdf` si hace falta.
- El recuadro visible es texto plano (sin imagen/logo), tamano fijo 220x60 pt.
- Un token a la vez (primer slot con token inicializado); operaciones serializadas con un lock global.
- Solo probado con SoftHSM2 (RSA-2048/SHA-256). Con el driver real del mToken puede hacer falta
  `cert_label` si el token trae varios certificados.
- Sin HTTPS ni autenticacion: pensado para escuchar solo en 127.0.0.1.

## Distribucion

`packaging/make-dmg.sh "app/build/Firma Digital.app"` arma `packaging/dist/Firma Digital-<version>.dmg`
(app + link a Aplicaciones + LEEME.txt, firmado ad-hoc) e imprime el sha256; `packaging/verify-dmg.sh <dmg>`
lo verifica. Sin certificado Developer ID la app no esta notarizada y el destinatario la habilita a mano la
primera vez (pasos en `packaging/LEEME.txt`). Firma Developer ID + notarizacion: `packaging/README.md`.
