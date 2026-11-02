#!/usr/bin/env bash
# ==============================================================================
# Lectoria — Script de Instalação Local (Idempotente)
# Destino: Positivo C1260 / Linux Mint XFCE (base Ubuntu/Debian)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

ALLOW_ROOT=0
RECREATE_VENV=0
SKIP_PIP=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --allow-root)
            ALLOW_ROOT=1
            shift
            ;;
        --recreate-venv)
            RECREATE_VENV=1
            shift
            ;;
        --skip-pip)
            SKIP_PIP=1
            shift
            ;;
        -h|--help)
            cat <<EOF
Uso: ./deployment/install.sh [OPÇÕES]

Opções:
  --recreate-venv   Recria o ambiente virtual (.venv) do zero
  --skip-pip        Pula a instalação/atualização de pacotes via pip (modo offline)
  --allow-root      Permite executar como root (não recomendado)
  -h, --help        Exibe esta ajuda
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
echo " Lectoria — Instalação e Preparação do Ambiente"
echo "============================================================"

if [[ "$(id -u)" -eq 0 && "${ALLOW_ROOT}" -eq 0 ]]; then
    echo "[ERRO] Não execute este script diretamente como root (sudo)." >&2
    echo "       Execute com o seu usuário comum do Linux Mint para que os" >&2
    echo "       diretórios 'data/', 'backups/' e '.venv/' tenham as permissões corretas." >&2
    exit 1
fi

# 1. Verificação de pacotes do sistema operacional
echo "[1/5] Verificando dependências do sistema operacional..."
MISSING_PKGS=()

if ! command -v python3 >/dev/null 2>&1; then
    MISSING_PKGS+=("python3")
fi

if ! python3 -c "import venv" >/dev/null 2>&1; then
    MISSING_PKGS+=("python3-venv")
fi

if [[ "${#MISSING_PKGS[@]}" -gt 0 ]]; then
    echo "[ERRO] Pacotes essenciais ausentes: ${MISSING_PKGS[*]}" >&2
    echo "       No Linux Mint / Ubuntu / Debian, instale com:" >&2
    echo "       sudo apt update && sudo apt install -y python3 python3-venv python3-pip sqlite3 zenity" >&2
    exit 1
fi

if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "[AVISO] Utilitário 'sqlite3' de linha de comando não encontrado (opcional, mas recomendado)."
    echo "        Para instalar: sudo apt install -y sqlite3"
fi

if ! command -v zenity >/dev/null 2>&1; then
    echo "[AVISO] Utilitário 'zenity' não encontrado (opcional, usado apenas para o seletor gráfico local)."
    echo "        No Linux Mint XFCE ele já costuma vir instalado. Caso queira: sudo apt install -y zenity"
fi

PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')"
echo "[OK] Python detectado: ${PYTHON_VERSION}"

# 2. Preparação do ambiente virtual (.venv)
echo "[2/5] Verificando ambiente virtual Python (.venv)..."
VENV_DIR="${PROJECT_ROOT}/.venv"
VENV_PYTHON="${VENV_DIR}/bin/python3"

# Detecta se o .venv foi copiado de outra máquina com caminho incompatível
if [[ -d "${VENV_DIR}" && "${RECREATE_VENV}" -eq 0 ]]; then
    if ! "${VENV_PYTHON}" -c "import sys" >/dev/null 2>&1; then
        echo "[INFO] Ambiente .venv existente pertence a outro caminho/máquina. Recriando..."
        RECREATE_VENV=1
    elif [[ -f "${VENV_DIR}/bin/pip" ]]; then
        PIP_SHEBANG="$(head -n 1 "${VENV_DIR}/bin/pip" || true)"
        if [[ "${PIP_SHEBANG}" != "#!${VENV_DIR}/bin/python"* ]]; then
            echo "[INFO] Ajustando ambiente .venv migrado de outro diretório..."
            RECREATE_VENV=1
        fi
    fi
fi

if [[ "${RECREATE_VENV}" -eq 1 && -d "${VENV_DIR}" ]]; then
    rm -rf "${VENV_DIR}"
fi

if [[ ! -d "${VENV_DIR}" ]]; then
    echo "[*] Criando ambiente virtual em ${VENV_DIR}..."
    python3 -m venv "${VENV_DIR}"
fi

# 3. Instalação das dependências Python
echo "[3/5] Verificando dependências Python (requirements.txt)..."
if [[ "${SKIP_PIP}" -eq 0 ]]; then
    "${VENV_PYTHON}" -m pip install --upgrade pip >/dev/null 2>&1 || true
    "${VENV_PYTHON}" -m pip install -r "${PROJECT_ROOT}/requirements.txt"
else
    echo "[INFO] Instalação via pip ignorada (--skip-pip)."
fi

# Verifica importação dos módulos principais
"${VENV_PYTHON}" -c "import fastapi, uvicorn, pypdf; print('[OK] Dependências Python verificadas (FastAPI, Uvicorn, pypdf).')"

# 4. Configuração de diretórios, .env e unidade systemd
echo "[4/5] Configurando diretórios persistentes, .env e arquivo systemd..."
bash "${SCRIPT_DIR}/configure.sh" --non-interactive

# 5. Inicialização e migração segura do banco de dados SQLite
echo "[5/5] Inicializando e verificando integridade do banco de dados SQLite..."
"${VENV_PYTHON}" -c "
import config, database
from backup_service import verify_sqlite_integrity
database.init_db()
assert verify_sqlite_integrity(config.DB_PATH), 'Falha de integridade no SQLite'
print(f'[OK] Banco de dados pronto e íntegro em: {config.DB_PATH} (Schema V{database.SCHEMA_VERSION})')
"

echo ""
echo "============================================================"
echo " Instalação concluída com sucesso!"
echo "============================================================"
echo " • Para testar manualmente agora:"
echo "     ${VENV_PYTHON} \"${PROJECT_ROOT}/main.py\""
echo ""
echo " • Para instalar e ativar como serviço automático no Linux Mint (systemd):"
echo "     sudo cp \"${PROJECT_ROOT}/deployment/lectoria.service\" /etc/systemd/system/lectoria.service"
echo "     sudo systemctl daemon-reload"
echo "     sudo systemctl enable --now lectoria.service"
echo ""
echo " • Para verificar diagnóstico completo:"
echo "     ${VENV_PYTHON} \"${PROJECT_ROOT}/scripts/diagnose.py\""
echo "============================================================"
