# Lectoria — Fase 12.1: Análise de Rede, HTTP/2, Compressão e Cache

**Conclusão executiva:** O túnel Tailscale é **direto (IPv6, sem DERP)** e apresenta **RTT de apenas ~20–26 ms** em conexão HTTP/2 reutilizada. O problema não é latência de ida e volta nem handshake — é a **vazão efetiva limitada por fluxo (~380–450 KB/s ≈ 3,0–3,6 Mbps)** combinada com **megabytes de payloads não comprimidos e sem política de cache imutável**, causando contenção severa de banda no link HTTP/2.

---

## 1. Decomposição da Latência de Rede (Ambiente C — Tailscale Serve HTTPS)

| Componente | Mediana Medida | Observação |
| :--- | ---: | :--- |
| **Conexão TCP** (nova conexão) | 15,3 – 18,7 ms | Túnel direto IPv6 (`active; direct`) |
| **Handshake TLS** (nova conexão) | 23,6 – 28,7 ms | HTTP/2 (`h2`) negociado via Tailscale Serve |
| **TTFB em nova conexão** (`/api/health`) | 68,8 – 70,4 ms | TCP + TLS + 1 RTT + ~2 ms servidor |
| **TTFB em conexão HTTP/2 reutilizada** | **25,6 – 26,2 ms** | Baixa latência base (~24 ms RTT + ~2 ms VPS) |
| **Vazão efetiva em payloads ≥ 250 KB** | **~380 – 450 KB/s (~3,1 – 3,6 Mbps)** | `1.087 KB` em `2,52 s` = `431 KB/s`; `524 KB` em `1,20 s` = `437 KB/s`; `1.363 KB` em `3,44 s` = `396 KB/s` |

> **Implicação prática:** Cada **1 MB desnecessário** enviado pela rede custa aproximadamente **2,3 a 2,6 segundos** de tempo real de espera para o usuário — e bloqueia/atrasa outras requisições simultâneas compartilhando o mesmo túnel HTTP/2 (como observado no leitor, onde `/api/books/{id}/collective-reads` de apenas 22 bytes levou **2,79 s** por disputar banda com `pdf.worker.min.js` e chunks de 512 KB).

---

## 2. Auditoria de Compressão HTTP (`gzip`)

Atualmente, `main.py` **não possui `GZipMiddleware`** (nem compressão no Tailscale Serve). **100% dos arquivos HTML, CSS, JS e JSON trafegam em texto puro (`Content-Encoding` ausente).**

### 2.1. Assets Estáticos (`static/` — 16 arquivos)

| Arquivo | Bruto (B) | Gzip Nível 6 (B) | Economia | Tempo Remoto Atual (med) | Tempo Estimado pós-Gzip* |
| :--- | ---: | ---: | ---: | ---: | ---: |
| `/static/vendor/pdfjs/pdf.worker.min.js` | 1.087.212 | 292.326 | **-73,1%** | **2.587,8 ms** | ~750 ms |
| `/static/vendor/pdfjs/pdf.min.js` | 320.004 | 89.287 | **-72,1%** | **705,9 ms** | ~250 ms |
| `/static/js/app.js?v=1.9.2` | 239.199 | 46.681 | **-80,5%** | **236,7 ms** (máx 568 ms) | ~140 ms |
| `/static/vendor/epubjs/epub.min.js` | 223.875 | 63.985 | **-71,4%** | **385,3 ms** | ~180 ms |
| `/static/js/reader.js?v=1.9.2` | 204.223 | 43.884 | **-78,5%** | **215,2 ms** (máx 575 ms) | ~135 ms |
| `/static/js/knowledge.js?v=1.9.2` | 167.265 | 32.267 | **-80,7%** | **339,3 ms** | ~110 ms |
| `/static/css/style.css?v=1.9.2` | 136.703 | 21.747 | **-84,1%** | **319,3 ms** | ~90 ms |
| `/` (`static/index.html`) | 102.097 | 19.092 | **-81,3%** | **128,3 ms** | ~85 ms |
| `/static/vendor/epubjs/jszip.min.js` | 97.630 | 28.533 | **-70,8%** | **137,3 ms** | ~100 ms |
| `/static/reader.html` | 60.891 | 12.150 | **-80,0%** | **103,9 ms** | ~80 ms |
| `/static/js/reader-knowledge.js?v=1.9.2` | 56.318 | 10.937 | **-80,6%** | **108,2 ms** | ~75 ms |
| `/static/js/shared_reading.js?v=1.9.2` | 52.807 | 10.626 | **-79,9%** | **103,8 ms** | ~75 ms |
| `/static/js/book-knowledge.js?v=1.9.2` | 42.640 | 8.336 | **-80,5%** | **95,5 ms** | ~70 ms |
| `/static/js/knowledge-api.js?v=1.9.2` | 27.770 | 7.064 | **-74,6%** | **88,9 ms** | ~70 ms |
| **Total do shell `/`** (HTML+CSS+5 JS) | **768.481 B** | **145.813 B** | **-81,0%** | ~1,6–2,0 s (frio) | ~0,35–0,45 s |
| **Total do leitor PDF** (HTML+CSS+JS+PDF.js) | **1.893.115 B** | **481.466 B** | **-74,6%** | ~4,5–5,5 s (frio) | ~1,1–1,4 s |

