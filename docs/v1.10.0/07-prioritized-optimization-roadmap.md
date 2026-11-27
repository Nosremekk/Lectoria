# Lectoria — Fase 12.1: Roadmap Priorizado de Otimizações (v1.10.0)

Este roadmap ordena as otimizações exclusivamente por **impacto perceptível comprovado pelos dados da Fase 12.1**, respeitando todas as diretrizes arquiteturais da Lectoria (zero frameworks frontend, zero build step, SQLite WAL, `--workers 1`, hardening de segurança e privacidade).

---

## 1. Matriz de Priorização (P0 a P3)

### P0 — Impacto Crítico Imediato (Máximo Ganho Perceptível, Baixo Risco)

| ID | Otimização | Problema Comprovado (Baseline v1.9.2) | Ganho Esperado | Risco / Complexidade |
| :--- | :--- | :--- | :--- | :--- |
| **P0.1** | **Compressão HTTP (`GZipMiddleware`) no FastAPI** (`minimum_size=500`, `compresslevel=5`) | 100% de HTML/CSS/JS/JSON trafega sem compressão em túnel de ~400 KB/s: shell `/` = 768 KB, leitor PDF = 1,89 MB, `/api/books` = 302 KB, `/api/discovery/shelves` = 201 KB. | Redução de **72% a 88%** no volume de texto: `/` cai 768→146 KB; `/api/books` 302→35 KB (605 ms → ~120 ms); `pdf.worker.min.js` 1,09 MB→292 KB (2,59 s → ~0,75 s). | Muito baixo (middleware padrão Starlette, CPU VPS < 5%). |
| **P0.2** | **Cache HTTP imutável para estáticos versionados (`/static/*`) + `?v=X.Y.Z` nos vendors** | `/static/*` não envia `Cache-Control` algum; até em navegação quente (`warm_nav`), 1,04–1,83 MB de estáticos são re-transferidos! | **0 bytes / 0 ms** de rede para CSS/JS/PDF.js/EPUB.js em visitas recorrentes e trocas de livro. | Muito baixo (sincronizado com `APP_VERSION` e `test_v151_frontend_backend_version_alignment`). |
| **P0.3** | **Pipeline de miniaturas (thumbnails) de capas + correção do `covers_service.py`** | 213 capas somam **35,5 MB** (média 171 KB, p95 652 KB, máx 3,44 MB / 2550×3300 px). Descoberta baixa 4,3 MB (17,9 s); Acervo baixa 4,7–7,4 MB (**20,2–21,9 s**!). Além disso, `covers_service.py` lê o arquivo inteiro em RAM e executa `zlib.decompress()` em PNGs a cada GET! | Gerar/servir thumbnails de card (ex.: largura máx. 320–360 px, WebP/JPEG q=80, **~15–25 KB**), eliminar `read_bytes()+zlib` no GET, versionar URL da capa (`?v=<hash/ts>`) e aplicar `Cache-Control: private, max-age=86400, immutable` + `ETag`/`304`. **Redução de ~85–90% no tráfego de capas** (Acervo 1ªs 12 capas: **21 s → ~1,5–2,5 s** frio; **< 0,3 s** quente!). | Médio-baixo (geração lazy em disco `/data/covers/thumbs/` mantendo originais intactos; sem alterar schema obrigatório ou compatível com v13). |

---

### P1 — Alto Impacto na Fluidez de Boot e Leitores

