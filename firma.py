#!/usr/bin/env python3
"""Firma Digital (Ley 25.506) de PDFs con token PKCS#11 — app web local minima.

Uso: FIRMA_PKCS11_LIB=/ruta/lib.dylib python3 firma.py [--lib RUTA] [--port 8780]
Raices de confianza opcionales: ./trust/*.pem (si existen, se valida cadena y se embebe CRL/OCSP = PAdES-B-LT).
"""
import argparse
import email.parser
import email.policy
import glob
import io
import json
import logging
import os
import sys
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pkcs11
from pkcs11 import Attribute, ObjectClass
from pyhanko import stamp
from pyhanko.keys import load_certs_from_pemder
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import fields, signers, validation
from pyhanko.sign.pkcs11 import PKCS11Signer
from pyhanko_certvalidator import ValidationContext
from pyhanko_certvalidator.policy_decl import DisallowWeakAlgorithmsPolicy

HERE = os.path.dirname(os.path.abspath(__file__))
TRUST_DIR = os.environ.get("FIRMA_TRUST_DIR", os.path.join(HERE, "trust"))
# Driver oficial Longmai/Macroseguridad para mToken CryptoID FIPS 140-3 (VID 4C4D PID 8B15).
# Orden: instalacion por usuario, instalacion del .pkg oficial, SoftHSM de desarrollo.
CANDIDATE_LIBS = [
    os.path.expanduser("~/Library/CryptoIDE/lib/liblm_cryptoide_pkcs11.dylib"),
    "/opt/CryptoIDE/lib/liblm_cryptoide_pkcs11.dylib",
    "/opt/homebrew/lib/softhsm/libsofthsm2.so",
]
DEFAULT_LIB = next((p for p in CANDIDATE_LIBS if os.path.exists(p)), CANDIDATE_LIBS[0])
LIB = os.environ.get("FIRMA_PKCS11_LIB", DEFAULT_LIB)
# ponytail: lock global sobre el token; los tokens USB no toleran sesiones concurrentes.
TOKEN_LOCK = threading.Lock()
log = logging.getLogger("firma")


# ---------- helpers PKCS#11 / confianza ----------
def trust_roots():
    """Certs PEM/DER en ./trust. Vacio si la carpeta no existe."""
    certs = []
    for f in sorted(glob.glob(os.path.join(TRUST_DIR, "*.pem")) + glob.glob(os.path.join(TRUST_DIR, "*.crt"))):
        certs.extend(load_certs_from_pemder([f]))
    return certs


def validation_context():
    roots = trust_roots()
    return ValidationContext(trust_roots=roots, allow_fetching=True) if roots else None


def cert_cn(cert):
    return cert.subject.native.get("common_name", "")


def lib_ok():
    return os.path.isfile(LIB)


def lib_error():
    return (f"No se encuentra la libreria PKCS#11 en '{LIB}'. Instala el driver del token y "
            "configura FIRMA_PKCS11_LIB (env) o --lib.")


def token_certs(token):
    """Certificados X.509 publicos del token (sin PIN). [(label, cert_id, Certificate)]."""
    from asn1crypto import x509
    with token.open() as s:
        objs = list(s.get_objects({Attribute.CLASS: ObjectClass.CERTIFICATE}))
        return [(o[Attribute.LABEL], o[Attribute.ID], x509.Certificate.load(o[Attribute.VALUE])) for o in objs]


def status():
    out = {"lib": LIB, "lib_found": lib_ok(), "trust_roots": len(trust_roots()), "tokens": []}
    if not out["lib_found"]:
        out["error"] = lib_error()
        return out
    try:
        for tok in tokens():
            out["tokens"].append({
                "slot": tok.slot.slot_id, "label": tok.label.strip(), "serial": tok.serial.decode(errors="replace").strip(),
                "certs": [{"label": lbl, "id": cid.hex(), "cn": cert_cn(c), "subject": c.subject.human_friendly,
                           "valid_until": c.not_valid_after.isoformat()} for lbl, cid, c in token_certs(tok)],
            })
    except pkcs11.PKCS11Error as e:
        out["error"] = f"Error PKCS#11: {type(e).__name__} {e}"
    return out


def tokens():
    """Tokens presentes e inicializados (SoftHSM expone un slot libre extra sin token usable)."""
    out = []
    for slot in pkcs11.lib(LIB).get_slots(token_present=True):
        try:
            tok = slot.get_token()
        except pkcs11.PKCS11Error:
            continue
        if tok.flags & pkcs11.TokenFlag.TOKEN_INITIALIZED:
            out.append(tok)
    return out


def first_token():
    toks = tokens()
    if not toks:
        raise RuntimeError("No hay token conectado.")
    return toks[0]


