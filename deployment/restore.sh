#!/usr/bin/env bash
# ==============================================================================
# Lectoria — Script de Restauração Segura de Backup
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -x "${PROJECT_ROOT}/.venv/bin/python3" ]]; then
    PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python3"
else
    PYTHON_BIN="python3"
fi

if [[ $# -eq 0 || "$1" == "--list" || "$1" == "-l" ]]; then
    "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/backup.py" --list
    echo ""
    echo "Uso para restaurar um backup:"
    echo "  ./deployment/restore.sh <nome_do_arquivo.db> [--force]"
    exit 0
fi

if [[ "$1" == "-h" || "$1" == "--help" ]]; then
    cat <<EOF
Uso:
  ./deployment/restore.sh --list
  ./deployment/restore.sh <nome_do_arquivo.db> [--force]

Restaura o banco de dados SQLite e as capas personalizadas associadas,
aplicando automaticamente eventuais migrações de schema pendentes.
EOF
    exit 0
fi

BACKUP_FILE="$1"
shift
EXTRA_ARGS=("$@")

# Se o argumento já for --restore (compatibilidade com scripts/backup.py), repassa diretamente
if [[ "${BACKUP_FILE}" == "--restore" ]]; then
    exec "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/backup.py" --restore "${EXTRA_ARGS[@]}"
fi

if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet lectoria.service 2>/dev/null; then
    echo "[AVISO] O serviço 'lectoria.service' está em execução no momento."
    echo "        Recomenda-se parar o serviço antes de restaurar (sudo systemctl stop lectoria.service)"
    echo "        e iniciá-lo novamente após a restauração (sudo systemctl start lectoria.service)."
fi

exec "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/backup.py" --restore "${BACKUP_FILE}" "${EXTRA_ARGS[@]}"
