# Lectoria v1.9.0 — Benchmarks de Desempenho e Profiling (Fase 8)

- **Data**: 2026-11-22
- **Branch**: `feature/v1.9.0-connected-knowledge`
- **Scripts reproduzíveis**:
  - Backend e SQLite/Uvicorn: [`tests/performance/benchmark_v190_atlas.py`](../../tests/performance/benchmark_v190_atlas.py)
  - Frontend (Playwright/Chromium): [`tests/performance/profile_v190_frontend.py`](../../tests/performance/profile_v190_frontend.py)
- **Hardware de execução**: AMD Ryzen 5 5600GT (12 threads), 13.843,1 MB RAM, Fedora Linux, Python 3.14.9, SQLite 3.53.4.
- **Nota metodológica**: Todos os números abaixo são medições reais observadas localmente antes (Etapa A) e depois (Etapa B) das otimizações. Nenhuma medição foi realizada em contêiner Docker ou na VPS de produção (ver limitações em [27-infrastructure-audit.md](27-infrastructure-audit.md)).

---

## 1. Resumo Comparativo: Antes (Etapa A) vs Depois (Etapa B)

### 1.1 Backend, Consultas SQLite e Concorrência Uvicorn (`--workers 1`)

| Métrica / Cenário | Antes (Etapa A) | Depois (Etapa B) | Variação |
| :--- | ---: | ---: | :--- |
| **Uvicorn `/api/health` idle (mediana)** | 0,712 ms | 0,795 ms | Estável (< 1 ms) |
| **Uvicorn `/api/health` durante exportação (máx.)** | **495,468 ms** | **16,889 ms** | **-96,6% (29,3× mais rápido)** |
| **6× exportações simultâneas (`BoundedSemaphore(4)`)** | `{'200': 6}` (serializado) | **`{'200': 4, '503': 2}`** | Semáforo efetivo em paralelo |
| **Uvicorn `VmHWM` pico (6× exportações)** | 97,92 MB (serializado) | **170,68 MB** (4 paralelas) | Pico real sob paralelismo |
| **`list_concepts(limit=50)` (1k conceitos)** | 15,98 ms (4 SQLs) | **2,591 ms** (5 SQLs) | **-83,8% (6,2× mais rápido)** |
| **`list_concepts(limit=50)` (10k conceitos)** | 188,51 ms (13,71 MB) | **2,811 ms** (0,124 MB) | **-98,5% (67× mais rápido / -99% pico RAM)** |
| **`list_reflections(limit=50)` (1k reflexões)** | 50,20 ms (**2.004 SQLs**) | **2,955 ms** (**6 SQLs**) | **-94,1% (17× mais rápido / -99,7% SQLs)** |
| **`list_reflections(limit=50)` (10k escala)** | 55,88 ms | **3,221 ms** | **-94,2% (17,3× mais rápido)** |
| **`build_atlas_export_snapshot` (SQLs)** | **2.007 SQLs** | **8 SQLs** | **-99,6% SQLs (zero N+1)** |
| **`atlas_summary` (escala 10k/10k/50k)** | 28,01 ms | **23,466 ms** | **-16,2%** |
| **Busca FTS5 termo único (10k/10k/50k)** | 229,76 ms | **149,874 ms** | **-34,8%** |
| **Busca FTS5 multi-termo 2k candidatos (10k/10k/50k)** | **1.041,84 ms** | **565,540 ms** | **-45,7%** |
| **Grafo local 2 saltos, 80 nós / 160 arestas (50k links)** | **60,05 ms** (12 SQLs) | **34,649 ms** (12 SQLs) | **-42,3%** |

### 1.2 Frontend (Chromium Headless, 200 livros + Atlas)

| Métrica / Cenário | Antes (Etapa A) | Depois (Etapa B) | Variação |
| :--- | ---: | ---: | :--- |
| **Carga inicial `/` (Descoberta) — chamadas API (sem capas)** | 7 | **4** | **-42,9%** |
| **Carga inicial `/` — chamadas duplicadas** | 2 (`/shelves`, `/taxonomy`) | **0 (`{}`)** | **Eliminadas** |
| **Carga inicial `/` — payload inicial (sem capas)** | 308.682 B | **37.662 B** | **-87,8%** |
| **`/?tab=atlas` — chamadas API (sem capas)** | 6 | **4** | **-33,3%** |
| **`/?tab=atlas` — payload inicial (sem capas)** | 238.510 B | **2.469 B** | **-99,0%** |
| **`/?tab=atlas&subtab=graph` — payload inicial** | 258.797 B | **22.756 B** | **-91,2%** |
| **Overflow horizontal (1366px / 768px / 360px)** | 0 (`false`) | **0 (`false`)** | Preservado |
| **Erros de página (`pageerror`)** | 0 | **0** | Preservado |
| **Cards iniciais na aba Biblioteca (lote)** | 36 | **36** | Preservado |
| **Chamadas API durante filtro local na Biblioteca** | 0 | **0** | Preservado |

---

## 2. Detalhamento Completo dos Cenários Pós-Otimização (Etapa B)

### 2.1 Acervo e Startup
- **Startup vazio**: `VmRSS` 59,68 MB (`VmHWM` 59,68 MB), DB 819 KB.
- **100 livros**: `get_books` mediana 31,42 ms (142,5 KB); `get_discovery_shelves` 34,59 ms; `get_book` 3,27 ms.
- **1.000 livros** (DB 1,78 MB): `get_books` mediana 282,58 ms (1,43 MB JSON, `tracemalloc` pico 8,16 MB); `get_discovery_shelves` 326,99 ms; `get_book` 3,32 ms.

