# Lectoria — Fase 11.4: Restauração de Backups e Hardening Residual da Infraestrutura

- **Status**: **`PHASE_11_4_READY_FOR_REVIEW`** *(implementada e validada localmente em branch dedicada com dados sintéticos representativos; sem deploy em produção, sem alterações na VPS, sem push/merge/tag; `APP_VERSION = "1.9.1"`, `SCHEMA_VERSION = 13`)*
- **Data**: `2026-11-25`
- **Branch**: `refactor/v1.9.2-backup-and-container-hardening` (baseada em `origin/main` @ `4d682e3d2c611b3e67062a51f07c922a20aadecf`)

> **Transparência de validação**: Todos os testes de restauração executados nesta etapa utilizaram **dados sintéticos representativos** em ambiente 100% isolado. A recuperação funcional a partir do backup real da VPS (`/srv/nosreme/lectoria/backups/2026-11-25T19-14-08Z`) **não foi executada** e aguarda autorização expressa do proprietário conforme o procedimento privado da Seção 11.

---

## 1. Encerramento Definitivo da Fase 11.3

A Fase 11.3 foi formalmente encerrada como **`PHASE_11_3_COMPLETED`** em [`13-phase-11.3-http-security.md`](./13-phase-11.3-http-security.md) e [`05-implementation-roadmap.md`](./05-implementation-roadmap.md), diferenciando:
- **Verificações automatizadas**: PR #2 integrado em `main` (`4d682e3`), `publish.yml` (`Run #45679963000`, `390 passed`, `:stable` em `sha256:1eac5477...`, `:v1.9.1` e `:1.9.1` intactas), deploy controlado via `lectoria-auto-update.service` (`UPDATE_SUCCESS`, backup obrigatório `2026-11-25T19-14-08Z`, SQLite `integrity=ok` / Schema `13` com contagens preservadas), validação HTTPS real via Tailscale Serve e execução automática subsequente do timer (`2026-11-25 19:29:43–19:29:44 UTC`, `NO_EXECUTABLE_CHANGE`, `Result=success`, container `c49acdfd...` preservado sem backup extra).
- **Verificações manuais confirmadas pelo proprietário no navegador**: login/logout, cookies `lectoria_session` e `lectoria_csrf` com atributos esperados, Biblioteca/capas, Descoberta/Atlas, leitores PDF/EPUB, preservação de progresso/anotações e zero violações CSP nos cenários efetivamente testados (sem declarar ausência universal fora desses cenários).

---

## 2. Inventário da Cobertura Real dos Backups

| Elemento | Backup interno (`backup_service.py`) | Snapshot do servidor (`lectoria-backup.service`) | Classificação |
| :--- | :---: | :---: | :--- |
| **Banco SQLite (`lectoria.db`)**: usuários e hashes de senha, configurações (`system_settings`), fontes (`library_sources`), metadados manuais, progresso, bookmarks, notas, destaques, desenhos, coleções, clubes/discussões/citações, Atlas (`concepts`, `reflections`, `knowledge_links`), auditoria e sessões | Sim (Online Backup API, WAL-safe) | Sim (Online Backup API, WAL-safe) | **Incluído no backup** |
| **Capas personalizadas (`*_custom.*`)** | Sim (`<stem>_covers/`) | Sim (`covers.tar.gz`) | **Incluído no backup** |
| **Capas extraídas (`*_extracted.*`)** | Não | Sim (`covers.tar.gz`) | **Regenerável** a partir dos livros originais |
| **Arquivos originais dos livros (PDF / EPUB)** | **Não** | **Não** | **Volume independente (`/app/books:ro`) — NÃO incluído no backup do SQLite/capas** |
| **Configuração operacional (`compose.yaml`, `.env`, systemd, proxy/Tailscale)** | **Não** | **Não** | **Exige procedimento separado do administrador** |
| **Cópia externa (off-site)** | **Não** | **Não** (Restic/Rclone intencionalmente desabilitado) | **Sem proteção externa contra perda física do host** |

> **Conclusão crítica**: o backup de `lectoria.db` + `covers.tar.gz` **não** constitui backup integral da biblioteca. A preservação física dos arquivos PDF/EPUB originais e das configurações do host permanece responsabilidade operacional separada.

---

## 3. Resultado dos Testes de Restauração (Ambiente Isolado com Dados Sintéticos)

