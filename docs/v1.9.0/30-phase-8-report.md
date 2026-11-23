# Lectoria v1.9.0 — Relatório de Conclusão da Fase 8

- **Classificação Final**: **`PHASE_8_COMPLETE_WITH_LIMITATIONS`**
- **Data**: 2026-11-22
- **Branch**: `feature/v1.9.0-connected-knowledge`
- **Versão da aplicação (`config.APP_VERSION`)**: `1.8.0` (inalterada conforme restrição)
- **Schema SQLite (`database.SCHEMA_VERSION`)**: `13` (inalterado conforme restrição)

---

## 1. Resumo Executivo

A Fase 8 executou integralmente a **Etapa A (diagnóstico e auditoria local)** e a **Etapa B (otimizações locais pontuais, testes de migração/infraestrutura, reexecução comparativa de benchmarks, arquivo candidato de Docker Compose e documentação de prontidão/rollback)**. A **Etapa C (execução na VPS de produção)** não estava autorizada e não foi executada.

A classificação é **`PHASE_8_COMPLETE_WITH_LIMITATIONS`** exclusivamente por duas restrições objetivas de ambiente/escopo:
1. O host local não possui `docker`, `podman` nem `systemd-run` instalados, impossibilitando a construção/execução real da imagem `python:3.12-slim` e testes reais de cgroup (`512M`/`768M`/`1G`/`1.5G`) nesta máquina.
2. A VPS de produção (Hostinger, Ubuntu 24.04, 4 GB RAM, Caddy, UFW, Tailscale, Restic/Rclone) não foi acessada nem modificada por proibição explícita de escopo.

---

## 2. Principais Gargalos Identificados e Resolvidos (Antes vs Depois)

| Gargalo Identificado (Etapa A) | Correção Aplicada (Etapa B) | Ganho Medido |
| :--- | :--- | :--- |
| **Event loop bloqueado no Uvicorn (`--workers 1`)** por 178 rotas `async def` síncronas (`main.py`) | Conversão de 174 rotas para `def` + `run_in_threadpool` nas 9 rotas `async def` com `await`/tasks assíncronas; `/api/health` mantido `async def` puro; `threading.Lock` + poda em `auth_service.py` | `/api/health` máx. durante exportação: **495,5 ms → 16,9 ms (-96,6%)**; 6× exports: `{'200': 6}` serializado → **`{'200': 4, '503': 2}`** paralelo |
| **N+1 severo em `list_reflections()`** (2.004 SQLs / 50,2 ms para 1k reflexões) | `COUNT(*)` + `LIMIT/OFFSET` em SQL no caminho padrão + hidratação em lote de `primary_book_id` | **50,20 ms → 2,96 ms (-94,1%)**; **2.004 → 6 SQLs** |
| **Carga integral em `list_concepts()`** (188,5 ms / 13,7 MB RAM em 10k conceitos) | `COUNT(*)` + `LIMIT/OFFSET` em SQL quando sem filtro textual/tag | **188,51 ms → 2,81 ms (-98,5%)** em 10k conceitos |
| **N+1 em `build_atlas_export_snapshot()`** (2.007 SQLs) | Remoção de loop redundante + consultas `IN (...)` em blocos de 500 | **2.007 → 8 SQLs (-99,6%)** |
| **N+1 e formatação antecipada em `search_knowledge()`** (1.041,8 ms em 2k candidatos) | Hidratação em lote por tipo + formatação de snippets adiada para a fatia paginada | **1.041,84 ms → 565,54 ms (-45,7%)** |
| **Cláusulas `OR` impedindo índices compostos** em `get_knowledge_graph()` e `get_atlas_overview_summary()` | `UNION ALL` (Hop 1), `UNION` por tipo em blocos (Hop 2), `EXISTS` separados | Grafo 2 saltos (50k links): **60,05 ms → 34,65 ms (-42,3%)**; `EXPLAIN QUERY PLAN` confirmado com `idx_klinks_user_source`/`target` |
| **Chamadas duplicadas e `/api/books` antecipado no carregamento inicial** (`static/js/app.js`) | `setMainTab(..., { skipLoad: true })`, carregamento apenas da aba ativa em `initAuth`, `AbortController` em `loadShelves()` | APIs iniciais `/`: **7 → 4**, duplicatas **2 → 0**, payload inicial **308,7 KB → 37,7 KB (-87,8%)** |
| **Safety backup em `restore_backup()` via `shutil.copy2`** (risco em modo WAL) | `sqlite3.Connection.backup()` com fallback seguro + fechamento `try...finally` em `backup_service.py` | Backup pré-restauração WAL-safe validado com `PRAGMA integrity_check == ok` |
| **Consultas `IN (...)` sem chunking** em `library.py` (`get_books`) | Chunking em blocos de 500 IDs | Proteção contra limites de parâmetros em acervos grandes |

