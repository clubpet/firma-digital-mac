#!/usr/bin/env python3
"""Firma Digital (Ley 25.506) de PDFs con token PKCS#11: motor local HTTP de firma y verificacion.

Uso: python3 firma.py [--port N] [--host 127.0.0.1] [--lib RUTA] [--trust DIR] [--token SECRETO] [--lt] [--tsa URL|none]
Firma PAdES-B-T (sello de la TSA); --tsa none = PAdES-B-B; con --lt (o FIRMA_LT=1) y ./trust embebe CRL/OCSP =
PAdES-B-LT (la CRL de ONTI suma ~9 MB al PDF).
Modo web: sin --token, GET / sirve una pagina minima (pdf.js por CDN) para firmar y verificar desde el navegador.
Con --token (asi la lanza la app) esa pagina no sirve: el navegador no manda X-Firma-Token y todo da 401.
"""

import argparse
import asyncio
import dataclasses
import email.parser
import email.policy
import glob
import hmac
import io
import json
import logging
import os
import sys
import threading
import traceback
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pkcs11
from pkcs11 import Attribute, ObjectClass, TokenFlag
from pyhanko import stamp
from pyhanko.keys import load_certs_from_pemder
from pyhanko.pdf_utils import generic, layout, text
from pyhanko.pdf_utils.font.basic import SimpleFontEngineFactory
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.misc import PdfError
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import fields, signers, validation
from pyhanko.sign.pkcs11 import PKCS11Signer
from pyhanko.sign.timestamps import HTTPTimeStamper
from pyhanko_certvalidator import CertificateValidator, ValidationContext
from pyhanko_certvalidator.policy_decl import DisallowWeakAlgorithmsPolicy

HERE = os.path.dirname(os.path.abspath(__file__))
try:  # VERSION en la raiz del repo; PyInstaller lo empaqueta en _MEIPASS (backend.spec)
    with open(os.path.join(getattr(sys, "_MEIPASS", HERE), "VERSION")) as _f:
        VERSION = _f.read().strip()
except OSError:
    VERSION = "dev"
if getattr(sys, "frozen", False):  # PyInstaller: ./trust junto al ejecutable, si no el empaquetado
    _side = os.path.join(os.path.dirname(sys.executable), "trust")
    DEFAULT_TRUST = _side if os.path.isdir(_side) else os.path.join(sys._MEIPASS, "trust")
else:
    DEFAULT_TRUST = os.path.join(HERE, "trust")
TRUST_DIR = os.environ.get("FIRMA_TRUST_DIR", DEFAULT_TRUST)
LT = os.environ.get("FIRMA_LT") == "1"  # B-LT opcional: embeber la CRL de ONTI suma ~9 MB a cada PDF
# Sello de tiempo RFC 3161. Default: TSA publica en el host de Encode; los tokens los firma
# "Servicio de Sellado de Tiempo de JGM", emitido por AC ONTI (ya esta en ./trust). "none" = sin sello.
DEFAULT_TSA = "https://tsa.encodesa.com.ar/tsa"
TSA = os.environ.get("FIRMA_TSA", DEFAULT_TSA)
TSA = None if TSA.lower() in ("", "none") else TSA
TSA_TIMEOUT = 8
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
VC_LOCK = threading.Lock()  # el ValidationContext compartido no es thread-safe
_VC = None
log = logging.getLogger("firma")


class FirmaError(RuntimeError):
    """Error con mensaje para el usuario, codigo HTTP y headers extra."""

    def __init__(self, msg, code=400, headers=()):
        super().__init__(msg)
        self.code, self.headers = code, list(headers)


# ---------- helpers PKCS#11 / confianza ----------
def trust_roots(sub=""):
    """Certs PEM/DER en ./trust (o ./trust/<sub>). Vacio si la carpeta no existe."""
    certs = []
    d = os.path.join(TRUST_DIR, sub)
    for f in sorted(glob.glob(os.path.join(d, "*.pem")) + glob.glob(os.path.join(d, "*.crt"))):
        certs.extend(load_certs_from_pemder([f]))
    return certs


