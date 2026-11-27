# Lectoria — Fase 12.4: Otimização do Frontend e dos Leitores PDF/EPUB (`v1.10.0`)

**Data:** 2026-11-27  
**Branch de implementação:** `perf/v1.10.0-phase-12.4-frontend-readers` (derivada de `perf/v1.10.0-phase-12.3-cover-thumbnails` @ `2057e6d`)  
**Versões preservadas:** `APP_VERSION = 1.9.2` | `SCHEMA_VERSION = 13`  
**Produção:** Intocada (nenhum deploy, nenhum ajuste em produção).

---

## 1. Verificações Complementares da Fase 12.3 (Seção 3)

| Item Verificado | Resultado / Ajuste Aplicado |
| :--- | :--- |
| **Exclusão de miniaturas do backup obrigatório** | Confirmado: `backup_service.create_backup()` copia apenas `config.COVERS_DIR.glob("*_custom.*")` (nível raiz), excluindo `data/covers/thumbs/`. Backups antigos restauram normalmente e regeneram derivados sob demanda. |
| **Orçamento de memória e concorrência de thumbnails (VPS 1 vCPU / 768 MiB)** | **Ajustado preventivamente** em [`thumbnail_service.py`](../../thumbnail_service.py): `MAX_THUMB_SOURCE_PIXELS` reduzido de `40 MP` para **`25 MP`** (~100 MiB pico RGBA, ainda acima de 5000×5000 px vs. maior capa real de produção 2550×3300 = 8,4 MP) e `_GENERATION_SEMAPHORE` reduzido de `2` para **`1`** geração simultânea, garantindo zero contenção com Uvicorn/leitura e teto de RAM muito abaixo de 768 MiB. |
| **Dependência Pillow e auditoria (`pip check`)** | `Pillow==12.3.0` (wheel manylinux com WebP/JPEG/zlib), `pip check` sem conflitos (`No broken requirements found`). |
| **Acúmulo de derivados órfãos** | `cleanup_orphan_thumbnails(valid_book_ids)` disponível em `thumbnail_service.py` operando estritamente em `thumbs/` sem tocar originais. |

---

## 2. Alterações Implementadas na Fase 12.4

### 2.1. Carregamento Seletivo de Módulos JS e Boot Paralelo (`index.html`, `app.js`, `shared_reading.js`, `http_cache_service.py`)
- **`AssetManifest` expandido** em [`http_cache_service.py`](../../http_cache_service.py): expõe via `<meta name="lectoria-asset-manifest">` (100% compatível com `CSP enforce` sem scripts inline) os caminhos versionados por hash SHA-256 de `knowledge-api.js`, `book-knowledge.js`, `knowledge.js`, `shared_reading.js`, `pdf.min.js`, `pdf.worker.min.js`, `jszip.min.js` e `epub.min.js`.
- **Shell inicial `/` enxuto ([`static/index.html`](../../static/index.html)):** carrega apenas `app.js` no boot inicial — **eliminando 290,5 KB brutos (~58,3 KB gzipped, -44% do JS inicial)** de módulos secundários (`knowledge.js`, `shared_reading.js`, `book-knowledge.js`, `knowledge-api.js`) até que o usuário abra Atlas, Clubes ou Detalhes.
- **Carregador idempotente em [`static/js/app.js`](../../static/js/app.js):** `loadLectoriaModule()` deduplica Promises em `Map`, evitando condições de corrida ou carregamento duplicado ao alternar abas rapidamente. [`static/js/shared_reading.js`](../../static/js/shared_reading.js) adaptado com verificação de `document.readyState` para vincular eventos imediatamente quando carregado após `DOMContentLoaded`.
- **Eliminação da cascata bloqueante no `initAuth()`:** `loadActiveTabInitialData()` agora dispara imediatamente em paralelo com `loadCollections()` e `loadTaxonomyTree()`, sem aguardar respostas de outras abas.

