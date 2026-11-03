## Qué cambia

<!-- Qué hace este PR y por qué. Si cierra un issue: "Cierra #123". -->

## Cómo lo probé

- [ ] `.venv/bin/python test_firma.py` pasa
- [ ] Si toca la app: `sh app/build-app.sh` y probé a mano o con el selftest (SoftHSM)
- [ ] Si toca firma o verificación: el PDF firmado da "válido" en https://validadordefirmas.gob.ar
- [ ] Probado con token real (modelo: ...) / solo con SoftHSM

## Checklist

- [ ] Sin datos personales (PIN, CUIL, serie del token, PDFs reales) en código, tests, capturas ni logs
- [ ] `CHANGELOG.md` actualizado en "Sin publicar" si el cambio lo ve el usuario
- [ ] Documentación actualizada (README / docs) si cambia el uso o la API
