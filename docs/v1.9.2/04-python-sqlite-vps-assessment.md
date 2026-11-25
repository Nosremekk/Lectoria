# Lectoria — Fase 11.1: Avaliação Comparativa Python/FastAPI, SQLite, Frontend e Capacidade da VPS

- **Escopo**: Análise fundamentada em evidências reais da VPS (`1 vCPU AMD EPYC 9355P`, `3,8 GiB RAM`, `238 livros`, `5 usuários`, `lectoria.db = 6,5 MiB`) e do código-fonte da `v1.9.1`.
- **Restrições respeitadas**: Nenhuma alteração de arquitetura, workers, PRAGMAs ou Compose aplicada nesta fase.

---

## 1. Avaliação do Backend Python / FastAPI (Parte IV — Seção 6)

### Evidência objetiva
- Na loopback da VPS (`127.0.0.1:8765`), o backend Python/FastAPI responde em **`1,6–4,9 ms`** (`/api/health`: `1,91 ms`; `/api/auth/status`: `4,87 ms`; `/`: `3,03 ms`).
- Desde a Fase 8 (`v1.9.0`), 174 rotas síncronas foram convertidas para `def` (executadas automaticamente no threadpool AnyIO do Starlette) e as 9 rotas `async def` com I/O utilizam `run_in_threadpool`, mantendo `/api/health` em `async def` puro (`< 17 ms` mesmo sob exportação ZIP).
- Consumo real de CPU do processo `uvicorn` na VPS: **`0,3%`**, RSS **`81,6 MiB`**.

### Comparação das Alternativas (A–F)

| Alternativa | Descrição | Vantagens | Riscos / Custo | Parecer Baseado em Evidências |
| :--- | :--- | :--- | :--- | :--- |
| **A. Manter arquitetura atual e otimizar funções** | Adicionar `GZipMiddleware`, cabeçalhos de cache imutável em `/static/*`, ajustar cache de capas e otimizar serializações/queries pontuais. | Ganho imediato de 60–80% no tráfego de rede; risco mínimo; zero aumento de RAM. | Requer cuidado para não comprimir respostas já comprimidas (PDF/EPUB/JPEG/ZIP). | **RECOMENDADA (Prioridade Máxima)** |
| **B. Threadpool para operações bloqueantes** | Já aplicado amplamente na Fase 8 (`def` + `run_in_threadpool`). Revisar as 9 rotas `async def` restantes (`update_progress`, uploads de capa, importação). | Impede bloqueio do event loop em I/O pontual. | Baixo risco; ganho incremental (maioria já convertida). | **RECOMENDADA (Revisão Cirúrgica na Fase 12.2)** |
| **C. Tarefas assíncronas / processos separados** | Mover varredura pesada de acervo ou extração em lote de capas/PDFs para worker/fila dedicada (já existe `background_enricher.py`). | Evita disputa de CPU durante operações administrativas em lote. | Aumenta complexidade operacional; em 1 vCPU processos extras ainda competem pelo mesmo núcleo. | **OPCIONAL (Somente se profiling na Fase 12.1 mostrar bloqueio real)** |
| **D. Aumentar número de workers Uvicorn (`--workers > 1`)** | Rodar 2+ processos Uvicorn. | Paralelismo de CPU em máquinas multi-core. | **Contraproducente nesta VPS**: (1) a VPS tem apenas **1 vCPU**; (2) dobra o consumo de RAM (`81 → ~165+ MiB`); (3) **quebra** estado em memória por processo (`rate limiter` em `auth_service.py`, `BoundedSemaphore(4)` em `knowledge_export_service.py`, `background_enricher` e tarefas agendadas); (4) aumenta contenção de escrita SQLite (`database is locked`). | **NÃO RECOMENDADA** |
| **E. Bibliotecas nativas específicas (ex.: `orjson`, `pymupdf`)** | Substituir `json` padrão por `orjson` ou otimizar extração PDF. | Baixo esforço para `orjson` caso serialização JSON de listas grandes apareça no profiling. | Adiciona dependência binária nativa no Dockerfile. | **CONDICIONAL** (apenas se Fase 12.1 comprovar tempo relevante de serialização) |
| **F. Reescrita de componentes em outra linguagem (Go/Rust)** | Reescrever rotas ou servidor em linguagem compilada. | Menor overhead de CPU por requisição. | Custo enorme de reescrita, manutenção bilíngue e risco de regressão; **zero evidência de que Python seja o gargalo** (backend responde em `2–5 ms` vs `137–431 ms` de transferência não-comprimida na rede!). | **REJEITADA (Sem evidência técnica)** |

