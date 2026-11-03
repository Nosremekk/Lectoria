#!/usr/bin/env bash
# ==============================================================================
# Lectoria — Script de Configuração de Ambiente e Serviço systemd
# Compatível com Linux Mint XFCE / Ubuntu / Debian
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PARENT_DIR="$(cd "${PROJECT_ROOT}/.." && pwd)"

# Se estivermos dentro da pasta app/ de um pacote com pastas de dados na raiz:
if [[ -d "${PARENT_DIR}/data" && -d "${PARENT_DIR}/books" && -d "${PARENT_DIR}/backups" ]]; then
    STORAGE_ROOT="${PARENT_DIR}"
else
    STORAGE_ROOT="${PROJECT_ROOT}"
fi

ENV_FILE="${PROJECT_ROOT}/.env"
if [[ -f "${STORAGE_ROOT}/.env" ]]; then
    ENV_FILE="${STORAGE_ROOT}/.env"
fi
SERVICE_FILE="${PROJECT_ROOT}/deployment/lectoria.service"
NON_INTERACTIVE=0

# Valores padrão iniciais
CFG_HOST="0.0.0.0"
CFG_PORT="8765"
CFG_LIBRARY_DIR="${STORAGE_ROOT}/books"
CFG_DATA_DIR="${STORAGE_ROOT}/data"
CFG_BACKUPS_DIR="${STORAGE_ROOT}/backups"
CFG_USER="$(id -un)"
CFG_GROUP="$(id -gn)"

# Se já existir um arquivo .env, carrega os valores atuais como padrão
load_existing_env() {
    local target_env="$1"
    if [[ -f "${target_env}" ]]; then
        while IFS='=' read -r key val || [[ -n "${key}" ]]; do
            key="$(echo "${key}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
            [[ -z "${key}" || "${key}" == \#* ]] && continue
            val="$(echo "${val}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")"
            case "${key}" in
                LECTORIA_HOST) [[ -n "${val}" ]] && CFG_HOST="${val}" ;;
                LECTORIA_PORT) [[ -n "${val}" ]] && CFG_PORT="${val}" ;;
                LECTORIA_LIBRARY_DIR) [[ -n "${val}" ]] && CFG_LIBRARY_DIR="${val}" ;;
                LECTORIA_DATA_DIR) [[ -n "${val}" ]] && CFG_DATA_DIR="${val}" ;;
                LECTORIA_BACKUPS_DIR) [[ -n "${val}" ]] && CFG_BACKUPS_DIR="${val}" ;;
            esac
        done < "${target_env}"
    fi
}

usage() {
    cat <<EOF
Uso: ./deployment/configure.sh [OPÇÕES]

Opções:
  --non-interactive       Aplica padrões/argumentos sem perguntas interativas
  --host <IP>             Endereço de escuta (padrão: 0.0.0.0)
  --port <PORTA>          Porta HTTP (padrão: 8765)
  --library-dir <DIR>     Diretório principal de PDFs (padrão: ./books)
  --data-dir <DIR>        Diretório de banco SQLite e capas (padrão: ./data)
  --backups-dir <DIR>     Diretório de backups (padrão: ./backups)
  --user <USUARIO>        Usuário Linux para o serviço systemd (padrão: usuário atual)
  --group <GRUPO>         Grupo Linux para o serviço systemd (padrão: grupo atual)
  --env-file <ARQUIVO>    Caminho alternativo para o arquivo .env
  --service-file <ARQ>    Caminho alternativo para gerar o lectoria.service
  -h, --help              Exibe esta mensagem de ajuda
EOF
}

# Faz uma primeira leitura se o .env padrão existir
load_existing_env "${ENV_FILE}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --non-interactive)
            NON_INTERACTIVE=1
            shift
            ;;
        --host)
            CFG_HOST="$2"
            shift 2
            ;;
        --port)
            CFG_PORT="$2"
            shift 2
            ;;
        --library-dir)
            CFG_LIBRARY_DIR="$2"
            shift 2
            ;;
        --data-dir)
            CFG_DATA_DIR="$2"
            shift 2
            ;;
        --backups-dir)
            CFG_BACKUPS_DIR="$2"
            shift 2
            ;;
        --user)
            CFG_USER="$2"
            shift 2
            ;;
        --group)
            CFG_GROUP="$2"
            shift 2
            ;;
        --env-file)
            ENV_FILE="$2"
            shift 2
            ;;
        --service-file)
            SERVICE_FILE="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "[ERRO] Opção desconhecida: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [[ "${NON_INTERACTIVE}" -eq 0 && -t 0 ]]; then
    echo "============================================================"
    echo " Lectoria — Configuração do Ambiente (Positivo C1260 / Mint)"
    echo "============================================================"
    echo "Pressione ENTER para manter o valor padrão entre colchetes."
    echo ""

    read -r -p "1. Endereço IP de escuta [${CFG_HOST}]: " input_host
    CFG_HOST="${input_host:-${CFG_HOST}}"

    read -r -p "2. Porta HTTP do servidor [${CFG_PORT}]: " input_port
    CFG_PORT="${input_port:-${CFG_PORT}}"

    read -r -p "3. Diretório principal de livros PDF [${CFG_LIBRARY_DIR}]: " input_lib
    CFG_LIBRARY_DIR="${input_lib:-${CFG_LIBRARY_DIR}}"

    read -r -p "4. Diretório de dados persistentes (SQLite/capas) [${CFG_DATA_DIR}]: " input_data
    CFG_DATA_DIR="${input_data:-${CFG_DATA_DIR}}"

    read -r -p "5. Diretório de backups [${CFG_BACKUPS_DIR}]: " input_backups
    CFG_BACKUPS_DIR="${input_backups:-${CFG_BACKUPS_DIR}}"

    read -r -p "6. Usuário do sistema para o serviço systemd [${CFG_USER}]: " input_user
    CFG_USER="${input_user:-${CFG_USER}}"
