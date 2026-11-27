# Lectoria — Fase 12.1: Análise de Frontend, Renderização e Capas

**Conclusão executiva:** O gargalo **nº 1** da experiência cotidiana na biblioteca (Descoberta e Acervo) é o **sistema de capas** — responsável por **79% a 83% de todos os bytes transferidos** na tela inicial e por atrasos medidos de **15,9 s a 21,9 s** até as primeiras capas visíveis ficarem prontas. Em contrapartida, a execução de JavaScript no navegador registrou **0 Long Tasks (> 50 ms)** em todos os 13 cenários: a interface não está travada por CPU do cliente, mas sim **estrangulada por megabytes de imagens brutas e cascata sequencial de boot**.

---

## 1. Diagnóstico das Capas em Produção (`data/covers/`)

Inspeção somente-leitura das **213 capas reais** armazenadas em `/srv/nosreme/lectoria/data/covers/`:

| Métrica | Valor Real Medido | Ideal para Cards (~180×255 px @2x = 360×510 px) |
| :--- | :--- | :--- |
| **Total de arquivos** | 213 (`193` extraídas + `20` personalizadas; `177` JPEG, `36` PNG) | Thumbnails WebP/JPEG otimizados |
| **Espaço total** | **35,52 MB** | ~2,5 – 4,5 MB (~88–93% menor) |
| **Tamanho mínimo / mediana / média** | 1,0 KB / **69,9 KB** / **170,8 KB** | **12 – 25 KB** (mediana/média) |
| **Tamanho p95 / máximo** | **651,5 KB** / **3.441,3 KB (3,36 MB!)** | ≤ 45 KB |
| **Capas > 200 KB / > 500 KB / > 1 MB** | **46** / **14** / **6** | **0** |
| **Largura (mediana / p95 / máxima)** | 588 px / **1.588 px** / **2.550 px** | 320 – 360 px (card) / 600 px (detalhe) |
| **Altura (mediana / p95 / máxima)** | 751 px / **1.651 px** / **3.300 px** | 480 – 540 px (card) / 900 px (detalhe) |

### 1.1. Efeito Real no Navegador (Ambiente C — Chromium via Tailscale)

| Cenário | Capas Baixadas | Bytes de Capas | Tempo Medido | Observação Crítica |
| :--- | ---: | ---: | ---: | :--- |
| **S01/S09 — Descoberta (frio)** | **46 – 48** | **3,70 – 4,27 MB** | **15,9 – 17,9 s** (1ªs 8 capas) | Apesar de `loading="lazy"`, as prateleiras horizontais disparam dezenas de capas simultaneamente na abertura de `/`, competindo por ~400 KB/s! |
| **S03/S04 — Acervo (1ªs 12 capas)** | **58 – 64** | **4,71 – 7,43 MB** | **20,2 – 21,9 s** | Ao clicar em "Biblioteca", o navegador ainda está drenando capas da Descoberta e soma mais 36 cards do 1º lote — **mediana de 6,7–8,4 s por capa**, pico de **21,3 s**! |
| **S05 — Scroll (2º lote 36→72)** | 16 – 24 | 0,69 – 1,73 MB | **5,3 – 6,9 s** (cards em 106–154 ms) | O DOM renderiza em ~110 ms, mas as capas brutas travam a pintura visual por 5–7 s. |
| **Capa individual `COVER-L` (239 KB)** | 1 | 238,8 KB | **510 ms** (p95 655 ms) | 58× mais lento que a VPS interna (8,8 ms). |
| **Capa individual `COVER-XL` (1,16 MB PNG)** | 1 | 1.162,1 KB | **3.059 ms** (máx 3.549 ms) | **3 segundos para UMA única miniatura de card!** |

