# Seguridad

## Cómo reportar una vulnerabilidad

**No abras un issue público.** Usá el reporte privado de GitHub: pestaña **Security** del repositorio →
**Report a vulnerability**. Contá qué encontraste, cómo reproducirlo y qué versión de la app usás.

Respondemos dentro de los 7 días. Si el problema se confirma, lo arreglamos, publicamos una versión nueva y te
damos crédito en el CHANGELOG (salvo que prefieras no figurar). No hay programa de recompensas (bug bounty):
es un proyecto chico, mantenido a pulmón.

**Nunca mandes tu PIN, tu certificado ni PDFs con datos personales reales.** Si necesitás un ejemplo, usá el
token de prueba SoftHSM (`sh dev/softhsm-setup.sh`).

## Qué está en alcance

- **Manejo del PIN:** que el PIN quede guardado, logueado, en memoria más de lo necesario o que viaje a algún
  lado que no sea el token.
- **Motor local (`firma-backend`):** escucha solo en `127.0.0.1` y exige el header `X-Firma-Token` con un
  secreto que la app genera en cada arranque. Cualquier forma de que otro proceso o una página web use el
  token sin ese secreto (bypass del secreto, DNS rebinding, CSRF contra el modo web, etc.) está en alcance.
- **Correctitud de la firma:** PDFs firmados que no validan en https://validadordefirmas.gob.ar, firmas que
  cubren un contenido distinto del que se mostró, o el verificador marcando como válida una firma que no lo es
  (o al revés), comparado con el validador oficial.
- **Cadena de suministro:** el driver propietario se descarga en el build con sha256 fijado
  (`app/fetch-driver.sh`); fallas en esa verificación o en las dependencias fijadas en `requirements.txt`.

## Fuera de alcance

- Vulnerabilidades del driver propietario de Longmai/Macroseguridad o del firmware del token: reportalas al
  fabricante (podemos ayudarte a escalar).
- Bloqueo del token por 3 PIN incorrectos: es una protección del hardware, no un bug.
- Que la app no esté notarizada (por ahora se distribuye firmada ad-hoc, ver README).
- Ataques que requieren control total de tu usuario de macOS (quien controla tu sesión puede leer lo que
  escribís).

## Versiones soportadas

Solo la última versión publicada en Releases recibe correcciones de seguridad.