fi

# Validação de porta numérica
if ! [[ "${CFG_PORT}" =~ ^[0-9]+$ ]] || [[ "${CFG_PORT}" -lt 1024 ]] || [[ "${CFG_PORT}" -gt 65535 ]]; then
    echo "[ERRO] Porta inválida '${CFG_PORT}'. Informe um valor entre 1024 e 65535." >&2
    exit 1
fi

# Garante que os diretórios existam e resolve caminhos absolutos
mkdir -p "${CFG_LIBRARY_DIR}" "${CFG_DATA_DIR}" "${CFG_DATA_DIR}/covers" "${CFG_BACKUPS_DIR}"
CFG_LIBRARY_DIR="$(cd "${CFG_LIBRARY_DIR}" && pwd)"
CFG_DATA_DIR="$(cd "${CFG_DATA_DIR}" && pwd)"
CFG_COVERS_DIR="${CFG_DATA_DIR}/covers"
CFG_BACKUPS_DIR="$(cd "${CFG_BACKUPS_DIR}" && pwd)"

# Grava o arquivo .env
cat > "${ENV_FILE}" <<EOF
# ==============================================================================
# Lectoria — Configuração de Ambiente Local (.env)
# Gerado por deployment/configure.sh
# ==============================================================================

LECTORIA_APP_NAME="Lectoria"
LECTORIA_HOST=${CFG_HOST}
LECTORIA_PORT=${CFG_PORT}

# Diretórios persistentes
LECTORIA_LIBRARY_DIR="${CFG_LIBRARY_DIR}"
LECTORIA_DATA_DIR="${CFG_DATA_DIR}"
LECTORIA_COVERS_DIR="${CFG_COVERS_DIR}"
LECTORIA_BACKUPS_DIR="${CFG_BACKUPS_DIR}"
EOF

chmod 600 "${ENV_FILE}" 2>/dev/null || true
echo "[OK] Arquivo de configuração salvo em: ${ENV_FILE}"

# Gera o arquivo de unidade systemd atualizado para os caminhos reais
mkdir -p "$(dirname "${SERVICE_FILE}")"
cat > "${SERVICE_FILE}" <<EOF
# Arquivo de unidade systemd para o Lectoria (Linux Mint XFCE / Ubuntu / Debian)
# Nota: Execute ./deployment/configure.sh ou ./deployment/install.sh no servidor de destino
# para ajustar automaticamente User, Group, WorkingDirectory, ExecStart e ReadWritePaths.

[Unit]
Description=Lectoria — Servidor Pessoal de Livros Digitais
Documentation=https://github.com/Nosremekk/Lectoria
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${CFG_USER}
Group=${CFG_GROUP}
WorkingDirectory=${PROJECT_ROOT}
EnvironmentFile=-${ENV_FILE}
ExecStart="${PROJECT_ROOT}/.venv/bin/python3" main.py

# Reinicialização automática controlada em caso de falha transitória
Restart=on-failure
RestartSec=5

# Variáveis de ambiente padrão (podem ser sobrescritas no arquivo .env)
Environment=PYTHONUNBUFFERED=1
Environment=LECTORIA_HOST=${CFG_HOST}
Environment=LECTORIA_PORT=${CFG_PORT}
Environment=PORT=${CFG_PORT}

# Logs integrados ao systemd journal (journalctl -u lectoria.service)
StandardOutput=journal
StandardError=journal
SyslogIdentifier=lectoria

# Diretivas de isolamento e segurança para hardware doméstico (Positivo C1260)
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths="${CFG_DATA_DIR}" "${CFG_BACKUPS_DIR}"

# Limites de recursos para proteger a coexistência com o Jellyfin (Intel Celeron 847 / 8 GB RAM)
MemoryMax=512M
CPUWeight=100

[Install]
WantedBy=multi-user.target
EOF

echo "[OK] Unidade systemd atualizada em: ${SERVICE_FILE}"
echo "     • Host/Porta: ${CFG_HOST}:${CFG_PORT}"
echo "     • Biblioteca: ${CFG_LIBRARY_DIR}"
echo "     • Dados:      ${CFG_DATA_DIR}"
echo "     • Backups:    ${CFG_BACKUPS_DIR}"
