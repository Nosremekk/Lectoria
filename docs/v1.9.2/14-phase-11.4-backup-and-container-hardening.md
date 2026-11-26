# Lectoria — Fase 11.4: Restauração de Backups e Hardening Residual da Infraestrutura

- **Status**: **`PHASE_11_4_COMPLETED`** *(PR #3 integrado em `main` em `acfc42364e6ed60d78b202191db1f97f0c2d05a8`, publicado em `:stable` com 400/400 testes e smoke test, implantado na VPS com migração restrita `root` → `1000:1000` em 8 s de indisponibilidade, validado técnica e funcionalmente e atualizador reativado com `NO_EXECUTABLE_CHANGE`; `APP_VERSION = "1.9.1"`, `SCHEMA_VERSION = 13`)*
- **Data**: `2026-11-25` (implementação) · `2026-11-26` (correção `data/covers/`, drill real aprovado, merge, deploy controlado e encerramento)
- **Branch / PR / Merge em `main`**: `refactor/v1.9.2-backup-and-container-hardening` @ `11ba9e5` → [PR #3](https://github.com/Nosremekk/Lectoria/pull/3) integrado em `main` no commit `acfc42364e6ed60d78b202191db1f97f0c2d05a8` (`2026-11-26T18:41:44Z`, sem tag Git nem release)

> **Validação real executada**: A restauração funcional a partir do backup real da VPS (`/srv/nosreme/lectoria/backups/2026-11-25T19-14-08Z`) foi executada em ambiente privado, descartável e isolado (`127.0.0.1:18765`, `UID 1000`, `--read-only --cap-drop ALL --dns 127.0.0.1`, tarefas agendadas desativadas no banco descartável), encontrando e corrigindo o bug real de prefixo `data/covers/` em `covers.tar.gz` (`11ba9e5`) e confirmando `212` capas na raiz de `covers/`, `238` livros, `5` usuários, `0` sessões antigas, imutabilidade da origem e aprovação manual via túnel SSH pelo proprietário.

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

## 10. Resultados dos Testes (Local e CI Remoto)

- **Suíte local completa (`pytest -q`, Python 3.14.9 + Playwright Chromium)**: `395 passed, 2 skipped, 0 failed, 3 warnings in 582.23s (0:09:42)` (pré-revisão) + `18 passed, 1 skipped, 0 failed in 1.29s` nos testes de backup/restauração/hardening após as correções da revisão direcionada.
- **CI remoto no GitHub Actions (`ubuntu-latest`, Python 3.12 + `-c constraints.txt` + Docker Engine real + Playwright Chromium)**:
  - **[Run #45698754259](https://github.com/Nosremekk/Lectoria/actions/runs/45698754259)** (`CI` · `push` @ `11ba9e5`): **`completed / success`** — **`400 passed, 0 skipped, 0 failed, 3 warnings in 818.00s (0:13:37)`**.
  - **[Run #45698770112](https://github.com/Nosremekk/Lectoria/actions/runs/45698770112)** (`CI` · `pull_request` @ `11ba9e5`): **`completed / success`** — **`400 passed, 0 skipped, 0 failed, 3 warnings in 832.10s (0:13:52)`**.
  - **Testes Docker reais aprovados em ambos os eventos**:
    - `tests/test_v192_docker_auto_update_integration.py::test_v192_docker_real_integration_end_to_end PASSED`
    - `tests/test_v192_docker_hardening_integration.py::test_v192_real_docker_nonroot_and_hardening_runtime PASSED` (verificou `id -u != 0`, `NoNewPrivs: 1`, `CapEff: 0000000000000000`, `/app` e `/app/books` somente-leitura, `/tmp` gravável e `noexec`, `/api/health`, `/api/auth/setup`, CSRF, Atlas + exportação JSON usando `/tmp`, `scripts/backup.py --backup`, `--verify` e `--restore --target-dir`).
  - **Regressão real do drill coberta**: `tests/test_v192_backup_restore_and_hardening.py::test_v192_extract_covers_normalizes_data_covers_prefix PASSED`.
  - **Suíte E2E Playwright (56/56)**: aprovada integralmente, incluindo `test_v192_csp_enforce_zero_violations_across_library_atlas_pdf_epub PASSED`.
  - **Natureza dos 3 warnings**: (1) `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated` em `tests/conftest.py:21`; (2 e 3) 2 `DeprecationWarning: Setting per-request cookies=<...>` em `tests/test_audit_v140.py::test_admin_queue_endpoints_require_admin` — todos legados e não bloqueantes.
  - **Publicação GHCR**: zero execuções de `publish.yml`; `:stable` permanece inalterado em `sha256:1eac54772ffeb00e60aed00adb41b2597d7fd0950495fb299fe862e2f9fd51cd`.

---

## 11. Procedimento Privado de Recuperação Real Isolada (NÃO EXECUTAR SEM AUTORIZAÇÃO)

Para validar funcionalmente o backup real `/srv/nosreme/lectoria/backups/2026-11-25T19-14-08Z` (`41 MB`, Schema `13`, `238` livros, `5` usuários, `213` capas) na própria VPS **utilizando exatamente o commit validado no CI** (sem alterar `:stable`, sem tocar em produção, sem duplicar a biblioteca e sem exposição pública):

### 11.1 Execução isolada (build a partir do commit validado + restauração + instância em `127.0.0.1:18765`)
```bash
# EXECUTAR APENAS COM AUTORIZACAO EXPRESSA DO PROPRIETARIO NA VPS
(
set -euo pipefail

COMMIT_SHA="7f16d5fbe63cc3c6f5eb510e87d3ececb3a14e4f"
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

## 12. Integração Controlada em Produção e Encerramento (`2026-11-26`)

- **Momento A (`PHASE_11_4_MOMENT_A_COMPLETED`)**: `lectoria-auto-update.timer` pausado (`inactive`), artefatos preservados em `/root/lectoria-phase114-prep-20261126T183733Z/` (`compose.yaml.orig` SHA-256 `2017f3a8...`, `permissions-inventory.tsv` SHA-256 `c03e4dd7...`, `compose.yaml.candidate` SHA-256 `5f3daa49...`), backup preventivo `/srv/nosreme/lectoria/backups/2026-11-26T18-37-33Z` validado (`integrity=ok`, `schema=13`).
- **Momento B (`PHASE_11_4_MOMENT_B_COMPLETED`)**:
  - PR #3 integrado em `main` (`acfc42364e6ed60d78b202191db1f97f0c2d05a8`); `publish.yml` ([Run #45877971972](https://github.com/Nosremekk/Lectoria/actions/runs/45877971972)): `400 passed`, smoke test `@digest` aprovado, `:stable` e `:sha-acfc423` promovidos para `sha256:130be101114b8845892a169a9a601744d78fd9cdbf849a2221ab440f1261a3fd` (`linux/amd64` `sha256:cd964910...`, config `sha256:0c69ab35...`, `:v1.9.1` e `:1.9.1` intactas em `sha256:da4dca9c...`).
  - **Deploy manual controlado por digest imutável** (necessário pela parada prévia para migração de posse SQLite, sem alterar `docker-auto-update.sh`): backup final `/srv/nosreme/lectoria/backups/2026-11-26T19-11-55Z`, parada controlada, inventário definitivo `permissions-inventory-stopped.tsv` (`222` itens migrados incluindo `-wal`/`-shm`, `0` symlinks, log de rollback `migrated-items.tsv` SHA-256 `29d732ff...`), ativação de `compose.yaml.candidate` e subida do container `8497625b252b5100d58e7c5a094006d2e59f868a362c2e65c7763fa7e0702a47` com **`DOWNTIME_SECONDS=8`**.
  - Validação técnica pós-deploy: `UID=1000`, `GID=1000`, `CapEff=0000000000000000`, `NoNewPrivs=1`, `ReadonlyRootfs=true`, `BOOKS_RW=false`, `MEM_BYTES=805306368`, `127.0.0.1:8765`, `tmpfs /tmp:rw,noexec,nosuid,size=64m`, `WRITABLE_PROBES_OK=YES`, `RESTARTS=0`, `BLOCKED=NO`, `PERM_ERRORS=0`, `DB_AND_COVERS_COUNTS_MATCH=YES` (`books=238`, `users=5`, `reading_progress=54`, `notes=22`, `highlights=1`, `collections=0`, `concepts=5`, `reflections=1`, `knowledge_links=6`, `covers=212`), backup pós-deploy compatível (`2026-11-26T19-12-05Z`, `POST_DEPLOY_BKP_OK=YES`) e HTTPS real Tailscale Serve validado.
- **Momento C (`PHASE_11_4_COMPLETED`)**:
  - `lectoria-auto-update.timer` reativado (`enabled/active`); execução de convergência `lectoria-auto-update.service` (`19:15:11–19:15:12 UTC`, `Result=success`, `ExecMainStatus=0`) confirmou `NO_EXECUTABLE_CHANGE` (`exec_fp=sha256:22c61bba...`), `CONTAINER_PRESERVED=YES`, `NO_EXTRA_BACKUP=YES`, `lectoria-backup.timer` `enabled/active`, `nosreme-restic-backup.timer` `disabled/inactive` e `BLOCKED_EXISTS=NO`.

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