def pick_cert(token, label):
    """Devuelve (cert_label, cert_id) a usar: por label, o el unico cert, o el primero con clave privada."""
    certs = token_certs(token)
    if not certs:
        raise RuntimeError("El token no tiene certificados.")
    if label:
        if not any(l == label for l, _, _ in certs):
            raise RuntimeError(f"No hay certificado con etiqueta '{label}'.")
        return label, None
    if len(certs) == 1:
        return certs[0][0], None
    return None, certs[0][1]  # ponytail: con varios certs tomamos el primero por ID; pedir label si no sirve


# ---------- firmar / verificar ----------
def sign_pdf(pdf_bytes, pin, page=1, x=50, y=50, reason=None, location=None, cert_label=None):
    if not lib_ok():
        raise RuntimeError(lib_error())
    with TOKEN_LOCK:
        token = first_token()
        label, cert_id = pick_cert(token, cert_label)
        try:
            session = token.open(user_pin=pin)
        except pkcs11.PinIncorrect:
            raise RuntimeError("PIN incorrecto.")
        except pkcs11.PinLocked:
            raise RuntimeError("PIN bloqueado: demasiados intentos fallidos.")
        with session:
            # key_label/key_id: pyHanko busca la clave privada que coincida con el cert elegido
            signer = PKCS11Signer(session, cert_label=label, cert_id=cert_id, key_label=label, key_id=cert_id)
            vc = validation_context()
            r = PdfFileReader(io.BytesIO(pdf_bytes))
            n = len(r.embedded_signatures) + 1
            if not 1 <= page <= r.root["/Pages"]["/Count"]:
                raise RuntimeError(f"La pagina {page} no existe.")
            w = IncrementalPdfFileWriter(io.BytesIO(pdf_bytes))
            meta = signers.PdfSignatureMetadata(
                field_name=f"Firma{n}", md_algorithm="sha256", reason=reason or None, location=location or None,
                subfilter=fields.SigSeedSubFilter.PADES,
                embed_validation_info=vc is not None, validation_context=vc,
            )
            style = stamp.TextStampStyle(
                stamp_text="Firmado digitalmente por\n%(signer)s\n%(ts)s", border_width=1,
                timestamp_format="%d/%m/%Y %H:%M:%S %Z",
            )
            spec = fields.SigFieldSpec(f"Firma{n}", on_page=page - 1, box=(x, y, x + 220, y + 60))
            out = io.BytesIO()
            signers.PdfSigner(meta, signer=signer, stamp_style=style, new_field_spec=spec).sign_pdf(w, output=out)
            return out.getvalue(), ("PAdES-B-LT" if vc else "PAdES-B-B"), cert_cn(signer.signing_cert)


def verify_pdf(pdf_bytes):
    roots = trust_roots()
    # Como validadordefirmas.gob.ar: cadena evaluada a la fecha de firma declarada (certs vencidos siguen
    # valiendo), SHA-1 aceptado (GDE/BORA todavia lo usan), revocacion soft-fail (OCSP de ONTI responde
    # "unauthorized"; la CRL si anda). strict=False por PDFs hibridos de GDE/BORA.
    weak = DisallowWeakAlgorithmsPolicy(weak_hash_algos={"md2", "md5"})
    r = PdfFileReader(io.BytesIO(pdf_bytes), strict=False)
    res = []
    for sig in r.embedded_signatures:
        try:
            claimed = sig.self_reported_timestamp
            vc = ValidationContext(trust_roots=roots, allow_fetching=claimed is None, revocation_mode="soft-fail",
                                   algorithm_usage_policy=weak, **({"moment": claimed} if claimed else {}))
            st = validation.validate_pdf_signature(sig, signer_validation_context=vc)
        except Exception as e:  # firma rota/ilegible: igual la reportamos
            res.append({"campo": sig.field_name, "error": f"{type(e).__name__}: {e}"})
            continue
        subj = st.signing_cert.subject.native
        cov = str(st.coverage).rsplit(".", 1)[-1]
        res.append({
            "campo": sig.field_name,
            "firmante": subj.get("common_name", ""),
            "serial_number": subj.get("serial_number", ""),  # en AR: "CUIL 20xxxxxxxx5"
            "emisor": st.signing_cert.issuer.native.get("common_name", ""),
            "fecha_firma": st.signer_reported_dt.isoformat() if st.signer_reported_dt else None,
            "integra": bool(st.intact and st.valid),
            "sin_modificar": bool(st.intact and st.valid and st.docmdp_ok is not False and cov in ("ENTIRE_FILE", "ENTIRE_REVISION")),
            "cobertura": cov,
            "modificaciones": str(st.modification_level).rsplit(".", 1)[-1] if st.modification_level else None,
            "cadena_confiable": bool(st.trusted),
            "motivo_confianza": ("sin raices de confianza (./trust vacio)" if not roots else
                                 "OK" if st.trusted else (str(st.trust_problem_indic) if st.trust_problem_indic else "cadena no valida")),
            "sello_de_tiempo": st.timestamp_validity is not None,
            "validado_a": "fecha de firma declarada" if claimed else "ahora",
            "resumen": st.summary(),
        })
    return {"firmas": res, "raices_confianza": len(roots)}


