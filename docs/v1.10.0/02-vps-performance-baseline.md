# Lectoria — Fase 12.1: Baseline de Desempenho da VPS e Comparativo entre Ambientes

**Conclusão executiva:** A VPS (`srv2137600`) está **amplamente ociosa** tanto em repouso quanto sob carga intensa de navegação real no Chromium (incluindo rajadas de 60+ capas e 60 requisições HTTP Range simultâneas). O servidor interno responde em **1,9–29,4 ms** em todos os endpoints. Os atrasos percebidos pelo usuário (**2,5 s a > 20 s**) **não** são causados por saturação de CPU, memória, disco ou contenção do container na VPS.

---

## 1. Inventário de Hardware e Runtime na VPS (`srv2137600`)

| Recurso | Especificação / Estado Medido |
| :--- | :--- |
| **CPU** | 1 vCPU AMD EPYC 9355P (`id=98–99%`, `wa=0%`, `st=0%` — zero *steal time*) |
| **RAM do Host** | 3,8 GiB total (`751 MiB` usada, `3,2 GiB` buff/cache, **`3,1 GiB` disponível**) |
| **Swap** | 2,0 GiB total (`512 KiB` em uso — praticamente zero) |
| **PSI (Pressure Stall Info)** | `cpu full=0.00`, `memory full=0.00`, `io full=0.00` |
| **Disco (`/dev/sda1`)** | 48 GB total, 20 GB usado (41%), 29 GB livre |
| **Container `lectoria`** | `mem_limit=768 MiB`, `UID/GID 1000:1000`, `ReadonlyRootfs=true`, `CapEff=0`, `NoNewPrivs=1` |
| **Processo Uvicorn** | `--workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1,::1,172.19.0.1` |
| **Conectividade Tailscale** | `fedora` ↔ `srv2137600`: **`active; direct` via IPv6** (sem relay DERP) |

---

## 2. Telemetria da VPS em Repouso vs. Sob Carga Real (364 Amostras)

Durante os ~12 minutos de execução contínua dos 13 cenários reais no Chromium/Playwright (Ambiente C), um coletor SSH registrou a VPS a cada 2 segundos:

| Métrica | Em Repouso (Baseline) | Sob Carga Real (364 amostras) | Limite / Alerta | Avaliação |
| :--- | :---: | :---: | :---: | :--- |
| **Uvicorn `%CPU`** | 0,5% | 0,7% mín / **0,8% med** / **0,9% máx** | 100% (1 vCPU) | Folga > 99% |
| **Uvicorn `RSS`** | 80,8 MiB | 95,25 – **96,87 MiB** | 768 MiB | Estável (~12,6%) |
| **Cgroup `memory.current`** | 63,5 MiB (pico 78,8 MiB) | 103,57 – **123,66 MiB** | 768 MiB | Folga > 644 MiB (16,1% do limite) |
| **Cgroup `oom` / `oom_kill`** | 0 / 0 | 0 / 0 | 0 | Saudável |
| **Cgroup `nr_throttled` (delta)** | 0 | **0** | 0 | Zero throttling de CPU |
| **Host `load average (1m)`** | 0,00 | 0,00 – **0,27** | 1,00 | Muito abaixo da saturação |

---

## 3. Latência de Armazenamento Somente-Leitura na VPS

Leitura direta de chunks de 512 KB (equivalente ao `rangeChunkSize` do PDF.js) nos arquivos reais do acervo (`/srv/nosreme/lectoria/books`), sem limpar o page cache do SO:

| Código | Tamanho do Arquivo | Chunk Lido | Mediana (ms) | Máximo (ms) |
| :--- | ---: | ---: | ---: | ---: |
| `DOC-PDF-S` | 1,42 MB | 512 KB | 0,212 ms | 1,089 ms |
| `DOC-PDF-M` | 17,60 MB | 512 KB | 0,027 ms | 0,799 ms |
| `DOC-PDF-L` | 99,16 MB | 512 KB | 0,030 ms | 1,294 ms |
| `DOC-EPUB-S` | 0,78 MB | 512 KB | 0,031 ms | 0,779 ms |
| `DOC-EPUB-M` | 1,30 MB | 512 KB | 0,024 ms | 0,677 ms |