def signing_context():
    """ValidationContext compartido entre firmas: cachea las CRL/OCSP bajadas (la CRL de ONTI pesa ~9 MB).
    Se rehace si alguna CRL cacheada ya vencio (nextUpdate). Llamar con VC_LOCK."""
    global _VC
    now = datetime.now(timezone.utc)
    if _VC is None or any((c["tbs_cert_list"]["next_update"].native or now) < now for c in _VC.crls):
        roots = trust_roots()
        _VC = ValidationContext(trust_roots=roots, allow_fetching=True) if roots else None
    return _VC


def revinfo_ready(vc, cert):
    """Valida la cadena del firmante con vc (baja y cachea CRL/OCSP). True si hay con que hacer B-LT:
    cadena confiable y, si el cert publica CRL/OCSP, al menos una respuesta bajada. Llamar con VC_LOCK."""

    async def run():
        try:
            await CertificateValidator(cert, validation_context=vc).async_validate_usage(set())
        finally:
            await vc.aclose()

    try:
        asyncio.run(run())
    except Exception as e:
        log.warning("Sin B-LT para %s: %s", cert_cn(cert), e)
        return False
    return not (cert.crl_distribution_points or cert.ocsp_urls) or bool(vc.crls or vc.ocsps)


def prewarm():
    """Al arrancar: baja CRL/OCSP de los certs del token para que la primera firma no espere."""
    try:
        with TOKEN_LOCK:
            certs = [c for t in tokens() for _, _, c in token_certs(t)] if lib_ok() else []
        with VC_LOCK:
            vc = signing_context()
            for c in certs if vc else []:
                revinfo_ready(vc, c)
        log.info("Pre-carga de revocacion lista (%d certs)", len(certs))
    except Exception as e:
        log.warning("Pre-carga de revocacion fallo: %s", e)


_TS = None


def timestamper():
    """HTTPTimeStamper reusado: cachea la respuesta de prueba que pyHanko pide para reservar espacio,
    asi cada firma hace un solo pedido a la TSA. Llamar con TOKEN_LOCK."""
    global _TS
    if TSA and (_TS is None or _TS.url != TSA):
        _TS = HTTPTimeStamper(TSA, timeout=TSA_TIMEOUT)
    return _TS if TSA else None


def pin_state(token):
    """Estado del contador de PIN segun los flags del token (CKF_USER_PIN_*)."""
    f = token.slot.get_token().flags  # releer: los flags cambian tras cada intento
    if f & TokenFlag.USER_PIN_LOCKED:
        return "bloqueado"
    if f & TokenFlag.USER_PIN_FINAL_TRY:
        return "ultimo_intento"
    if f & TokenFlag.USER_PIN_COUNT_LOW:
        return "bajo"
    return "ok"


PIN_HEADER = {"bloqueado": "0", "ultimo_intento": "1", "bajo": "bajo"}


def cert_cn(cert):
    return cert.subject.native.get("common_name", "")


def lib_ok():
    return os.path.isfile(LIB)


def lib_error():
    return (
        f"No se encuentra la libreria PKCS#11 en '{LIB}'. Instala el driver del token y "
        "configura FIRMA_PKCS11_LIB (env) o --lib."
    )


def token_certs(token):
    """Certificados X.509 publicos del token (sin PIN). [(label, cert_id, Certificate)]."""
    from asn1crypto import x509

    with token.open() as s:
        objs = list(s.get_objects({Attribute.CLASS: ObjectClass.CERTIFICATE}))
        return [(o[Attribute.LABEL], o[Attribute.ID], x509.Certificate.load(o[Attribute.VALUE])) for o in objs]


def status():
    out = {"lib": LIB, "lib_found": lib_ok(), "trust_roots": len(trust_roots()), "lt": LT, "tsa": TSA, "tokens": []}
    if not out["lib_found"]:
        out["error"] = lib_error()
        return out
    try:
        with TOKEN_LOCK:
            for tok in tokens():
                out["tokens"].append(
                    {
                        "slot": tok.slot.slot_id,
                        "label": tok.label.strip(),
                        "serial": tok.serial.decode(errors="replace").strip(),
                        "pin_status": pin_state(tok),
                        "certs": [
                            {
                                "label": lbl,
                                "id": cid.hex(),
                                "cn": cert_cn(c),
                                "subject": c.subject.human_friendly,
                                "valid_until": c.not_valid_after.isoformat(),
                            }
                            for lbl, cid, c in token_certs(tok)
                        ],
                    }
                )
    except Exception as e:  # lib que no carga (arquitectura, dependencias) o error del token
        out["error"] = out["pkcs11_error"] = f"Error PKCS#11: {type(e).__name__} {e}"
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
        raise FirmaError("Token no conectado.", 409)
    return toks[0]