Implementado em [`tests/test_v192_backup_restore_and_hardening.py`](../../tests/test_v192_backup_restore_and_hardening.py) usando diretório de dados, banco SQLite, capas, livros sintéticos (PDF + EPUB válidos) e backups 100% isolados em `tmp_path`:

- Restauração completa de snapshot (`lectoria.db` + `covers.tar.gz` + `commit.txt`) via `restore_snapshot_to_directory(...)`: **APROVADA**.
- `PRAGMA integrity_check == ok` e `SCHEMA_VERSION == 13`: **APROVADOS**.
- Inicialização da aplicação contra a instância restaurada (`/api/health` = `200`): **APROVADA**.
- **Decisão documentada sobre sessões restauradas**: `restore_backup` e `restore_snapshot_to_directory` agora **invalidam sessões antigas por padrão** (`invalidate_sessions=True`, executando `DELETE FROM sessions`). Justificativa: em cenários de recuperação após incidente ou migração, tokens de sessão contidos no snapshot podem estar expirados ou comprometidos; invalidá-los força reautenticação limpa sem perder nenhum dado do usuário (senhas/hashes em `users` são preservados). Validado no teste: cookie antigo retorna `401` e novo login com a senha preservada retorna `200`.
- Isolamento entre usuários (`alice_admin` vs. `bob_reader`): confirmado que `bob_reader` faz login normalmente após restauração e recebe listas vazias para notas privadas e registros do Atlas de `alice_admin`.
- Abertura de livros restaurados: PDF (incluindo HTTP Range `206 Partial Content`) e EPUB (`200 OK`, cabeçalho `PK`) servidos normalmente; quando os arquivos originais dos livros estão ausentes no destino, a aplicação sobe normalmente, preserva os registros no banco (`is_missing = 1`) e retorna `404` limpo ao tentar abrir o arquivo.

---

## 4. Comparações de Integridade e Conteúdo

| Item Verificado | Método de Comparação | Resultado |
| :--- | :--- | :--- |
| **Imutabilidade da origem** (`books/*.pdf`, `books/*.epub`, `snapshot/lectoria.db`, `snapshot/covers.tar.gz`) | SHA-256 antes vs. depois da restauração | Idêntico (0 bytes alterados) |
| **Capa personalizada** (`<id>_custom.png` via `POST /api/books/{id}/cover` → `covers.tar.gz` → `GET /api/books/{id}/cover`) | SHA-256 exato do conteúdo HTTP servido | Idêntico |
| **Metadados manuais, progresso, destaques, notas, coleções e Atlas** | Comparação de registros representativos e relacionamentos via API REST após boot | 100% preservados |

---

## 5. Falhas Identificadas na Revisão Direcionada e Correções Aplicadas

Em vez de criar um segundo sistema de backup, foram corrigidas 8 fragilidades concretas diretamente em [`backup_service.py`](../../backup_service.py) e [`scripts/backup.py`](../../scripts/backup.py):

