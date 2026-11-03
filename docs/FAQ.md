# Preguntas frecuentes

### ¿Tengo que instalar el driver del token?

No. El DMG ya trae el driver oficial (PKCS#11 de Longmai) dentro de la app y lo usa directamente. El token
es un dispositivo USB CCID estándar: macOS lo reconoce sin drivers USB. Solo necesitás instalar el driver
en el sistema si además querés usar el token desde Adobe Acrobat o Firefox; la app lo ofrece ("Instalar en
mi usuario" o "Instalar paquete oficial…").

### ¿Cuál es el PIN?

El que te dio la Autoridad de Registro cuando cargó el certificado. Si nunca lo cambiaron, el PIN de fábrica
del mToken CryptoID es `12345678`. Cambialo apenas puedas (con la app oficial del fabricante).

### Me equivoqué de PIN tres veces y el token se bloqueó

Es una protección del hardware: ningún software lo puede destrabar sin el PUK. La app te avisa cuando quedan
pocos intentos. Si no tenés el PUK, tenés que contactar a quien te vendió el token (en general lo reformatean
y la Autoridad de Registro vuelve a emitir el certificado).

### ¿La firma tiene validez legal?

La validez la da tu certificado, no la app: si fue emitido por un certificador licenciado (por ejemplo AC
ONTI), la firma es una firma digital en los términos de la Ley 25.506. La app genera firmas PAdES estándar
(las mismas que produce Adobe) y la prueba de fuego es el validador oficial:
https://validadordefirmas.gob.ar. Un PDF firmado con la app da "Documento Válido" con sello de tiempo.

### Adobe Acrobat dice "validez desconocida"

La raíz de la PKI argentina no está en la lista de confianza de Adobe (AATL). La firma está bien; Adobe no
conoce la raíz. Podés importar los certificados de `trust/` en Acrobat como identidades de confianza, o
verificar con la app o con el validador oficial.

### Vista Previa (Preview) muestra el recuadro de la firma vacío

Pasa con algunos PDFs cuyas páginas tienen un recorte (CropBox) que no empieza en el origen: es un
comportamiento de PDFKit, el motor de PDF de macOS. La firma está y es válida; Acrobat y el validador
oficial la muestran bien.

### ¿Por qué el validador dice "La hora de la firma procede del reloj del equipo"?

Porque esa firma salió sin sello de tiempo: la app no pudo contactar a la TSA (por ejemplo, sin internet) y
firmó igual. La firma vale, pero sin una hora certificada por un tercero. Con conexión, cada firma lleva el
sello de tiempo de la TSA de la Jefatura de Gabinete (PAdES-B-T).

### ¿Funciona con otros tokens?

Está probada con el mToken CryptoID FIPS 140-3. Cualquier token con un driver PKCS#11 para macOS debería
funcionar con el modo web (`--lib /ruta/al/driver.dylib`) o lanzando la app desde Terminal con
`FIRMA_PKCS11_LIB=/ruta/al/driver.dylib`. Si lo probás con otro modelo, contanos en un issue.

### ¿Mi PDF sale de la computadora?

No. El PDF se firma y verifica en tu Mac. Lo único que sale a internet es el pedido de sello de tiempo (un
hash de la firma, no el documento) y, al verificar, la descarga de listas de revocación públicas. El botón
"Validador oficial" solo abre la página del Gobierno: sos vos quien decide subir el PDF ahí.

### ¿Hay versión para Intel o Windows?

No. El motor está compilado solo para Apple Silicon (arm64) y la app es nativa de macOS 14 o posterior. En
Windows existen el driver y las guías oficiales del fabricante.

### ¿Por qué macOS dice que no puede abrir la app?

Porque todavía no está firmada con un certificado Developer ID ni notarizada por Apple. Los pasos para
abrirla la primera vez están en el [README](../README.md#instalación-para-usuarios) y en el `LEEME.txt` del DMG.

### ¿Qué es PAdES-B-T, B-B y B-LT?

Niveles de la norma europea de firmas en PDF (ETSI EN 319 142). B-B: firma básica. B-T: firma más sello de
tiempo (lo que hace la app por defecto). B-LT: además embebe las listas de revocación para validación a largo
plazo; se activa con `--lt` en el modo web, pero la lista de AC ONTI pesa ~9 MB y cada PDF crece eso.
