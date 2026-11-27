# Lectoria — Fase 12.1: Análise de Python, SQLite e Recursos do Backend

**Conclusão executiva:** **Python (FastAPI/Uvicorn `--workers 1`) e SQLite (modo WAL, schema v13) NÃO são gargalos.** Todas as consultas e rotas de API respondem internamente na VPS em **5,5 ms a 29,4 ms** com uso de CPU máximo de **0,9%** sob carga real. **Não há justificativa técnica alguma** para migrar de Python/FastAPI, substituir SQLite por PostgreSQL/Redis, nem aumentar plano de VPS ou adicionar múltiplos workers nesta fase.

---

## 1. Saúde e Estrutura do Banco SQLite em Produção (`lectoria.db`)

Inspeção direta somente-leitura (`mode=ro`) do banco real em `/srv/nosreme/lectoria/data/lectoria.db`:

| Propriedade | Valor Medido | Avaliação |
| :--- | :--- | :--- |
| **Tamanho do banco** | **6,41 MiB** (`6.721.536 B`) | Muito compacto; cabe integralmente em RAM/page cache |
| **WAL / SHM ativos** | `0 B` / `0 B` | Checkpoints saudáveis, sem acúmulo de WAL |
| **Journal Mode / Schema** | `wal` / `v13` | Configuração canônica ativa |
| **Page Size / Page Count / Freelist** | `4096 B` / `1641` / `100` (~6% livre) | Fragmentação irrelevante; não requer `VACUUM` |
| **Índices existentes** | **129 índices** | Cobertura extensa de chaves estrangeiras e buscas |
| **Volumetria real** | `books=238` (193 ativos, 45 ausentes), `users=5`, `reading_progress=54`, `notes=22`, `highlights=1`, `reading_clubs=1`, `concepts=5`, `reflections=1`, `knowledge_links=6`, `sessions=4` | Banco pequeno; consultas levam < 2 ms em SQLite |

---

## 2. Latência Real de Cada Endpoint no Backend (Ambiente B — VPS `127.0.0.1:8765`)

| Endpoint | Código | Payload | TTFB (med) | Total (med / p95 / máx) | Diagnóstico |
| :--- | :---: | ---: | ---: | ---: | :--- |
| `GET /api/health` | 200 | 60 B | 1,68 ms | 1,93 / 2,33 / 2,35 ms | Excelente (`async def` puro) |
| `GET /api/auth/status` | 200 | 167 B | 5,47 ms | 5,56 / 6,48 / 6,53 ms | Excelente (sessão SHA-256 indexada) |
| `GET /api/collections` | 200 | 18 B | 5,88 ms | 6,17 / 7,23 / 7,98 ms | Excelente |
| `GET /api/knowledge/summary` | 200 | 186 B | 7,03 ms | 7,24 / 8,01 / 8,60 ms | Excelente |
| `GET /api/knowledge/concepts` | 200 | 61 B | 7,18 ms | 7,51 / 8,23 / 8,46 ms | Excelente |
| `GET /api/books/{id}` | 200 | 2,88 KB | 8,90 ms | 9,19 / 9,95 / 10,56 ms | Excelente |
| `GET /api/taxonomy/tree` | 200 | 3,18 KB | 11,85 ms | 12,19 / 15,75 / 15,75 ms | Rápido |
| `GET /api/knowledge/search?q=a` | 200 | 22,9 KB | 16,12 ms | 16,32 / 18,45 / 18,62 ms | Rápido (FTS5) |
| `GET /api/books/{id}/annotations` | 200 | 226 B | 16,47 ms | 16,95 / 21,82 / 22,36 ms | Rápido |
| `GET /api/books/{id}/file` (Range 512 KB) | 206 | 524,3 KB | 6,96–8,12 ms | 9,94–11,77 ms | Excelente (`resolve_book_file_for_user` leve) |
| `GET /api/books/{id}/cover` (`S`/`M`/`L`) | 200 | 22–239 KB | 7,28–8,46 ms | 8,79–9,10 ms | Contém I/O redundante em `covers_service.py` |
| `GET /api/books/{id}/cover` (`XL` PNG) | 200 | 1.162 KB | **14,77 ms** | **20,05 ms** | Penalizado por `read_bytes()` + `zlib.decompress()` a cada request! |
| `GET /api/discovery/shelves` | 200 | 200,9 KB | 26,69 ms | 27,21 / 31,30 / 31,54 ms | Chama `get_books()` completo e duplica objetos completos nas prateleiras |
| `GET /api/books?sort_by=title&order=asc` | 200 | 301,6 KB | 28,70 ms | 29,43 / 33,97 / 87,25 ms | Enriquecimento de 193 livros + serialização de campos grandes (`description`) |

---

## 3. Planos de Execução SQLite (`EXPLAIN QUERY PLAN`)

- `books` (listagem principal): `SCAN b` + `USE TEMP B-TREE FOR ORDER BY` em tabela de 238 linhas (< 0,5 ms).
- `reading_progress`, `book_reviews`, `notes`, `concepts`, `knowledge_links`, `sessions`: todos utilizando índices (`SEARCH ... USING INDEX`).
- **Oportunidades reais no backend (sem trocar de stack):**
  1. **Eliminar dupla leitura e `zlib.decompress()` por request em `covers_service.py`** (P0/P1): validar a imagem apenas na gravação/extração; no `GET /cover`, servir diretamente o thumbnail/capa em cache com suporte imediato a `ETag`/`304`.
  2. **Adicionar `GZipMiddleware` (nível 5–6, `minimum_size=500`)** no FastAPI (P0): custo de CPU estimado < 2–3 ms por resposta JSON/JS (com folga > 95% de CPU na VPS) e redução de **72% a 88%** no volume de rede.
  3. **Payload enxuto em `/api/books` e `/api/discovery/shelves`** (P1): omitir campos pesados não usados em cards (ex.: `description` longa, metadados de auditoria/erros), mantendo-os apenas em `GET /api/books/{id}`.
  4. **Manter `--workers 1`:** sob 364 amostras reais, o pico de CPU do worker único foi **0,9%** e o RSS ficou em **96,9 MiB**. Adicionar workers consumiria ~90 MiB extras de RAM por worker sem melhorar em nada o gargalo real (rede/payload/capas) e ainda aumentaria risco de contenção de locks de escrita no SQLite.

---

## 4. Veredito sobre Stack e Infraestrutura

| Pergunta | Veredito Baseado em Evidência |
| :--- | :--- |
| Python/FastAPI está causando os delays? | **NÃO.** Tempo médio de API na VPS é 5,5–29 ms (CPU máx. 0,9%). |
| SQLite está causando os delays? | **NÃO.** Banco de 6,4 MiB em WAL com 129 índices, 0 WAL backlog, queries em poucos ms. |
| Docker / hardening (`1000:1000`, `ReadonlyRootfs`, `768m`) está causando os delays? | **NÃO.** Pico de memória do cgroup foi 123,7 MiB (16,1% do limite), 0 OOM, 0 CPU throttling. |
| A VPS precisa de upgrade de CPU/RAM? | **NÃO.** 3,1 GiB de RAM livre, 98–99% de CPU ociosa, `loadavg` máx. 0,27 sob carga real. |
