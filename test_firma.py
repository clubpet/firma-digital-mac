#!/usr/bin/env python3
"""Prueba punta a punta contra SoftHSM2: crea token (2 certs) y una TSA local de prueba, levanta el backend
(B-T, --lt, --tsa none, TSA caida), firma, verifica, altera. Sin red salvo el chequeo opcional contra la TSA real.
Uso: .venv/bin/python test_firma.py                         (firma.py)
     FIRMA_BACKEND=dist/firma-backend/firma-backend .venv/bin/python test_firma.py   (binario congelado)
"""
import io
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.environ.get("FIRMA_PKCS11_LIB", "/opt/homebrew/lib/softhsm/libsofthsm2.so")
BACKEND = [os.environ["FIRMA_BACKEND"]] if os.environ.get("FIRMA_BACKEND") else [sys.executable, os.path.join(HERE, "firma.py")]
SECRET = secrets.token_hex(16)
BASE = None


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


def req(path, fields=None, token=SECRET):
    data, ct = multipart(fields) if fields is not None else (None, None)
    h = {"X-Firma-Token": token} if token else {}
    if ct:
        h["Content-Type"] = ct
    r = urllib.request.Request(BASE + path, data=data, headers=h, method="POST" if fields is not None else "GET")
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def make_pdf(rotate=None, crop=None):
    from pyhanko.pdf_utils import generic, writer
    page = {"/Type": generic.pdf_name("/Page"),
            "/MediaBox": generic.ArrayObject([generic.NumberObject(n) for n in (0, 0, 595, 842)])}
    if rotate:
        page["/Rotate"] = generic.NumberObject(rotate)
    if crop:
        page["/CropBox"] = generic.ArrayObject([generic.NumberObject(n) for n in crop])
    w = writer.PdfFileWriter()
    w.insert_page(generic.DictionaryObject({generic.pdf_name(k): v for k, v in page.items()}))
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def widget(pdf):
    from pyhanko.pdf_utils.reader import PdfFileReader
    f = PdfFileReader(io.BytesIO(pdf)).root["/AcroForm"]["/Fields"][-1].get_object()
    return [float(v) for v in f["/Rect"]], f["/AP"]["/N"].get_object()


def add_ca_cert(trust, tsa_dir):
    """Segundo cert (id 02) emitido por una CA de prueba, con CRL en un puerto cerrado: simula estar sin red.
    Tambien un cert de TSA emitido por la misma CA (tsa_dir/tsa.pem + tsa.key)."""
    d = tempfile.mkdtemp()
    run = lambda *a: subprocess.run(a, check=True, capture_output=True, cwd=d)
    run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650", "-keyout", "ca.key", "-out", "ca.pem",
        "-subj", "/C=AR/O=Prueba/CN=CA Prueba")
    run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "k.pem", "-out", "c.csr",
        "-subj", "/C=AR/O=Prueba/CN=Ana Gomez/serialNumber=CUIL 27123456784")
    open(os.path.join(d, "ext.cnf"), "w").write(
        "keyUsage=critical,digitalSignature,nonRepudiation\ncrlDistributionPoints=URI:http://127.0.0.1:9/ca.crl\n")
    run("openssl", "x509", "-req", "-in", "c.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial", "-days", "3650",
        "-sha256", "-extfile", "ext.cnf", "-out", "c.pem")
    run("openssl", "x509", "-in", "c.pem", "-outform", "DER", "-out", "c.der")
    run("softhsm2-util", "--import", "k.pem", "--token", "FirmaDev", "--pin", "1234", "--label", "firma2", "--id", "02")
    run("pkcs11-tool", "--module", LIB, "--token-label", "FirmaDev", "--login", "--pin", "1234",
        "--write-object", "c.der", "--type", "cert", "--label", "firma2", "--id", "02")
    open(os.path.join(d, "tsa.cnf"), "w").write("extendedKeyUsage=critical,timeStamping\nkeyUsage=critical,digitalSignature\n")
    run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", os.path.join(tsa_dir, "tsa.key"), "-out", "t.csr",
        "-subj", "/C=AR/O=Prueba/CN=TSA Prueba")
    run("openssl", "x509", "-req", "-in", "t.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial", "-days", "3650",
        "-sha256", "-extfile", "tsa.cnf", "-out", os.path.join(tsa_dir, "tsa.pem"))
    shutil.copy(os.path.join(d, "ca.pem"), os.path.join(trust, "ca.pem"))
    shutil.rmtree(d)