| ID | Otimização | Problema Comprovado | Ganho Esperado | Risco / Complexidade |
| :--- | :--- | :--- | :--- | :--- |
| **P1.1** | **Eliminar cascata bloqueante no `initAuth()` (`app.js`) e adiar scripts secundários em `index.html`** | `initAuth()` faz `await Promise.all([loadCollections(), loadTaxonomyTree()])` **antes** de chamar `loadShelves()`/`loadBooks()`; `knowledge.js` (167 KB) e `shared_reading.js` (53 KB) carregam em `/` mesmo sem abrir Atlas ou Clubes. | Disparar `loadShelves()`/`loadBooks()` em paralelo com taxonomia/coleções; carregar `knowledge.js` e `shared_reading.js` sob demanda (ou `defer`/lazy script). Reduz tempo até cards utilizáveis em **~400–800 ms**. | Baixo. |
| **P1.2** | **Carregamento condicional de PDF.js / EPUB.js no leitor + priorização da 1ª página** | `reader.html` baixa `pdf.min.js` (320 KB / 1,93 s) até quando abre um **EPUB**; `reader.js` carrega `jszip.min.js` e `epub.min.js` sequencialmente; `/collective-reads`, `/annotations` e a capa do header disputam banda com a 1ª página. | Carregar `pdf.min.js` apenas para PDF e `jszip`+`epub.min.js` em paralelo apenas para EPUB; adiar `/collective-reads`, `/annotations` e capa mini (usando thumbnail!) até após pintura da 1ª página. Reduz abertura de EPUB em **~2 s** e elimina contenção HTTP/2 na 1ª página do PDF. | Baixo-médio. |
| **P1.3** | **Política de cache inteligente para `/api/books/{id}/file` (EPUB/PDF)** | `Cache-Control: private, no-cache` força re-download completo do EPUB (0,8–1,4 MB = 2–3,5 s) e revalidação de chunks mesmo ao reabrir o livro minutos depois. | Permitir `Cache-Control: private, max-age=300` (ou `max-age=600` com `ETag` baseado em `mtime+size`), preservando privacidade (`private`) e consistência quando o arquivo muda. | Muito baixo. |
| **P1.4** | **Atributos de imagem e controle de concorrência nas prateleiras (`app.js`)** | Descoberta dispara 46–48 capas simultaneamente, saturando o link HTTP/2 quando o usuário muda para Biblioteca (`S03` saltou de 2,3 s para 9,8–14,9 s!). | Adicionar `decoding="async"`, dimensões explícitas (`width`/`height`), `fetchpriority="high"` nas 6–8 capas visíveis iniciais e limitar renderização inicial de capas fora da viewport nas prateleiras horizontais (IntersectionObserver). | Baixo. |

---

### P2 — Melhorias Complementares (Médio Prazo / Casos Extremos)

| ID | Otimização | Problema Comprovado | Ganho Esperado | Risco / Complexidade |
| :--- | :--- | :--- | :--- | :--- |
| **P2.1** | **Payload enxuto em `/api/books` e `/api/discovery/shelves`** | `/api/books` envia `description` completa e metadados de auditoria para todos os 193 livros (301 KB); `/api/discovery/shelves` repete objetos completos entre prateleiras (201 KB). | Reduz ~35–50% do JSON bruto adicionalmente ao gzip, acelerando serialização Python e parse JSON no cliente (inclusive quando o acervo crescer para 1.000+ livros). | Baixo (manter compatibilidade de contrato com campos usados pelos cards e filtros). |
| **P2.2** | **Tuning de `rangeChunkSize` e feedback progressivo para PDFs grandes não linearizados (`DOC-PDF-L`)** | `DOC-PDF-L` (99 MB) exigiu 60 chunks de 512 KB (~29,2 MB) em 90 s sem feedback de progresso de carregamento ao leitor. | Exibir barra/indicador de progresso de carregamento do PDF no leitor (`loadingTask.onProgress`), avaliar `rangeChunkSize` adaptativo (ex.: 256 KB vs 512 KB) e documentar ferramenta opcional de linearização offline (`qpdf --linearize`) para PDFs muito grandes do acervo. | Baixo-médio. |
| **P2.3** | **`ETag` / `304` em `/api/books` e `/api/discovery/shelves`** | Re-transferência de listagens inalteradas ao recarregar a página. | 0 bytes de corpo quando acervo/progresso não mudaram. | Médio (cuidado com invalidação por `user_id` + progresso + metadados). |

---

### P3 — Explicitamente Descartadas (Sem Evidência de Benefício / Contraproducentes)

