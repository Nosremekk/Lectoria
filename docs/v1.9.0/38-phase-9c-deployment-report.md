# Lectoria v1.9.0 — Relatório Final de Publicação e Deploy em Produção (Fase 9.C)

- **Estado Final da Publicação**: **`DEPLOY_SUCCESS`**
- **Data / Hora do Deploy na VPS**: `2026-11-23 14:57:38 UTC` (via `lectoria-auto-update.timer`)
- **Versão em Produção Verificada**: **`1.9.0`** (`http://127.0.0.1:8765/api/health` e `/api/auth/status`)
- **Schema SQLite em Produção Verificado**: **`13`** (`PRAGMA integrity_check = ok`)

---

## 1. Linha do Tempo de Commits e Hotfixes de CI

| Commit | Branch | Descrição | Resultado no GitHub Actions |
| :--- | :--- | :--- | :--- |
| `92828e0` | `feature/v1.9.0-connected-knowledge` | Release Lectoria v1.9.0 (Atlas + otimizações Fase 8 + documentação) | Integrado em `main` via merge `--no-ff` |
| `c919050` | `main` (`tag: v1.9.0`) | `merge: release Lectoria v1.9.0` | **Falha preventiva na coleta (`exit code 2`)**: 13 arquivos `tests/e2e/test_v190_*.py` continham caminho absoluto `/home/vini/.gemini/antigravity/brain/...` (`PermissionError`). Nenhuma imagem publicada. |
| `8f2eb43` | `main` | `fix(ci): use portable tests/artifacts directory in v1.9.0 E2E tests` (criado `tests/e2e/artifact_utils.py`, atualizados 13 testes E2E e `.gitignore`) | **358/359 aprovados** (`488,55s`). Única falha: `test_v190_phase6_quantitative_scale_and_explain_query_plan` exigia nome nominal `IDX_KLINKS` em consulta `OR` antiga, enquanto SQLite 3.45 (Ubuntu 24.04) escolheu `uq_knowledge_links_tuple (user_id=?)`. Nenhuma imagem publicada. |
| **`9ff89cd`** (`9ff89cd3c45e1be0eec969131950e381ec78a767`) | **`main`** | **`fix(ci): validate indexed UNION ALL graph plan across SQLite versions`** (alinhado o `EXPLAIN QUERY PLAN` à consulta real `UNION ALL` do serviço, validado em tabela vazia e com 50.000 links antes e depois de `ANALYZE`) | **`CI` (#45195460278): `success`** · **`Publish Docker Image (GHCR)` (#45195460137): `success`** |

> **Nota sobre a tag `v1.9.0`**: conforme instrução expressa, a tag `v1.9.0` existente não foi movida nem recriada; a imagem `ghcr.io/nosremekk/lectoria:stable` (e `:v1.9.0` / `:1.9.0` geradas pelo workflow a partir de `config.APP_VERSION = "1.9.0"` no commit `9ff89cd`) foi publicada com sucesso pelo run `#45195460137`.

---

## 2. Resultado dos Workflows GitHub Actions e Imagem Docker

- **`CI`** — Run [`#45195460278`](https://github.com/Nosremekk/Lectoria/actions/runs/45195460278): **`completed / success`** (Python `3.12`, **359/359 testes aprovados**).
- **`Publish Docker Image (GHCR)`** — Run [`#45195460137`](https://github.com/Nosremekk/Lectoria/actions/runs/45195460137): **`completed / success`**:
  - Step 5 (`Verify Python bytecode compilation`): `success`
  - Step 6 (`Run full automated test suite` — 359 testes): `success`
  - Step 10 (`Build and push Docker image` — `ghcr.io/nosremekk/lectoria:v1.9.0`, `:1.9.0`, `:stable`): `success`
  - Step 11 (`Validate published GHCR image and /api/health` — smoke test em container isolado `python:3.12-slim` descartável inicializando Schema v13 + `knowledge_fts` FTS5 `unicode61 remove_diacritics 2` e validando `/api/health` `version == 1.9.0`): `success`.
- **Digest Docker**: registrado nos logs autenticados do Step 11 (`Published digest: ...`) do run `#45195460137` no GitHub Actions (não legível sem autenticação via API pública e sem `docker.sock` na VPS).

---

## 3. Validação Pós-Deploy na VPS (`srv2137600`) — Somente-Leitura e Não Destrutiva

| Verificação | Evidência Real Observada na VPS (`2026-11-23 14:59 UTC`) | Status |
| :--- | :--- | :--- |
| **`/api/health`** | `{"status":"healthy","app_name":"Lectoria","version":"1.9.0"}` | **APROVADO** |
| **`/api/auth/status`** | `{"authenticated":false,"has_admin":true,"user":null,"setup_required":false,"version":"1.9.0"}` | **APROVADO** |
| **Frontend HTML & Atlas** | `data-app-version="1.9.0"`, `/static/js/knowledge.js?v=1.9.0`, `id="tab-nav-atlas"` presentes em `/` | **APROVADO** |
| **Migração SQLite `v12 → v13`** | `schema_version = 13` · `v13_tables = 4` (`concepts`, `reflections`, `knowledge_links`, `knowledge_fts`) · `fts_rows = 9` (backfill automático FTS5 concluído) | **APROVADO** |
| **Integridade SQLite** | `PRAGMA integrity_check = ok` (verificação somente-leitura `mode=ro&immutable=1`) | **APROVADO** |
| **Backup pré-migração automático** | `/srv/nosreme/lectoria/data/lectoria_pre_migration_v12_to_v13.db.bak` (`5,5 MB`, criado em `Nov 23 14:57`) | **APROVADO** |
| **Preservação do acervo e capas** | `books_count = 238`, `covers = 37M`, `books = 6,9G` intactos | **APROVADO** |
| **Memória / OOM / Processos** | `UVICORN_COUNT = 1` · `memory.current = 73,9 MiB` / `768 MiB` · `memory.events: low 0 high 0 max 0 oom 0 oom_kill 0` | **APROVADO** |
| **Demais serviços da VPS** | `AdGuardHome (1)`, `caddy (1)`, `dockerd (1)`, `uvicorn (1)` operacionais | **APROVADO** |
| **Política de backups (Fase 8.D)** | `lectoria-backup.timer`: **`enabled` / `active`** (`NEXT: Tue 2026-11-24 03:00:00 UTC`); `nosreme-restic-backup.timer`: **`disabled` / `inactive`** (zero processos `restic`/`rclone`) | **APROVADO** |

---

## 4. Recomendações Finais Pós-Deploy (Uso Real)

1. **Verificação interativa pelo proprietário no navegador (Tailscale Serve)**: autenticar na sua conta real e testar rapidamente a abertura de um livro PDF/EPUB, a aba **Atlas**, a pesquisa FTS5 e o modal de exportação (sem criar dados descartáveis em produção).
2. **Opcional futuro**: caso deseje adotar as melhorias de `docker-compose.v190-candidate.yml` na VPS (`mem_limit: 1g`, `start_period: 30s`, `LECTORIA_SECURE_COOKIES: "true"`, rotação de logs), isso pode ser feito em uma janela operacional separada preservando integralmente os bind mounts `/storage/*`.
