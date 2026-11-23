# Lectoria v1.9.0 — Auditoria de Infraestrutura e Compatibilidade (Fase 8)

- **Data**: 2026-11-22
- **Branch**: `feature/v1.9.0-connected-knowledge`
- **Versão da aplicação no código (`config.APP_VERSION`)**: `1.8.0` (inalterada nesta fase)
- **Schema SQLite (`database.SCHEMA_VERSION`)**: `13` (inalterado nesta fase)
- **Escopo**: Etapa A (diagnóstico e auditoria local) e Etapa B (otimizações locais reversíveis e artefatos candidatos). **Etapa C (VPS/produção) NÃO autorizada e NÃO executada.**

---

## 1. Ambiente Local Auditado e Limitações de Execução

| Propriedade | Valor observado localmente |
| :--- | :--- |
| **Sistema Operacional** | Fedora Linux (`Linux-7.3.3-200.fc44.x86_64`) |
| **CPU** | AMD Ryzen 5 5600GT (12 núcleos lógicos) |
| **Memória RAM / Swap** | 13.843,1 MB RAM total / 8.192,0 MB swap |
| **Python** | `3.14.9` (`.venv`) |
| **SQLite** | `3.53.4` (`ENABLE_FTS5=True`, `THREADSAFE=1`, `MUTEX_PTHREADS`, `MAX_VARIABLE_NUMBER=32766`) |
| **Docker / Podman / `systemd-run`** | **Não instalados neste host local** |

### Limitações Explícitas
1. **Sem runtime de contêiner local (`docker`/`podman`)**: não foi possível construir a imagem `python:3.12-slim` nem validar diretamente `ENABLE_FTS5` dentro do binário SQLite empacotado no contêiner Debian/Bookworm da imagem oficial.
2. **Sem `systemd-run` / cgroups isolados**: não foi possível impor tetos reais de memória cgroup (`512M`, `768M`, `1G`, `1.5G`) em execução; a análise de dimensionamento de RAM é derivada do consumo real observado (`VmRSS`, `VmHWM` e `tracemalloc`) no processo Uvicorn (`--workers 1`) sob carga concorrente.
3. **Sem acesso à VPS de produção**: nenhuma verificação ou modificação foi executada no servidor Hostinger (Ubuntu 24.04, 4 GB RAM, Caddy, UFW, Tailscale, Restic/Rclone). Todo o roteiro de inspeção da VPS encontra-se classificado em [29-production-readiness-and-rollback.md](29-production-readiness-and-rollback.md).

---

## 2. Auditoria de `Dockerfile`, `docker-compose.yml` e Workflows de Publicação

Foram inspecionados `Dockerfile`, `docker-compose.yml`, `docker-compose.example.yml`, `.dockerignore`, `.github/workflows/ci.yml` e `.github/workflows/publish.yml` (nenhum deles foi alterado na Fase 8):

### Achados Críticos em Infraestrutura de Contêiner
1. **Healthcheck quebrado no `docker-compose.yml` (linha 25) e `docker-compose.example.yml` (linha 20)**:
   - O `Dockerfile` (linha 66) consulta `http://127.0.0.1:8765/api/health` (sem autenticação, zero I/O).
   - Entretanto, `docker-compose.yml` sobrescreve o `healthcheck` para consultar `http://127.0.0.1:8765/api/books`, que exige sessão autenticada (`HTTP 401 Unauthorized`). Em qualquer instância com usuários cadastrados, o contêiner é marcado permanentemente como **`unhealthy`**.
2. **Tag de imagem inconsistente (`:latest` vs `:stable`)**:
   - `docker-compose.yml` (linha 3) referencia `ghcr.io/nosremekk/lectoria:latest`.
   - Porém `.github/workflows/publish.yml` (linhas 91–94) publica apenas `:vX.Y.Z`, `:X.Y.Z` e `:stable` — nunca `:latest`.
3. **Execução como `root` no `Dockerfile`**:
   - O `Dockerfile` não define diretiva `USER`, executando o processo Uvicorn como `root` dentro do contêiner. Um patch candidato não-root (`appuser` UID 1000) com atenção à posse dos volumes `lectoria_data`/`lectoria_backups` está documentado em `29-production-readiness-and-rollback.md`.