1. **Validação superficial do SQLite**: `verify_sqlite_integrity` aceitava bancos vazios/sem tabelas e não checava compatibilidade de schema. **Correção**: criado `validate_backup_sqlite(db_path)` em modo somente-leitura (`file:...?mode=ro`), exigindo tamanho > 0, `PRAGMA integrity_check == ok`, presença das tabelas centrais (`schema_migrations`, `users`, `books`, `reading_progress`) e `1 <= schema_version <= database.SCHEMA_VERSION` (rejeitando explicitamente schemas futuros sem downgrade automático).
2. **Ausência de suporte a snapshots com `covers.tar.gz` e restauração em diretório isolado**: **Correção**: criado `restore_snapshot_to_directory(source, target_data_dir, *, allow_overwrite=False, invalidate_sessions=True)` e opções `--target-dir` / `--keep-sessions` em `scripts/backup.py`.
3. **Ausência de extrator seguro para `covers.tar.gz`**: **Correção**: criado `extract_covers_archive_safely(archive_path, dest_dir)` com pré-validação integral antes da escrita, bloqueando arquivos corrompidos/truncados, path traversal (`..`), caminhos absolutos, symlinks, hardlinks, dispositivos, FIFOs, **entradas duplicadas / colisões de caminhos** e **symlinks preexistentes no destino** (`_assert_no_symlinks_under`).
4. **Limites proporcionais contra consumo desproporcional de disco (tar-bomb)**: **Correção**: impostos limites proporcionais aos backups legítimos da Lectoria (`MAX_COVERS_ARCHIVE_FILES = 10_000`, `MAX_SINGLE_COVER_BYTES = 25 MiB`, `MAX_TOTAL_COVERS_BYTES = 512 MiB`) verificados tanto no cabeçalho tar quanto nos bytes reais gravados em stream.
5. **Pressão de staging sobre `tmpfs /tmp` (64 MB)**: na revisão direcionada identificou-se que extrair `covers.tar.gz` em `/tmp` poderia estourar o `tmpfs` de `64 MB` em backups grandes (`covers.tar.gz` da VPS tem `35 MB` compactados). **Correção**: tanto `extract_covers_archive_safely` quanto `restore_snapshot_to_directory` fazem *staging* no **mesmo filesystem do destino** (`/app/data/.lectoria_*_stage_*`), eliminando uso de `/tmp` na restauração e mantendo `tmpfs /tmp:rw,noexec,nosuid,size=64m` seguro para exportações do Atlas.
6. **Consistência e rollback coordenado entre banco e capas**: a substituição do arquivo SQLite (`temp_target.replace(dst_db)`) é atômica no filesystem, mas a troca conjunta de banco + diretório de capas envolve duas operações (não se declara atomicidade integral de kernel entre múltiplos arquivos). **Correção**: o diretório de capas anterior é preservado via `rename` no mesmo filesystem (`.lectoria_covers_pre_restore_bak`) durante a troca; se ocorrer falha após substituir o banco (em `init_db`, invalidação de sessões ou troca de capas), **tanto o banco anterior (ou remoção parcial em destino novo) quanto as capas anteriores são revertidos automaticamente**.
7. **Sobrescrita sem confirmação explícita**: `restore_snapshot_to_directory` adota `allow_overwrite=False` por padrão, gerando cópia de proteção WAL-safe (`lectoria_pre_restore.db.bak`) quando `allow_overwrite=True`.
8. **Persistência de sessões antigas após restauração**: **Correção**: invalidação automática da tabela `sessions` (`DELETE FROM sessions`) por padrão, afetando exclusivamente o banco restaurado.

---

## 6. Avaliação das Permissões, Privilégios Docker e Diagnóstico Real da VPS

- **Estado anterior da imagem**: o `Dockerfile` não definia diretiva `USER`, executando Uvicorn/Python como `root` (`UID 0 / GID 0`).
- **Superfície real de escrita da aplicação** (verificada no código-fonte):
  - Graváveis necessários: `LECTORIA_DATA_DIR` (`lectoria.db`, `-wal`, `-shm`, `covers/`), `LECTORIA_BACKUPS_DIR` e `/tmp` (usado apenas por `knowledge_export_service.py` via `tempfile.mkstemp` para exportações `.json`/`.zip` do Atlas — tipicamente < 5 MB, compatível com `tmpfs 64m`).
  - Estritamente somente-leitura: `LECTORIA_LIBRARY_DIR` (`/app/books` ou `/storage/books`, Lectoria nunca altera arquivos dos livros) e `/app` (bytecode pré-compilado com `PYTHONDONTWRITEBYTECODE=1`).
- **Diagnóstico somente-leitura na VPS real (`srv2137600`, `2026-11-25`, sem `sudo`)**:
  - Recursos do host: `1 vCPU`, `3.8 GiB RAM` (`3.1 GiB` disponíveis), `2.0 GiB Swap`.
  - Usuário do host: `emerson` (`UID 1000 / GID 1000` — coincide diretamente com `lectoria` `1000:1000`!).
  - `/srv/nosreme/lectoria`: `1000:1000 755`; `/srv/nosreme/lectoria/books`: `1000:1000 755` (**já legível por UID 1000 sem alterar permissões da biblioteca**).
  - `/srv/nosreme/lectoria/data`: `1000:1000 700`, porém os arquivos internos (`lectoria.db`, `covers/`, `lectoria_pre_migration_*.db.bak`) pertencem atualmente a **`root:root`** (`-rw-r--r--` / `drwxr-xr-x`), criados pelo container anterior executando como `root`.
  - `/srv/nosreme/lectoria/backups`: `1000:1000 700`; `lectoria-backup.service` executa como `User=root` (`/usr/local/sbin/lectoria-backup`), compatível com leitura de arquivos `1000:1000` em `data/`.
  - `compose.yaml` atual na VPS usa volumes `./books:/storage/books` (ainda sem `:ro`), `./data:/storage/data`, `./backups:/storage/backups`, `mem_limit: 768m` e as 4 variáveis da Fase 11.3.
  - **Impacto crítico confirmado**: como os arquivos dentro de `/srv/nosreme/lectoria/data/` pertencem a `root:root`, **não se deve fazer merge/deploy em `:stable` antes de migrar a posse desses arquivos para `1000:1000`** (Seção 12).

