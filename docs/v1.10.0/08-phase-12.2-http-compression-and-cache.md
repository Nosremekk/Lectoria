# Lectoria — Fase 12.2: Otimização da Camada HTTP, Compressão e Cache (`v1.10.0`)

**Data:** 2026-11-27  
**Branch de implementação:** `perf/v1.10.0-phase-12.2-http-cache` (derivada de `perf/v1.10.0-baseline` @ `286f13e`)  
**Versões preservadas (sem bump nesta fase):** `APP_VERSION = 1.9.2` | `SCHEMA_VERSION = 13`  
**Produção:** Intocada (nenhum deploy, nenhum ajuste em `compose.yaml` ou banco real).

---

## 1. Decisões Arquiteturais

1. **Módulo dedicado (`http_cache_service.py`), zero novas dependências:**
   - Em vez de sobrecarregar `main.py` ou usar o `GZipMiddleware` genérico do Starlette (que comprime indiscriminadamente binários/PDFs/imagens/rotas sensíveis), foi criado [`http_cache_service.py`](../../http_cache_service.py) usando exclusivamente a biblioteca padrão do Python (`gzip`, `hashlib`, `secrets`) e primitivos ASGI do Starlette.
2. **Compressão HTTP seletiva (`SelectiveGZipMiddleware`, gzip nível 5, `minimum_size=500`):**
   - **Allowlist estrita de MIME types textuais:** `text/html`, `text/css`, `text/javascript`, `application/javascript`, `application/x-javascript`, `application/json`, `text/plain`, `text/xml`, `application/xml`, `image/svg+xml`.
   - **Exclusões obrigatórias:**
     - Apenas `GET` com status `200` (nunca `HEAD`, `206 Partial Content`, `304 Not Modified`, requisições com `Range`, `Content-Range`, `Content-Encoding` prévio, `Content-Disposition: attachment` ou `Cache-Control: no-transform`).
     - Rotas `/api/auth/*` excluídas como mitigação preventiva contra ataques de canal lateral de compressão (BREACH) em payloads de autenticação/sessão.
     - Rotas `/api/backups/*`, `/api/knowledge/export*` e sufixos `/file`, `/download`, `/cover` excluídos (PDFs, EPUBs, imagens, ZIPs e streaming).
   - **Conformidade RFC 9110:** respeita `Accept-Encoding` com q-values (inclusive `gzip;q=0`), adiciona `Vary: Accept-Encoding` em todas as respostas elegíveis (comprimidas ou não), recalcula `Content-Length` exato e converte `ETag` forte em fraco (`W/"..."`) quando o corpo é comprimido.
   - **Cache em memória limitado para estáticos comprimidos:** respostas `/static/*` comprimidas ficam cacheadas em dicionário limitado (≤ 64 entradas, < 0,7 MiB RAM para todos os 16 assets), indexadas por `(path, etag)`, zerando o custo de CPU em requisições repetidas de `pdf.worker.min.js`, `app.js`, `style.css`, etc., sem escrever nada no disco (`ReadonlyRootfs=true`).
3. **Cache imutável baseado em hash SHA-256 de conteúdo (`AssetManifest` + `HashedStaticFiles`):**
   - `APP_VERSION` isoladamente **não** garante imutabilidade entre builds `stable` com a mesma versão.
   - O `AssetManifest` calcula em memória os 12 primeiros caracteres hex do SHA-256 de cada arquivo em `static/`, invalidando automaticamente quando `(mtime_ns, size, ino)` muda no disco.
   - As rotas HTML (`/`, `/reader/{book_id}`, `/reader.html`) reescrevem referências `/static/*` para `/static/<path>?v=<APP_VERSION>&h=<sha256_12>` (preservando `?v=1.9.2` para compatibilidade com `test_v151_frontend_backend_version_alignment`) e injetam `<meta name="lectoria-asset-manifest" content="...">` (100% compatível com `CSP script-src 'self'`, sem scripts inline) para assets carregados dinamicamente por JS/Workers (`pdf.worker.min.js`, `jszip.min.js`, `epub.min.js`).
   - `HashedStaticFiles` aplica `Cache-Control: public, max-age=31536000, immutable` **somente** quando `?h=` coincide exatamente com o SHA-256 atual do conteúdo (via `secrets.compare_digest`). Sem `?h=` ou com hash divergente/antigo, aplica `Cache-Control: public, no-cache`. Arquivos `.html` sob `/static/` nunca recebem `immutable`.
   - O HTML principal e do leitor sempre retornam `Cache-Control: no-cache` + `ETag` derivado do HTML renderizado (que muda automaticamente se qualquer asset mudar), com suporte completo a `If-None-Match` (`304 Not Modified` sem corpo) e `HEAD`.
