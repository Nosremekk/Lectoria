#!/usr/bin/env bash
# ==============================================================================
# Lectoria — Script de Atualização Segura (Preserva Dados, Capas e Backups)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SKIP_GIT=0
SKIP_PIP=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-git)
            SKIP_GIT=1
            shift
            ;;
        --skip-pip)
            SKIP_PIP=1
            shift
            ;;
        -h|--help)
            cat <<EOF
Uso: ./deployment/update.sh [OPÇÕES]

Realiza a atualização segura do Lectoria preservando integralmente:
  - Banco de dados SQLite (data/lectoria.db)
  - Capas geradas e personalizadas (data/covers/)
  - Backups existentes (backups/)
  - Diretórios de livros (books/ e bibliotecas adicionais)
  - Configurações locais (.env)

Opções:
  --skip-git   Não executa 'git pull' (útil quando o código já foi atualizado via cópia/rsync)
  --skip-pip   Não executa 'pip install -r requirements.txt' (modo offline)
  -h, --help   Exibe esta ajuda
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
echo " Lectoria — Atualização Segura"
echo "============================================================"

if [[ -x "${PROJECT_ROOT}/.venv/bin/python3" ]]; then
    PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python3"
else
    PYTHON_BIN="python3"
fi

# 1. Backup preventivo obrigatório antes de atualizar
echo "[1/5] Executando backup preventivo antes da atualização..."
DB_EXISTS="$("${PYTHON_BIN}" -c "import config; print('1' if config.DB_PATH.exists() else '0')")"
if [[ "${DB_EXISTS}" == "1" ]]; then
    bash "${SCRIPT_DIR}/backup.sh"
else
    echo "[INFO] Banco de dados ainda não existe; etapa de backup preventivo dispensada."
fi

# 2. Atualização do código via Git (se aplicável)
echo "[2/5] Verificando código-fonte..."
if [[ "${SKIP_GIT}" -eq 0 && -d "${PROJECT_ROOT}/.git" ]] && command -v git >/dev/null 2>&1; then
    if git -C "${PROJECT_ROOT}" remote | grep -q .; then
        echo "[*] Atualizando repositório Git (git pull --ff-only)..."
        git -C "${PROJECT_ROOT}" pull --ff-only
    else
        echo "[INFO] Repositório Git local sem remoto configurado; mantendo árvore atual."
    fi
else
    echo "[INFO] Atualização via Git ignorada (--skip-git ou diretório sem .git)."
fi

# 3. Atualização das dependências no ambiente virtual
echo "[3/5] Verificando dependências Python..."
if [[ "${SKIP_PIP}" -eq 0 && -x "${PROJECT_ROOT}/.venv/bin/python3" ]]; then
    "${PROJECT_ROOT}/.venv/bin/python3" -m pip install -r "${PROJECT_ROOT}/requirements.txt"
else
    echo "[INFO] Atualização de pacotes pip ignorada."
fi

# 4. Reconciliação de configurações (.env e lectoria.service)
echo "[4/5] Reconciliando arquivos de configuração..."
bash "${SCRIPT_DIR}/configure.sh" --non-interactive

# 5. Migração automática de schema e verificação de integridade
echo "[5/5] Aplicando migrações de banco de dados e validando integridade..."
"${PYTHON_BIN}" -c "
import config, database
from backup_service import verify_sqlite_integrity
database.init_db()
assert verify_sqlite_integrity(config.DB_PATH), 'Erro de integridade após migração'
print(f'[OK] Schema atualizado para V{database.SCHEMA_VERSION} e integridade SQLite verificada.')
"

echo ""
echo "============================================================"
echo " Atualização concluída com sucesso!"
echo "============================================================"
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet lectoria.service 2>/dev/null; then
    echo " • O serviço systemd está ativo. Para aplicar a atualização, execute:"
    echo "     sudo cp \"${PROJECT_ROOT}/deployment/lectoria.service\" /etc/systemd/system/lectoria.service"
    echo "     sudo systemctl daemon-reload"
    echo "     sudo systemctl restart lectoria.service"
fi
