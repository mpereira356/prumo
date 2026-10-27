#!/usr/bin/env bash
# Prepara apenas o ambiente Python; não modifica serviços ou configurações existentes.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
python3 -m venv .venv
.venv/bin/python -m pip --isolated install -r requirements.lock.txt
printf '\nAmbiente pronto. Siga deploy/README.md para configurar o serviço e HTTPS.\n'