---

## 2. Avaliação do SQLite (Parte IV — Seção 7)

### Estado Atual Verificado
- Banco atual: **`6,5 MiB`** (`1.641` páginas de 4 KB, `203` páginas livres), cabendo 100% na memória de cache de páginas do kernel (`3,1 GiB` buff/cache).
- Índices compostos iniciados por `user_id` já validados na Fase 8/9 (`UNION ALL` indexado no grafo Atlas e FTS5 `unicode61 remove_diacritics 2`).
- `get_connection()` (`database.py:17-29`) executa `PRAGMA journal_mode=WAL;`, `PRAGMA synchronous=NORMAL;` e `PRAGMA foreign_keys=ON;` a **cada abertura de conexão**, com `timeout=30.0`.
  - *Nota técnica*: a inspeção somente-leitura via `mode=ro&immutable=1` reporta `journal_mode=delete` por comportamento interno do SQLite em modo `immutable=1`, não indicando ausência de WAL em runtime.

### Oportunidades Seguras (Para Benchmarking Isolado na Fase 12.1 / 12.2)
1. Executar `PRAGMA journal_mode=WAL;` apenas em `init_db()` (pois é persistente no arquivo), evitando syscall/lock de verificação em toda requisição.
2. Avaliar em banco sintético isolado (sem dados pessoais): `PRAGMA temp_store=MEMORY;`, `PRAGMA cache_size=-8000;` (8 MiB) e `PRAGMA busy_timeout=5000;` explícito.
3. **Conclusão sobre SQLite**: com `6,5 MiB`, 5 usuários e 238 livros, **SQLite NÃO é o gargalo** dos atrasos atuais e permanece a escolha arquitetural ideal para a Lectoria.

---

## 3. Avaliação do Frontend e Leitores PDF/EPUB

- **Frontend (`static/`)**:
  - Maior impacto imediato: **compressão HTTP (GZip)** + **`Cache-Control: immutable` em `/static/*`** + **remoção do `Set-Cookie: lectoria_csrf` em `/static/*`** + **carregamento sob demanda (`defer`/lazy)** de `knowledge.js` (`164 KB`) e `shared_reading.js` (`52 KB`).
  - Capas (`/api/books/{id}/cover`) usam `Cache-Control: private, max-age=3600` (apenas 1 h); ampliar para `max-age=86400` + ETag/versão na URL reduz revalidações de 36 imagens sobre Tailscale.
  - Em `app.js`, monitorar na Fase 12.1 se `GET /api/books` sem paginação + `innerHTML` em lote geram *long tasks* > 50 ms.
- **Leitores PDF e EPUB**:
  - **PDF.js** já utiliza HTTP Range (206) em chunks de 512 KB com `disableStream: true` — arquitetura correta para arquivos de `124–377 MB`. Gargalo provável a medir na Fase 12.1: download inicial de `pdf.worker.min.js` (`1,1 MB` sem gzip!) + número de round-trips Range de 60 ms em PDFs não-linearizados (*Fast Web View* ausente).
  - **EPUB.js** exige download completo do arquivo `.epub` + descompressão `JSZip` no navegador; medir tempo de abertura para EPUBs pequenos vs. grandes na Fase 12.1.

