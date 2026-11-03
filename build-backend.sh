#!/bin/sh
# Congela firma.py con PyInstaller -> dist/firma-backend/firma-backend (onedir, arm64).
# ONEFILE=1 sh build-backend.sh -> dist/firma-backend (un archivo; arranca mas lento: se descomprime en cada inicio).
set -eu
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || /usr/local/bin/python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
rm -rf build dist/firma-backend
.venv/bin/pyinstaller --noconfirm --clean --log-level WARN backend.spec
ls -d dist/firma-backend*
