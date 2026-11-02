#!/usr/bin/env bash
# ==============================================================================
# Lectoria — Script de Backup Consistente (SQLite Online Backup + Capas Custom)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -x "${PROJECT_ROOT}/.venv/bin/python3" ]]; then
    PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python3"
else
    PYTHON_BIN="python3"
fi

if [[ $# -eq 0 ]]; then
    exec "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/backup.py" --backup
else
    exec "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/backup.py" "$@"
fi
