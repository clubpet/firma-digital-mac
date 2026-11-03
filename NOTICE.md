# Software y datos de terceros

El código de este repositorio es MIT (ver [LICENSE](LICENSE)). La app distribuida (DMG) incluye además los
componentes de abajo, cada uno con su propia licencia. Versiones: las fijadas en `requirements.txt`.

## Motor de firma (Python, congelado con PyInstaller dentro de la app)

| Componente | Licencia | Proyecto |
|---|---|---|
| pyHanko | MIT | https://github.com/MatthiasValvekens/pyHanko |
| pyhanko-certvalidator | MIT | https://github.com/MatthiasValvekens/certvalidator |
| python-pkcs11 | MIT | https://github.com/pyauth/python-pkcs11 |
| asn1crypto | MIT | https://github.com/wbond/asn1crypto |
| oscrypto | MIT | https://github.com/wbond/oscrypto |
| cryptography | Apache-2.0 OR BSD-3-Clause (incluye OpenSSL, Apache-2.0) | https://github.com/pyca/cryptography |
| aiohttp | Apache-2.0 AND MIT | https://github.com/aio-libs/aiohttp |
| aiosignal, frozenlist, multidict, propcache, yarl | Apache-2.0 | https://github.com/aio-libs |
| aiohappyeyeballs | PSF-2.0 | https://github.com/aio-libs/aiohappyeyeballs |
| attrs | MIT | https://github.com/python-attrs/attrs |
| cffi | MIT-0 | https://github.com/python-cffi/cffi |
| pycparser | BSD-3-Clause | https://github.com/eliben/pycparser |
| idna | BSD-3-Clause | https://github.com/kjd/idna |
| certifi | MPL-2.0 | https://github.com/certifi/python-certifi |
| lxml | BSD-3-Clause (incluye libxml2/libxslt, MIT) | https://github.com/lxml/lxml |
| tzlocal | MIT | https://github.com/regebro/tzlocal |
| uritools | MIT | https://github.com/tkem/uritools |
| typing_extensions | PSF-2.0 | https://github.com/python/typing_extensions |
| Intérprete Python 3.12 | PSF-2.0 | https://www.python.org |

**PyInstaller** (https://pyinstaller.org) se usa para congelar el motor. Su licencia es GPLv2 o posterior *con
una excepción* para el bootloader: permite distribuir los ejecutables generados bajo cualquier licencia. El
bootloader que queda dentro de `firma-backend` está cubierto por esa excepción; PyInstaller en sí no se
redistribuye.

## Driver del token (propietario, no incluido en el repositorio)

`liblm_cryptoide_pkcs11.dylib` y el instalador `MSCryptoID-FIPS140-3_Mac_Spanish_V2.0.25.0912.pkg` son el
middleware oficial del mToken CryptoID FIPS 140-3, de **Longmai Technology** (firmado con Developer ID
`4M7RX53G2N` y notarizado por Apple), distribuido en Argentina por **Macroseguridad**. Es software propietario:

- **No está en este repositorio.** `app/fetch-driver.sh` lo descarga en el momento del build desde la copia
  pública del Colegio de Escribanos de la Provincia de Buenos Aires y verifica los hashes:
  - URL: https://www.colescba.org.ar/static/descarga/fima-digital/2026/MSCryptoID-FIPS140-3_Mac_Spanish_V2.0.25.0912%201.zip
  - sha256 del zip: `0ebb810531a6716be9fe3957f803c836e5e91b1e77a944f6c04c3d22f41686da`
  - sha256 de la dylib: `534209db0634f3ec217da28f42814f7115da2e5ecddf6ca8582f60f81a5dc9e4`
- El DMG lo incluye tal cual, sin modificaciones, para que quien tiene el token no tenga que instalar nada.
- Sus derechos son de sus autores. Si sos titular de esos derechos y preferís que no se redistribuya dentro del
  DMG, abrí un issue y lo sacamos (la app puede usar el driver instalado en el sistema).

## Certificados de la PKI argentina

`trust/*.pem` son los certificados públicos de las autoridades certificantes de la Infraestructura de Firma
Digital de la República Argentina (AC Raíz, AC ONTI, AC Modernización-PFDR y certificadores licenciados),
publicados por la Jefatura de Gabinete de Ministros en https://pki.jgm.gov.ar/docs/Certificados_AC_Manual.zip.
Origen y huellas en [trust/README.md](trust/README.md).

## Frameworks de Apple

La app usa SwiftUI, AppKit y PDFKit, que forman parte de macOS y no se redistribuyen.