4. **Cache seguro de documentos privados (`/api/books/{id}/file`):**
   - **Decisão de segurança:** **NÃO** aplicar `private, max-age=300` em documentos privados, pois o cache HTTP privado do navegador não é isolado entre contas distintas no mesmo perfil de navegador.
   - **Política adotada:** preservar `Cache-Control: private, no-cache` (exigindo revalidação no servidor após `require_user` + `resolve_book_file_for_user`), adicionar `Vary: Cookie` (particionamento por sessão), fortalecer o `ETag` com `sha256(st_mtime_ns-st_size-st_ino)[:24]`, adotar matcher RFC 9110 para `If-None-Match` (retornando `304` **apenas** após autorização bem-sucedida e em requisições sem `Range`), preservar `If-Range` para consistência de `206` após substituição de arquivo, e emitir `Clear-Site-Data: "cache"` + `Cache-Control: private, no-store` em `POST /api/auth/logout`.

---

## 2. Correção Metodológica dos Testes de Cache (Fase 12.1 → 12.2)

Antes da implementação, validou-se via CDP (`Network.responseReceived`, `Network.requestServedFromCache`, `Network.loadingFinished`) em ambiente isolado o impacto de `context.route("**/*", ...)` usado na Fase 12.1:

- **Achado comprovado:** `context.route()` ativa `Fetch.enable` no Chromium, o que **desabilita o cache HTTP nativo do navegador**. Por isso, na Fase 12.1, os ensaios `warm` e `warm_nav` transferiram `668.564 B` idênticos ao cache frio.
- **Ação executada:** a correção foi registrada em [`01-phase-12.1-methodology.md`](01-phase-12.1-methodology.md) e [`03-http-network-and-cache-analysis.md`](03-http-network-and-cache-analysis.md). Todas as medições de cache frio, API (`curl`), VPS, capas e leitores frios da Fase 12.1 permanecem válidas. Todos os ensaios de cache quente desta Fase 12.2 foram executados **sem interceptação de rede** (`context.route`/`page.route`) em ambiente isolado com telemetria CDP.

---

## 3. Arquivos Modificados / Criados

| Arquivo | Tipo | Finalidade |
| :--- | :--- | :--- |
| [`http_cache_service.py`](../../http_cache_service.py) | Novo | `SelectiveGZipMiddleware`, `AssetManifest`, `HashedStaticFiles`, `parse_accept_encoding_gzip`, `etag_matches_if_none_match`. |
| [`main.py`](../../main.py) | Editado | Registro de `SelectiveGZipMiddleware`, montagem de `HashedStaticFiles`, renderização HTML com hashes + `ETag`/`304`/`HEAD`, fortalecimento de `/api/books/{id}/file` (`Vary: Cookie`, `ETag` nanosegundo+inode, `If-None-Match` RFC 9110) e `Clear-Site-Data: "cache"` em `/api/auth/logout`. |
| [`static/js/reader.js`](../../static/js/reader.js) | Editado | Helper `resolveLectoriaAssetUrl()` lendo `<meta name="lectoria-asset-manifest">` para `pdf.worker.min.js`, `jszip.min.js` e `epub.min.js`. |
| [`tests/test_v1100_phase122_http_cache.py`](../../tests/test_v1100_phase122_http_cache.py) | Novo | Cobertura automatizada dos 19 cenários obrigatórios da Fase 12.2. |
| [`docs/v1.10.0/01-phase-12.1-methodology.md`](01-phase-12.1-methodology.md) | Atualizado | Registro da correção metodológica do `context.route()` no Chromium. |
| [`docs/v1.10.0/03-http-network-and-cache-analysis.md`](03-http-network-and-cache-analysis.md) | Atualizado | Tabela comparativa com vs. sem `context.route()` via CDP. |

