#!/usr/bin/env python3
"""Prueba punta a punta contra SoftHSM2: crea token, levanta el server, firma, verifica, altera un byte.
Uso: .venv/bin/python test_firma.py
"""
import io
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
LIB = os.environ.get("FIRMA_PKCS11_LIB", "/opt/homebrew/lib/softhsm/libsofthsm2.so")


def multipart(fields):
    b = uuid.uuid4().hex
    body = io.BytesIO()
    for k, v in fields.items():
        body.write(f"--{b}\r\n".encode())
        if isinstance(v, tuple):
            fn, data = v
            body.write(f'Content-Disposition: form-data; name="{k}"; filename="{fn}"\r\nContent-Type: application/pdf\r\n\r\n'.encode())
            body.write(data)
        else:
            body.write(f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}'.encode())
        body.write(b"\r\n")
    body.write(f"--{b}--\r\n".encode())
    return body.getvalue(), f"multipart/form-data; boundary={b}"


def post(path, fields):
    data, ct = multipart(fields)
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": ct}, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def blank_pdf():
    from pyhanko.pdf_utils import generic, writer
    w = writer.PdfFileWriter()
    w.insert_page(generic.DictionaryObject({
        generic.pdf_name("/Type"): generic.pdf_name("/Page"),
        generic.pdf_name("/MediaBox"): generic.ArrayObject([generic.NumberObject(n) for n in (0, 0, 595, 842)]),
    }))
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def main():
    subprocess.run(["sh", os.path.join(HERE, "dev", "softhsm-setup.sh")], check=True, stdout=subprocess.DEVNULL)
    env = dict(os.environ, SOFTHSM2_CONF=os.path.join(HERE, "dev", "softhsm2.conf"),
               FIRMA_PKCS11_LIB=LIB, FIRMA_TRUST_DIR=os.path.join(HERE, "dev"))  # dev/test-cert.pem = raiz
    srv = subprocess.Popen([PY, os.path.join(HERE, "firma.py"), "--port", str(PORT)], env=env)
    try:
        for _ in range(50):
            try:
                st = json.load(urllib.request.urlopen(BASE + "/status"))
                break
            except Exception:
                time.sleep(0.2)
        else:
            raise SystemExit("server no arranco")
        print("status:", json.dumps(st, ensure_ascii=False))
        assert st["lib_found"] and st["tokens"] and st["tokens"][0]["certs"][0]["cn"] == "Juan Perez"
        assert st["trust_roots"] == 1

        pdf = blank_pdf()
        code, body, h = post("/sign", {"pdf": ("contrato.pdf", pdf), "pin": "0000"})
        print("PIN malo ->", code, body.decode())
        assert code == 400 and "PIN incorrecto" in body.decode()

        code, signed, h = post("/sign", {"pdf": ("contrato.pdf", pdf), "pin": "1234", "reason": "Prueba", "location": "Neuquen"})
        assert code == 200, signed.decode()
        print("firmado:", h["X-Level"], h["X-Filename"], len(signed), "bytes")
        assert h["X-Filename"] == "contrato-firmado.pdf" and h["X-Level"] == "PAdES-B-LT"

        code, body, _ = post("/verify", {"pdf": ("x.pdf", signed)})
        v = json.loads(body)["firmas"]
        print("verify:", json.dumps(v, ensure_ascii=False, indent=1))
        assert code == 200 and len(v) == 1
        f = v[0]
        assert f["firmante"] == "Juan Perez" and f["serial_number"] == "CUIL 20123456785"
        assert f["integra"] and f["sin_modificar"] and f["cadena_confiable"], f

        # alteracion: un byte en el medio del contenido (fuera del campo de firma)
        tampered = bytearray(signed)
        i = tampered.find(b"/MediaBox")
        tampered[i + 1] ^= 0x01
        code, body, _ = post("/verify", {"pdf": ("x.pdf", bytes(tampered))})
        f = json.loads(body)["firmas"][0]
        print("alterado:", f.get("resumen") or f.get("error"))
        assert not f.get("sin_modificar", False), f

        # PDF sin firmas
        code, body, _ = post("/verify", {"pdf": ("x.pdf", pdf)})
        assert json.loads(body)["firmas"] == []
        print("OK: firma + verificacion + deteccion de alteracion")
    finally:
        srv.terminate()
        srv.wait()


if __name__ == "__main__":
    main()
