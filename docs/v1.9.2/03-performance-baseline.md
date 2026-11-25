# Lectoria — Fase 11.1: Baseline de Performance e Diagnóstico de Atrasos

- **Objetivo**: Investigar a origem real dos atrasos percebidos no uso cotidiano percorrendo o caminho completo:
  `Usuário → Navegador → Rede/Tailscale → Proxy → FastAPI/Uvicorn → SQLite/Disco → Resposta → Renderização`.
- **Rigor metodológico**: Todas as métricas abaixo distinguem explicitamente **medições reais efetuadas em `2026-11-23`** de **hipóteses pendentes de profiling detalhado**. Nenhum benchmark destrutivo ou carga sintética foi aplicado em produção.

---

## 1. Inventário Real da Infraestrutura em Produção (`lectoria-vps`)

| Recurso | Valor Medido (`2026-11-23 19:38 UTC`) | Avaliação |
| :--- | :--- | :--- |
| **CPU** | `1 vCPU` — **AMD EPYC 9355P** (1 core, 1 thread) | CPU moderno de alto IPC; `load average: 0.04, 0.02, 0.00` (ociosa). |
| **RAM Total / Disponível** | `3,8 GiB` total · `732 MiB` usada · **`3,1 GiB` disponível** | Folga ampla de memória no host (~81% livre). |
| **Swap** | `2,0 GiB` (`/swapfile`) · **`512 KiB` usados** | Praticamente intocada (zero pressão de memória). |
| **Disco** | `/dev/sda1` `48 GiB` · `18 GiB` usados (`38%`) · `30 GiB` livres | Folga confortável. |
| **Container Lectoria** | `uvicorn --workers 1`, `mem_limit: 768m`, RSS real **`81,6 MiB`** (`64,9 MiB` cgroup), `0` eventos de OOM/pressão | Uso baixíssimo de RAM (~10% do teto de 768 MiB). |
| **Demais Serviços na VPS** | `AdGuardHome` (`119 MiB`), `dockerd` (`73 MiB`), `tailscaled` (`48 MiB`), `containerd` (`37 MiB`), `fail2ban` (`32 MiB`), `caddy` (`29 MiB`) | Convivência completamente saudável e leve. |
| **Banco SQLite** | `/srv/nosreme/lectoria/data/lectoria.db` = **`6,5 MiB`** (`page_size=4096`, `page_count=1641`, `freelist=203`) | Pequeno o suficiente para caber **inteiro** no page cache do Linux (`3,1 GiB` buff/cache). |
| **Volume de Dados** | `238` livros no banco (`193` arquivos físicos `.pdf`/`.epub`), `5` usuários, `54` registros de progresso, `12` notas, `4` conceitos, `1` reflexão, `4` conexões | Escala pessoal pequena. |
| **Tamanho dos Livros** | Total `6.868,4 MB` (`6,9 GiB`), mínimo `0,09 MB`, **mediana `16,22 MB`**, **p95 `124,52 MB`**, **máximo `377,36 MB`** | Arquivos significativamente grandes (especialmente p95 > 124 MB). |
| **Proxy / Rede** | `tailscale serve` (`https://srv2137600.tailf5c3ce.ts.net`) → `http://127.0.0.1:8765` | HTTP/2 + TLS terminado no `tailscaled`. |

---

## 2. Medições Reais Realizadas: Loopback (VPS) vs. Acesso Remoto (Tailscale)

### 2.1 Latência Interna na VPS (`http://127.0.0.1:8765`, 5 amostras por endpoint)

| Endpoint | Tamanho | TTFB (min–máx) | Tempo Total (min–máx) | Mediana Total |
| :--- | :---: | :---: | :---: | :---: |
| `/api/health` | `60 B` | `1,49 – 2,82 ms` | `1,63 – 3,04 ms` | **`1,91 ms`** |
| `/api/auth/status` | `93 B` | `4,33 – 6,25 ms` | `4,57 – 6,38 ms` | **`4,87 ms`** |
| `/` (`index.html`) | `102.147 B` | `2,02 – 3,53 ms` | `2,72 – 4,78 ms` | **`3,03 ms`** |
| `/static/css/style.css?v=1.9.1` | `136.703 B` | `1,92 – 2,50 ms` | `3,23 – 4,34 ms` | **`3,36 ms`** |
| `/static/js/app.js?v=1.9.1` | `238.903 B` | `1,74 – 2,56 ms` | `3,31 – 4,26 ms` | **`3,67 ms`** |

**Conclusão direta**: o servidor FastAPI/Uvicorn + SQLite + disco na VPS responde em **2 a 5 milissegundos**! O backend em si é extremamente rápido nesses endpoints.