# ---------- HTTP ----------
HTML = """<!doctype html><html lang="es"><meta charset="utf-8"><title>Firma Digital</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font:15px/1.4 system-ui,sans-serif;margin:0;background:#f3f4f1;color:#1a1a18}
main{max-width:960px;margin:2rem auto;padding:0 1rem;display:grid;gap:1.5rem;grid-template-columns:1fr 1fr}
h1{grid-column:1/-1;margin:0;font-size:1.4rem} .card{background:#fff;border:1px solid #ddd;border-radius:10px;padding:1.2rem}
h2{margin:0 0 .8rem;font-size:1.1rem} label{display:block;margin:.5rem 0 .2rem;font-size:.9rem;color:#444}
input,button{font:inherit} input[type=text],input[type=password],input[type=number]{width:100%;box-sizing:border-box;padding:.4rem;border:1px solid #bbb;border-radius:6px}
.row{display:flex;gap:.6rem} .row>div{flex:1}
button{margin-top:1rem;padding:.55rem 1rem;border:0;border-radius:6px;background:#1a1a18;color:#fff;cursor:pointer}
button:disabled{opacity:.5} pre{background:#f7f7f5;padding:.7rem;border-radius:6px;overflow:auto;font-size:.8rem;white-space:pre-wrap}
.err{color:#b00020} .ok{color:#0a7a2f} #status{grid-column:1/-1;font-size:.85rem;color:#555}
@media(max-width:700px){main{grid-template-columns:1fr}}
</style>
<main>
<h1>Firma Digital — PDF con token PKCS#11</h1>
<div id="status">Consultando token…</div>
<section class="card"><h2>Firmar</h2>
<form id="f">
<label>PDF</label><input type="file" name="pdf" accept="application/pdf" required>
<label>PIN del token</label><input type="password" name="pin" autocomplete="off" required>
<div class="row"><div><label>Página</label><input type="number" name="page" value="1" min="1"></div>
<div><label>X (pt)</label><input type="number" name="x" value="50"></div><div><label>Y (pt)</label><input type="number" name="y" value="50"></div></div>
<label>Motivo (opcional)</label><input type="text" name="reason">
<label>Lugar (opcional)</label><input type="text" name="location">
<button>Firmar y descargar</button></form><pre id="fo"></pre></section>
<section class="card"><h2>Verificar</h2>
<form id="v"><label>PDF firmado</label><input type="file" name="pdf" accept="application/pdf" required>
<button>Verificar</button></form><pre id="vo"></pre></section>
</main>
<script>
const $=s=>document.querySelector(s);
fetch('/status').then(r=>r.json()).then(s=>{
 let t=`Librería: ${s.lib} ${s.lib_found?'(encontrada)':'(NO ENCONTRADA)'} · Raíces de confianza: ${s.trust_roots} → ${s.trust_roots?'PAdES-B-LT':'PAdES-B-B (sin ./trust, no se valida cadena)'}`;
 if(s.error) t+=`\\n<span class=err>${s.error}</span>`;
 else if(!s.tokens.length) t+='\\n<span class=err>No hay token conectado.</span>';
 else for(const k of s.tokens) t+=`\\nToken "${k.label}" · certs: `+(k.certs.map(c=>`${c.label} (${c.cn})`).join(', ')||'ninguno');
 $('#status').innerHTML=t.replace(/\\n/g,'<br>');});
$('#f').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;$('#fo').textContent='Firmando…';
 const fd=new FormData(e.target);const r=await fetch('/sign',{method:'POST',body:fd});e.target.pin.value='';
 if(!r.ok){$('#fo').innerHTML='<span class=err>'+(await r.text())+'</span>';b.disabled=false;return}
 const name=decodeURIComponent(r.headers.get('X-Filename'));const a=document.createElement('a');a.href=URL.createObjectURL(await r.blob());a.download=name;a.click();
 $('#fo').innerHTML=`<span class=ok>Firmado (${r.headers.get('X-Level')}) por ${decodeURIComponent(r.headers.get('X-Signer'))} → ${name}</span>`;b.disabled=false};
$('#v').onsubmit=async e=>{e.preventDefault();$('#vo').textContent='Verificando…';
 const r=await fetch('/verify',{method:'POST',body:new FormData(e.target)});const j=await r.json();
 if(!r.ok){$('#vo').innerHTML='<span class=err>'+j.error+'</span>';return}
 if(!j.firmas.length){$('#vo').textContent='El PDF no tiene firmas.';return}
 $('#vo').innerHTML=j.firmas.map(f=>f.error?`<span class=err>${f.campo}: ${f.error}</span>`:
 `<b>${f.campo}</b> — ${f.firmante} ${f.serial_number?'('+f.serial_number+')':''}\\nFecha: ${f.fecha_firma}\\n`+
 `<span class="${f.sin_modificar?'ok':'err'}">${f.sin_modificar?'Documento íntegro, sin modificar después de la firma':f.integra?'Firma íntegra pero el documento fue modificado después ('+f.modificaciones+')':'FIRMA INVÁLIDA: el documento fue alterado'}</span>\\n`+
 `<span class="${f.cadena_confiable?'ok':'err'}">Cadena confiable: ${f.cadena_confiable?'sí':'no'} — ${f.motivo_confianza}</span>\\nSello de tiempo: ${f.sello_de_tiempo?'sí':'no'}`).join('\\n\\n')};
</script></html>"""


