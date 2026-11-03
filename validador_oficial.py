#!/usr/bin/env python3
"""Sube un PDF al validador oficial (validadordefirmas.gob.ar) y devuelve el JSON de resultado.
Misma API que usa la SPA: POST multipart 'pdf' -> /api/PdfSignature/verificacion/async -> GET /api/PdfSignature/verificacion/{id}."""

import json
import sys
import time
import urllib.request
import uuid

BASE = "https://validadordefirmas.gob.ar"
path = sys.argv[1]
b = uuid.uuid4().hex
head = (
    f'--{b}\r\nContent-Disposition: form-data; name="pdf"; filename="doc.pdf"\r\nContent-Type: application/pdf\r\n\r\n'
)
with open(path, "rb") as f:
    body = head.encode() + f.read() + f"\r\n--{b}--\r\n".encode()
req = urllib.request.Request(
    f"{BASE}/api/PdfSignature/verificacion/async",
    data=body,
    headers={"Content-Type": f"multipart/form-data; boundary={b}", "User-Agent": "Mozilla/5.0"},
)
r = json.loads(urllib.request.urlopen(req, timeout=60).read())
print("POST ->", json.dumps(r, ensure_ascii=False)[:400], file=sys.stderr)
rid = r["idRequest"]
for _ in range(60):
    time.sleep(2)
    g = urllib.request.urlopen(
        urllib.request.Request(f"{BASE}/api/PdfSignature/verificacion/{rid}", headers={"User-Agent": "Mozilla/5.0"}),
        timeout=60,
    )
    d = json.loads(g.read())
    if str(d.get("state", "")).startswith("FINISH"):  # el backend devuelve literalmente "FINISHIED"
        print(json.dumps(d, indent=1, ensure_ascii=False))
        sys.exit(0)
    print("poll ->", json.dumps(d, ensure_ascii=False)[:200], file=sys.stderr)
print(json.dumps(d, indent=1, ensure_ascii=False))
