# Cómo contribuir

¡Gracias por darte una vuelta! Issues y PRs son bienvenidos, en castellano o en inglés.

## Antes de empezar

- Para algo más grande que un arreglo chico, abrí primero un issue y charlemos el enfoque.
- Nunca pegues tu PIN, tu CUIL, el número de serie de tu token ni PDFs reales en issues, PRs, logs o tests.
  Para todo lo de prueba existe el token SoftHSM con "Juan Perez, CUIL 20123456785".
- Al participar aceptás el [Código de Conducta](CODE_OF_CONDUCT.md). Problemas de seguridad: [SECURITY.md](SECURITY.md).

## Entorno de desarrollo

Requisitos: Mac con Apple Silicon, macOS 14 o posterior, Xcode 26 o posterior (Swift 6), Python 3.12 y
Homebrew.

```sh
brew install softhsm opensc
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
sh dev/softhsm-setup.sh          # token de prueba "FirmaDev", PIN 1234
```

No hace falta el token real para desarrollar: todo corre contra SoftHSM.

## Tests

```sh
.venv/bin/python test_firma.py                                                   # backend (firma.py)
sh build-backend.sh                                                              # congela el motor -> dist/firma-backend/
FIRMA_BACKEND=dist/firma-backend/firma-backend .venv/bin/python test_firma.py    # mismo test contra el binario
sh app/build-app.sh                                                              # app -> app/build/Firma Digital.app
```

Selftest de la app (abre una ventana y simula el mouse sobre el PDF, sin mover el mouse del sistema; solo con
SoftHSM, nunca con el token real):

```sh
SOFTHSM2_CONF=$PWD/dev/softhsm2.conf FIRMA_PKCS11_LIB=/opt/homebrew/lib/softhsm/libsofthsm2.so \
FIRMA_SELFTEST=/ruta/a/prueba.pdf FIRMA_SELFTEST_PIN=1234 FIRMA_SELFTEST_OUT=/tmp/selftest \
  "app/build/Firma Digital.app/Contents/MacOS/FirmaDigital"
```

Imprime `SELFTEST OK` o `SELFTEST FALLO (n)` y deja capturas y PDFs firmados en `FIRMA_SELFTEST_OUT`. El
chequeo de posición del sello asume páginas en blanco. Para iterar sobre la app sin recompilar el motor:
`FIRMA_BACKEND=$PWD/firma.py` (usa el `.venv` de al lado).

Si tocás la firma o la verificación, probá el PDF resultante en https://validadordefirmas.gob.ar y contá el
resultado en el PR.

## Estilo

- Python: `ruff check` y `ruff format` (configuración en `pyproject.toml`).
- Shell: POSIX `sh`, tiene que pasar `shellcheck -S warning`.
- Swift: seguí el estilo de los archivos existentes.
- Textos de la interfaz en castellano rioplatense, sin emojis.
- Cambios chicos y enfocados; sin dependencias nuevas si se puede evitar.

## Commits y PRs

- Mensajes de commit cortos, en castellano o inglés, que digan qué cambia y por qué
  (ej. `Verificacion: aceptar SHA-1 en firmas anteriores a 2020`).
- Un PR por tema. Completá la plantilla: qué cambia, cómo lo probaste, si el validador oficial está de
  acuerdo.
- El CI (`ci.yml`) corre los tests contra SoftHSM, arma la app y pasa shellcheck: tiene que estar verde.
- Si el cambio lo ve el usuario, agregalo en `CHANGELOG.md` bajo "Sin publicar".
- Releases: se publican al pushear un tag `v<VERSION>` (`release.yml`).

## Licencia

Al contribuir aceptás que tu aporte se publique bajo la [licencia MIT](LICENSE) del proyecto.