def parse_multipart(handler):
    """Multipart con stdlib (email). Devuelve {name: str | (filename, bytes)}."""
    ct = handler.headers.get("Content-Type", "")
    n = int(handler.headers.get("Content-Length", 0))
    if n > 200 * 1024 * 1024:
        raise RuntimeError("Archivo demasiado grande (max 200 MB).")
    body = handler.rfile.read(n)
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + ct.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    out = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name is None:
            continue
        fn = part.get_filename()
        payload = part.get_payload(decode=True)
        out[name] = (fn, payload) if fn is not None else payload.decode("utf-8", "replace")
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # solo metodo+ruta; nunca cuerpos ni PIN
        log.info("%s %s", self.command, self.path.split("?")[0])

    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=()):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self._send(200, HTML, "text/html; charset=utf-8")
        elif self.path == "/status":
            self._send(200, status())
        else:
            self._send(404, {"error": "no existe"})

    def do_POST(self):
        try:
            form = parse_multipart(self)
            if "pdf" not in form or not isinstance(form["pdf"], tuple) or not form["pdf"][1]:
                raise RuntimeError("Falta el PDF.")
            fn, pdf = form["pdf"]
            if self.path == "/sign":
                pin = form.get("pin", "")
                if not pin:
                    raise RuntimeError("Falta el PIN.")
                signed, level, cn = sign_pdf(
                    pdf, pin, page=int(form.get("page") or 1), x=int(form.get("x") or 50), y=int(form.get("y") or 50),
                    reason=form.get("reason"), location=form.get("location"), cert_label=form.get("cert_label") or None)
                del pin, form
                out = (os.path.splitext(fn or "documento")[0]) + "-firmado.pdf"
                q = urllib.parse.quote(out)
                self._send(200, signed, "application/pdf", [
                    ("Content-Disposition", f"attachment; filename*=UTF-8''{q}"),
                    ("X-Filename", q), ("X-Level", level), ("X-Signer", urllib.parse.quote(cn))])
            elif self.path == "/verify":
                self._send(200, verify_pdf(pdf))
            else:
                self._send(404, {"error": "no existe"})
        except Exception as e:
            log.debug(traceback.format_exc())
            msg = str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
            if self.path == "/sign":
                self._send(400, msg, "text/plain; charset=utf-8")
            else:
                self._send(400, {"error": msg})


def main():
    global LIB
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lib", default=LIB, help="ruta a la libreria PKCS#11 (default: env FIRMA_PKCS11_LIB o SoftHSM)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("-v", action="store_true", help="log debug")
    a = ap.parse_args()
    LIB = a.lib
    logging.basicConfig(level=logging.DEBUG if a.v else logging.INFO, format="%(asctime)s %(message)s")
    if not a.v:  # pyHanko loguea tracebacks completos por cada cadena no confiable
        for name in ("pyhanko", "pyhanko_certvalidator"):
            logging.getLogger(name).setLevel(logging.CRITICAL)
    if not lib_ok():
        log.warning(lib_error())
    log.info("Raices de confianza en %s: %d (%s)", TRUST_DIR, len(trust_roots()), "PAdES-B-LT" if trust_roots() else "PAdES-B-B")
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    log.info("Escuchando en http://%s:%d  (lib: %s)", a.host, a.port, LIB)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