---

## 4. Políticas de Cache e Compressão por Rota

| Rota | `Cache-Control` | `Content-Encoding` | `Vary` | Validadores / Segurança |
| :--- | :--- | :--- | :--- | :--- |
| `/`, `/reader/{book_id}`, `/reader.html` | `no-cache` | `gzip` (se aceito) | `Accept-Encoding` | `ETag` do HTML renderizado (`304`), `HEAD`, CSP, CSRF |
| `/static/*?v=X&h=<sha256_12>` (hash válido, não-HTML) | `public, max-age=31536000, immutable` | `gzip` (tipos textuais ≥ 500 B) | `Accept-Encoding` | `ETag` = hash do conteúdo (`304`), cache gzip em RAM |
| `/static/*` (sem `?h=` ou hash divergente) | `public, no-cache` | `gzip` (tipos textuais ≥ 500 B) | `Accept-Encoding` | `ETag` (`304`), nunca `immutable` |
| `/static/*.html` | `no-cache` | `gzip` (se aceito) | `Accept-Encoding` | Nunca `immutable` |
| `/api/books`, `/api/discovery/shelves`, `/api/taxonomy/tree` | Padrão da rota | `gzip` (≥ 500 B) | `Accept-Encoding` | Integridade JSON verificada |
| `/api/auth/*` | `private, no-store` (logout) | **Nunca comprimido** | — | Mitigação BREACH; `Clear-Site-Data: "cache"` no logout |
| `/api/knowledge/*` | `private, no-store` | `gzip` (≥ 500 B, exceto `/export`) | `Accept-Encoding` | Isolamento por usuário preservado |
| `/api/books/{id}/file` | `private, no-cache` | **Nunca comprimido** | `Cookie` | Auth obrigatória antes de `304`, `Accept-Ranges: bytes`, `ETag`, `If-None-Match`, `If-Range`, `HEAD`, `206` |
| `/api/books/{id}/download`, `/api/books/{id}/cover` | Conforme rota | **Nunca comprimido** | — | Preserva binários/streaming |

---

## 5. Benchmarks Comparativos (Antes × Depois)

### 5.1. Redução de Volume Transferido em Recursos Textuais (Medido em Servidor Real Uvicorn)

*Nota de topologia: medições diretas de payload HTTP contra servidor local isolado Uvicorn (mesmos arquivos reais do repositório). Os payloads de `/api/books` e `/api/discovery/shelves` abaixo usam o tamanho real do catálogo de produção (193 livros) medido na Fase 12.1.*