def mock_tsa(tsa_dir):
    """TSA RFC 3161 local (DummyTimeStamper de pyHanko detras de http.server). Devuelve la URL."""
    from asn1crypto import tsp
    from pyhanko.keys import load_cert_from_pemder, load_private_key_from_pemder
    from pyhanko.sign.timestamps import DummyTimeStamper
    ts = DummyTimeStamper(load_cert_from_pemder(os.path.join(tsa_dir, "tsa.pem")),
                          load_private_key_from_pemder(os.path.join(tsa_dir, "tsa.key"), None))

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            req = tsp.TimeStampReq.load(self.rfile.read(int(self.headers["Content-Length"])))
            body = ts.request_tsa_response(req).dump()
            self.send_response(200)
            self.send_header("Content-Type", "application/timestamp-reply")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/tsa"


def start(trust, *extra):
    global BASE
    t0 = time.time()
    srv = subprocess.Popen(BACKEND + ["--port", "0", "--token", SECRET, "--lib", LIB, "--trust", trust, *extra],
                           stdout=subprocess.PIPE, text=True)
    line = srv.stdout.readline().strip()
    assert line.startswith("READY port="), line
    BASE = f"http://127.0.0.1:{int(line.split('=')[1])}"
    print(f"{line} {' '.join(extra)} en {time.time() - t0:.2f}s")
    return srv


def stop(srv):
    assert req("/shutdown")[0] == 200
    assert srv.wait(timeout=5) == 0


def has_dss(pdf):
    from pyhanko.pdf_utils.reader import PdfFileReader
    return "/DSS" in PdfFileReader(io.BytesIO(pdf)).root


