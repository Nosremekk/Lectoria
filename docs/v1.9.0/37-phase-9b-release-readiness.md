# Lectoria v1.9.0 — Relatório de Prontidão para Release (Fase 9.B)

- **Data**: 2026-11-23
- **Branch**: `feature/v1.9.0-connected-knowledge`
- **Versão Alinhada (`config.APP_VERSION`)**: **`1.9.0`**
- **Schema SQLite (`database.SCHEMA_VERSION`)**: **`13`**
- **Classificação Final**: **`CONDITIONAL GO`** *(repositório local 100% pronto para commit/merge e construção da imagem `v1.9.0`; publicação em produção na Fase 9.C condicionada exclusivamente ao Gate operacional pré-deploy abaixo)*

---

## 1. Arquivos Modificados e Atualizados na Fase 9.B

### Versionamento e Cache-Busting (`1.9.0`)
- `config.py` — `APP_VERSION = "1.9.0"`.
- `static/index.html` — `data-app-version="1.9.0"`, `style.css?v=1.9.0` e scripts `knowledge-api.js`, `book-knowledge.js`, `knowledge.js`, `app.js`, `shared_reading.js` com `?v=1.9.0`.
- `static/reader.html` — `data-app-version="1.9.0"`, `style.css?v=1.9.0` e scripts `knowledge-api.js`, `reader-knowledge.js`, `reader.js` com `?v=1.9.0`.
- `static/js/app.js` — `window.LECTORIA_FRONTEND_VERSION = "1.9.0"`.
- `tests/test_v151_social_contracts.py` — atualizado `test_v151_frontend_backend_version_alignment` para exigir `1.9.0` e incluir a verificação dos scripts do Atlas em `index.html` e `reader.html`.

### Documentação Canônica e Roadmap
- `CHANGELOG.md` — adicionada seção completa `## [1.9.0] - 2026-11-23` (Atlas, API, exportação, otimizações da Fase 8 e política de backups locais).
- `README.md` — atualizados título e Seção 1 com os recursos do Atlas v1.9.0, otimizações e política de backups.
- `AGENTS.md` — atualizado para `v1.9.0`, Schema SQLite `13` e mapa de módulos do Atlas (`knowledge_service.py`, `knowledge_export_service.py`, `knowledge*.js`, `reader-knowledge.js`, `book-knowledge.js`).
- `docs/ARCHITECTURE.md`, `docs/DATABASE.md`, `docs/API.md`, `docs/AUTHORIZATION.md`, `docs/FRONTEND.md`, `docs/READER.md` — sincronizados com a arquitetura v1.9.0.
- `docs/v1.9.0/08-roadmap.md` — atualizado com o status da Fase 9.B.
- `docs/v1.9.0/37-phase-9b-release-readiness.md` — este relatório.

### Infraestrutura Candidata (Local)
- `docker-compose.v190-candidate.yml` — reescrito para espelhar integralmente a VPS real verificada na Fase 8.C: bind mounts `./books:/storage/books`, `./data:/storage/data`, `./backups:/storage/backups`, variáveis `LECTORIA_*_DIR=/storage/*`, porta `"127.0.0.1:8765:8765"`, `mem_limit: 1g`, `mem_reservation: 256m`, `pids_limit: 128`, `init: true`, `no-new-privileges:true`, `LECTORIA_SECURE_COOKIES: "true"`, `healthcheck` `/api/health` (`start_period: 30s`), rotação de logs `json-file` (`10m`×3) e tag imutável `:v1.9.0` — **removidos volumes nomeados `/app/*` e `cpus: 1.5`**. `docker-compose.yml` genérico e `compose.yaml` remoto não foram alterados.

---

## 2. Validação Final Executada (Após Todas as Edições da Fase 9.B)

| Verificação | Comando | Resultado | Duração |
| :--- | :--- | :--- | ---: |
| **Alinhamento `APP_VERSION` / `SCHEMA_VERSION`** | `config.APP_VERSION == "1.9.0"` e `database.SCHEMA_VERSION == 13` | **APROVADO** | < 1s |
| **Compilação estática (`py_compile`)** | `*.py scripts/*.py tests/test_v190_*.py tests/e2e/test_v190_*.py tests/performance/*.py` | **APROVADO** (`exit 0`) | < 1s |
| **Suíte Unitária e de Integração** | `pytest --ignore=tests/e2e -q` | **`305 passed, 0 failed, 0 skipped`** *(3 warnings pré-existentes `Starlette TestClient`)* | `326,21s` |
| **Suíte E2E (Playwright / Chromium)** | `pytest tests/e2e -q` | **`54 passed, 0 failed, 0 skipped`** *(1 warning pré-existente)* | `232,48s` |
| **Total Consolidado** | Suítes completas pós-versionamento `1.9.0` | **`359 / 359 APROVADOS`** | `558,69s` |
| **Alinhamento Frontend ↔ Backend** | `test_v151_frontend_backend_version_alignment` (incluído nos 305) | **APROVADO (`1.9.0`)** | — |
| **Auditoria AST / Segurança / Zero N+1** | `tests/test_v190_phase8_infra_and_migration.py` (incluído nos 305) | **11 / 11 APROVADOS** | — |

**Inventário Git Final (sem commits/tags)**: branch `feature/v1.9.0-connected-knowledge`, `28` arquivos rastreados modificados, `78` arquivos novos não rastreados (incluindo este relatório).

---

## 3. Pendências Resolvidas vs Bloqueadores Restantes