### 2.2. Descoberta e Biblioteca Orientadas à Viewport, Payloads Compactos e Modal Instantâneo
- **Carregamento orientado à viewport na Descoberta (`app.js`):**
  - Apenas as 2 primeiras prateleiras (e seus 5 primeiros cards horizontais) iniciam com `src` imediato (`fetchpriority="high"` nos 4 primeiros cards acima da dobra); demais cards horizontais e prateleiras abaixo da dobra usam `data-src` hidratado sob demanda por dois `IntersectionObserver` (vertical `rootMargin: 220px` + horizontal `rootMargin: 260px`).
  - Na Biblioteca, os 8 primeiros cards recebem `loading="eager" fetchpriority="high"` e os demais `loading="lazy" decoding="async"`.
  - Troca de abas cancela requisições obsoletas via `AbortController` (`shelvesAbortController` e `libraryCache.abortController`).
- **Projeção compacta retrocompatível ([`library.py`](../../library.py), [`main.py`](../../main.py)):**
  - `GET /api/books?compact=1` (default `False` preservando 100% dos contratos existentes) e `GET /api/discovery/shelves` omitem campos pesados não exibidos em cards (`description`, `manual_lock_fields`, `last_error_reason`, `reviewed_by`, `reviewed_at`), mantendo-os integralmente em `GET /api/books/{id}`.
- **Modal de detalhes instantâneo (`openBookDetails` em `app.js`):**
  - Pré-hidrata imediatamente título, autor, capa (`variant=detail`) e ações a partir do cache local (`libraryCache.books` / `discoveryBooksById`), limpa dados de livro anterior e usa `detailsAbortController` + contador monotônico `detailsRequestSeq` para descartar respostas tardias.

### 2.3. Separação PDF/EPUB, Caminho Crítico e Feedback Real de Progresso (`reader.html`, `reader.js`)
- **Remoção de `pdf.min.js` incondicional em [`static/reader.html`](../../static/reader.html):**
  - **EPUB:** nunca baixa `pdf.min.js` (320 KB bruto / 90 KB gzip) nem `pdf.worker.min.js` (1,09 MB bruto / 294 KB gzip); carrega apenas `jszip.min.js` → `epub.min.js` com URLs versionadas por hash.
  - **PDF:** carrega `pdf.min.js` e `pdf.worker.min.js` via `ensurePdfJsLoaded()` sob demanda apenas quando `format === "pdf"`.
- **Caminho crítico do leitor:** `loadEligibleCollectiveReads()` e a capa decorativa do cabeçalho (`reader-book-cover`, `variant=card`) são adiados para **após** a pintura da primeira página PDF ou primeira seção EPUB, reduzindo disputa de banda HTTP/2.
- **Feedback real de transferência (`loadingTask.onProgress`):** exibe bytes reais recebidos (`loaded / total` quando `total > 0`), sem porcentagens fictícias.

---

## 3. Investigação Técnica de `DOC-PDF-L` (99,16 MB) e `rangeChunkSize`

Inspeção somente-leitura diretamente no arquivo real `DOC-PDF-L` na VPS (sem modificar o original):

| Propriedade de `DOC-PDF-L` | Valor Real Medido |
| :--- | :--- |
| **Tamanho real / Páginas** | 99,16 MB (`103.974.726 B`) / 453 páginas |
| **Possui `/Linearized`?** | **Sim (`true`)**, `/L == 103.974.726` (coincide exatamente com o arquivo!), `/E = 399.932 B` (1ª página < 400 KB!) |
| **Estrutura interna** | Contém tanto xref clássico quanto xref stream + `startxref 116` |
| **Acervo completo (184 PDFs)** | **42 PDFs (22,8%) linearizados** e **142 PDFs (77,2%) não linearizados** |

**Causa técnica confirmada:**
- **`DOC-PDF-L` já era linearizado** — portanto, o problema de 60 chunks (29,2 MB) na Fase 12.1 **não** decorre apenas de ausência de linearização, mas da interação entre a tabela xref híbrida/objetos da página inicial (ou posição salva de leitura em página avançada / modo contínuo do leitor) que leva o PDF.js a buscar dezenas de chunks de 512 KiB sobre o link de ~3,5 Mbps.
- **Decisão sobre `rangeChunkSize`:** mantido **`524288` (512 KiB)** com `disableAutoFetch: true` e `disableStream: true`, pois já comprovou abrir `DOC-PDF-S` e `DOC-PDF-M` (17,6 MB) com apenas **3 requisições Range (`614–939 KB`)**; chunks menores (128/256 KiB) aumentam o número de round-trips em PDFs grandes sem eliminar o volume de objetos necessários, enquanto chunks maiores (1 MiB) ampliam o desperdício em PDFs pequenos.

