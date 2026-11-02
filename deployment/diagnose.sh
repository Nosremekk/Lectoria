#!/usr/bin/env bash
# ==============================================================================
# Lectoria — Atalho para Diagnóstico Completo do Ambiente
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -x "${PROJECT_ROOT}/.venv/bin/python3" ]]; then
    PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python3"
else
    PYTHON_BIN="python3"
fi

exec "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/diagnose.py" "$@"