def pick_cert(token, cert_id=None):
    """(cert_id, Certificate) a usar: el de cert_id (hex) o el primero del token."""
    certs = token_certs(token)
    if not certs:
        raise FirmaError("El token no tiene certificados.", 409)
    if not cert_id:
        return certs[0][1], certs[0][2]  # ponytail: con varios certs sin cert_id, el primero
    try:
        want = bytes.fromhex(cert_id)
    except ValueError:
        raise FirmaError("cert_id invalido: tiene que ser hexadecimal.")
    for _, cid, c in certs:
        if cid == want:
            return cid, c
    raise FirmaError(f"No hay certificado con id {cert_id} en el token.", 404)


@dataclasses.dataclass(frozen=True)
class RotatedTextStampStyle(stamp.TextStampStyle):
    """Sello que contrarrota su apariencia segun /Rotate de la pagina para leerse derecho en pantalla."""

    rotate: int = 0

    def create_stamp(self, writer, box, text_params):
        if self.rotate in (90, 270):  # en pantalla el recuadro se ve transpuesto
            box = layout.BoxConstraints(width=box.height, height=box.width)
        st = super().create_stamp(writer, box, text_params)
        if self.rotate:
            mat = {90: (0, 1, -1, 0), 180: (-1, 0, 0, -1), 270: (0, -1, 1, 0)}[self.rotate]
            orig = st.as_form_xobject

            def as_form_xobject():
                xo = orig()  # el visor ajusta BBox*Matrix al /Rect (ISO 32000 12.5.5)
                xo["/Matrix"] = generic.ArrayObject([generic.NumberObject(v) for v in (*mat, 0, 0)])
                return xo

            st.as_form_xobject = as_form_xobject
        return st


def page_geometry(reader, page):
    """(/Rotate efectivo, CropBox) de la pagina (0-based); ambos heredables por el arbol /Pages."""
    rot = crop = media = None
    p = reader.find_page_for_modification(page)[0].get_object()
    while p is not None:
        rot = p.get("/Rotate") if rot is None else rot
        crop = p.get("/CropBox") if crop is None else crop
        media = p.get("/MediaBox") if media is None else media
        p = p.get("/Parent")
        p = p.get_object() if p is not None else None
    box = [float(v) for v in (crop or media)]
    return int(rot or 0) % 360, (min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3]))


def dss_has_revinfo(pdf):
    dss = PdfFileReader(io.BytesIO(pdf)).root.get("/DSS")
    dss = dss.get_object() if dss is not None else None
    return dss is not None and ("/CRLs" in dss or "/OCSPs" in dss)


