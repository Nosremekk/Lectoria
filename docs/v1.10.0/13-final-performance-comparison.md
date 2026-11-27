# Lectoria — Comparativo Final de Desempenho (v1.9.2 Baseline vs v1.10.0 Candidata)

**Data:** 2026-11-27  
**Fonte de dados brutos (sanitizados):** `docs/v1.10.0/data/phase-12.5-staging-benchmarks.json`  
**Critério metodológico:** Playwright real em Chromium headless **sem `context.route()` / `page.route()`**, com critérios funcionais estritos (capas com `img.complete && naturalWidth > 0`; PDF com `<canvas>` renderizado `width > 100 && height > 100` e `page-count-display > 0`; EPUB com `iframe` montado e conteúdo/capa visível).

---

## 1. Distinção Explícita de Topologias

| Série | Versão | Topologia de Rede | Observação |
| :--- | :--- | :--- | :--- |
| **Baseline (Fase 12.1)** | `v1.9.2` | Navegador local → **Tailscale HTTPS** → VPS produção | RTT real WAN + TLS Tailscale |
| **Staging Integrado (Fase 12.5)** | `v1.10.0-rc` | Navegador local → **Túnel SSH** → VPS `127.0.0.1:18765` | Mesma VPS/acervo/container hardened (`768 MiB`, `1 vCPU`), sem camada Tailscale Serve |

> **Nota metodológica (Seção 14):** Os números em túnel SSH não devem ser equiparados diretamente aos de Tailscale HTTPS como se fossem topologias idênticas; porém, **reduções de bytes transferidos, número de requisições e comportamento de cache quente/frio são invariantes de topologia**, e os tempos ponta a ponta refletem latência real de rede até a VPS somada ao processamento real do container de 1 vCPU. Adicionalmente, o valor anterior de `289 ms` citado na Fase 12.4 em rede simulada local foi substituído por medições funcionais reais de renderização efetiva de `<canvas>` (727–842 ms em PDFs reais na VPS).

---

## 2. Resultados dos 13 Cenários no Staging Isolado da VPS (`v1.10.0-rc`)

| # | Cenário | Cache Frio (Mediana / Min–Máx) | Cache Quente (Mediana / Min–Máx) | Bytes (Frio → Quente) | Reqs (Frio → Quente) | Meta do Ciclo | Erros Console/JS |
| :--- | :--- | ---: | ---: | ---: | ---: | :--- | ---: |
| 1–2, 9 | **Descoberta (primeiras 8 capas visíveis)** | **677,1 ms** *(595–1311)* | **404,6 ms** *(385–422)* | **606,8 KiB → 113,4 KiB** | 33 → 28 | ~2.500 ms | 0 |
| 3–4 | **Biblioteca (primeiras 12 capas visíveis)** | **388,5 ms** *(368–417)* | **363,1 ms** *(358–370)* | **303,6 KiB → 31,5 KiB** | 13 → 16 | ~3.000 ms | 0 |
| 5 | **Scroll e novas 12 capas** | **417,9 ms** *(406–419)* | **404,7 ms** *(404–413)* | **240,7 KiB → 0,0 KiB** | 14 → 14 | Fluido | 0 |
| 6 | **Busca interativa** | **457,9 ms** *(458–458)* | **456,7 ms** *(456–457)* | **0,0 KiB → 0,0 KiB** | 0 → 0 | Instantâneo | 0 |
| 7 | **Filtros e ordenação** | **309,3 ms** *(308–311)* | **309,1 ms** *(308–309)* | **0,0 KiB → 0,0 KiB** | 0 → 0 | Instantâneo | 0 |
| 8 | **Modal de detalhes do livro** | **33,6 ms** *(31–50)* | **23,6 ms** *(21–32)* | **57,4 KiB → 57,4 KiB** | 8 → 8 | ~700 ms | 0 |
| 10 | **Abertura do Atlas (lazy load)** | **292,7 ms** *(293–313)* | **300,2 ms** *(280–323)* | **32,6 KiB → 32,6 KiB** | 2 → 2 | Sob demanda | 0 |
| 11a | **PDF pequeno (2,03 MB, original)** | **837,9 ms** *(834–842)* | — | 2,02 MB Range (4 reqs) | 17 | 2–4s frio | 0 |
| 11b | **PDF médio (15,07 MB, original)** | **744,8 ms** *(727–762)* | — | 1,44 MB Range (3 reqs) | 16 | 2–4s frio | 0 |
| 11c | **PDF médio (15,07 MB, derivado QPDF 12.2.0)** | **757,1 ms** *(756–758)* | — | **1,28 MB Range (-11,1%)** (3 reqs) | 16 | 2–4s frio | 0 |
| 11d | **`DOC-PDF-L` (99,16 MB, original defeituoso)** | **7.168,2 ms** *(n=1)* | — | **65,70 MB Range** (126 reqs) | 139 | Diagnóstico | 0 |
| 12 | **EPUB médio (1,74 MB, 1ª seção)** | **593,7 ms** *(593–594)* | **338,0 ms** *(329–347)* | 2,06 MB → 2,09 MB | 15 → 17 | 2–4s frio | 1* |
| 13 | **Navegação (próxima página PDF / seção EPUB)** | **669–726 ms** (PDF) / **563 ms** (EPUB) | **696 ms** (PDF opt) / **564 ms** (EPUB) | 12–15 KiB | 6 | Fluida | 0 |

*\*Nota transparente sobre EPUB: 1 erro de console observado em um recurso interno do EPUB de amostra (sem `pageerror` e sem impacto na renderização ou navegação).*

---

## 3. Comparativo Direto de Tráfego (Invariante de Topologia)

| Superfície | Baseline `v1.9.2` (Fase 12.1) | Candidata `v1.10.0` (Fase 12.5) | Redução Real |
| :--- | ---: | ---: | ---: |
| **Descoberta (capas + inicialização fria)** | ~3,7–4,3 MB | **606,8 KiB** (0,59 MB) | **-84% a -86%** |
| **Biblioteca (12 capas frias)** | ~2,0+ MB | **303,6 KiB** (0,30 MB) | **~-85%** |
| **Biblioteca (revisita cache quente)** | Transferência redundante | **31,5 KiB** | **-90% sobre frio** |
| **Scroll +12 capas (cache quente)** | Revalidação pesada | **0,0 KiB** | **100% cache hit** |
| **Busca / Ordenação** | — | **0,0 KiB / 0 reqs** | 100% local em memória |

---

## 4. Avaliação Honesta das Metas Globais e Limitações

- **Metas superadas com folga:** Biblioteca 12 capas (`389 ms` vs `3.000 ms`), Descoberta 8 capas (`677 ms` vs `2.500 ms`), Modal de detalhes (`34 ms` vs `700 ms`), PDF pequeno/médio (`~0,75–0,84s` vs `2–4s`), EPUB (`0,59s` vs `2–4s`), redução drástica de tráfego (> 84% na Descoberta/Biblioteca).
- **Pressão de recursos:** Container staging operou estável em `52,33 MiB / 768 MiB` (`0,20% CPU`) sem afetar o container de produção (`78,66 MiB / 768 MiB`, `0,25% CPU`).
- **Limitações registradas:** (1) Os benchmarks de staging usaram túnel SSH em vez de Tailscale Serve HTTPS (para não alterar a rede de produção sem autorização); (2) A otimização QPDF tem ganho modesto em PDFs já bem estruturados (`-11,1%` em Range no PDF de 15 MB) e ganho expressivo em PDFs com `xref` no final ou linearização corrompida (como `DOC-PDF-L`), razão pela qual permanece **opcional e `disabled` por padrão**.