---

## 7. Medidas de Hardening Implementadas

| Arquivo | Medida Aplicada | Benefício Concreto |
| :--- | :--- | :--- |
| [`Dockerfile`](../../Dockerfile) | Usuário dedicado `lectoria` (`ARG LECTORIA_UID=1000`, `ARG LECTORIA_GID=1000`), `USER lectoria:lectoria`, `chown` restrito a `/app/data /app/data/covers /app/backups` (mantendo `/app` e `/opt/venv` de `root:root` somente-leitura) | Elimina execução como `root` sem `chmod 777` e sem `chown -R` indiscriminado |
| [`docker-compose.yml`](../../docker-compose.yml) e [`docker-compose.example.yml`](../../docker-compose.example.yml) | `security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]`, `read_only: true`, `tmpfs: [/tmp:rw,noexec,nosuid,size=64m]`, `/app/books:ro`, imagem `:stable` e `healthcheck` em `/api/health` | Reduz drasticamente superfície de ataque com zero impacto funcional ou de memória |

---

## 8. Medidas Descartadas e Justificativas

- **Entrypoint com `chown -R` automático como `root` + `gosu`/`su-exec`**: descartado por exigir início privilegiado do container, introduzir dependência extra e arriscar alterações silenciosas de posse em bind mounts do host (incluindo risco se alguém montar volumes incorretamente). Preferiu-se `USER lectoria:lectoria` puro + procedimento explícito e reversível de migração.
- **Reativação de Restic/Rclone ou introdução de backup em nuvem obrigatório**: descartado por decisão explícita do proprietário e para manter o Lectoria livre de dependências externas obrigatórias.
- **Downgrade automático de schema SQLite**: descartado por ser intrinsecamente destrutivo; backups de versão futura são rejeitados com mensagem clara.

---

## 9. Compatibilidade com Instalações Existentes

Preservados integralmente: Docker Compose genérico, instalação independente (venv/systemd via `deployment/lectoria.service`), Localhost HTTP, LAN HTTP, Tailscale privado HTTP, Tailscale Serve HTTPS, reverse proxy HTTPS, múltiplas fontes de biblioteca, backup/restauração, atualizador Docker (`deployment/docker-auto-update.sh`), healthcheck `/api/health` e leitores PDF/EPUB. Zero dependência de domínio, VPS, Tailscale ou caminhos particulares do proprietário.

---

## 10. Resultados dos Testes

- **Suíte local completa (`pytest -q`, Python 3.14.9 + Playwright Chromium)**: **`395 passed, 2 skipped, 0 failed, 3 warnings in 582.23s (0:09:42)`**
  - Inclui 100% da suíte anterior (389 testes unitários/API + 56 E2E Playwright) + 6 novos testes da Fase 11.4 (`tests/test_v192_backup_restore_and_hardening.py`: restauração sintética ponta a ponta, isolamento entre usuários, invalidação de sessões, todos os cenários de falha/segurança de SQLite e `covers.tar.gz`, arquivos de livros ausentes e invariantes de hardening/portabilidade).
  - Os 3 warnings são avisos legados pré-existentes de deprecação do Starlette TestClient (`httpx` / `per-request cookies`), sem relação com a Fase 11.4.
- **Testes Docker reais (`test_v192_docker_auto_update_integration.py` e `test_v192_docker_hardening_integration.py`)**: **`2 skipped`** localmente por ausência de Docker Engine neste host (explicitamente **não** declarados como aprovados localmente; serão executados integralmente no runner `ubuntu-latest` do GitHub Actions na validação remota).

---

## 11. Procedimento Privado de Recuperação Real Isolada (NÃO EXECUTAR SEM AUTORIZAÇÃO)