### 2.2 Latência Remota Real via Tailscale (`https://srv2137600.tailf5c3ce.ts.net`, 3 amostras por endpoint, `Accept-Encoding: gzip, br`)

| Endpoint | `Content-Encoding` | `Cache-Control` | Tamanho Transferido | TTFB (mediana) | Tempo Total (min–máx) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `/api/health` | *nenhum* | *nenhum* | `60 B` | `61,7 ms` | `60,3 – 63,8 ms` |
| `/api/auth/status` | *nenhum* | *nenhum* | `93 B` | `71,3 ms` | `64,1 – 72,9 ms` |
| `/` (`index.html`) | **AUSENTE** | `no-cache` | `102.147 B` | `70,9 ms` | **`202,1 – 301,4 ms`** |
| `/static/css/style.css?v=1.9.1` | **AUSENTE** | **AUSENTE** (+ `Set-Cookie`!) | `136.703 B` | `66,3 ms` | **`119,8 – 145,6 ms`** |
| `/static/js/app.js?v=1.9.1` | **AUSENTE** | **AUSENTE** (+ `Set-Cookie`!) | `238.903 B` | `64,4 ms` | **`137,2 – 431,4 ms`** |

---

## 3. Gargalos Confirmados por Evidência vs. Hipóteses Pendentes

### 3.1 Gargalos **CONFIRMADOS** (Evidência Direta)

1. **Ausência total de compressão HTTP (`GZipMiddleware` / Brotli) + ativos estáticos grandes** *(EVIDÊNCIA REAL)*:
   - `main.py` não registra `GZipMiddleware` e o `tailscale serve` repassa a resposta sem comprimir.
   - A página inicial (`/`) exige ~**`763 KB` descomprimidos** (`index.html` 100 KB + `style.css` 134 KB + `app.js` 234 KB + `knowledge.js` 164 KB + `shared_reading.js` 52 KB + `book-knowledge.js` 42 KB + `knowledge-api.js` 28 KB) — mais ~`100+ KB` do JSON de `GET /api/books` (238 livros).
   - O leitor (`/reader`) exige ~**`1,9 MB` descomprimidos** (`pdf.worker.min.js` 1,1 MB + `pdf.min.js` 313 KB + `reader.js` 200 KB + `epub.min.js` 219 KB + `jszip.min.js` 96 KB + `style.css` 134 KB + `reader.html` 60 KB + `reader-knowledge.js` 55 KB).
   - Sobre Tailscale (RTT ~60 ms), baixar `app.js` sem compressão chegou a **`431 ms` em um único arquivo**. Com `GZipMiddleware` (nível 6), HTML/CSS/JS/JSON tipicamente encolhem **70–80%** (`~763 KB → ~160 KB`), cortando drasticamente o tempo de transferência.
2. **Cache estático ineficiente + `Set-Cookie` espúrio em `/static/*`** *(EVIDÊNCIA REAL)*:
   - `StaticFiles` do Starlette não envia `Cache-Control: public, max-age=31536000, immutable` mesmo para URLs com cache-busting `?v=1.9.1`.
   - O middleware CSRF (`main.py:316-328`) emite `Set-Cookie: lectoria_csrf=...` em todas as requisições `/static/*` sem cookie.
   - Resultado: o navegador precisa revalidar ETags (`304 Not Modified`, pagando 1 RTT de ~60–70 ms por recurso ou por lote) em vez de servir CSS/JS instantaneamente do cache em disco (`0 ms`).
3. **Carregamento síncrono de scripts não usados na primeira dobra** *(EVIDÊNCIA NO HTML)*:
   - `static/index.html` carrega `knowledge.js` (`164 KB`) e `shared_reading.js` (`52 KB`) como `<script>` bloqueantes no shell inicial mesmo quando o usuário só abre a aba **Descoberta** ou **Acervo**.

### 3.2 Hipóteses **PENDENTES DE MEDIÇÃO** (Para a Fase 12.1 — Profiling Aprofundado)

1. **Hipótese H1 — Abertura de EPUBs grandes e PDFs p95 (`124–377 MB`) sobre Tailscale**:
   - PDFs usam HTTP Range (chunks de 512 KB), mas PDFs digitalizados com muitas páginas/imagens pesadas podem disparar dezenas de Range requests sequenciais de ~60 ms RTT cada.
   - EPUBs são baixados integralmente como ZIP pelo `EPUB.js` + `JSZip` no navegador antes da primeira página renderizar. *Ainda não medido em produção autenticada.*
2. **Hipótese H2 — Carregamento paralelo de capas (`/api/books/{id}/cover`) e extração na primeira visita**:
   - Na abertura do Acervo (lote de 36 cards com `loading="lazy"`), cada capa faz uma requisição HTTP separada com `Cache-Control: private, max-age=3600` (apenas 1 hora!). Após 1 hora, 36 capas precisam ser revalidadas/rebaixadas sobre Tailscale.
