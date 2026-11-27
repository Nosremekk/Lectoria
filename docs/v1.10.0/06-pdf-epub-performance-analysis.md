# Lectoria — Fase 12.1: Análise de Desempenho dos Leitores PDF e EPUB

**Conclusão executiva:** Abrir um PDF pequeno/médio em produção leva **9,4 s a 11,3 s** (tanto em cache frio quanto em recarregamento quente!), abrir um EPUB leva **5,1 s a 8,0 s**, e um PDF grande não linearizado de 99 MB (`DOC-PDF-L`) **ultrapassou 90 segundos** (baixando 60 chunks = 29,2 MB antes mesmo da 1ª página pintar). Em contraste, a **troca de página dentro de um PDF/EPUB já aberto é rápida (~256–350 ms)**. Os culpados são 100% claros e corrigíveis: estáticos pesados sem gzip/immutable (`pdf.worker.min.js` de 1,09 MB = 2,6–3,3 s), `pdf.min.js` (320 KB) carregado até para EPUB, `Cache-Control: private, no-cache` no arquivo do livro, e PDFs grandes não linearizados.

---

## 1. Perfil Real do Acervo em Produção (`/srv/nosreme/lectoria/books`)

| Tipo | Quantidade | Total em Disco | Mín | p25 | Mediana | Média | p75 | p95 | Máx |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **PDF** | 184 | 6.859,92 MB | 0,09 MB | 3,94 MB | **17,58 MB** | 37,28 MB | 43,51 MB | **124,52 MB** | 377,36 MB |
| **EPUB** | 9 | 8,51 MB | 0,16 MB | 0,78 MB | **1,00 MB** | 0,95 MB | — | 1,66 MB | 1,66 MB |
| **Total** | **193** | **6.868,43 MB** | — | — | — | — | — | — | — |

---

## 2. Desempenho Medido no Leitor PDF (Cenários S11 e S13)

| Amostra | Tamanho / Págs | Cache Frio (1ª página útil) | Cache Quente / Reload | Chunks Range (`/file`) | Transferido Total | VPS Interna (1 chunk 512 KB) |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| **`DOC-PDF-S`** | 1,42 MB / 127 p. | **10,29 – 10,63 s** | **9,96 – 11,27 s** | 3 reqs (939 KB) | 2,84 MB | ~9,9 ms |
| **`DOC-PDF-M`** | 17,60 MB / 120 p. | **9,43 – 10,44 s** | **9,36 – 9,73 s** | 3 reqs (614 KB) | 2,55 MB | **9,94 ms** |
| **`DOC-PDF-L`** | 99,16 MB / 453 p. | **> 90,0 s (TIMEOUT)** | — | **60 reqs (29,2 MB!)** | **32,66 MB** | **11,77 ms** |
| **S13 — Troca de página (`DOC-PDF-M`)** | 3 avanços consecutivos | **256 – 280 ms / página** | — | 0 reqs extras | 0 B | — |

### 2.1. Decomposição dos ~10 Segundos em `DOC-PDF-S` e `DOC-PDF-M`
1. **Estáticos não comprimidos e sem cache imutável (`1,89 MB` brutos):**
   - `pdf.worker.min.js` (`1,09 MB`): **2,78 – 3,26 s** (cai para **292 KB / ~0,75 s** com gzip e **0 ms** em cache imutável!).
   - `pdf.min.js` (`320 KB`): **1,19 – 2,40 s** (cai para **89 KB / ~0,25 s** com gzip e **0 ms** em cache imutável!).
   - `reader.js` (`204 KB`) + `style.css` (`137 KB`) + `reader.html` (`61 KB`) + `reader-knowledge.js` (`56 KB`) + `knowledge-api.js` (`28 KB`): **~1,5 – 2,5 s** (caem ~80% com gzip e **0 ms** em cache imutável!).
2. **Requisições HTTP Range 206 (`rangeChunkSize: 524288` = 512 KB):**
   - Um chunk de 512 KB leva **1,25 – 1,27 s** isolado no `curl` e **3,7 – 4,7 s** no navegador quando disputa banda com `pdf.worker.min.js` e a capa do livro!
   - `DOC-PDF-S` e `DOC-PDF-M` exigem 3 requisições Range (`614–939 KB`) — provando que `disableStream: true` + `disableAutoFetch: true` **funciona muito bem** em PDFs padrão (não baixa os 17,6 MB inteiros!), mas a **falta de gzip nos estáticos** faz a 1ª página esperar pelo download concorrente de 1,89 MB de JS/CSS!
   - Em **`DOC-PDF-L` (99,16 MB)**, o PDF não é linearizado (ou possui tabela de objetos/fontes/imagens de alta resolução espalhadas), forçando o PDF.js a buscar **60 chunks de 512 KB (~29,2 MB)** sequencialmente a ~340 KB/s (> 90 s!).