4. **Exposição de porta em `0.0.0.0` e ausência de limites/rotação**:
   - `docker-compose.yml` expõe `"8765:8765"` em todas as interfaces (`0.0.0.0`), em vez de `"127.0.0.1:8765:8765"` atrás do Caddy.
   - Não há `mem_limit`, `mem_reservation`, `cpus`, `pids_limit`, `security_opt: [no-new-privileges:true]`, `init: true` nem rotação de logs (`json-file` com `max-size`/`max-file`).
   - `start_period: 10s` é apertado para o boot inicial da v1.9.0 quando houver migração v12 → v13 + backfill FTS5 + `scan_library()` síncrono no lifespan; recomendado `30s`.
5. **Dependência de `python:3.12-slim` e FTS5**:
   - O Debian Bookworm empacota SQLite 3.40+ com `ENABLE_FTS5` habilitado por padrão, mas a verificação explícita dentro da imagem publicada permanece item obrigatório do checklist de Etapa C antes de promover a v1.9.0.

---

## 3. Auditoria do Servidor ASGI (Uvicorn) e Event Loop

### Diagnóstico (Etapa A)
- O comando de produção (`Dockerfile`) executa Uvicorn com **`--workers 1`** — decisão correta para SQLite WAL em VPS de 4 GB (evita multiplicação de RAM, contention de escrita entre múltiplos processos e inconsistência de estado em memória como rate limiting).
- **Gargalo crítico descoberto**: 178 rotas em `main.py` estavam declaradas como `async def` contendo exclusivamente chamadas síncronas bloqueantes (SQLite, `zipfile`, `shutil`, `fitz`/PyMuPDF), sem nenhum `await`. Em FastAPI/Starlette, rotas `async def` executam diretamente na thread do *event loop* do Uvicorn.
- **Impacto medido antes da otimização**: durante uma única exportação `markdown_zip` (~546 ms), `/api/health` saltou de **0,712 ms** para **495,468 ms** (degradação de ~696×), e 6 exportações simultâneas serializaram completamente no event loop (`{'200': 6}`), impedindo que `BoundedSemaphore(4)` rejeitasse excesso com `HTTP 503`.

### Correção Aplicada (Etapa B)
- **`main.py`**: convertidas 174 rotas bloqueantes sem `await` de `async def` para `def` (despachadas automaticamente para o threadpool AnyIO do Starlette); nas 9 rotas `async def` com `await` (5 de upload/progresso/importação + 4 da fila assíncrona `metadata/queue/*` que agendam `asyncio.Task` no event loop), chamadas de serviço bloqueantes foram encapsuladas em `await run_in_threadpool(...)`. `/api/health` permaneceu `async def` puro (zero I/O).
- **`auth_service.py`**: adicionado `threading.Lock` (`_RATE_LIMIT_LOCK`) e poda de entradas expiradas (`_prune_expired_failed_logins_locked` acima de 1.000 chaves) para garantir *thread-safety* com múltiplas threads no threadpool.
- **Resultado pós-otimização**: `/api/health` durante exportação caiu de **495,468 ms** para **16,889 ms** (29,3× menor), e 6 exportações simultâneas passaram a executar em paralelo respeitando o semáforo (`{'200': 4, '503': 2}`).

---

## 4. Auditoria SQLite, Modo WAL, PRAGMAs, FTS5 e Planos de Execução

### PRAGMAs Efetivos Verificados (`database.py`)
- `journal_mode = wal`, `synchronous = 1 (NORMAL)`, `foreign_keys = 1`, `busy_timeout = 30000`, `cache_size = -2000` (~2 MB/conexão), `wal_autocheckpoint = 1000`, `mmap_size = 0`, `page_size = 4096`.
- Mediu-se o overhead de `PRAGMA journal_mode=WAL;` por conexão em `get_connection()`: 434,4 ms vs 432,1 ms em 500 conexões (0,004 ms/conexão — irrelevante). Mantido inalterado.

