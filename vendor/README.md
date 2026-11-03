# Driver macOS oficial del mToken CryptoID FIPS 140-3

- `MSCryptoID-FIPS140-3_Mac_Spanish_V2.0.25.0912.zip`: paquete oficial de Macroseguridad/Longmai (Developer ID Installer 4M7RX53G2N, notarizado). sha256 `0ebb810531a6716be9fe3957f803c836e5e91b1e77a944f6c04c3d22f41686da`. Origen: https://www.colescba.org.ar/static/descarga/fima-digital/2026/MSCryptoID-FIPS140-3_Mac_Spanish_V2.0.25.0912%201.zip
- `liblm_cryptoide_pkcs11.dylib`: la libreria PKCS#11 que instala ese pkg en `/opt/CryptoIDE/lib/` (universal x86_64+arm64, firmada Developer ID Application Longmai). sha256 `534209db0634f3ec217da28f42814f7115da2e5ecddf6ca8582f60f81a5dc9e4`.

`app/build-app.sh` toma ambos de aca.