### Resolvidas na Fase 9.B
- [x] `config.APP_VERSION = "1.9.0"`, `data-app-version="1.9.0"`, `LECTORIA_FRONTEND_VERSION = "1.9.0"` e cache-busting `?v=1.9.0` sincronizados e validados por teste.
- [x] `CHANGELOG.md`, `README.md`, `AGENTS.md` e documentação canônica `docs/*.md` atualizados.
- [x] `docker-compose.v190-candidate.yml` corrigido (removidos volumes nomeados `/app/*` e `cpus: 1.5`; preservados bind mounts `/storage/*` e `127.0.0.1:8765:8765`).
- [x] Reexecução integral dos **359 testes automatizados** com `0` falhas e `0` ignorados.

### Bloqueadores Operacionais Restantes (Exclusivos da Janela de Publicação — Fase 9.C)
1. **Risco de Deploy Automático no Workflow (`.github/workflows/publish.yml` + `lectoria-auto-update.timer`)**:
   - O workflow `publish.yml` dispara em qualquer `push` em `main` ou tag `v*` e publica simultaneamente `:v1.9.0`, `:1.9.0` e **`:stable`**.
   - Na VPS, `lectoria-auto-update.timer` verifica `:stable` a cada **15 minutos**.
   - **Ação obrigatória antes do merge em `main`**: pausar `lectoria-auto-update.timer` na VPS (`sudo systemctl stop lectoria-auto-update.timer`) **ou** fixar temporariamente `image: ghcr.io/nosremekk/lectoria:v1.8.0` no `/srv/nosreme/lectoria/compose.yaml`.
2. **Backup Pré-Upgrade (Política Atualizada na Fase 8.D)**:
   - Confirmar backup SQLite local íntegro (`lectoria-backup.service`) **e** realizar **cópia externa manual** de `lectoria.db`, `data/covers/` e `/srv/nosreme/lectoria/compose.yaml` para fora da VPS antes do upgrade.
3. **Validação Isolada de `ENABLE_FTS5` na Imagem Final `v1.9.0`**:
   - Executar na VPS após o `publish.yml` construir `ghcr.io/nosremekk/lectoria:v1.9.0` (sem montar volumes de produção):
     ```bash
     sudo docker pull ghcr.io/nosremekk/lectoria:v1.9.0
     sudo docker run --rm ghcr.io/nosremekk/lectoria:v1.9.0 python3 -c "import sqlite3; c=sqlite3.connect(':memory:'); opts={r[0] for r in c.execute('PRAGMA compile_options;')}; assert 'ENABLE_FTS5' in opts, opts; c.execute(\"CREATE VIRTUAL TABLE t USING fts5(x, tokenize='unicode61 remove_diacritics 2')\"); print('SQLite:', sqlite3.sqlite_version, 'FTS5 OK')"
     ```

---

## 4. Plano de Publicação Controlada (Fase 9.C) e Rollback

### Sequência Segura da Fase 9.C (Somente Após Autorização Explícita)
1. **Na VPS (antes de tocar em `main`)**:
   - Pausar `lectoria-auto-update.timer` (evita atualização automática em até 15 min).
   - Executar `sudo systemctl start lectoria-backup.service` e confirmar `SQLite: backup íntegro.`.
   - Copiar manualmente para sua máquina local: o último backup SQLite, `data/covers/` e `/srv/nosreme/lectoria/compose.yaml`.
2. **No Git / GitHub**:
   - Revisar `git status`, criar commits organizados na branch `feature/v1.9.0-connected-knowledge`, fazer merge em `main`, criar tag `v1.9.0` e aguardar `.github/workflows/publish.yml` concluir 100% verde no GHCR.
3. **Na VPS (validação e upgrade supervisionado)**:
   - Validar `ENABLE_FTS5` na imagem `ghcr.io/nosremekk/lectoria:v1.9.0` isolada (comando da Seção 3).
   - Atualizar `/srv/nosreme/lectoria/compose.yaml` preservando **integralmente** `./books:/storage/books`, `./data:/storage/data`, `./backups:/storage/backups` e `"127.0.0.1:8765:8765"`, apontando para `ghcr.io/nosremekk/lectoria:v1.9.0` (com `mem_limit: 1g`, `start_period: 30s`, rotação de logs e `LECTORIA_SECURE_COOKIES: "true"`).
   - Executar `docker compose up -d`, acompanhar logs da migração `v12 → v13`, validar `http://127.0.0.1:8765/api/health` (`version: 1.9.0`) e testar acesso via Tailscale Serve (Biblioteca, Leitor PDF/EPUB e Atlas).
   - Somente após validação completa, decidir se retorna para `:stable` e reativa `lectoria-auto-update.timer`.

### Rollback Seguro (`v1.9.0 / Schema v13` → `v1.8.0 / Schema v12`)
- **Rollback Imediato** (antes de uso em produção): `docker compose down` → mover `lectoria.db` (e eventuais `-wal`/`-shm`) para cópia forense → restaurar `lectoria_pre_migration_v12_to_v13.db.bak` (ou backup manual pré-upgrade) para `lectoria.db` → fixar `ghcr.io/nosremekk/lectoria:v1.8.0` no `compose.yaml` → `docker compose up -d` → validar `/api/health` (`1.8.0`).
- **Rollback Tardio** (após criação de dados no Atlas): **nunca sobrescrever silenciosamente** — exportar primeiro os dados do Atlas (`/api/knowledge/export?format=json`) ou gerar backup consistente do banco v13 via SQLite Online Backup API antes de seguir os passos de restauração v12.

---

## 5. Veredito Final da Fase 9.B

**Classificação**: **`CONDITIONAL GO`** — o código, o versionamento `1.9.0`, a documentação canônica, o `docker-compose.v190-candidate.yml` e os **359/359 testes automatizados** estão 100% aprovados e prontos para a **Fase 9.C (Publicação Controlada)** mediante cumprimento das 3 salvaguardas operacionais da Seção 3.