---

## 4. Avaliação de Capacidade da VPS — Respostas Objetivas (Parte VI — Seção 12)

1. **A Lectoria está limitada por CPU, RAM, disco, rede ou navegador?**
   - **Evidência atual aponta principalmente para REDE/TRANSFERÊNCIA NÃO COMPRIMIDA + AUSÊNCIA DE CACHE ESTÁTICO** (`~763 KB` na `/` e `~1,9 MB` no `/reader` sem gzip, somados a `~60–70 ms` de RTT do Tailscale e `Set-Cookie` em `/static/*`), seguido possivelmente pelo **navegador** (parse/JS/EPUB/PDF) — a confirmar na Fase 12.1. **NÃO está limitada por CPU** (`load 0.04`), **RAM** (`3,1 GiB` livres) ou **disco** (`30 GiB` livres, respostas locais em `2–5 ms`).
2. **O limite de `768 MiB` do container é adequado?**
   - **Sim, para o uso atual** (pico típico `65–120 MiB`, ~10–15% do teto, zero eventos OOM).
3. **Há benefício comprovável em aumentar para `1 GiB`?**
   - **Não há ganho de velocidade** (memória não é gargalo), mas **há benefício operacional de margem de segurança** durante exportações `.zip` concorrentes ou varreduras de PDFs muito grandes (`377 MB`), com custo zero dado que o host possui `3,1 GiB` livres. Pode ser adotado oportunisticamente junto ao Compose endurecido na Fase 11.4.
4. **A VPS consegue suportar mais workers sem prejudicar outros serviços?**
   - **Não vale a pena nem é seguro**: com apenas **1 vCPU** e estado em memória por processo no backend (rate limiter, semáforo de exportação, enricher), aumentar para `--workers 2` traria **zero ganho de CPU**, dobraria RAM e introduziria inconsistências funcionais e maior contenção no SQLite. **Manter `--workers 1`.**
5. **O único vCPU é o principal limitador em algum cenário?**
   - Não no uso cotidiano atual (`load 0.04`). Só poderia saturar brevemente durante tarefas administrativas pesadas (varredura completa de 238 PDFs ou múltiplos exports ZIP simultâneos com compressão).
6. **Existem operações que poderiam ser movidas para tarefas em segundo plano?**
   - Extração inicial de capas faltantes e enriquecimento de metadados já rodam sob demanda/background (`background_enricher.py`). O único candidato futuro (caso comprovado na Fase 12.1) seria pré-aquecimento assíncrono de capas após scan de biblioteca.
7. **Há recursos subutilizados?**
   - Sim: a VPS tem **~81% da RAM livre (`3,1 GiB`)** e **~96% da CPU ociosa** — o que significa que podemos usar `GZipMiddleware` (baixo custo de CPU no EPYC 9355P) e cache em memória/SQLite com enorme folga!
8. **Vale otimizar antes de considerar upgrade de VPS?**
   - **SIM, categoricamente.** Um upgrade de VPS (mais vCPUs/RAM) **não resolveria** o RTT de 60 ms do Tailscale nem a transferência de `763 KB–1,9 MB` de assets sem gzip e sem cache imutável. Otimizar compressão, cache HTTP, carregamento de scripts e leitores trará ganhos muito maiores a **custo zero**.

---

## 5. Veredito Explícito

- **Alterar linguagem (Python → Go/Rust)?** **NÃO** — zero evidência de gargalo em Python (`2–5 ms` locais).
- **Alterar número de workers Uvicorn (`--workers 1`)?** **NÃO** — VPS de 1 vCPU + SQLite WAL + estado in-process tornam `--workers 1` a configuração correta.
- **Alterar limites de recursos (`768m → 1g`)?** **Opcional na Fase 11.4** como margem preventiva (não resolve lentidão de rede/frontend), mantendo o container muito abaixo da RAM disponível (`3,1 GiB`).