3. **Hipótese H3 — `GET /api/books` sem paginação + filtragem/ordenação e `innerHTML` em lote no cliente (`app.js`)**:
   - `app.js:623` baixa todos os 238 livros de uma vez e constrói cards via concatenação `innerHTML` em lotes de 36. O tempo exato de JSON serialization + TTFB autenticado + *long tasks* (>50 ms) no navegador precisa ser medido com Playwright trace/Performance API.
4. **Hipótese H4 — `async def` com operações síncronas residuais ou contenção no `uvicorn --workers 1`**:
   - A Fase 8 converteu 174 rotas bloqueantes para `def`, mas restam 9 rotas `async def` (ex.: `update_progress`, `upload_custom_book_cover_endpoint`, `cache_rendered_book_cover_endpoint`, `preview_annotations_import`). Precisa-se medir se alguma delas bloqueia o event loop sob uso real (ex.: durante `sendBeacon` + troca de página).

---

## 4. Metodologia de Medição e Matriz de Cenários de Referência (Seção 11)

Todos os cenários intensivos devem ser medidos em **ambiente isolado local (com emulação de latência de rede de 60 ms / 10 Mbps via CDP/Playwright)** + **observação passiva leve** em produção (sem criar dados de teste nas contas reais e sem registrar tokens/cookies).

| # | Cenário | Ambiente / Cache | Repetições | Métricas a Coletar |
| :-: | :--- | :--- | :-: | :--- |
| 1 | Login (`/api/auth/login`) | Isolado (frio) | 5 | TTFB, tempo total (mediana/p95), tempo `scrypt` backend |
| 2 | Carregamento inicial da biblioteca (`/`) | Isolado (frio + quente) + VPS passivo | 10 | FCP, LCP, TTFB, bytes transferidos, total de requests, Long Tasks >50 ms |
| 3 | Navegação entre abas (Descoberta → Acervo → Atlas → Clubes) | Isolado (quente) | 10 | Tempo clique → paint, chamadas HTTP disparadas, Long Tasks |
| 4 | Pesquisa de livros (Acervo) | Isolado (238 e 1.000 livros) | 10 | Tempo input → DOM atualizado, reflows |
| 5 | Abertura de PDF (pequeno `1 MB`, mediano `16 MB`, grande `125 MB`) | Isolado (+ emulação 60 ms RTT) | 5 cada | Tempo até shell, 1ª página renderizada, nº de Range 206 requests, RAM aba |
| 6 | Abertura de EPUB (pequeno `1 MB`, grande `30 MB`) | Isolado (+ emulação 60 ms RTT) | 5 cada | Tempo download ZIP, descompressão JSZip, 1ª página renderizada |
| 7 | Navegação no leitor (próxima página, salvar progresso, carregar anotações) | Isolado | 10 | Tempo troca de página, latência `/progress` e `/annotations`, FPS |
| 8 | Abertura do Atlas (Visão Geral + Conceitos + Reflexões) | Isolado | 10 | TTFB `/api/knowledge/*`, render DOM |
| 9 | Pesquisa no Atlas (FTS5) | Isolado (base real-like + 1.000 itens) | 10 | Latência backend SQLite FTS5, tempo total UI |
| 10 | Criação de conceito | Isolado | 5 | Latência `POST /api/knowledge/concepts`, re-render lista |
| 11 | Edição e salvamento de reflexão (curta e 20.000 chars) | Isolado | 5 | Latência preview por tecla, Long Tasks, `PUT` save |
| 12 | Visualização do mapa SVG | Isolado (20 e 80 nós) | 5 | Latência `/graph`, tempo layout SVG |
| 13 | Abertura de detalhes de um livro | Isolado | 10 | Chamadas em paralelo (`/books/{id}`, `/reviews`, `/knowledge`), tempo modal |

### Metas Iniciais de Performance (Provisórias — a refinar após Fase 12.1)
- **Alternância de abas (cache quente)**: `< 100 ms` até resposta visual.
- **Long tasks na thread principal**: eliminar tarefas `> 100 ms` e reduzir tarefas `> 50 ms` durante digitação/navegação.
- **Payload inicial (`/`) com compressão**: redução `>= 65%` em bytes transferidos (`~763 KB → < 250 KB`).
- **Assets versionados (`?v=X.Y.Z`) em revisita**: `0` revalidações de rede (`200 from disk cache`).
- **Preview Markdown**: `< 16 ms` (1 frame) em reflexões comuns e sem bloqueio perceptível em textos longos.
- **Estabilidade de memória**: container `< 256 MiB` em uso normal; zero vazamento progressivo de heap JS no navegador.
