# Arquitectura

Resumen técnico para quien quiera entender, auditar o modificar el proyecto. La API HTTP completa está en el
[README](../README.md#api-del-backend).

## Componentes

| Componente | Código | Qué hace |
|---|---|---|
| App | `app/Sources/FirmaDigital/*.swift` (SwiftUI + PDFKit) | Muestra el PDF, deja dibujar el recuadro, pide el PIN, guarda el firmado y muestra la verificación. No sabe nada de PKCS#11 ni de PAdES. |
| Motor | `firma.py`, congelado con PyInstaller (`backend.spec`) como `firma-backend` | Servidor HTTP en `127.0.0.1`. Firma con pyHanko (`PKCS11Signer`) y verifica con `pyhanko_certvalidator`. |
| Driver | `liblm_cryptoide_pkcs11.dylib` (Longmai, se baja en el build) | Librería PKCS#11 que el motor carga con `dlopen` (python-pkcs11). Habla con el token por PC/SC. |
| Token | mToken CryptoID FIPS 140-3, USB CCID | Guarda la clave privada y el certificado. Hace `C_Sign` adentro: la clave nunca sale. |
| Raíces | `trust/*.pem` (22 certificados de la IFDRA) | Anclas de confianza para verificar y, con `--lt`, para armar la cadena B-LT. `trust/tsa/` (opcional) suma raíces solo para sellos de tiempo. |

Dentro del `.app`: `Contents/MacOS/FirmaDigital` (la app), `Contents/Resources/backend/firma-backend/`
(motor onedir), `Resources/driver/` (dylib + `.pkg` oficial), `Resources/trust/`.

## Arranque del motor

1. La app lanza `firma-backend --port 0 --lib <dylib> --trust <dir> --parent-pid <pid de la app>` con
   `FIRMA_TOKEN=<secreto>` en el entorno. El secreto son dos UUID v4 (~244 bits del CSPRNG del sistema),
   nuevo en cada arranque. Va por env y no por argumento porque los argumentos los ve cualquier usuario con `ps`.
2. El motor bindea un puerto libre en `127.0.0.1` e imprime por stdout una sola línea `READY port=<n>` (los logs
   van a stderr). La app la espera hasta 90 s; si no llega (o el proceso termina antes), mata el proceso y
   muestra "Reiniciar motor de firma".
3. La app sigue drenando stdout (y suelta el handler en EOF) y consulta `GET /status` cada 2 s: driver, token,
   certificados, estado del contador de PIN. No consulta mientras firma, para no competir por el token.
4. Al cerrar, la app llama `/shutdown` y después `terminate()`. Si la app muere sin cerrar (crash, `kill -9`),
   el motor ve que su proceso padre cambió (`--parent-pid`) y se cierra solo en ~1 s. Si el motor se cae, la
   app lo detecta por `terminationHandler` y ofrece reiniciarlo.

## Una firma, paso a paso

1. **Abrir el PDF.** PDFKit lo muestra; la app guarda la URL y el `PDFDocument`.
2. **Recuadro.** `SignPDFView` (subclase de `PDFView`) convierte lo que se dibuja en pantalla a coordenadas de
   página con `PDFView.convert(_:to: page)`: puntos PDF, espacio de usuario sin rotar, origen abajo-izquierda,
   absolutas (con CropBox no se resta su origen). Un clic sin arrastrar da 220x60 pt centrado.
3. **`POST /sign`** multipart: `pdf`, `pin`, `page` (1-based), `x`, `y`, `w`, `h`, `reason`, `location`,
   `cert_id`. La app vacía el campo PIN en cuanto lo toma.
4. **Validaciones** (motor): PDF legible y no cifrado, página existente, recuadro dentro del CropBox.
5. **Login.** Con un lock global (`TOKEN_LOCK`: los tokens USB no toleran sesiones concurrentes) abre sesión con
   `C_Login(PIN)`. PIN incorrecto: lee los flags `CKF_USER_PIN_COUNT_LOW` / `FINAL_TRY` / `LOCKED` y responde
   `403`/`423` con `X-Pin-Attempts-Left`.
6. **Firma.** pyHanko agrega un campo `/FirmaN` con apariencia visible (texto Helvetica; si la página tiene
   `/Rotate`, la apariencia se contrarrota con `/Matrix` para leerse derecha), calcula el SHA-256 del rango de
   bytes y pide la firma al token con `PKCS11Signer` (clave y cert emparejados por `CKA_ID`: en el mToken el
   label de la clave no coincide con el del cert). El token hace `C_Sign` (RSA PKCS#1 v1.5).
7. **CMS PAdES.** pyHanko arma la `SignedData` (subfilter `ETSI.CAdES.detached`, atributos firmados con el
   certificado).
8. **Sello de tiempo.** Pide un token RFC 3161 a la TSA (default `https://tsa.encodesa.com.ar/tsa`, firmado
   por "Servicio de Sellado de Tiempo de JGM") sobre el hash de la firma, y lo embebe como atributo no firmado
   = **PAdES-B-T**. Si la TSA no responde en 8 s, firma igual sin sello (B-B) y lo dice en `X-Level`.
9. **B-LT opcional** (`--lt`): valida la cadena, baja CRL/OCSP y los embebe en un `/DSS`.
10. **Actualización incremental.** Todo se agrega al final del archivo; las firmas anteriores siguen válidas.
    La app ofrece guardar `<nombre>-firmado.pdf`, lo abre y lo verifica.

## Verificación

`POST /verify` recorre `embedded_signatures` y, por cada firma, llama `validate_pdf_signature` con criterios
equivalentes a validadordefirmas.gob.ar:

- Cadena evaluada **a la fecha de firma declarada** (un certificado hoy vencido sigue valiendo si estaba vigente
  al firmar); si la firma no declara fecha, se evalúa a "ahora" con descarga de revocación.
- **SHA-1 aceptado** (GDE/BORA todavía lo usan); solo MD2/MD5 se rechazan.
- Revocación **soft-fail** (el OCSP de ONTI responde "unauthorized"; la CRL sí anda).
- `strict=False` al leer, por los PDFs híbridos de GDE/BORA.

Devuelve, entre otros: `integra` (hash y firma correctos), `sin_modificar` (además cubre todo el archivo o la
revisión entera, sin cambios DocMDP no permitidos), `cadena_confiable` (llega a una raíz de `trust/`) y los
datos del sello de tiempo. Una firma ilegible se informa como `{campo, error}` sin cortar el resto.

## Modelo de amenazas

**Qué protege.** La clave privada no sale nunca del token. El motor solo escucha en `127.0.0.1` y en un
puerto al azar. Todo pedido sin el secreto del arranque recibe `401` (comparación en tiempo constante). El
PIN no se escribe en disco ni en logs (el log HTTP registra solo método y ruta), la app no lo guarda entre
intentos y nunca reintenta sola. Tres PIN incorrectos seguidos bloquean el token por hardware.

**Límites honestos.**

- El PIN viaja en claro por loopback y vive en la memoria de ambos procesos hasta que se libera: ni Swift ni
  Python permiten borrarlo de forma garantizada.
- Sin `--token` (modo web) no hay secreto: el motor solo acepta pedidos cuyo `Host` sea `127.0.0.1`/`localhost`
  y cuyo `Origin`, si viene, sea el mismo. Así una página web ajena no puede mandar PINs (CSRF) ni leer
  `/status` con DNS rebinding.

**Otro usuario de la misma Mac** no ve el secreto (no está en los argumentos; el entorno de procesos ajenos
no se lee sin root). Puede conectarse al puerto pero recibe `401`.

**Malware corriendo como el mismo usuario** está fuera del alcance de cualquier app sin sandbox: puede leer el
entorno del motor (`ps eww`) y con el secreto puede consultar `/status`, apagar el motor o **probar PINs hasta
bloquear el token**. Si además consigue el PIN (keylogger), puede firmar cualquier documento mientras el token
esté enchufado: el mToken no tiene botón ni pantalla de confirmación. Desenchufar el token cuando no se usa es
la mitigación real.

**Red.** Solo salen el pedido de sello de tiempo (un hash, no el documento) y, al verificar o con `--lt`, las
descargas de CRL/OCSP públicas. El PDF no sale de la Mac.

## Build y empaquetado

1. `build-backend.sh`: instala `requirements.txt` en `.venv` y congela `firma.py` con PyInstaller en modo
   **onedir** arm64 (`dist/firma-backend/`). Onefile se descomprime en cada arranque y tarda ~16 s.
2. `app/fetch-driver.sh`: baja el zip oficial del driver (copia pública del Colegio de Escribanos PBA) y
   verifica el **sha256 fijado** del zip y de la dylib extraída. El binario propietario no se versiona.
3. `app/build-app.sh`: `swift build -c release` universal (cae a solo arm64 si falla), arma el `.app`
   (Info.plist con la versión de `VERSION`, ícono, driver, `.pkg`, `trust/`, motor) y firma **ad-hoc**
   (`codesign -s -`).
4. **Sin hardened runtime** en el build ad-hoc: con hardened runtime, la *library validation* impide que el
   motor cargue una dylib firmada por otro equipo (Longmai, team 4M7RX53G2N). Con Developer ID,
   `packaging/make-dmg.sh` firma de adentro hacia afuera con hardened runtime +
   `com.apple.security.cs.disable-library-validation` (`packaging/entitlements.plist`) y notariza si hay
   `NOTARY_PROFILE`.
5. `packaging/make-dmg.sh` arma el DMG (app + link a Aplicaciones + `LEEME.txt`); `packaging/verify-dmg.sh`
   (bash) lo monta, verifica firmas, Gatekeeper, que el motor imprima `READY` y la firma de la dylib.

Pruebas: `test_firma.py` (motor contra SoftHSM2 y una TSA local, también contra el binario congelado) y el
selftest de la app (`FIRMA_SELFTEST`, ver `SelfTest.swift`), siempre con SoftHSM y nunca con el token real.

## Rarezas conocidas

- **CropBox en Vista Previa / PDFKit.** Si la página tiene un CropBox que no empieza en el origen, PDFKit no
  dibuja la apariencia del campo de firma (muestra el recuadro vacío o resaltado). La firma está y es válida;
  Acrobat y el validador oficial la muestran bien. El selftest de la app mide ~1.5 pt de desvío en esas páginas.
- **Primer arranque del motor congelado: ~17 s.** macOS escanea (Gatekeeper/XProtect) las ~70 librerías nativas de
  `_internal/` la primera vez; después arranca en ~0.3 s. Por eso la app espera `READY` hasta 90 s.
- **`--lt` pesa ~9 MB por PDF.** La CRL de AC ONTI ocupa ~9 MB y B-LT la embebe en cada firma. Por eso el
  default es B-T: el sello de tiempo alcanza para el validador oficial.
- **OCSP de ONTI responde "unauthorized"**; la revocación se resuelve por CRL y en verificación es soft-fail.