Para validar funcionalmente o backup real `/srv/nosreme/lectoria/backups/2026-11-25T19-14-08Z` (`41 MB`, Schema `13`, `238` livros, `5` usuários, `213` capas) na própria VPS **utilizando exatamente o commit validado no CI** (sem alterar `:stable`, sem tocar em produção, sem duplicar a biblioteca e sem exposição pública):

### 11.1 Execução isolada (build a partir do commit validado + restauração + instância em `127.0.0.1:18765`)
```bash
# EXECUTAR APENAS COM AUTORIZACAO EXPRESSA DO PROPRIETARIO NA VPS
(
set -euo pipefail

COMMIT_SHA="<COMMIT_VALIDADO_NO_CI>"
SNAP_DIR="/srv/nosreme/lectoria/backups/2026-11-25T19-14-08Z"
BOOKS_DIR="/srv/nosreme/lectoria/books"
DRILL_ID="drill-$(date -u +%Y%m%dT%H%M%SZ)"
ISO_DIR="/tmp/lectoria-restore-${DRILL_ID}"
DRILL_IMG="lectoria-restore-drill:${DRILL_ID}"
DRILL_CTR="lectoria-restore-${DRILL_ID}"

echo "=== [1] HASHES DA ORIGEM E SAUDE DA PRODUCAO ANTES ==="
PROD_BEFORE=$(sudo docker inspect lectoria --format 'ID={{.Id}} HEALTH={{.State.Health.Status}} RESTARTS={{.RestartCount}}')
echo "PROD_BEFORE=${PROD_BEFORE}"
DB_SHA_BEFORE=$(sudo sha256sum "${SNAP_DIR}/lectoria.db" | awk '{print $1}')
TAR_SHA_BEFORE=$(sudo sha256sum "${SNAP_DIR}/covers.tar.gz" | awk '{print $1}')
echo "SNAP_DB_SHA_BEFORE=${DB_SHA_BEFORE}"
echo "SNAP_TAR_SHA_BEFORE=${TAR_SHA_BEFORE}"

echo "=== [2] BUILD ISOLADO DA IMAGEM CANDIDATA (SEM ALTERAR :stable) ==="
mkdir -p "${ISO_DIR}/src" "${ISO_DIR}/data" "${ISO_DIR}/backups"
git clone --depth 1 --branch refactor/v1.9.2-backup-and-container-hardening https://github.com/Nosremekk/Lectoria.git "${ISO_DIR}/src"
git -C "${ISO_DIR}/src" rev-parse HEAD
# Respeita 1 vCPU / 3.8 GiB RAM da VPS (build enxuto em 1 thread)
sudo docker build --build-arg APP_VERSION=1.9.1 --build-arg VCS_REF="${COMMIT_SHA}" -t "${DRILL_IMG}" "${ISO_DIR}/src"
sudo chown 1000:1000 "${ISO_DIR}/data" "${ISO_DIR}/backups"

echo "=== [3] RESTAURACAO ISOLADA COM A NOVA IMPLEMENTACAO (ORIGEM :ro) ==="
sudo docker run --rm \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -v "${SNAP_DIR}:/snapshot:ro" \
  -v "${ISO_DIR}/data:/target_data" \
  "${DRILL_IMG}" \
  python3 scripts/backup.py --restore /snapshot --target-dir /target_data --force

echo "=== [4] VALIDACAO SQLITE RESTAURADO (INTEGRIDADE, SCHEMA, CONTAGENS, SESSOES=0) ==="
sudo python3 - "${ISO_DIR}/data/lectoria.db" <<'PY'
import sqlite3, sys
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
cur = con.cursor()
print("INTEGRITY:", cur.execute("PRAGMA integrity_check").fetchall())
print("SCHEMA:", cur.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0])
for t in ("books", "users", "reading_progress", "notes", "highlights", "collections", "concepts", "reflections", "knowledge_links", "sessions"):
    print(f"COUNT_{t.upper()}:", cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
con.close()
PY
echo "RESTORED_COVERS_COUNT=$(ls -1 "${ISO_DIR}/data/covers" | wc -l)"

echo "=== [5] INSTANCIA ISOLADA NAO-ROOT (127.0.0.1:18765, /storage/books:ro) ==="
sudo docker run -d --name "${DRILL_CTR}" \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m --memory 512m \
  -p 127.0.0.1:18765:8765 \
  -e LECTORIA_LIBRARY_DIR=/storage/books \
  -e LECTORIA_DATA_DIR=/storage/data \
  -e LECTORIA_DB_PATH=/storage/data/lectoria.db \
  -e LECTORIA_COVERS_DIR=/storage/data/covers \
  -e LECTORIA_BACKUPS_DIR=/storage/backups \
  -e LECTORIA_SECURE_COOKIES=false \
  -e LECTORIA_CSP_MODE=report-only \
  -v "${ISO_DIR}/data:/storage/data" \
  -v "${ISO_DIR}/backups:/storage/backups" \
  -v "${BOOKS_DIR}:/storage/books:ro" \
  "${DRILL_IMG}"

sleep 4
echo "DRILL_UID=$(sudo docker exec "${DRILL_CTR}" id -u)"
echo "DRILL_HEALTH=$(curl -fsS http://127.0.0.1:18765/api/health)"

echo "=== [6] VERIFICACAO DE IMUTABILIDADE DA ORIGEM E PRODUCAO ==="
DB_SHA_AFTER=$(sudo sha256sum "${SNAP_DIR}/lectoria.db" | awk '{print $1}')
TAR_SHA_AFTER=$(sudo sha256sum "${SNAP_DIR}/covers.tar.gz" | awk '{print $1}')
[ "${DB_SHA_BEFORE}" = "${DB_SHA_AFTER}" ] && [ "${TAR_SHA_BEFORE}" = "${TAR_SHA_AFTER}" ] && echo "ORIGIN_SNAPSHOT_UNCHANGED=YES"
echo "PROD_AFTER=$(sudo docker inspect lectoria --format 'ID={{.Id}} HEALTH={{.State.Health.Status}} RESTARTS={{.RestartCount}}')"
echo "DRILL_CTR=${DRILL_CTR} DRILL_IMG=${DRILL_IMG} ISO_DIR=${ISO_DIR}"
)
```

