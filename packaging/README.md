# Empaquetado (DMG)

```sh
packaging/make-dmg.sh "app/build/Firma Digital.app"     # -> packaging/dist/Firma Digital-<version>.dmg + sha256
packaging/verify-dmg.sh "packaging/dist/Firma Digital-<version>.dmg"
```

- La version sale de `CFBundleShortVersionString` del Info.plist de la app.
- El DMG lleva la app, un link a Aplicaciones y `LEEME.txt` (instrucciones para el destinatario).
  Formato ULFO (lzfse, macOS 10.11+), APFS, firmado ad-hoc. Solo `hdiutil`, sin dependencias.
  No hay fondo ni posiciones de iconos: eso requiere scriptear Finder por AppleScript (fragil en CI).
- `verify-dmg.sh` monta read-only, corre `codesign --verify --deep --strict`, `spctl -a -t exec -vv`
  (rechaza las apps ad-hoc: es esperado), arranca `Contents/Resources/backend/firma-backend --port 0`
  esperando `READY`, muestra `otool -L` / `codesign -dvv` del dylib del driver y la firma del .pkg, y desmonta.
- Prueba sin la app real: `packaging/test/make-dummy-app.sh` y pasarle `packaging/test/build/Firma Digital.app`.

## Firma Developer ID y notarizacion

Por defecto todo es ad-hoc: el destinatario tiene que habilitar la app a mano la primera vez (ver `LEEME.txt`).
Con un certificado Developer ID:

```sh
SIGN_IDENTITY="Developer ID Application: Nombre (TEAMID)" NOTARY_PROFILE=notary \
  packaging/make-dmg.sh "app/build/Firma Digital.app"
```

Firma cada Mach-O y la app con hardened runtime y `entitlements.plist`
(`com.apple.security.cs.disable-library-validation`, necesario porque el backend carga el dylib del mToken
firmado por otro team, Longmai 4M7RX53G2N). Los binarios que ya traen firma Developer ID de terceros se dejan
intactos. Despues `xcrun notarytool submit --wait` y `xcrun stapler staple` sobre el DMG.
Solo `SIGN_IDENTITY` (sin `NOTARY_PROFILE`) firma pero no notariza.

Que hace falta para tenerlo (una sola vez):

1. Cuenta del Apple Developer Program (99 USD/anio). Solo el Account Holder puede crear certificados
   Developer ID.
2. developer.apple.com -> Certificates, IDs & Profiles -> Certificates -> "+" -> "Developer ID Application".
   Generar el CSR en Acceso a Llaveros -> Asistente de certificados -> Solicitar un certificado de una
   autoridad. Descargar el .cer y abrirlo (queda en el llavero "inicio de sesion" con su clave privada).
   Comprobar: `security find-identity -v -p codesigning` debe listar "Developer ID Application: ...".
3. account.apple.com -> Inicio de sesion y seguridad -> Contrasenas para apps -> crear una.
4. `xcrun notarytool store-credentials notary --apple-id <mail> --team-id <TEAMID> --password <app-specific>`
5. Correr make-dmg.sh con `SIGN_IDENTITY` y `NOTARY_PROFILE=notary`. `verify-dmg.sh` debe mostrar
   "spctl acepta la app" y "ticket de notarizacion stapleado".