*\*Estimativa em conexão reutilizada (~430 KB/s + 26 ms RTT).*

### 2.2. Payloads JSON da API

| Endpoint | Registros | Bruto (B) | Gzip Nível 6 (B) | Economia | Remoto Atual (med / p95) |
| :--- | ---: | ---: | ---: | ---: | ---: |
| `GET /api/books?sort_by=title&order=asc` | 193 livros | **301.593 B** | **34.882 B** | **-88,4%** | **605,3 ms / 763,7 ms** |
| `GET /api/discovery/shelves` | Prateleiras | **200.920 B** | **24.666 B** | **-87,7%** | **387,9 ms / 481,7 ms** |
| `GET /api/taxonomy/tree` | Árvore | 3.177 B | 757 B | -76,2% | 78,4 ms / 82,7 ms |

> **Nota sobre o payload de `/api/books`:** O endpoint envia ~1,56 KB por livro (incluindo `description` completa, `manual_lock_fields`, `last_error_reason`, etc., campos que **não são exibidos nos cards da grade**, apenas no modal de detalhes!). Comprimir com gzip já reduz 301 KB → 34,9 KB; enxugar campos desnecessários na listagem reduz ainda mais o custo de serialização e parse JSON.

---

## 3. Auditoria de Cabeçalhos de Cache (`Cache-Control`, `ETag`, `304`)

| Rota | Cabeçalho Atual | Problema Diagnosticado |
| :--- | :--- | :--- |
| `/static/*` (`app.js?v=1.9.2`, `style.css?v=1.9.2`, `pdf.worker.min.js`, etc.) | **AUSENTE** (apenas `ETag` e `Last-Modified` do `StaticFiles`) | Sem `Cache-Control`, o navegador recorre a cache heurístico fraco ou revalidações condicionais (`304`) frequentes. Além disso, os arquivos em `/static/vendor/*` nem sequer usam query string `?v=1.9.2`. Com `?v=X.Y.Z` já mantido sincronizado por teste automatizado, ativos versionados deveriam receber `Cache-Control: public, max-age=31536000, immutable`. |
| `/` e `/reader/{id}` (HTML) | `Cache-Control: no-cache` + `ETag` | Adequado para HTML (garante atualização de versão), mas precisa de `gzip` (102 KB → 19 KB). |
| `/api/books/{id}/cover` | `Cache-Control: private, max-age=3600` (apenas 1 h) | **Sem URL versionada por `cover_updated_at` / hash** na listagem e sem fast-path `304 If-None-Match` antes de ler a imagem no `covers_service.py`. Após 1 h (ou em reload), todas as capas expiram. Com versão na URL (`?v=<hash_ou_ts>`), pode usar `private, max-age=86400` ou `immutable` com invalidação instantânea ao trocar capa. |
| `/api/books/{id}/file` | `Cache-Control: private, no-cache` + `ETag` | Para PDFs com Range 206, `no-cache` força o navegador a revalidar/rebaixa chunks a cada reabertura. Para EPUBs (GET 200 completo), `If-None-Match` existe, mas `no-cache` exige round-trip de revalidação e, se o navegador não reaproveitar o cache, baixa 0,8–1,4 MB inteiros novamente (~2–3,5 s). Usar `private, max-age=300` (ou `must-revalidate` com versão) reduz re-downloads imediatos. |
| `/api/books`, `/api/discovery/shelves`, `/api/taxonomy/tree` | Sem `Cache-Control` / sem `ETag` | Re-trafegam 201–301 KB inteiros em toda troca de sessão ou reload. |

---

## 4. Impacto da Ausência de Cache Imutável em Recarregamento Quente

Nos testes Playwright (Cenários S01, S02, S11):
- Em `S02` (shell não autenticado, `page.reload()`): **668 KB** de estáticos foram re-transferidos integralmente, levando **1,95–2,18 s** mesmo em cache quente.
- Em `S11` (leitor PDF `DOC-PDF-S` e `DOC-PDF-M`, `page.reload()`): **1,83 MB** de estáticos + **614–939 KB** de chunks PDF foram re-transferidos, mantendo o tempo quente em **9,4–11,3 s** — praticamente idêntico ao tempo frio (**9,4–10,6 s**)!
- **Observação metodológica:** `page.reload()` envia `Cache-Control: max-age=0` (simulando F5 do usuário), o que expõe a falta de `immutable` e `304` rápido; na navegação normal entre abas/páginas (`warm_nav`), um `Cache-Control: public, max-age=31536000, immutable` elimina completamente o tráfego de rede dos estáticos (**0 bytes transferidos, 0 ms de rede**).