### 1.2. Bugs de Desempenho no `covers_service.py` (Descoberta de Código)
Em [`covers_service.py`](../../covers_service.py#L545-L600), a função `get_or_create_book_cover(book_id)` executa **em toda requisição `GET /api/books/{id}/cover`**:
1. `resolve_book_path(book_id)` (query SQLite + `stat` no arquivo PDF/EPUB em `/storage/books`);
2. Segunda query SQLite em `books` + subconsulta em `book_subjects`;
3. `cached_bytes = cached_path.read_bytes()` — **lê a capa inteira para a RAM do Python** (até 3,44 MB!);
4. `is_extracted_cover_visually_valid(cached_bytes)` — para PNGs, **executa `zlib.decompress()` em todos os chunks `IDAT` em toda requisição HTTP!** (Motivo de `COVER-XL` PNG levar 20,1 ms na VPS vs 8,8 ms de JPEG);
5. Descarta `cached_bytes` e devolve `FileResponse(path=cached_path)`, que **abre e lê o arquivo do disco uma segunda vez**!

> A validação visual deveria ocorrer **uma única vez na extração/upload**, nunca no caminho quente de leitura (`GET /cover`).

---

## 2. Cascata Sequencial de Inicialização (`static/index.html` e `static/js/app.js`)

### 2.1. Scripts Síncronos Carregados em `/` Sem Necessidade Imediata
Em [`static/index.html`](../../static/index.html#L1784-L1788):
- `knowledge-api.js` (27,8 KB) + `book-knowledge.js` (42,6 KB) + **`knowledge.js` (167,3 KB)** + **`shared_reading.js` (52,8 KB)** = **290,5 KB brutos (37,8% do shell `/`)** são baixados e parseados **antes do `DOMContentLoaded`** mesmo quando o usuário só abre Descoberta ou Acervo (sem nunca abrir Atlas ou Clubes).

### 2.2. Waterfall Bloqueante no `initAuth()` ([`static/js/app.js`](../../static/js/app.js#L1131-L1168))
```
1. HTML (102 KB) → CSS (137 KB) → 5 scripts JS (530 KB)          [~1.640 ms frio]
2.   └─ await fetch("/api/auth/status")                           [+~70 ms]
3.        └─ await Promise.all([loadCollections(), loadTaxonomyTree()])  [+~80 ms BLOQUEANDO A ABA ATIVA!]
4.             └─ loadShelves() → fetch("/api/discovery/shelves", 201 KB) [+~390–600 ms]
5.                  └─ 46–48 requisições /cover (3,7–4,3 MB)      [+13.000–15.400 ms!]
```
- **Gargalo de cascata:** `loadCollections()` e `loadTaxonomyTree()` são aguardados com `await` **antes** de disparar `loadShelves()` (que nem usa coleções ou selects de taxonomia na primeira pintura!).
- **Saturação cruzada entre abas:** ao clicar em **Biblioteca** (`S03`) logo após a abertura de `/`, as 46 capas pendentes da Descoberta continuam ocupando o túnel HTTP/2, fazendo `GET /api/books` (301 KB) levar **9,8–14,9 s** (Runs 1–2) em vez dos ~2,3 s observados quando a fila já havia drenado (Run 3).

---

## 3. Interações Client-Side (Busca, Filtros, Detalhes, Atlas)

| Cenário | Mediana / Faixa Medida | Long Tasks (>50 ms) | Avaliação |
| :--- | ---: | :---: | :--- |
| **S06 — Busca por título/autor** | **258 – 263 ms** (inclui debounce 100 ms) | 0 | Rápida (filtragem em memória no `libraryCache.books`). |
| **S07 — Filtros e ordenação** | **42 – 59 ms** | 0 | Instantânea (client-side em 193 livros). |
| **S08 — Modal de detalhes do livro** | **1.893 – 2.897 ms** | 0 | Atrasada por `GET /api/books/{id}` + re-carregamento/disputa da capa grande no modal enquanto fila de capas está ativa. |
| **S10 — Aba Atlas** | **152 – 211 ms** | 0 | Rápida e leve (payloads `/api/knowledge/*` de 61–186 B). |

### Resumo dos Achados de Frontend
- **CPU / JS do navegador NÃO é gargalo:** `0` Long Tasks (> 50 ms) em todos os 13 cenários; `filter_and_sort` em 42–59 ms; renderização do 2º lote de 36 cards em 106–154 ms.
- **Atributos faltantes nas `<img>` de capa:** falta `decoding="async"`, `width`/`height` explícitos e `fetchpriority="high"` apenas para as primeiras capas *above-the-fold* (e redução de pré-carregamento agressivo nas prateleiras horizontais fora da viewport).
- **Redimensionamento de capas (thumbnails)** é o maior multiplicador isolado de percepção de velocidade de todo o projeto.