**Conclusão de I/O:** O disco da VPS entrega chunks de 512 KB em **< 0,3 ms** (mediana). Não há gargalo de disco.

---

## 4. Comparativo Direto: Ambiente A (Fedora Local) vs. Ambiente B (VPS Interna) vs. Ambiente C (Tailscale HTTPS)

| Endpoint / Recurso | Tamanho | Amb. A: Fedora Local (med) | Amb. B: VPS `127.0.0.1` (med) | Amb. C: Tailscale HTTPS (med / máx) | Razão C / B |
| :--- | ---: | ---: | ---: | ---: | ---: |
| `/api/health` | 60 B | 2,09 ms | **1,93 ms** | 70,52 ms / 72,04 ms | 36,6× |
| `/api/auth/status` (autenticado) | 167 B | 3,86 ms | **5,56 ms** | 71,85 ms / 76,92 ms | 12,9× |
| `/` (`index.html`) | 102,1 KB | 3,30 ms | **2,75 ms** | 128,25 ms / 238,32 ms | 46,6× |
| `/static/css/style.css?v=1.9.2` | 136,7 KB | 3,80 ms | **3,01 ms** | 319,30 ms / 408,81 ms | 106,0× |
| `/static/js/app.js?v=1.9.2` | 239,2 KB | 3,80 ms | **3,24 ms** | 236,68 ms / 567,55 ms | 73,1× |
| `/static/vendor/pdfjs/pdf.min.js` | 320,0 KB | — | **4,29 ms** | 705,88 ms / 1.059,43 ms | 164,5× |
| `/static/vendor/pdfjs/pdf.worker.min.js` | 1.087,2 KB | 8,24 ms | **7,69 ms** | **2.587,84 ms** / 2.749,39 ms | **336,4×** |
| `/api/books?sort_by=title&order=asc` | 301,6 KB | — | **29,43 ms** | **605,32 ms** / 848,12 ms | 20,6× |
| `/api/discovery/shelves` | 200,9 KB | — | **27,21 ms** | **387,90 ms** / 562,26 ms | 14,3× |
| `/api/books/{id}/cover` (`COVER-L`) | 238,8 KB | — | **8,79 ms** | **510,47 ms** / 669,41 ms | 58,1× |
| `/api/books/{id}/cover` (`COVER-XL`) | 1.162,1 KB | — | **20,05 ms** | **3.058,89 ms** / 3.549,33 ms | **152,6×** |
| `/api/books/{id}/file` (Range 512 KB) | 524,3 KB | — | **9,94 ms** | **1.274,87 ms** / 1.560,17 ms | **128,3×** |
| `/api/books/{id}/file` (`DOC-EPUB-M`) | 1.362,8 KB | — | **13,41 ms** | **3.513,65 ms** / 3.994,13 ms | **262,0×** |

### Diagnóstico Central do Comparativo
1. **A VPS é tão rápida quanto o Ryzen 5 5600GT local** para servir a aplicação (1,9–8 ms em estáticos/saúde e 6–29 ms nas consultas de API).
2. **Entre 95% e 99,7% do tempo total de transferência de recursos médios e grandes ocorre na rede remota**, onde a vazão efetiva medida por fluxo HTTP/2 sobre Tailscale é de **~380–450 KB/s (~3,0–3,6 Mbps)**, amplificada dramaticamente por:
   - **Ausência total de compressão HTTP (`gzip`)** em HTML, CSS, JS e JSON;
   - **Ausência de `Cache-Control: immutable`** em `/static/*`;
   - **Entrega de capas em resolução e peso originais** (até 3,44 MB / 2550×3300 px por capa);
   - **Política `Cache-Control: private, no-cache`** nos arquivos de livros e ausência de miniaturas/otimização de carregamento.