---

## 4. Benchmarks Medidos (Ambiente Local Simulado via CDP 3,5 Mbps / 25 ms RTT — Sem `context.route()`)

*Ressalva explícita de topologia: medições obtidas em servidor local isolado Uvicorn com emulação de rede CDP (`3,5 Mbps` ≈ `437 KB/s`, `25 ms RTT`), **sem `context.route()`** (cache HTTP real do Chromium ativo) e `LECTORIA_CSP_MODE=enforce`. Não substituem a re-medição real sobre Tailscale na VPS prevista para a Fase 12.5.*

| Cenário | Baseline v1.9.2 (Remoto Real, 12.1) | Fase 12.4 (Local CDP 3,5 Mbps / 25 ms RTT, Sem `route()`) | Observação |
| :--- | ---: | ---: | :--- |
| **Descoberta — aba utilizável (frio)** | 2.521 ms | **360,8 ms** | Apenas `app.js` carregado no boot (`99,4 KB` totais, 15 reqs) |
| **Descoberta — primeiras capas visíveis (frio)** | 15.946 – 17.894 ms | **531,1 ms** | Capas orientadas à viewport + thumbnails WebP |
| **Biblioteca — troca de aba até cards** | 2.307 – 14.910 ms | **256,3 ms** | Sem saturação residual da Descoberta + `compact=1` |
| **Modal de detalhes do livro** | 1.893 – 2.897 ms | **168,6 ms** | Pré-hidratação instantânea do cache local |
| **Leitor PDF (frio → nova aba quente)** | 9.427 – 10.632 ms | **289,0 ms → 100,7 ms** (`0 B` estáticos em quente) | Gzip + `immutable` + secundários adiados |
| **Leitor EPUB (frio → nova aba quente)** | 5.147 – 7.334 ms | **544,2 ms → 276,2 ms** (`3,1 KB` em quente) | Zero `pdf.min.js` baixado no EPUB |

---

## 5. Resultados da Suíte de Testes Automatizados

- **Novos testes da Fase 12.4 (`tests/test_v1100_phase124_frontend_readers.py`):** **4/4 aprovados** (incluindo validação real no Chromium sob `LECTORIA_CSP_MODE=enforce` confirmando carga seletiva de módulos, ausência de `pdf.min.js` em EPUB, ausência de `epub.min.js`/`jszip.min.js` em PDF, projeção `compact=1` e orçamento de memória de thumbnails).
- **Suíte unitária e integração (`--ignore=tests/e2e`):** **355 passed, 2 skipped** (os 2 testes que exigem daemon Docker local ativo), **0 failed**.
- **Suíte E2E Playwright (`tests/e2e`):** **56 passed, 0 failed** (todos os 45 aprovados na rodada inicial + 11 revalidados 100% após correção de lazy-init do Atlas e `hydrateDeferredReaderExtras`).
- **Total consolidado local:** **411 passed, 2 skipped, 0 failed**.

---

## 6. Recomendações para a Fase 12.4.1 (Preparação — Não Implementado Agora)

1. **Linearização com QPDF focada nos 142 PDFs não linearizados (77,2% do acervo)** e re-linearização limpa de PDFs com xref híbrido/incremental, preservando 100% dos arquivos originais em `/storage/books:ro` e gravando derivados regeneráveis em armazenamento separado com teto explícito de disco.
2. **Processamento em janela ociosa/noturna** integrado à fila de enriquecimento existente, com 1 worker de baixa prioridade (`nice`/`ionice`) e trava para nunca concorrer com leitura ativa ou `lectoria-backup.service`.
3. **EPUBs:** não presumir linearização equivalente à de PDFs; avaliar entrega de capítulos/recursos internos apenas se o acervo EPUB crescer além da mediana atual de 1,0 MB (hoje 9 EPUBs, máx. 1,66 MB).

---

## 7. Procedimento de Reversão

- `git revert` dos commits da branch `perf/v1.10.0-phase-12.4-frontend-readers` ou retorno para `perf/v1.10.0-phase-12.3-cover-thumbnails` (`2057e6d`). Nenhuma alteração de schema (`SCHEMA_VERSION = 13`) ou de produção foi realizada.