### 11.2 Acesso privado via túnel SSH (sem alterar Tailscale Serve)
Na máquina local do proprietário:
```bash
ssh -N -L 18765:127.0.0.1:18765 lectoria-vps
```
Abrir `http://127.0.0.1:18765` para testar login, 238 livros, metadados manuais, progresso, anotações, Atlas, abertura de PDF/EPUB representativos e isolamento entre usuários (sem alterar senhas em produção).

### 11.3 Limpeza cirúrgica (sem `docker system prune`)
```bash
sudo docker rm -f "${DRILL_CTR}" && sudo docker rmi -f "${DRILL_IMG}" && sudo rm -rf "${ISO_DIR}"
```

---

## 12. Plano Conciso de Migração de Permissões e Integração em Produção (11 Passos — Aguardar Autorização)

> **Não fazer merge antes da migração de permissões**: o `lectoria-auto-update.timer` está ativo na VPS e os arquivos internos de `/srv/nosreme/lectoria/data/` pertencem atualmente a `root:root`.

1. **Pausar o timer**: `sudo systemctl stop lectoria-auto-update.timer`.
2. **Confirmar serviço inativo**: verificar `lectoria-auto-update.service` = `inactive` e `BLOCKED_EXISTS=NO`.
3. **Preservar configuração e permissões anteriores**: salvar cópia de `/srv/nosreme/lectoria/compose.yaml` e registrar `stat -c '%u:%g %a %n'` de `/srv/nosreme/lectoria/data` e seus arquivos internos em `/root/`.
4. **Criar backup preventivo**: `sudo systemctl start lectoria-backup.service` e validar `PRAGMA integrity_check = ok`.
5. **Preparar permissões estritamente necessárias**: parar o container (`docker compose stop lectoria`) e aplicar `sudo chown 1000:1000 /srv/nosreme/lectoria/data/lectoria.db* /srv/nosreme/lectoria/data/lectoria_pre_migration_*.db.bak` e `sudo chown -R 1000:1000 /srv/nosreme/lectoria/data/covers` (**nunca** alterar `/srv/nosreme/lectoria/books` e **nunca** usar `chmod 777`).
6. **Atualizar `/srv/nosreme/lectoria/compose.yaml`**: manter `/storage/*`, `mem_limit: 768m` e as 4 variáveis da Fase 11.3; adicionar `./books:/storage/books:ro`, `security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]`, `read_only: true`, `tmpfs: [/tmp:rw,noexec,nosuid,size=64m]`.
7. **Validar configuração**: `docker compose config --quiet` (sem recriar o container com a imagem antiga ainda).
8. **Merge autorizado do PR #3 em `main`** (sem criar tag).
9. **Publicação (`publish.yml`) e deploy controlado**: executar `sudo systemctl start lectoria-auto-update.service`, confirmar `UPDATE_SUCCESS`, `id -u == 1000`, `CapEff == 0`, `Health=healthy`, SQLite `integrity=ok` e contagens preservadas.
10. **Validação funcional**: HTTPS Tailscale Serve + checklist no navegador (login, PDF/EPUB, escrita SQLite, exportação Atlas).
11. **Reativação do timer**: `sudo systemctl start lectoria-auto-update.timer` e confirmação de `NO_EXECUTABLE_CHANGE`.
- **Recuperação em caso de falha**: manter o timer pausado, restaurar o `compose.yaml` anterior, fixar o digest imutável anterior (`ghcr.io/nosremekk/lectoria@sha256:1eac54772ffeb00e60aed00adb41b2597d7fd0950495fb299fe862e2f9fd51cd`) e, se necessário, restaurar a posse anterior (`root:root`) registrada no passo 3 — **nunca** restaurar o banco automaticamente.