# ---------- firmar / verificar ----------
def sign_pdf(pdf_bytes, pin, page=1, x=50, y=50, width=220, height=60, reason=None, location=None, cert_id=None):
    """Firma PAdES visible. Caja (x, y, w, h) en puntos, espacio de usuario de la pagina (sin rotar, origen
    abajo-izquierda del espacio de la pagina, igual que PDFKit/PDFAnnotation.bounds)."""
    if not lib_ok():
        raise FirmaError(lib_error(), 409)
    r = PdfFileReader(io.BytesIO(pdf_bytes))
    if r.encrypted:
        raise FirmaError("El PDF esta protegido con contrasena: quitale la proteccion antes de firmarlo.")
    n = len(r.embedded_signatures) + 1
    if not 1 <= page <= r.root["/Pages"]["/Count"]:
        raise FirmaError(f"La pagina {page} no existe.")
    rot, (cx1, cy1, cx2, cy2) = page_geometry(r, page - 1)
    if width <= 0 or height <= 0 or x < cx1 - 1 or y < cy1 - 1 or x + width > cx2 + 1 or y + height > cy2 + 1:
        raise FirmaError(f"El recuadro de la firma cae fuera de la pagina visible ({cx1:g},{cy1:g})-({cx2:g},{cy2:g}).")
    with TOKEN_LOCK:
        try:
            token = first_token()
            cid, cert = pick_cert(token, cert_id)
            with VC_LOCK:
                vc = signing_context() if LT else None
                lt = vc is not None and revinfo_ready(vc, cert)  # sin red o cadena no confiable: B-B
                try:
                    session = token.open(user_pin=pin)
                except (pkcs11.PinIncorrect, pkcs11.PinLocked) as e:
                    st = "bloqueado" if isinstance(e, pkcs11.PinLocked) else pin_state(token)
                    msg = {
                        "bloqueado": "PIN bloqueado: demasiados intentos fallidos. Desbloquealo con el PUK.",
                        "ultimo_intento": "PIN incorrecto. Queda 1 intento: si vuelve a fallar el token se bloquea.",
                        "bajo": "PIN incorrecto. Ya hubo intentos fallidos: con 3 seguidos el token se bloquea.",
                    }.get(st, "PIN incorrecto.")
                    hdr = [("X-Pin-Attempts-Left", PIN_HEADER[st])] if st in PIN_HEADER else []
                    raise FirmaError(msg, 423 if st == "bloqueado" else 403, hdr)
                except pkcs11.PinLenRange:
                    raise FirmaError("PIN con largo invalido.", 403)
                with session:
                    # en el mToken el label de la clave difiere del cert; coinciden por CKA_ID
                    signer = PKCS11Signer(session, cert_id=cid, key_id=cid)
                    meta = signers.PdfSignatureMetadata(
                        field_name=f"Firma{n}",
                        md_algorithm="sha256",
                        reason=reason or None,
                        location=location or None,
                        subfilter=fields.SigSeedSubFilter.PADES,
                        embed_validation_info=lt,
                        validation_context=vc if lt else None,
                    )
                    style = RotatedTextStampStyle(
                        stamp_text="Firmado digitalmente por\n%(signer)s\n%(ts)s",
                        border_width=1,
                        timestamp_format="%d/%m/%Y %H:%M:%S %Z",
                        rotate=rot,
                        text_box_style=text.TextBoxStyle(
                            font=SimpleFontEngineFactory("Helvetica", avg_width=0.5), font_size=9
                        ),
                    )
                    spec = fields.SigFieldSpec(f"Firma{n}", on_page=page - 1, box=(x, y, x + width, y + height))

                    def do_sign(ts):
                        out = io.BytesIO()
                        signers.PdfSigner(
                            meta, signer=signer, stamp_style=style, new_field_spec=spec, timestamper=ts
                        ).sign_pdf(IncrementalPdfFileWriter(io.BytesIO(pdf_bytes)), output=out)
                        return out.getvalue()

                    ts_err = None
                    try:
                        signed = do_sign(timestamper())
                    except pkcs11.PKCS11Error:
                        raise
                    except Exception as e:  # TSA caida o lenta: se firma igual, sin sello, y se avisa
                        if not TSA:
                            raise
                        ts_err = f"{type(e).__name__}: {e}"[:150].encode("ascii", "replace").decode()
                        log.warning("Sin sello de tiempo (%s): %s", TSA, ts_err)
                        signed = do_sign(None)
        except pkcs11.PKCS11Error as e:
            raise FirmaError(f"Error del token ({type(e).__name__}). Verifica que siga conectado.", 409)
    if lt and (cert.crl_distribution_points or cert.ocsp_urls) and not dss_has_revinfo(signed):
        lt = False
    if ts_err:
        level = f"PAdES-B-B (sin sello de tiempo: {ts_err})"
    elif LT and not lt:
        level = (
            "PAdES-B-B (sin raices de confianza)"
            if vc is None
            else "PAdES-B-B (sin CRL/OCSP: sin conexion o cadena no confiable)"
        ) + (" con sello de tiempo" if TSA else "")
    else:
        level = "PAdES-B-LT" if lt else "PAdES-B-T" if TSA else "PAdES-B-B"
    return signed, level, cert_cn(cert)


