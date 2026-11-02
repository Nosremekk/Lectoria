#!/usr/bin/env bash
# ==============================================================================
# Lectoria — Script de Desinstalação Segura (Preserva Dados do Usuário)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

REMOVE_VENV=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --remove-venv)
            REMOVE_VENV=1
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            cat <<EOF
Uso: ./deployment/uninstall.sh [OPÇÕES]

Desativa e remove o serviço systemd do Lectoria preservando integralmente:
  - Banco de dados SQLite e capas (data/)
  - Backups gerados (backups/)
  - Livros PDF (books/ e pastas externas)
  - Arquivo de configuração (.env)

Opções:
  --remove-venv   Remove também o ambiente virtual Python (.venv/)
  --dry-run       Apenas exibe as ações que seriam executadas
  -h, --help      Exibe esta ajuda
EOF
            exit 0
            ;;
        *)
            echo "[ERRO] Opção desconhecida: $1" >&2
            exit 1
            ;;
    esac
done

echo "============================================================"
echo " Lectoria — Desinstalação Segura"
echo "============================================================"

SYSTEMD_UNIT="/etc/systemd/system/lectoria.service"

if [[ -f "${SYSTEMD_UNIT}" ]]; then
    if [[ "${DRY_RUN}" -eq 1 ]]; then
        echo "[DRY-RUN] Pararia e removeria ${SYSTEMD_UNIT}"
    elif [[ "$(id -u)" -eq 0 ]]; then
        echo "[*] Parando e desabilitando serviço lectoria.service..."
        systemctl stop lectoria.service || true
        systemctl disable lectoria.service || true
        rm -f "${SYSTEMD_UNIT}"
        systemctl daemon-reload
        echo "[OK] Serviço systemd removido."
    else
        echo "[INFO] Unidade ${SYSTEMD_UNIT} encontrada. Para removê-la do systemd, execute:"
        echo "       sudo systemctl stop lectoria.service"
        echo "       sudo systemctl disable lectoria.service"
        echo "       sudo rm -f ${SYSTEMD_UNIT}"
        echo "       sudo systemctl daemon-reload"
    fi
else
    echo "[INFO] Nenhuma unidade instalada em ${SYSTEMD_UNIT}."
fi

if [[ "${REMOVE_VENV}" -eq 1 ]]; then
    if [[ "${DRY_RUN}" -eq 1 ]]; then
        echo "[DRY-RUN] Removeria ${PROJECT_ROOT}/.venv"
    elif [[ -d "${PROJECT_ROOT}/.venv" ]]; then
        rm -rf "${PROJECT_ROOT}/.venv"
        echo "[OK] Ambiente virtual (.venv) removido."
    fi
fi

echo ""
echo "[PROTEÇÃO DE DADOS] Os seguintes diretórios e arquivos foram PRESERVADOS:"
echo "  • Dados e capas: ${PROJECT_ROOT}/data"
echo "  • Backups:       ${PROJECT_ROOT}/backups"
echo "  • Livros PDF:    ${PROJECT_ROOT}/books"
[[ -f "${PROJECT_ROOT}/.env" ]] && echo "  • Configuração:  ${PROJECT_ROOT}/.env"
echo "============================================================"