---

## 13. Riscos Residuais

1. **Ausência de backup externo (off-site)**: com Restic/Rclone intencionalmente desabilitado, uma perda física total do servidor/disco impediria a recuperação se não houver cópia externa manual periódica de `backups/` e `books/`.
2. **Migração de posse em instalações legadas**: hosts com bind mounts pertencentes a `root:root` que atualizarem para a imagem não-root sem executar o plano da Seção 12 terão erro de permissão na escrita SQLite (capturado com segurança pelo *fail-closed* do atualizador em `blocked`).
3. **Validação real em Docker Engine e backup real da VPS**: os testes Docker reais (`test_v192_docker_hardening_integration.py`) aguardam execução no CI (`ubuntu-latest`), e o drill com o backup real da VPS aguarda autorização do proprietário.

---

## 14. Arquivos Modificados e Commits

- [`docs/v1.9.2/13-phase-11.3-http-security.md`](./13-phase-11.3-http-security.md) e [`docs/v1.9.2/05-implementation-roadmap.md`](./05-implementation-roadmap.md) — encerramento formal da Fase 11.3 (`PHASE_11_3_COMPLETED`) e atualização da Fase 11.4.
- [`backup_service.py`](../../backup_service.py) e [`scripts/backup.py`](../../scripts/backup.py) — validação estrutural/schema em `mode=ro`, extração segura de `covers.tar.gz`, proteção de sobrescrita, invalidação de sessões e restauração isolada de snapshots.
- [`Dockerfile`](../../Dockerfile), [`docker-compose.yml`](../../docker-compose.yml), [`docker-compose.example.yml`](../../docker-compose.example.yml) — usuário não-root `lectoria` (`1000:1000`) e hardening residual de container.
- [`docs/DEPLOYMENT.md`](../DEPLOYMENT.md) — consolidação dos procedimentos operacionais, cobertura de backup, permissões e recuperação.
- [`tests/test_v192_backup_restore_and_hardening.py`](../../tests/test_v192_backup_restore_and_hardening.py) e [`tests/test_v192_docker_hardening_integration.py`](../../tests/test_v192_docker_hardening_integration.py) — testes funcionais, de falha/segurança e de integração Docker real.

---

## 15. Pendências Estritamente Necessárias para a Release `v1.9.2`

1. Revisão final e validação remota desta branch no GitHub Actions (`ubuntu-latest`), executando integralmente os 2 testes Docker reais (`docker-auto-update` + `docker-hardening`).
2. (Opcional/recomendado antes do deploy na VPS) Execução autorizada do drill isolado com o backup real da VPS (Seção 11) e migração de posse `1000:1000` em `data/` e `backups/` antes do deploy da imagem não-root (Seção 12).
3. Fase 11.5: bump coordenado de `APP_VERSION` para `1.9.2` (`config.py`, HTML `?v=1.9.2`, `CHANGELOG.md`), merge em `main` e publicação da tag imutável `v1.9.2`.