def verify_pdf(pdf_bytes):
    roots = trust_roots()
    ts_roots = roots + trust_roots("tsa")  # raices extra solo para sellos de tiempo (TSA extranjera)
    # Como validadordefirmas.gob.ar: cadena evaluada a la fecha de firma declarada (certs vencidos siguen
    # valiendo), SHA-1 aceptado (GDE/BORA todavia lo usan), revocacion soft-fail (OCSP de ONTI responde
    # "unauthorized"; la CRL si anda). strict=False por PDFs hibridos de GDE/BORA.
    weak = DisallowWeakAlgorithmsPolicy(weak_hash_algos={"md2", "md5"})
    r = PdfFileReader(io.BytesIO(pdf_bytes), strict=False)
    res = []
    for sig in r.embedded_signatures:
        try:
            claimed = sig.self_reported_timestamp
            kw = dict(
                allow_fetching=claimed is None,
                revocation_mode="soft-fail",
                algorithm_usage_policy=weak,
                **({"moment": claimed} if claimed else {}),
            )
            st = validation.validate_pdf_signature(
                sig,
                signer_validation_context=ValidationContext(trust_roots=roots, **kw),
                ts_validation_context=ValidationContext(trust_roots=ts_roots, **kw),
            )
        except Exception as e:  # firma rota/ilegible: igual la reportamos
            res.append({"campo": sig.field_name, "error": f"{type(e).__name__}: {e}"})
            continue
        subj = st.signing_cert.subject.native
        cov = str(st.coverage).rsplit(".", 1)[-1]
        res.append(
            {
                "campo": sig.field_name,
                "firmante": subj.get("common_name", ""),
                "serial_number": subj.get("serial_number", ""),  # en AR: "CUIL 20xxxxxxxx5"
                "emisor": st.signing_cert.issuer.native.get("common_name", ""),
                "fecha_firma": st.signer_reported_dt.isoformat() if st.signer_reported_dt else None,
                "integra": bool(st.intact and st.valid),
                "sin_modificar": bool(
                    st.intact and st.valid and st.docmdp_ok is not False and cov in ("ENTIRE_FILE", "ENTIRE_REVISION")
                ),
                "cobertura": cov,
                "modificaciones": str(st.modification_level).rsplit(".", 1)[-1] if st.modification_level else None,
                "cadena_confiable": bool(st.trusted),
                "motivo_confianza": (
                    "sin raices de confianza (./trust vacio)"
                    if not roots
                    else "OK"
                    if st.trusted
                    else (str(st.trust_problem_indic) if st.trust_problem_indic else "cadena no valida")
                ),
                "sello_de_tiempo": (tv := st.timestamp_validity) is not None,
                "sello_de_tiempo_confiable": bool(tv and tv.trusted),
                "sello_de_tiempo_fecha": tv.timestamp.isoformat() if tv else None,
                "sello_de_tiempo_emisor": tv.signing_cert.subject.native.get("common_name", "") if tv else None,
                "validado_a": "fecha de firma declarada" if claimed else "ahora",
                "resumen": st.summary(),
            }
        )
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
<div><label>X (pt)</label><input type="number" name="x" value="50"></div><div><label>Y (pt)</label><input type="number" name="y" value="50"></div>
<div><label>Ancho</label><input type="number" name="w" value="220"></div><div><label>Alto</label><input type="number" name="h" value="60"></div></div>
<label>Posición de la firma: arrastrá un recuadro sobre la página</label>
<div id="pv" style="position:relative;display:none;border:1px solid #bbb;border-radius:6px;overflow:hidden;cursor:crosshair;user-select:none"><canvas id="cv" style="display:block;width:100%"></canvas><div id="box" style="position:absolute;border:2px solid #0a7a2f;background:rgba(10,122,47,.12);display:none;pointer-events:none"></div></div>
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
 let t=`Librería: ${s.lib} ${s.lib_found?'(encontrada)':'(NO ENCONTRADA)'} · Raíces de confianza: ${s.trust_roots} → firma ${s.lt&&s.trust_roots?'PAdES-B-LT':s.tsa?'PAdES-B-T':'PAdES-B-B'}`;
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
</script>
<script type="module">
// Vista previa con pdf.js: el recuadro arrastrado se traduce a puntos PDF (origen abajo-izquierda).
import * as pdfjs from 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/pdf.min.mjs';
pdfjs.GlobalWorkerOptions.workerSrc='https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/pdf.worker.min.mjs';
const f=document.getElementById('f'),pv=document.getElementById('pv'),cv=document.getElementById('cv'),box=document.getElementById('box');
let doc=null,vp=null;
async function render(){ if(!doc)return; const n=Math.min(Math.max(1,+f.page.value||1),doc.numPages); f.page.value=n; f.page.max=doc.numPages;
 const pg=await doc.getPage(n); vp=pg.getViewport({scale:2}); cv.width=vp.width; cv.height=vp.height;
 await pg.render({canvasContext:cv.getContext('2d'),viewport:vp}).promise; pv.style.display='block'; box.style.display='none'; }
