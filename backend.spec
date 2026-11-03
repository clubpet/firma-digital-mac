# PyInstaller: backend congelado de firma.py. Uso: sh build-backend.sh  (ONEFILE=1 para un solo archivo)
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ONEFILE = os.environ.get("ONEFILE") == "1"
hidden = (collect_submodules("pkcs11") + collect_submodules("pyhanko_certvalidator.fetchers.aiohttp_fetchers")
          + collect_submodules("oscrypto") + collect_submodules("tzlocal"))  # imports diferidos/Cython
datas = collect_data_files("pyhanko") + [("trust/*.pem", "trust"), ("trust/README.md", "trust")]

a = Analysis(["firma.py"], datas=datas, hiddenimports=hidden,
             excludes=["tkinter", "PIL", "uharfbuzz", "fontTools", "qrcode", "yaml"])
pyz = PYZ(a.pure)
if ONEFILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="firma-backend", console=True,
              target_arch="arm64", upx=False)
else:
    exe = EXE(pyz, a.scripts, exclude_binaries=True, name="firma-backend", console=True,
              target_arch="arm64", upx=False)
    coll = COLLECT(exe, a.binaries, a.datas, name="firma-backend", upx=False)