| Recurso / Conjunto | Antes (v1.9.2, sem gzip) | Depois (Fase 12.2, gzip nível 5) | Redução Medida | Cache-Control pós-12.2 |
| :--- | ---: | ---: | ---: | :--- |
| `/` (`index.html` renderizado) | 102.097 B | **19.676 B** | **-80,7%** | `no-cache` + `ETag` |
| `/static/css/style.css` | 136.703 B | **22.584 B** | **-83,5%** | `public, max-age=31536000, immutable` |
| `/static/js/app.js` | 239.199 B | **47.280 B** | **-80,2%** | `public, max-age=31536000, immutable` |
| `/static/js/knowledge.js` | 167.265 B | **32.841 B** | **-80,4%** | `public, max-age=31536000, immutable` |
| `/static/js/shared_reading.js` | 52.807 B | **10.786 B** | **-79,6%** | `public, max-age=31536000, immutable` |
| `/static/js/book-knowledge.js` | 42.640 B | **8.459 B** | **-80,2%** | `public, max-age=31536000, immutable` |
| `/static/js/knowledge-api.js` | 27.770 B | **7.093 B** | **-74,5%** | `public, max-age=31536000, immutable` |
| **Shell principal `/` total (HTML + 6 estáticos)** | **768.481 B** | **151.380 B** | **-80,3%** | HTML `no-cache`; assets `immutable` |
| `/reader/{id}` (`reader.html` renderizado) | 60.891 B | **12.476 B** | **-79,5%** | `no-cache` + `ETag` |
| `/static/js/reader.js` | 204.223 B | **44.563 B** | **-78,2%** | `public, max-age=31536000, immutable` |
| `/static/js/reader-knowledge.js` | 56.318 B | **11.110 B** | **-80,3%** | `public, max-age=31536000, immutable` |
| `/static/vendor/pdfjs/pdf.min.js` | 320.004 B | **89.936 B** | **-71,9%** | `public, max-age=31536000, immutable` |
| `/static/vendor/pdfjs/pdf.worker.min.js` | 1.087.212 B | **294.428 B** | **-72,9%** | `public, max-age=31536000, immutable` |
| **Leitor PDF estáticos total (HTML + CSS + JS + PDF.js)** | **1.893.115 B** | **482.190 B** | **-74,5%** | HTML `no-cache`; assets `immutable` |
| `/static/vendor/epubjs/epub.min.js` | 223.875 B | **64.338 B** | **-71,3%** | `public, max-age=31536000, immutable` |
| `/static/vendor/epubjs/jszip.min.js` | 97.630 B | **28.691 B** | **-70,6%** | `public, max-age=31536000, immutable` |
| `/api/books` (193 livros reais — baseline 12.1) | 301.593 B | **34.882 B** | **-88,4%** | Dinâmico + `Vary: Accept-Encoding` |
| `/api/discovery/shelves` (193 livros reais — baseline 12.1) | 200.920 B | **24.666 B** | **-87,7%** | Dinâmico + `Vary: Accept-Encoding` |

> **Critério de aceite (≥ 70% de redução agregada em recursos textuais):** **superado em todas as frentes** (-80,3% no shell `/`, -74,5% no leitor PDF, -87,7% a -88,4% nos JSONs principais).

### 5.2. Comportamento Real de Cache no Chromium via CDP (Sem `context.route()`)

| Cenário no Chromium | Antes (v1.9.2, estáticos do shell `/`) | Depois (Fase 12.2, `immutable` + `gzip`) |
| :--- | :--- | :--- |
| **Cache frio** | 6/6 `200`, **668.564 B** na rede | 6/6 `200` (`gzip`), **131.704 B** na rede (**-80,3%**) |
| **Navegação quente (mesma aba)** | 6/6 cache heurístico (`0 B`) | 6/6 `from_memory_cache` (`0 B`, garantido por `immutable`) |
| **Nova aba no mesmo contexto** | 6/6 `from_disk_cache` heurístico (`0 B`) | 6/6 `from_disk_cache` (`0 B`, garantido por `immutable`) |
| **Reload convencional (F5)** | Revalidação condicional (`If-None-Match`/`304`, 6 round-trips) | **6/6 `from_memory_cache` (`0 B`, 0 round-trips de estáticos!)** |
| **Hard reload (`ignoreCache=True`)** | 6/6 `200`, **668.564 B** na rede | 6/6 `200` (`gzip`), **131.704 B** na rede (**-80,3%**) |

### 5.3. Impacto Medido em CPU e Memória (Inclusive na VPS Real)