### 2.2 Atlas 1.000 Conceitos / 1.000 Reflexões / 10.000 Conexões (DB 12,27 MB)
- `list_concepts(50)`: mediana **2,591 ms**, p95 2,940 ms, 5 SQLs, `tracemalloc` pico 0,123 MB.
- `list_reflections(50)`: mediana **2,955 ms**, p95 3,502 ms, 6 SQLs, `tracemalloc` pico 0,246 MB.
- `atlas_summary`: mediana **6,762 ms**, p95 7,676 ms.
- Exportações (streaming em disco temporário):
  - `markdown_zip`: mediana **1.643,83 ms** (p95 1.724,17 ms), `VmRSS` 85,68 MB, `tracemalloc` pico 20,04 MB.
  - `json`: mediana **1.153,62 ms** (p95 1.390,70 ms), `VmRSS` 82,39 MB, `tracemalloc` pico 16,38 MB.
  - `csv`: mediana **680,52 ms** (p95 730,72 ms), `VmRSS` 83,40 MB, `tracemalloc` pico 16,60 MB.

### 2.3 Uvicorn `--workers 1` sob Concorrência Real
- Memória idle: `VmRSS` **72,43 MB** (2 threads).
- `/api/health` idle: mediana **0,795 ms**, p95 1,060 ms, máx. 1,344 ms.
- Exportação única `markdown_zip`: **648,28 ms** (2,58 MB ZIP).
- `/api/health` durante exportação: mediana **1,651 ms**, p95 **6,249 ms**, máx. **16,889 ms**.
- Escrita de progresso (`/api/books/{id}/progress`) durante exportação: **0 erros**, mediana **12,625 ms**, p95 38,355 ms, máx. 55,225 ms.
- 6× exportações simultâneas: 4 concluídas com `HTTP 200` (3,05–3,38 s cada), 2 rejeitadas limpidamente com `HTTP 503` (~2,01 s após timeout de aquisição do semáforo), `VmRSS` pós-carga 159,13 MB, **`VmHWM` pico 170,68 MB** (8 threads).

### 2.4 Escala Extrema (10.000 Conceitos / 1.000 Reflexões / 10.000 Anotações / 50.000 Conexões — DB 42,75 MB)
- `list_concepts(50)`: **2,811 ms** (p95 2,930 ms).
- `list_concepts(search="tecnica")`: **646,85 ms** (filtro NFD em Python sobre 10k linhas — operação pontual quando há busca textual na aba Conceitos; a pesquisa global usa FTS5).
- `list_reflections(50)`: **3,221 ms** (p95 3,943 ms).
- `get_reflection_detail`: **1,150 ms**.
- `book_annotations_summary` (500 itens): **7,534 ms**.
- `search_fts5_diacritics` ("tecnicacritica"): **149,874 ms** (p95 152,565 ms, `tracemalloc` pico 0,915 MB).
- `search_fts5_multi_term` ("técnica método", 2k candidatos): **565,540 ms** (p95 585,870 ms, `tracemalloc` pico 4,56 MB).
- `search_relational_fallback`: **597,873 ms**.
- `graph_hub_hop1_80nodes`: **11,870 ms** (p95 12,394 ms).
- `graph_hub_hop2_80nodes_160edges`: **34,649 ms** (p95 36,884 ms, 12 SQLs).
- `save_progress_isolated`: **1,054 ms**.
- **Concorrência WAL** (escritas de progresso + leituras de busca/grafo simultâneas): **0 erros**, escritas de progresso mediana **6,849 ms** (p95 15,524 ms), `PRAGMA wal_checkpoint(TRUNCATE)` retornou `[0, 0, 0]` (`wal_bytes = 0`).

### 2.5 Overhead dos Triggers FTS5 (Schema v13)
| Linhas em `knowledge_fts` | Mediana INSERT (ms) | p95 INSERT (ms) | Mediana UPDATE (ms) | p95 UPDATE (ms) |
| ---: | ---: | ---: | ---: | ---: |
| 1.000 | 0,230 | 0,401 | 0,234 | 0,290 |
| 10.000 | 1,866 | 2,795 | 1,848 | 1,955 |
| 50.000 | 10,197 | 10,711 | 10,165 | 14,515 |

---

## 3. Análise Analítica de Dimensionamento de RAM para VPS de 4 GB

Com base no pico real medido de **`VmHWM = 170,68 MB`** sob 4 exportações `markdown_zip` paralelas em Uvicorn `--workers 1` (e margem para renderização PyMuPDF de capas/PDFs e page cache interno do SQLite):

| Teto `mem_limit` | Folga sobre idle (72 MB) | Folga sobre pico 4× export (171 MB) | Avaliação para VPS de 4 GB compartilhada |
| :--- | :---: | :---: | :--- |
| `512m` | 7,1× | 3,0× | Funcional para uso leve, mas apertado se PyMuPDF processar PDFs pesados durante exportações simultâneas. |
| `768m` | 10,6× | 4,5× | Seguro para a maioria das cargas, com margem intermediária. |
| **`1g` (Recomendado)** | **14,1×** | **6,0×** | **Melhor equilíbrio**: >6× de headroom sobre o pior pico medido sem risco de OOM Kill e preservando 3 GB para SO, Caddy e serviços irmãos. |
| `1.5g` | 21,2× | 9,0× | Só justificável se medições reais na VPS (Etapa C) mostrarem picos > 600 MB em extração de PDFs gigantes. |

> **Nota**: Um `mem_limit` no Docker **não reserva** RAM permanentemente; ele atua exclusivamente como teto máximo de alocação sob demanda.