---

## 3. Arquivos Criados e Modificados na Fase 8

### Arquivos Rastreados Modificados (Etapa B)
- `auth_service.py` — `threading.Lock` e poda de entradas expiradas no rate limiter em memória.
- `backup_service.py` — fechamento determinístico `try...finally` e safety backup WAL-safe via `sqlite3.Connection.backup()`.
- `knowledge_export_service.py` — chunking `_chunked(..., 500)` e eliminação do loop N+1 de verificação de livros.
- `knowledge_service.py` — paginação SQL em `list_concepts`/`list_reflections`, hidratação em lote + snippets adiados em `search_knowledge`, `UNION ALL`/`UNION` indexados em `get_knowledge_graph` e `EXISTS` separados em `get_atlas_overview_summary`.
- `library.py` — chunking de 500 IDs nas consultas `IN (...)` de `get_books()`.
- `main.py` — conversão de 174 rotas síncronas de `async def` para `def` + `run_in_threadpool` nas 9 rotas `async def` com `await`/tasks assíncronas.
- `static/js/app.js` — eliminação de chamadas duplicadas e carga preguiçosa por aba ativa na inicialização.

### Novos Arquivos Criados na Fase 8
- `tests/performance/benchmark_v190_atlas.py` — suíte reproduzível de benchmarks backend/SQLite/Uvicorn.
- `tests/performance/profile_v190_frontend.py` — profiler Playwright/Chromium e captura das 11 telas.
- `tests/test_v190_phase8_infra_and_migration.py` — 11 testes automatizados de migração v12 → v13, FTS5, backup/restore WAL, zero N+1, `EXPLAIN QUERY PLAN`, thread-safety, AST de `main.py` e concorrência Uvicorn.
- `docker-compose.v190-candidate.yml` — proposta candidata de produção (`:stable`, `/api/health`, `127.0.0.1:8765`, `mem_limit: 1g`, `cpus: 1.5`, rotação de logs, `no-new-privileges`).
- `docs/v1.9.0/27-infrastructure-audit.md`
- `docs/v1.9.0/28-performance-benchmarks.md`
- `docs/v1.9.0/29-production-readiness-and-rollback.md`
- `docs/v1.9.0/30-phase-8-report.md`
- Atualização de `docs/v1.9.0/08-roadmap.md`.

### Arquivos Deliberadamente Preservados Intocados
- `Dockerfile`, `docker-compose.yml`, `docker-compose.example.yml`, `.github/workflows/*`, `config.py` (`APP_VERSION = "1.8.0"`), `database.py` (`SCHEMA_VERSION = 13`).

---

## 4. Validação Visual (11 Capturas Inspecionadas)

Todas as 11 capturas pós-otimização foram inspecionadas visualmente em Chromium headless (desktop `1366×860`, tablet `768×1024`, mobile `360×800`, tema claro e tema escuro): biblioteca, leitor PDF + caderno, leitor EPUB, Atlas visão geral, pesquisa, mapa SVG, editor de reflexões, modal de exportação, responsivo tablet/mobile e tema escuro. **Zero overflow horizontal, zero `pageerror` e layout preservado integralmente.**

---

## 5. Handoff para Próximas Etapas

1. **Etapa C (VPS — requer autorização explícita do proprietário)**: executar o runbook read-only em `29-production-readiness-and-rollback.md`, confirmar FTS5 na imagem `python:3.12-slim` publicada e validar os limites reais do host de 4 GB.
2. **Fase 9 (Validação Final e Release `v1.9.0`)**: alinhar `APP_VERSION = "1.9.0"` e cache-busting `?v=1.9.0`, atualizar documentação canônica (`AGENTS.md`, `README.md`, `CHANGELOG.md`, `docs/*.md`), aprovar incorporação dos ajustes do `docker-compose.v190-candidate.yml` e executar bateria final antes de commit/merge/tag.
