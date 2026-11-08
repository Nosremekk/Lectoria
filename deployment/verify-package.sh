#!/usr/bin/env bash
# ==============================================================================
# Lectoria — Verificador de Integridade do Pacote de Distribuição
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${1:-}"

if [[ -z "${TARGET_DIR}" ]]; then
    if [[ -f "${SCRIPT_DIR}/manifest.json" && -f "${SCRIPT_DIR}/SHA256SUMS" ]]; then
        TARGET_DIR="${SCRIPT_DIR}"
    elif [[ -f "${SCRIPT_DIR}/../manifest.json" && -f "${SCRIPT_DIR}/../SHA256SUMS" ]]; then
        TARGET_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
    elif [[ -f "${SCRIPT_DIR}/../../manifest.json" && -f "${SCRIPT_DIR}/../../SHA256SUMS" ]]; then
        TARGET_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
    else
        TARGET_DIR="$(pwd)"
    fi
fi

TARGET_DIR="$(cd "${TARGET_DIR}" && pwd)"

echo "============================================================"
echo " Lectoria — Verificação de Integridade do Pacote"
echo " Alvo: ${TARGET_DIR}"
echo "============================================================"

FAILED=0

# 1. Verificação da estrutura de diretórios
echo "[1/5] Verificando estrutura de diretórios..."
REQUIRED_DIRS=("app" "data" "books" "backups")
for d in "${REQUIRED_DIRS[@]}"; do
    if [[ ! -d "${TARGET_DIR}/${d}" ]]; then
        echo "  [FALHA] Diretório obrigatório ausente: ${d}/"
        FAILED=1
    else
        echo "  [OK] Diretório presente: ${d}/"
    fi
done

# 2. Verificação de arquivos essenciais
echo ""
echo "[2/5] Verificando arquivos fundamentais de distribuição..."
REQUIRED_FILES=(
    "manifest.json"
    "SHA256SUMS"
    "INSTALL.txt"
    "TRANSFER.txt"
    "VERSION.txt"
    "app/main.py"
    "app/config.py"
    "app/database.py"
    "app/epub_service.py"
    "app/automation_service.py"
    "app/social_service.py"
    "app/requirements.txt"
    "app/.env.example"
)
for f in "${REQUIRED_FILES[@]}"; do
    if [[ ! -f "${TARGET_DIR}/${f}" ]]; then
        echo "  [FALHA] Arquivo obrigatório ausente: ${f}"
        FAILED=1
    else
        echo "  [OK] Arquivo presente: ${f}"
    fi
done

# 3. Verificação de permissões executáveis nos scripts
echo ""
echo "[3/5] Verificando permissões de execução dos scripts..."
SCRIPTS_TO_CHECK=(
    "install.sh"
    "verify-package.sh"
    "app/deployment/install.sh"
    "app/deployment/configure.sh"
    "app/deployment/update.sh"
    "app/deployment/backup.sh"
    "app/deployment/restore.sh"
    "app/deployment/uninstall.sh"
    "app/deployment/diagnose.sh"
    "app/scripts/diagnose.py"
    "app/scripts/backup.py"
    "app/scripts/manage_users.py"
)
for s in "${SCRIPTS_TO_CHECK[@]}"; do
    full_path="${TARGET_DIR}/${s}"
    if [[ -f "${full_path}" ]]; then
        if [[ ! -x "${full_path}" ]]; then
            echo "  [FALHA] Script sem permissão de execução (+x): ${s}"
            FAILED=1
        else
            echo "  [OK] Executável: ${s}"
        fi
    fi
done

# 4. Validação de hashes SHA-256
echo ""
echo "[4/5] Validando somas de verificação SHA-256 (SHA256SUMS)..."
SHA_FILE="${TARGET_DIR}/SHA256SUMS"
if [[ ! -f "${SHA_FILE}" ]]; then
    echo "  [FALHA] Arquivo SHA256SUMS não encontrado em ${TARGET_DIR}"
    FAILED=1
else
    if command -v sha256sum >/dev/null 2>&1; then
        # Executa no diretório alvo para que caminhos relativos correspondam
        (
            cd "${TARGET_DIR}"
            if sha256sum --check --status SHA256SUMS 2>/dev/null; then
                TOTAL_CHECKED="$(wc -l < SHA256SUMS | tr -d ' ')"
                echo "  [OK] Todos os ${TOTAL_CHECKED} arquivos conferem com SHA256SUMS (100% íntegros)."
            else
                echo "  [FALHA] Uma ou mais somas de verificação SHA-256 divergiram!"
                sha256sum --check SHA256SUMS | grep -v 'OK$' || true
                FAILED=1
            fi
        )
    else
        echo "  [AVISO] Utilitário sha256sum não disponível no sistema."
    fi
fi

# 5. Verificação de consistência do banco de dados (se incluído)
echo ""
echo "[5/5] Verificando integridade de banco de dados SQLite (se presente)..."
DB_FOUND=0
for db_candidate in "${TARGET_DIR}/data/lectoria.db" "${TARGET_DIR}/migration/lectoria.db"; do
    if [[ -f "${db_candidate}" ]]; then
        DB_FOUND=1
        echo "  [*] Testando banco: ${db_candidate}"
        if command -v sqlite3 >/dev/null 2>&1; then
            INTEGRITY_RES="$(sqlite3 "${db_candidate}" "PRAGMA integrity_check;" 2>&1 || true)"
            if [[ "${INTEGRITY_RES}" == "ok" ]]; then
                echo "  [OK] Integridade SQLite confirmada (PRAGMA integrity_check: ok)."
            else
                echo "  [FALHA] Falha de integridade no banco: ${INTEGRITY_RES}"
                FAILED=1
            fi
            # Confirma ausência de sessões ativas
            SESSIONS_COUNT="$(sqlite3 "${db_candidate}" "SELECT COUNT(*) FROM sessions;" 2>/dev/null || echo "0")"
            if [[ "${SESSIONS_COUNT}" -eq 0 ]]; then
                echo "  [OK] Banco seguro para transferência: 0 sessões ativas registradas."
            else
                echo "  [AVISO] Banco contém ${SESSIONS_COUNT} sessões ativas gravadas!"
            fi
        elif command -v python3 >/dev/null 2>&1; then
            PY_RES="$(python3 -c "import sqlite3; conn = sqlite3.connect('${db_candidate}'); print(conn.execute('PRAGMA integrity_check;').fetchone()[0])" 2>&1 || true)"
            if [[ "${PY_RES}" == "ok" ]]; then
                echo "  [OK] Integridade SQLite confirmada via python3 (PRAGMA integrity_check: ok)."
            else
                echo "  [FALHA] Falha de integridade no banco: ${PY_RES}"
                FAILED=1
            fi
        fi
    fi
done

if [[ "${DB_FOUND}" -eq 0 ]]; then
    echo "  [INFO] Nenhum banco de dados pré-existente no pacote (Modalidade A: Instalação Limpa)."
fi

echo ""
echo "============================================================"
if [[ "${FAILED}" -eq 0 ]]; then
    echo " RESULTADO: PACOTE APROVADO COM SUCESSO!"
    echo " O pacote está íntegro e pronto para ser transferido."
    echo "============================================================"
    exit 0
else
    echo " RESULTADO: FALHA NA VALIDAÇÃO DO PACOTE."
    echo " Verifique os erros listados acima antes de transferir."
    echo "============================================================"
    exit 1
fi