f.pdf.onchange=async()=>{ doc=await pdfjs.getDocument({data:await f.pdf.files[0].arrayBuffer()}).promise; f.page.value=1; render(); };
f.page.onchange=render;
let start=null; const pos=e=>{const r=cv.getBoundingClientRect();return [e.clientX-r.left,e.clientY-r.top,r.width/vp.width]};
pv.onpointerdown=e=>{start=pos(e);pv.setPointerCapture(e.pointerId);box.style.display='block'};
pv.onpointermove=e=>{ if(!start)return; const [x,y]=pos(e); const l=Math.min(x,start[0]),t=Math.min(y,start[1]);
 Object.assign(box.style,{left:l+'px',top:t+'px',width:Math.abs(x-start[0])+'px',height:Math.abs(y-start[1])+'px'}); };
pv.onpointerup=e=>{ if(!start)return; const [x,y,k]=pos(e); const [sx,sy]=start; start=null; // k = px pantalla por unidad canvas
 const s=k*2; // px pantalla por punto PDF (canvas a escala 2)
 let w=Math.abs(x-sx)/s,h=Math.abs(y-sy)/s; if(w<20||h<10){box.style.display='none';return}
 const left=Math.min(x,sx)/s, bottom=(cv.getBoundingClientRect().height-Math.max(y,sy))/s;
 f.x.value=Math.round(left); f.y.value=Math.round(bottom); f.w.value=Math.round(w); f.h.value=Math.round(h); };