3. **Saturação de micro-APIs na abertura do leitor:**
   - Durante o carregamento, `/api/books/{id}/collective-reads` (apenas **22 bytes**) levou **2,21 – 2,79 s** e a miniatura da capa no header (`43–233 KB`) levou **1,1 – 3,3 s** por competir no mesmo túnel HTTP/2!
   - **Ações óbvias:** adiar requisições secundárias (`/collective-reads`, `/annotations`, capa do header) para **depois** da pintura da 1ª página, e usar miniatura de capa (~15 KB) no header.

---

## 3. Desempenho Medido no Leitor EPUB (Cenários S12 e S13)

| Amostra | Tamanho / Seções | Cache Frio (1º conteúdo) | Navegação Quente (`warm_nav`, nova aba) | Tempo do `/file` | Transferido Total | VPS Interna (`/file`) |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| **`DOC-EPUB-S`** | 0,78 MB / 34 | **5,15 – 5,95 s** | **6,10 – 6,23 s** (`static_tr=1.043 KB`, `file_tr=796 KB`!) | 1,98 – 2,15 s | 2,13 MB | ~8–10 ms |
| **`DOC-EPUB-M`** | 1,30 MB / 15 | **7,25 – 7,33 s** | **7,94 – 7,96 s** (`static_tr=1.043 KB`, `file_tr=1.331 KB`!) | 3,11 – 3,16 s | 2,87 MB | **13,41 ms** |
| **S13 — Troca de seção (`DOC-EPUB-M`)** | 3 avanços | **~350 ms** (inclui pausa de 350 ms do teste) | — | 0 reqs | 0 B | — |

### 3.1. Gargalos Específicos Confirmados no Leitor EPUB
1. **Carregamento inútil de `pdf.min.js` (320 KB) em livros EPUB:**
   - [`static/reader.html`](../../static/reader.html#L1009) inclui `<script src="/static/vendor/pdfjs/pdf.min.js"></script>` incondicionalmente. Ao abrir `DOC-EPUB-S`, o navegador desperdiçou **1,93 segundos** baixando `pdf.min.js` sem nunca usá-lo!
2. **Carregamento sequencial de `jszip.min.js` e `epub.min.js`:**
   - [`static/js/reader.js`](../../static/js/reader.js#L4703-L4704) executa `await loadScript("jszip.min.js")` e só depois `await loadScript("epub.min.js")` (321 KB brutos sem gzip, **1,27 – 1,66 s**).
3. **Cache quente ineficaz (`warm_nav` = 6,1–8,0 s!):**
   - Mesmo abrindo o mesmo EPUB em uma **nova aba do mesmo contexto do navegador** (sem F5!), **1.043 KB de estáticos e 796–1.331 KB do arquivo EPUB foram re-baixados integralmente** por falta de `Cache-Control: public, max-age=31536000, immutable` em `/static/*` e por `Cache-Control: private, no-cache` em `/api/books/{id}/file`.
4. **Disputa com capa e anotações:**
   - Em `DOC-EPUB-M`, `/api/books/{id}/cover` tinha **429 KB** (levou 1,30 s) e `/api/books/{id}/annotations` (233 B) levou **2,30 – 3,61 s** disputando banda com o download do EPUB!

---

## 4. Resumo de Impacto Esperado nos Leitores (v1.10.0)

| Fluxo | Baseline Atual (v1.9.2) | Estimativa pós-P0 (Gzip + Immutable + Lazy PDF.js + Adiar Secundários) | Estimativa Cache Quente pós-P0/P1 |
| :--- | :---: | :---: | :---: |
| **PDF Pequeno/Médio (`DOC-PDF-S`/`M`)** | **9,4 – 11,3 s** | **~2,8 – 3,8 s** (-65% a -70%) | **~1,5 – 2,2 s** (apenas chunks Range) |
| **EPUB Pequeno/Médio (`DOC-EPUB-S`/`M`)** | **5,1 – 8,0 s** | **~2,4 – 3,6 s** (-50% a -55%) | **< 0,8 – 1,2 s** (com cache curto/revalidação 304) |
| **PDF Grande não linearizado (`DOC-PDF-L`)** | **> 90 s (timeout)** | Redução moderada com `rangeChunkSize` ajustável (256 KB) + feedback de progresso; ganho definitivo só com linearização (QPDF/Fast Web View) fora do escopo do servidor ou opcional sob demanda (P2). | — |
