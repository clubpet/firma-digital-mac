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

Abrir http://127.0.0.1:8780. Flags: `--lib RUTA`, `--trust DIR`, `--port N` (0 = al azar), `--host`, `--token SECRETO`, `--lt`, `--tsa URL|none`,
`-v` (debug). La UI web no manda `X-Firma-Token`: con `--token` solo sirve la API.

## Uso

- **Firmar:** PDF + PIN (+ pagina, posicion X/Y en puntos desde abajo-izquierda, motivo, lugar). Devuelve
  `<nombre>-firmado.pdf` con una firma PAdES-B-T (con sello de tiempo) visible (recuadro de texto con el CN del firmante y fecha/hora).
  Cada firma agrega un campo `FirmaN`; un PDF ya firmado se puede volver a firmar (revision incremental).
  El PIN se usa solo en esa peticion: no se loguea ni se guarda. Con varios certificados en el token se
  toma el primero; se elige con `cert_id` (hex, el `id` de `/status`).
- **Verificar:** PDF → por cada firma: firmante (CN), `serialNumber` del sujeto (en certificados argentinos
  es el `CUIL 20xxxxxxxx5`), emisor, fecha, integridad, si el documento fue modificado despues de firmar,
  si la cadena es confiable (y por que no), y si tiene sello de tiempo.
- **`GET /status`:** JSON con ruta de la libreria, si existe, tokens presentes y certificados (sin PIN).

## Raices de confianza (PAdES-B-LT y validacion de cadena)

`trust/` ya trae los 22 certificados oficiales de la PKI argentina (AC Raiz 2007 y 2016, AC ONTI, AC
Modernizacion-PFDR, Encode, Lakaut, Box Custodia, Digilogix, AFIP, ANSES, etc.), identicos byte a byte al zip
oficial `https://pki.jgm.gov.ar/docs/Certificados_AC_Manual.zip`; origen y huellas SHA-256 en `trust/README.md`.
Con `trust/` presente:

- por defecto se firma **PAdES-B-B**: sin CRL/OCSP embebidas ni descargas al firmar. Con `--lt` (o
  `FIRMA_LT=1`) se embeben CRL/OCSP de la cadena → **PAdES-B-LT**, pero la CRL de ONTI (`FD.crl`) pesa ~9 MB:
  un PDF de 50 KB sale de ~9,3 MB (en B-B, 58 KB). En modo `--lt` el `ValidationContext` se arma una vez y se
  reusa entre firmas, se pre-carga en segundo plano al arrancar con los certs del token y se rehace cuando una
  CRL cacheada pasa su `nextUpdate`; los dos espejos de la CRL se bajan pero pyHanko embebe una sola copia
  (dedup por contenido). Sin red (o cadena no confiable) se firma igual en **PAdES-B-B** y `X-Level` lo dice;