</script></html>"""


def parse_multipart(handler):
    """Multipart con stdlib (email). Devuelve {name: str | (filename, bytes)}."""
    ct = handler.headers.get("Content-Type", "")
    try:
        n = int(handler.headers.get("Content-Length", 0))
    except ValueError:
        raise FirmaError("Content-Length invalido.")
    if n < 0:
        raise FirmaError("Content-Length invalido.")
    if n > 200 * 1024 * 1024:
        raise FirmaError("Archivo demasiado grande (max 200 MB).", 413)
    body = handler.rfile.read(n)
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + ct.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    )
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
    secret = None  # --token: si esta, cada request debe traer X-Firma-Token

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

    def _authorized(self):
        if self.secret is None or hmac.compare_digest(
            self.headers.get("X-Firma-Token", "").encode(), self.secret.encode()
        ):
            return True
        self._send(401, "No autorizado.", "text/plain; charset=utf-8")
        return False

    def _shutdown(self):
        self._send(200, {"ok": True})
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def do_GET(self):
        path = self.path.split("?")[0]
        if not self._authorized():
            return
        if path == "/":
            self._send(200, HTML, "text/html; charset=utf-8")
        elif path == "/health":
            self._send(200, {"ok": True, "version": VERSION})
        elif path == "/status":
            self._send(200, status())
        elif path == "/shutdown":
            self._shutdown()
        else:
            self._send(404, {"error": "no existe"})

    def do_POST(self):
        path = self.path.split("?")[0]
        if not self._authorized():
            return
        if path == "/shutdown":
            return self._shutdown()
        try:
            form = parse_multipart(self)
            if "pdf" not in form or not isinstance(form["pdf"], tuple) or not form["pdf"][1]:
                raise FirmaError("Falta el PDF.")
            fn, pdf = form["pdf"]
            if path == "/sign":
                pin = form.get("pin", "")
                if not pin:
                    raise FirmaError("Falta el PIN.")
                try:
                    num = {k: float(form.get(k) or d) for k, d in (("x", 50), ("y", 50), ("w", 220), ("h", 60))}
                    page = int(form.get("page") or 1)
                except ValueError:
                    raise FirmaError("page/x/y/w/h tienen que ser numeros.")
                signed, level, cn = sign_pdf(
                    pdf,
                    pin,
                    page=page,
                    x=num["x"],
                    y=num["y"],
                    width=num["w"],
                    height=num["h"],
                    reason=form.get("reason"),
                    location=form.get("location"),
                    cert_id=form.get("cert_id") or None,
                )
                del pin, form
                out = (os.path.splitext(fn or "documento")[0]) + "-firmado.pdf"
                q = urllib.parse.quote(out)
                self._send(
                    200,
                    signed,
                    "application/pdf",
                    [
                        ("Content-Disposition", f"attachment; filename*=UTF-8''{q}"),
                        ("X-Filename", q),
                        ("X-Level", level),
                        ("X-Signer", urllib.parse.quote(cn)),
                    ],
                )
            elif path == "/verify":
                self._send(200, verify_pdf(pdf))
            else:
                self._send(404, {"error": "no existe"})
        except Exception as e:
            log.debug(traceback.format_exc())
            if isinstance(e, PdfError):  # pyHanko no pudo leer/escribir el PDF
                e = FirmaError(f"El archivo no es un PDF valido o esta danado ({e}).")
            msg = str(e) if isinstance(e, FirmaError) else f"Error inesperado: {type(e).__name__}: {e}"
            code, hdrs = (e.code, e.headers) if isinstance(e, FirmaError) else (400, [])
            if path == "/sign":
                self._send(code, msg, "text/plain; charset=utf-8", hdrs)
            else:
                self._send(code, {"error": msg}, headers=hdrs)


def main():
    global LIB, TRUST_DIR, LT, TSA
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--lib", default=LIB, help="ruta a la libreria PKCS#11 (default: env FIRMA_PKCS11_LIB o autodeteccion)"
    )
    ap.add_argument(
        "--trust", default=TRUST_DIR, help="carpeta de raices de confianza (default: env FIRMA_TRUST_DIR o ./trust)"
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8780, help="0 = puerto libre al azar")
    ap.add_argument("--token", default=None, help="secreto exigido en el header X-Firma-Token")
    ap.add_argument("--lt", action="store_true", default=LT, help="firmar PAdES-B-LT (embebe CRL/OCSP, ~9 MB con ONTI)")
    ap.add_argument(
        "--tsa",
        default=TSA or "none",
        help=f"URL de la TSA RFC 3161 (default: env FIRMA_TSA o {DEFAULT_TSA}); none = sin sello",
    )
    ap.add_argument("-v", action="store_true", help="log debug")
    a = ap.parse_args()
    LIB, TRUST_DIR, LT, Handler.secret = a.lib, a.trust, a.lt, a.token
    TSA = None if a.tsa.lower() in ("", "none") else a.tsa
    logging.basicConfig(level=logging.DEBUG if a.v else logging.INFO, format="%(asctime)s %(message)s")  # stderr
    if not a.v:  # pyHanko loguea tracebacks completos por cada cadena no confiable
        for name in ("pyhanko", "pyhanko_certvalidator"):
            logging.getLogger(name).setLevel(logging.CRITICAL)
    if not lib_ok():
        log.warning(lib_error())
    n = len(trust_roots())
    level = "PAdES-B-LT" if LT and n else "PAdES-B-T" if TSA else "PAdES-B-B"
    log.info("Raices de confianza en %s: %d; firma %s; TSA %s", TRUST_DIR, n, level, TSA)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    log.info("Escuchando en http://%s:%d  (lib: %s)", a.host, srv.server_address[1], LIB)
    print(f"READY port={srv.server_address[1]}", flush=True)  # la app parsea esta linea
    if LT:
        threading.Thread(target=prewarm, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    srv.server_close()


if __name__ == "__main__":
    main()