def main():
    subprocess.run(["sh", os.path.join(HERE, "dev", "softhsm-setup.sh")], check=True, stdout=subprocess.DEVNULL)
    os.environ["SOFTHSM2_CONF"] = os.path.join(HERE, "dev", "softhsm2.conf")
    trust = tempfile.mkdtemp()
    shutil.copy(os.path.join(HERE, "dev", "test-cert.pem"), trust)  # cert autofirmado 01 = raiz
    tsa_dir = tempfile.mkdtemp()
    add_ca_cert(trust, tsa_dir)
    tsa = mock_tsa(tsa_dir)
    srv = start(trust, "--tsa", tsa)
    try:

        assert req("/health", token=None)[0] == 401 and req("/health", token="otro")[0] == 401
        code, body, _ = req("/health")
        assert code == 200 and json.loads(body)["ok"], body

        st = json.loads(req("/status")[1])
        print("status:", json.dumps(st, ensure_ascii=False))
        assert st["lib_found"] and st["trust_roots"] == 2 and st["lt"] is False
        certs = {c["id"]: c["cn"] for c in st["tokens"][0]["certs"]}
        assert certs == {"01": "Juan Perez", "02": "Ana Gomez"}, certs
        assert st["tokens"][0]["pin_status"] == "ok"

        pdf = make_pdf()
        code, body, h = req("/sign", {"pdf": ("contrato.pdf", pdf), "pin": "0000"})
        print("PIN malo ->", code, body.decode(), h.get("X-Pin-Attempts-Left"))
        assert code == 403 and "PIN incorrecto" in body.decode()

        times = []
        for _ in range(3):
            t = time.time()
            code, signed, h = req("/sign", {"pdf": ("contrato.pdf", pdf), "pin": "1234", "reason": "Prueba",
                                            "location": "Neuquen", "cert_id": "01", "x": "50.5", "y": "60", "w": "200", "h": "50"})
            times.append(time.time() - t)
            assert code == 200, signed.decode()
        print("firmado:", h["X-Level"], h["X-Filename"], len(signed), "bytes; tiempos", [round(x, 3) for x in times])
        assert h["X-Filename"] == "contrato-firmado.pdf" and h["X-Level"] == "PAdES-B-T" and not has_dss(signed)
        assert widget(signed)[0] == [50.5, 60, 250.5, 110]

        code, body, _ = req("/verify", {"pdf": ("x.pdf", signed)})
        v = json.loads(body)["firmas"]
        print("verify:", json.dumps(v, ensure_ascii=False, indent=1))
        assert code == 200 and len(v) == 1
        f = v[0]
        assert f["firmante"] == "Juan Perez" and f["serial_number"] == "CUIL 20123456785"
        assert f["integra"] and f["sin_modificar"] and f["cadena_confiable"], f
        assert f["sello_de_tiempo"] and f["sello_de_tiempo_confiable"] and f["sello_de_tiempo_emisor"] == "TSA Prueba", f

        # segunda firma con cert 02 elegido por cert_id
        code, signed2, h = req("/sign", {"pdf": ("c.pdf", signed), "pin": "1234", "cert_id": "02", "x": "300", "y": "60"})
        assert code == 200, signed2.decode()
        assert h["X-Level"] == "PAdES-B-T" and h["X-Signer"] == "Ana%20Gomez", h
        v = json.loads(req("/verify", {"pdf": ("x.pdf", signed2)})[1])["firmas"]
        assert [x["firmante"] for x in v] == ["Juan Perez", "Ana Gomez"] and all(x["integra"] for x in v), v
        assert req("/sign", {"pdf": ("c.pdf", pdf), "pin": "1234", "cert_id": "ff"})[0] == 404

        # pagina rotada 90: /Rect tal cual (espacio de usuario) y apariencia contrarrotada con caja transpuesta
        code, s, _ = req("/sign", {"pdf": ("r.pdf", make_pdf(rotate=90)), "pin": "1234", "x": "100", "y": "200", "w": "60", "h": "220"})
        assert code == 200, s.decode()
        rect, ap = widget(s)
        assert rect == [100, 200, 160, 420] and [float(v) for v in ap["/Matrix"]] == [0, 1, -1, 0, 0, 0], (rect, ap)
        b = [float(v) for v in ap["/BBox"]]
        assert (abs(b[2] - b[0]), abs(b[3] - b[1])) == (220, 60), b
        # pagina recortada: coordenadas absolutas (incluyen el origen del CropBox); fuera del CropBox = error
        crop = make_pdf(crop=(100, 100, 400, 500))
        code, s, _ = req("/sign", {"pdf": ("c.pdf", crop), "pin": "1234", "x": "120", "y": "120"})
        assert code == 200 and widget(s)[0] == [120, 120, 340, 180], s[:200]
        code, body, _ = req("/sign", {"pdf": ("c.pdf", crop), "pin": "1234", "x": "20", "y": "20"})
        print("fuera del CropBox ->", code, body.decode())
        assert code == 400 and "fuera de la pagina" in body.decode()

        # alteracion: un byte en el medio del contenido (fuera del campo de firma)
        tampered = bytearray(signed)
        i = tampered.find(b"/MediaBox")
        tampered[i + 1] ^= 0x01
        f = json.loads(req("/verify", {"pdf": ("x.pdf", bytes(tampered))})[1])["firmas"][0]
        print("alterado:", f.get("resumen") or f.get("error"))
        assert not f.get("sin_modificar", False), f

        assert json.loads(req("/verify", {"pdf": ("x.pdf", pdf)})[1])["firmas"] == []

        stop(srv)

        # --lt: cert 01 (autofirmado = raiz, sin CRL que bajar) -> B-LT; cert 02 con CRL caida -> B-B y lo dice
        srv = start(trust, "--lt", "--tsa", tsa)
        assert json.loads(req("/status")[1])["lt"] is True
        code, s, h = req("/sign", {"pdf": ("c.pdf", pdf), "pin": "1234", "cert_id": "01"})
        assert code == 200 and h["X-Level"] == "PAdES-B-LT" and has_dss(s), h
        code, s, h = req("/sign", {"pdf": ("c.pdf", pdf), "pin": "1234", "cert_id": "02"})
        print("--lt cert 02:", h["X-Level"])
        assert code == 200 and h["X-Level"].startswith("PAdES-B-B") and "sin CRL" in h["X-Level"], h
        stop(srv)

        # --tsa none -> B-B; TSA caida -> firma igual sin sello y lo dice
        for args, want in ((["--tsa", "none"], "PAdES-B-B"), (["--tsa", "http://127.0.0.1:9/tsa"], "PAdES-B-B (sin sello de tiempo:")):
            srv = start(trust, *args)
            code, s, h = req("/sign", {"pdf": ("c.pdf", pdf), "pin": "1234"})
            print(args[1], "->", h.get("X-Level"))
            assert code == 200 and h["X-Level"].startswith(want), h
            assert not json.loads(req("/verify", {"pdf": ("x.pdf", s)})[1])["firmas"][0]["sello_de_tiempo"]
            stop(srv)

        # TSA real por defecto (JGM via Encode); se saltea si no hay red
        srv = start(trust)
        code, s, h = req("/sign", {"pdf": ("c.pdf", pdf), "pin": "1234"})
        if h.get("X-Level") == "PAdES-B-T":
            f = json.loads(req("/verify", {"pdf": ("x.pdf", s)})[1])["firmas"][0]
            print("TSA real:", f["sello_de_tiempo_emisor"], f["sello_de_tiempo_fecha"])
            assert f["sello_de_tiempo"] and f["sello_de_tiempo_emisor"] == "Servicio de Sellado de Tiempo de JGM", f
        else:
            print("TSA real no disponible, salteado:", h.get("X-Level"))
        stop(srv)
        print("OK: B-T + cert_id + --lt + --tsa none + TSA caida + rotada/recortada + verificacion + alteracion + shutdown")
    finally:
        if srv.poll() is None:
            srv.terminate()
            srv.wait()
        shutil.rmtree(trust, ignore_errors=True)
        shutil.rmtree(tsa_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