### Consultas Auditadas (`EXPLAIN QUERY PLAN`) e Gargalos Resolvidos
1. **`list_reflections()` (`knowledge_service.py`)**: chamava `_format_reflection_row(r, cursor)` com consulta por linha — **2.004 SQLs** para 1.000 reflexões. Corrigido com `COUNT(*)` + `LIMIT/OFFSET` em SQL no caminho padrão e hidratação em lote de `primary_book_id` apenas da página atual (**6 SQLs** — redução de 334×).
2. **`list_concepts()` (`knowledge_service.py`)**: carregava todos os conceitos em memória antes de paginar. Corrigido com `COUNT(*)` + `LIMIT/OFFSET` em SQL quando não há filtro textual/etiqueta (mantendo NFD/diacríticos em Python quando há busca).
3. **`build_atlas_export_snapshot()` (`knowledge_export_service.py`)**: executava `_check_book_accessible_row` em loop sobre livros já filtrados — **2.007 SQLs**. Removido loop redundante e aplicadas consultas `IN (...)` em blocos de 500 IDs (**8 SQLs** — redução de 250×).
4. **`search_knowledge()` (`knowledge_service.py`)**: hidratava cada candidato FTS5 individualmente (N+1) e gerava snippets para todos os candidatos antes de paginar. Reescrito com hidratação em lote por `entity_type` (em blocos de 500) e formatação de snippets adiada apenas para a fatia `[offset : offset + limit]`.
5. **`get_knowledge_graph()` e `get_atlas_overview_summary()` (`knowledge_service.py`)**: cláusulas `OR` entre `source` e `target` impediam o uso direto dos índices compostos. Reescritas com `UNION ALL` (Hop 1), `UNION` por tipo em blocos de 400 IDs (Hop 2) e `EXISTS`/contagens separadas por `source` e `target`, confirmando `SEARCH knowledge_links USING INDEX idx_klinks_user_source` e `idx_klinks_user_target` no `EXPLAIN QUERY PLAN`.
6. **`library.py` (`get_books`)**: cláusulas `IN (...)` de coleções, categorias, assuntos e leituras coletivas agora fatiadas em blocos de 500 IDs (seguro mesmo acima de milhares de livros).

### Observação Estrutural sobre Triggers FTS5 (`SCHEMA_VERSION = 13`)
- Os triggers `trg_*_fts` executam `DELETE FROM knowledge_fts WHERE entity_type = ? AND entity_id = ?` sobre colunas `UNINDEXED`. Em medições controladas, a escrita unitária escala linearmente: **0,23 ms** (1k linhas FTS5), **1,87 ms** (10k linhas) e **10,20 ms** (50k linhas). Aceitável para v1.9.0 sem alterar o Schema v13; registrado como candidato para otimização futura em `v1.11.0` (uso de tabela auxiliar `entity -> fts_rowid`).

---

## 5. Auditoria de Backup, Restauração e Consistência WAL

- **`backup_service.py`**:
  - `create_backup()` utiliza a SQLite Online Backup API (`sqlite3.Connection.backup`), segura em modo WAL. Adicionado fechamento garantido com `try...finally` em `create_backup()` e `verify_sqlite_integrity()`.
  - `restore_backup()` anteriormente criava `lectoria_pre_restore.db.bak` com `shutil.copy2` (inseguro se houver páginas pendentes no `-wal`). Corrigido para usar `sqlite3.Connection.backup()` com fallback para `shutil.copy2` apenas caso o banco atual esteja corrompido/ilegível.
- **Migração v12 → v13 (`database.py`)**:
  - Validada em testes sintéticos com dados históricos v1.8.0: gera backup pré-migração íntegro (`lectoria_pre_migration_v12_to_v13.db.bak`), aplica migração transacionalmente, faz backfill FTS5 e realiza rollback limpo sob interrupção ou `OperationalError: database or disk is full`.

---

## 6. Auditoria de Frontend e Consumo de Rede

- **Achado (Etapa A)**: na carga inicial de `/` (aba Descoberta, 200 livros), `static/js/app.js` disparava 7 chamadas API (308,6 KB), com duplicatas em `/api/discovery/shelves` (2×) e `/api/taxonomy/tree` (2×), além de carregar `/api/books` (233,6 KB) antecipadamente sem necessidade na aba Descoberta ou Atlas.
- **Correção (Etapa B)**: `setMainTab` recebeu `options.skipLoad`, `initAuth` passou a carregar `collections` + `taxonomy/tree` uma única vez e somente os dados da aba ativa, e `loadShelves()` recebeu `AbortController`.
- **Resultado**: 4 chamadas API na carga inicial, **zero duplicatas** e payload inicial reduzido de **308.682 B para 37.662 B (-87,8%)**.