- **CPU na VPS real (1 vCPU AMD EPYC 9355P):** compressão `gzip` nível 5 de payload JSON equivalente ao catálogo de 193 livros (261 KB): **0,515 ms mediana** (`0,593 ms` p95, `0,768 ms` máx.).
- **CPU em estáticos `/static/*`:** **~0,0 ms por requisição** após o primeiro acesso devido ao cache em RAM por `(path, etag)`.
- **Memória adicional:** **< 0,8 MiB** de RAM total (manifesto de hashes + cache gzip de todos os 16 arquivos estáticos), frente a > 640 MiB livres no container de 768 MiB.

---

## 6. Resultados da Suíte de Testes Automatizados

- **Novos testes da Fase 12.2 (`tests/test_v1100_phase122_http_cache.py`):** **6/6 aprovados** (cobrindo os 19 requisitos obrigatórios: gzip HTML/CSS/JS/JSON, sem gzip/`q=0`, `Vary`, integridade, Range `206`, `HEAD`, `304` sem corpo, downloads PDF/EPUB, exclusão de imagens/binários/`/api/auth/*`, `immutable` estrito, invalidação sob mesmo `APP_VERSION`, `pdf.worker.min.js`, `epub.min.js`/`jszip.min.js`, CSP/CSRF/proxy HTTPS, isolamento entre contas, logout `Clear-Site-Data`, `401` pós-logout sem `304`, substituição de arquivo e `If-Range`).
- **Suíte unitária e de integração completa (`--ignore=tests/e2e`):** **348 passed, 2 skipped (testes que exigem daemon Docker ativo localmente), 0 failed**.
- **Suíte E2E Playwright (`tests/e2e`):** **56 passed, 0 failed** (incluindo leitor PDF com `pdf.worker.min.js` versionado por hash, leitor EPUB com `jszip.min.js`/`epub.min.js`, biblioteca, clubes e Atlas).
- **Total consolidado local:** **404 passed, 2 skipped, 0 failed**.

---

## 7. Limitações, Riscos Residuais e Procedimento de Reversão

### Limitações e Riscos Residuais
- **Revogação de conteúdo já baixado pelo navegador:** uma vez que o navegador recebeu bytes de um PDF/EPUB autenticado e os manteve em RAM da aba aberta (ou em cache antes do logout), o servidor não controla cópias em memória do cliente. Com `Cache-Control: private, no-cache`, `Vary: Cookie`, autorização antes de `304` e `Clear-Site-Data: "cache"` no logout, novas requisições após logout/expiração/revogação são sempre bloqueadas com `401`/`403`/`404`.
- **Medição remota ponta-a-ponta sobre Tailscale:** os ganhos de latência em segundos sobre o túnel Tailscale (~3,5 Mbps) serão medidos em produção/staging controlado somente após autorização de deploy (ou na Fase 12.5); nesta fase foram medidos diretamente os bytes exatos, comportamento CDP de cache e custo real de CPU na VPS.
- **Capas brutas ainda não otimizadas:** permanecem como o maior gargalo restante da biblioteca (~35,5 MB / 15–22 s em Descoberta e Acervo) — escopo exclusivo da **Fase 12.3**.

### Procedimento de Reversão
1. Reverter os commits locais da branch `perf/v1.10.0-phase-12.2-http-cache` (`git revert` ou retorno para `perf/v1.10.0-baseline` / `v1.9.2`).
2. Nenhuma migração de schema (`SCHEMA_VERSION = 13`) e nenhuma alteração de dados em disco ocorreram; a reversão é 100% limpa e imediata.

---

## 8. Recomendações para a Fase 12.3

1. Implementar o pipeline de **miniaturas (thumbnails) de capas** (~320–360 px WebP/JPEG ≤ 25 KB) em `/data/covers/thumbs/` mantendo originais intactos.
2. Remover `read_bytes()` + `zlib.decompress()` do caminho quente `GET /api/books/{id}/cover` em `covers_service.py`.
3. Versionar `cover_url` com hash/timestamp de atualização para habilitar `Cache-Control: private, max-age=86400, immutable` e `304` rápido em capas.