- al verificar se valida la cadena contra esas raices, con la misma semantica que el validador oficial
  (https://validadordefirmas.gob.ar): cadena evaluada a la fecha de firma declarada, SHA-1 aceptado,
  revocacion soft-fail (el OCSP de ONTI responde `unauthorized`, la CRL diaria si anda).

Sin `trust/` la verificacion reporta "sin raices de confianza".
`FIRMA_TRUST_DIR` cambia la carpeta (lo usa el test).

## Sello de tiempo (RFC 3161)

Cada firma lleva por defecto un sello de tiempo de **`https://tsa.encodesa.com.ar/tsa`**: el host es de Encode
(AC licenciada) y los tokens los firma **"Servicio de Sellado de Tiempo de JGM"**, emitido por **AC ONTI**
("Autoridad Certificante de Firma Digital", ya en `trust/`), asi que `/verify` lo da por confiable sin agregar
nada. Sin sello, validadordefirmas.gob.ar avisa "La hora de la firma procede del reloj del equipo del firmante".
Suma ~9 KB al PDF y ~0,5 s por firma (~1,2 s la primera: pyHanko pide un sello de prueba para reservar lugar; se
reusa el cliente para que no se repita). Si la TSA no responde (timeout 8 s) se firma igual, sin sello, y
`X-Level` lo dice. `--tsa URL` (o `FIRMA_TSA`) cambia la TSA, `--tsa none` la apaga.

Relevamiento 2026-11-03 (POST de un `TimeStampReq` a cada URL):

| TSA | Resultado |
|---|---|
| `https://tsa.encodesa.com.ar/tsa` | OK, emisor JGM / AC ONTI (politica OID `2.3.4.5`) |
| `tsa.firmar.gob.ar`, `tsa.jgm.gov.ar`, `tsa.lakaut.com.ar`, `tsa.digilogix.com.ar` | no resuelven (DNS) |
| `tsa.pki.gob.ar` (IIS de ONTI) | existe, 404 en todas las rutas probadas |
| `tst.firmar.gob.ar` | testing de la Plataforma de Firma Digital Remota, no es TSA |
| `tsa.boxcustodia.com` | sitio web, no TSA |
| `timestamp.digicert.com`, `timestamp.sectigo.com`, `ts.ssl.com`, `freetsa.org/tsr` | OK, extranjeras |

validadordefirmas.gob.ar muestra `tsaInfo` (`subjectCN`, `issuerCN`, `valid`, `containsTsa`) y acepta como
`valid: true` tanto la TSA de JGM como DigiCert: no informa si la TSA es licenciada. Para usar una TSA extranjera
y que `/verify` diga `sello_de_tiempo_confiable: true`, poner su raiz en `trust/tsa/*.pem` (solo se usa para
validar sellos de tiempo, no firmas).

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

Recrea el token SoftHSM con 2 certs (01 autofirmado = raiz; 02 emitido por una CA de prueba con la CRL en un
puerto cerrado) y una TSA local de prueba (DummyTimeStamper de pyHanko), levanta el backend con `--port 0 --token`,
prueba 401, PIN incorrecto, firma B-T con `cert_id` 01 y 02, pagina rotada y recortada, verifica (sello confiable),
altera un byte y llama `/shutdown`; despues `--lt` (B-LT con 01, fallback a B-B con 02), `--tsa none` (B-B), TSA
caida (B-B "sin sello de tiempo") y, si hay red, la TSA real por defecto. Contra el binario congelado: `FIRMA_BACKEND=dist/firma-backend/firma-backend .venv/bin/python test_firma.py`.

## Limitaciones

- Sin PAdES-B-LTA (sello de documento para archivo a largo plazo).
- El recuadro visible es texto plano (sin imagen/logo), tamano fijo 220x60 pt.
- Un token a la vez (primer slot con token inicializado); operaciones serializadas con un lock global.
- Firma probada con SoftHSM2 (RSA-2048/SHA-256); con el mToken real se probo `/status` y la pre-carga de CRL.
- Sin HTTPS: pensado para escuchar solo en 127.0.0.1; `--token` evita que otros procesos locales usen el token.

## Backend congelado (PyInstaller)

```sh
sh build-backend.sh        # -> dist/firma-backend/ (onedir, arm64, ~48 MB); ejecutable dist/firma-backend/firma-backend
```

Onedir a proposito: onefile (`ONEFILE=1`) se descomprime en /tmp en cada arranque y macOS re-escanea las
dylibs, ~16 s por inicio; onedir arranca en ~0.3 s (el primer arranque tras compilar tarda mas por el escaneo
de Gatekeeper). Copiar el directorio entero (`firma-backend` + `_internal/`). Las raices: `--trust`, si no una
carpeta `trust/` junto al ejecutable, si no la copia empaquetada en `_internal/trust`.

## API

Contrato estable que usa la app nativa. Todas las respuestas de error de `/sign` son texto plano en castellano;
las de los demas endpoints, JSON `{"error": "..."}`.

**CLI:** `firma-backend [--port N] [--host 127.0.0.1] [--lib RUTA] [--trust DIR] [--token SECRETO] [--lt] [--tsa URL|none]`. Al
arrancar imprime por stdout exactamente una linea `READY port=<n>` (los logs van a stderr). `FIRMA_PKCS11_LIB`,
`FIRMA_TRUST_DIR`, `FIRMA_LT=1` y `FIRMA_TSA` valen como default de `--lib`/`--trust`/`--lt`/`--tsa`
(default TSA `https://tsa.encodesa.com.ar/tsa`). Sin `--lt` el backend queda
ocioso al arrancar (no baja nada). Con `--token`, todo request sin el header
`X-Firma-Token: <secreto>` recibe `401`.

- `GET /health` → `{"ok": true, "version": "1.0.0"}`
- `GET /status` → `{"lib", "lib_found", "trust_roots", "lt", "tsa" (URL o null), "tokens": [{"slot", "label", "serial", "pin_status",
  "certs": [{"label", "id", "cn", "subject", "valid_until"}]}]}` mas `"error"` si algo falla y `"pkcs11_error"`
  si la lib existe pero no carga o el token falla. `pin_status`: `ok` | `bajo` (CKF_USER_PIN_COUNT_LOW: hubo
  intentos fallidos desde el ultimo correcto) | `ultimo_intento` (CKF_USER_PIN_FINAL_TRY) | `bloqueado`.
- `POST /sign` multipart: `pdf` (archivo), `pin`, `page` (1-based, default 1), `x`, `y`, `w`, `h` (puntos,
  default 50/50/220/60, admiten decimales), `reason`, `location`, `cert_id` (hex, opcional).
  - Coordenadas: espacio de usuario por defecto de la pagina, sin rotar, origen abajo-izquierda: el mismo
    espacio que PDFKit "page space" (`PDFView.convert(_:to: page)`, `PDFAnnotation.bounds`). Se usan tal cual
    como `/Rect`. Con `/CropBox` distinto del `/MediaBox` las coordenadas son absolutas (no restar el origen del
    CropBox); la caja tiene que caer dentro del CropBox o es `400`. Con `/Rotate` 90/180/270 el `/Rect` sigue en
    espacio sin rotar (en una pagina a 90 grados, una caja que en pantalla es ancha tiene `w` chico y `h` grande,
    que es lo que devuelve PDFKit); el backend contrarrota la apariencia (`/Matrix`) para que el texto se lea
    derecho en pantalla.
  - `200`: bytes del PDF; headers `X-Filename` y `X-Signer` (URL-encoded), `X-Level` (comparar por prefijo):
    `PAdES-B-T` (default, con sello de tiempo); `PAdES-B-B` (`--tsa none`); `PAdES-B-B (sin sello de tiempo:
    <motivo>)` (TSA caida, la firma vale igual). Con `--lt`: `PAdES-B-LT`, o `PAdES-B-B (sin raices de confianza)`
    / `PAdES-B-B (sin CRL/OCSP: sin conexion o cadena no confiable)`, seguidos de ` con sello de tiempo` si lo hubo.
  - Errores: `400` datos invalidos (falta PDF/PIN, pagina inexistente, caja fuera de la pagina, cert_id no hex);
    `401` falta `X-Firma-Token`; `403` PIN incorrecto o de largo invalido; `404` no hay cert con ese `cert_id`;
    `409` token no conectado, sin certificados, lib no encontrada o error del token; `423` PIN bloqueado. En `403`/
    `423` puede venir `X-Pin-Attempts-Left`: `bajo`, `1` (ultimo intento: el proximo error bloquea) o `0`.
- `POST /verify` multipart `pdf` → `{"firmas": [{campo, firmante, serial_number, emisor, fecha_firma, integra,
  sin_modificar, cobertura, modificaciones, cadena_confiable, motivo_confianza, sello_de_tiempo, sello_de_tiempo_confiable,
  sello_de_tiempo_fecha (ISO, hora de la TSA), sello_de_tiempo_emisor (CN de la TSA), validado_a, resumen} | {campo, error}], "raices_confianza": n}`.
- `GET` o `POST /shutdown` → `{"ok": true}` y el proceso termina con codigo 0.

## Distribucion

`packaging/make-dmg.sh "app/build/Firma Digital.app"` arma `packaging/dist/Firma Digital-<version>.dmg`
(app + link a Aplicaciones + LEEME.txt, firmado ad-hoc) e imprime el sha256; `packaging/verify-dmg.sh <dmg>`
lo verifica. Sin certificado Developer ID la app no esta notarizada y el destinatario la habilita a mano la
primera vez (pasos en `packaging/LEEME.txt`). Firma Developer ID + notarizacion: `packaging/README.md`.