| Ideia Avaliada | Decisão | Justificativa Quantitativa |
| :--- | :---: | :--- |
| Migrar backend de Python/FastAPI para Go/Rust/Node | **DESCARTAR** | Backend responde em 1,9–29 ms com CPU máx. de **0,9%**. Reescrita teria ganho perceptível próximo de zero. |
| Trocar SQLite por PostgreSQL ou adicionar Redis | **DESCARTAR** | SQLite (6,4 MiB, WAL, 129 índices) responde em < 2 ms e viola a diretriz arquitetural canônica. |
| Aumentar `--workers` do Uvicorn (> 1) | **DESCARTAR** | Worker único usa 0,9% de CPU e 97 MiB RSS; múltiplos workers desperdiçariam RAM e aumentariam risco de lock de escrita no SQLite. |
| Upgrade de plano da VPS (mais vCPUs / RAM) | **DESCARTAR** | VPS tem 98–99% CPU ociosa, 3,1 GiB RAM livre, 0 throttling, 0 OOM, disco < 0,3 ms. |
| Introduzir React/Vue/Vite/Webpack | **DESCARTAR** | `0` Long Tasks (> 50 ms) no frontend Vanilla JS; filtros rodam em 42–59 ms; viola governança do projeto. |

---

## 2. Plano de Execução Sugerido para as Fases 12.2 a 12.5 (`v1.10.0`)

| Fase | Escopo | Entregáveis e Critérios de Aceite |
| :--- | :--- | :--- |
| **Fase 12.2 — Camada HTTP, Cache e Compressão (P0.1 + P0.2 + P1.3)** | `GZipMiddleware` no `main.py`; `Cache-Control: public, max-age=31536000, immutable` para `/static/*` versionados (incluindo `?v=APP_VERSION` nos vendors PDF.js/EPUB.js); `Cache-Control: private, max-age=300` em `/api/books/{id}/file`. | Redução ≥ 70% em bytes de texto na rede; 0 bytes de estáticos em `warm_nav`; 100% dos 400+ testes passando + novos testes de headers HTTP/cache/gzip. |
| **Fase 12.3 — Pipeline de Miniaturas de Capas e `covers_service.py` (P0.3)** | Remover `read_bytes()+zlib.decompress()` do caminho quente de `GET /cover`; gerar thumbnails otimizados (~320–360 px WebP/JPEG ≤ 30 KB) em `/data/covers/thumbs/` (lazy + cache seguro, sem alterar originais); versionar `cover_url` com hash/timestamp + `ETag`/`304` + `max-age=86400, immutable`. | Redução ≥ 80% nos bytes de capas em Descoberta e Acervo; tempo até primeiras 12 capas no Acervo cair de **20–22 s** para **≤ 3 s** (frio) e **< 0,5 s** (quente). |
| **Fase 12.4 — Boot do Frontend e Otimização dos Leitores PDF/EPUB (P1.1 + P1.2 + P1.4 + P2.1/P2.2)** | Desbloquear `initAuth()` (paralelizar prateleiras/livros vs taxonomia/coleções); lazy-load de `knowledge.js`/`shared_reading.js`; carregamento condicional de `pdf.min.js` vs `jszip`+`epub.min.js` (em paralelo); adiar requisições secundárias no leitor até pintura da 1ª página; `decoding="async"` + controle de capas nas prateleiras; indicador de progresso no PDF.js; payload enxuto opcional. | Abertura de PDF pequeno/médio cair de **9,4–11,3 s** para **≤ 3,8 s** (frio) / **≤ 2,2 s** (quente); EPUB cair de **5,1–8,0 s** para **≤ 3,5 s** (frio) / **≤ 1,2 s** (quente); 0 regressões nos 56 testes E2E Playwright. |
| **Fase 12.5 — Re-medição Comparativa, Validação Final e Release `v1.10.0`** | Re-executar a mesma suíte de benchmarks da Fase 12.1 nos 3 ambientes (A, B, C), gerar relatório comparativo Antes × Depois (`v1.9.2` vs `v1.10.0`), atualizar `APP_VERSION = "1.10.0"`, validar CI completo + produção. | Ganho real comprovado em tabela Antes × Depois nos 13 cenários reais, zero regressões de segurança/privacidade/hardening, tag e release `v1.10.0`. |
