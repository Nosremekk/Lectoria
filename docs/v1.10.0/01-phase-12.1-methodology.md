# Lectoria — Fase 12.1: Metodologia de Diagnóstico de Desempenho (v1.10.0)

**Data:** 2026-11-26  
**Branch de diagnóstico:** `perf/v1.10.0-baseline` (base `main` @ `a47f93a43fd2113043fa71ca19854dc3cb6f9c9a`)  
**Versão avaliada em produção:** `APP_VERSION = 1.9.2` | `SCHEMA_VERSION = 13`  
**Imagem imutável em produção:** `ghcr.io/nosremekk/lectoria@sha256:ccb39f08c9f796aa23e49c40e4c4a00fa45f84d8ab8817be7ae79bfee7866547`

---

## 1. Objetivo e Escopo

Este diagnóstico investiga, mede e atribui quantitativamente os delays percebidos no uso cotidiano da Lectoria em produção, sem presumir antecipadamente que Python, SQLite, Docker, Tailscale ou o hardware da VPS sejam a causa raiz.

**Regras operacionais estritamente cumpridas durante toda a coleta:**
- **Zero alterações em produção:** nenhuma modificação em `compose.yaml`, limites de CPU/RAM, código da aplicação, banco SQLite, metadados, capas, anotações ou progresso de leitura.
- **Zero reinícios / zero limpeza de cache de SO / zero `VACUUM` ou `REINDEX`.**
- **Proteção estrita somente-leitura nos testes de navegador:** todas as requisições não-`GET`/`HEAD`/`OPTIONS` (incluindo `POST /api/books/{id}/progress` e `POST /api/books/{id}/cover/cache`) foram interceptadas no Playwright via `context.route` com resposta sintética local, impedindo qualquer mutação em produção.
- **Privacidade e segurança:** autenticação obtida por login interativo local do proprietário no Chromium (nenhuma senha trafegou pelo chat); `storage_state` temporário salvo fora do repositório com permissão `600` e removido imediatamente após o término dos benchmarks; nenhum título privado, conteúdo de anotação, cookie, token ou HAR bruto foi versionado.

---

## 2. Ambientes Medidos

| Ambiente | Descrição | Papel na Análise |
| :--- | :--- | :--- |
| **Ambiente A — Local (Fedora)** | AMD Ryzen 5 5600GT, 16 GB RAM, `TestClient` isolado em diretório temporário | Referência comparativa de latência de CPU/aplicação sem rede. |
| **Ambiente B — VPS Interna** | Host `srv2137600` (1 vCPU AMD EPYC 9355P, 3,8 GiB RAM), acesso direto `http://127.0.0.1:8765` via SSH | Isolamento do tempo real de servidor (Uvicorn + FastAPI + SQLite + disco), eliminando rede externa e TLS. |
| **Ambiente C — Remoto Real HTTPS (Principal)** | Chromium/Playwright + `curl` no Fedora acessando `https://srv2137600.tailf5c3ce.ts.net` (Tailscale Serve HTTPS/HTTP2 direto IPv6), com telemetria simultânea da VPS a cada 2 s | Experiência real de uso cotidiano (rede + TLS + HTTP/2 + payload + navegador). |

---

## 3. Ferramentas e Protocolo Estatístico

- **Camada HTTP (`curl -w`):** 1–2 aquecimentos descartados seguidos de 6–15 amostras por endpoint, reportando `min`, `mediana (p50)`, `p95`, `max`, código HTTP, versão HTTP, tamanho transferido e cabeçalhos de cache/compressão.
- **Camada de Navegador (Playwright/Chromium 1366×850):**
  - **Cache frio:** novo `browser.new_context(storage_state=...)` limpo por execução.
  - **Cache quente:** recarregamento (`page.reload()`) e navegação em nova aba no mesmo contexto (`warm_nav`), permitindo distinguir o comportamento de *reload* forçado (`max-age=0`) versus navegação normal com cache HTTP.
  - **Métricas coletadas:** TTFB, `DOMContentLoaded`, `load`, FCP, LCP, tempo até elemento utilizável, tempo até primeiras capas visíveis, Long Tasks (> 50 ms via `PerformanceObserver`), número de requisições, bytes transferidos/codificados/decodificados, top 5 recursos mais lentos e erros de console.
- **Telemetria simultânea da VPS:** coleta contínua a cada 2 s via SSH durante toda a suíte Playwright (364 amostras): `loadavg`, `memory.current` e `cpu.stat` (`nr_throttled`) do cgroup do container `lectoria`, `%CPU` e `RSS` do processo Uvicorn.
- **Amostragem anônima do acervo real:**

| Código Anônimo | Formato | Tamanho | Páginas / Seções | Tipo de Capa |
| :--- | :--- | ---: | ---: | :--- |
| `DOC-PDF-S` | PDF | 1,42 MB (1.485.142 B) | 127 | `extracted` |
| `DOC-PDF-M` | PDF | 17,60 MB (18.453.771 B) | 120 | `extracted` |
| `DOC-PDF-L` | PDF | 99,16 MB (103.974.726 B) | 453 | `extracted` |
| `DOC-EPUB-S` | EPUB | 0,78 MB (815.543 B) | 34 | `extracted` |
| `DOC-EPUB-M` | EPUB | 1,30 MB (1.362.838 B) | 15 | `extracted` |
| `COVER-S` / `M` / `L` / `XL` | JPG / PNG | 21,5 KB / 65,7 KB / 238,8 KB / 1.162,1 KB | — | Quartis reais de `data/covers/` |

---

## 4. Artefatos de Dados Brutos Anonimizados

- [authenticated_api_benchmarks.json](data/authenticated_api_benchmarks.json) — Latências autenticadas (Ambiente B vs Ambiente C) e economia de gzip dos JSONs.
- [browser_13_scenarios.json](data/browser_13_scenarios.json) — Métricas dos 13 cenários reais no Chromium + 364 amostras de telemetria da VPS.
- [summary_metrics.csv](data/summary_metrics.csv) — Tabela consolidada em CSV para comparação futura pós-v1.10.0.
