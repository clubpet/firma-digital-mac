# Changelog

Todos los cambios relevantes de este proyecto. Formato [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/),
versionado [SemVer](https://semver.org/lang/es/). La version vive en el archivo `VERSION`; cada release es el tag `v<VERSION>`.

## [Sin publicar]

## [1.1.0] - 2026-11-03

Primera version publica (codigo abierto).

### Agregado
- Sello de tiempo RFC 3161 por defecto: las firmas salen PAdES-B-T con la TSA del Servicio de Sellado de Tiempo
  de JGM (via `https://tsa.encodesa.com.ar/tsa`, emitida por AC ONTI). `--tsa URL|none` / `FIRMA_TSA`; si la TSA
  no responde la firma sale PAdES-B-B y lo avisa en `X-Level`.
- Verificacion del sello de tiempo: `sello_de_tiempo_confiable`, `sello_de_tiempo_fecha` y `sello_de_tiempo_emisor`.
- App: boton "Abrir PDF" visible que reemplaza el PDF abierto.
- Archivo `VERSION` como fuente unica de la version (app, backend `/health` y nombre del DMG).
- CI en GitHub Actions (test contra SoftHSM, build de la app, shellcheck) y workflow de release que arma, verifica
  y publica el DMG con su sha256.

### Cambiado
- App: los paneles se muestran como hoja (sheet).
- El driver oficial del mToken (Macroseguridad/Longmai) ya no se versiona: `app/fetch-driver.sh` lo descarga en el
  build y verifica el sha256 fijado del zip y de la dylib.
- Repositorio publico.

## 1.0.0 - 2026-11-03

Primera version (DMG de distribucion privada).

### Agregado
- App nativa macOS (SwiftUI + PDFKit) con el motor de firma embebido (backend `firma.py` congelado con PyInstaller).
- Firma PAdES-B-B con token PKCS#11 (mToken CryptoID FIPS 140-3, driver oficial incluido); PAdES-B-LT opcional
  con `--lt` / `FIRMA_LT=1`. Clave privada por CKA_ID, eleccion de certificado (`cert_id`), avisos de PIN
  (intentos bajos, ultimo intento, bloqueado).
- Selector visual de posicion y tamano de la firma; paginas rotadas y con CropBox.
- Verificador con el criterio del validador oficial y las 22 raices de la PKI argentina; cliente de
  validadordefirmas.gob.ar.
- Empaquetado DMG firmado ad-hoc (`packaging/make-dmg.sh`, `packaging/verify-dmg.sh`, LEEME y guia Developer ID).
- Prueba punta a punta contra SoftHSM2 (`test_firma.py`) y selftest de la app.

[Sin publicar]: https://github.com/clubpet/firma-digital-mac/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/clubpet/firma-digital-mac/releases/tag/v1.1.0
