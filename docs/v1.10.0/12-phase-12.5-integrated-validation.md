# Lectoria — Fase 12.5: Validação Integrada, QPDF Real e Revisão da Fase 12.4.1

**Data:** 2026-11-27  
**Branch:** `perf/v1.10.0-phase-12.5-validation-release` (a partir de `99141b4`)  
**Candidata preparada:** `APP_VERSION = 1.10.0` | `SCHEMA_VERSION = 13` (zero migração)  
**Produção:** Intacta na `v1.9.2` (zero merge, zero push, zero deploy, zero alteração de `compose.yaml` ou tag `stable`)

---

## 1. Correções Direcionadas da Revisão da Fase 12.4.1

A revisão rigorosa e os testes com **`qpdf` real** identificaram e corrigiram **seis riscos concretos**:

1. **Flag real do `qpdf` (`--check-linearization` vs `--is-linearized`):**
   - O `qpdf 11.9.0` (VPS) e `12.2.0` (imagem Docker) utilizam `--check-linearization` (não `--is-linearized`). Corrigido em `document_optimization_service.py` com verificação do código de saída (`0`/`3`) e ausência de `"is not linearized"`.
2. **Rejeição de comentários falsos `/Linearized`:**
   - A checagem textual anterior aceitava comentários `% /Linearized 1 /L <size>`. Agora `_strip_pdf_comments()` remove linhas de comentário e exige um objeto de dicionário PDF real (`<n> <g> obj << ... /Linearized ... /L <N> ... >>`), complementado por `qpdf --check-linearization` e coberto por teste negativo dedicado.
3. **Bug real de `IndirectObject` em `/Annots` no `pypdf`:**
   - Capturado ao otimizar um PDF real de 2 MB no container staging (`object of type 'IndirectObject' has no len()`). Corrigido com resolução explícita `.get_object()` antes da contagem de anotações.
4. **Guarda anti-regressão de tamanho (> +15%):**
   - Um PDF real de 15,97 MB cresceu `1,71x` (28,69 MB) ao linearizar. Adicionada guarda que descarta derivados acima de `+15%` do tamanho original e marca o item como `ignored` (`size_regression_exceeded`).
5. **Fixação imutável da representação por sessão (`?rep=<rep_id>`):**
   - Adicionado `GET /api/books/{id}/reader-representation` e `?rep=<rep_id>` em `/api/books/{id}/file`, corrigindo também o bug em `static/js/reader.js` que passava `length: bookData.filesize` (tamanho do original) ao PDF.js mesmo quando servindo derivado.
   - Derivados em uso ficam protegidos contra limpeza administrativa e evicção LRU; logout revoga sessões pinadas; substituição do original ou remoção do derivado retorna `HTTP 409 Conflict` (`X-Document-Representation-Stale: 1`) sem jamais misturar bytes. `/api/books/{id}/download` permanece sempre original.
6. **Subprocessos seguros sem `preexec_fn` + fila real + alinhamento da UI administrativa:**
   - Criado `scripts/bounded_exec.py` aplicando `RLIMIT_AS` e `RLIMIT_CPU` no processo filho após `exec` (eliminando risco de deadlock `fork/exec` em ambiente multithread), com cancelamento real (`terminate`/`kill`).
   - Adicionado worker daemon cooperativo com recuperação pós-reinício (`recover_on_startup()`), gancho pós-scan e coordenação bidirecional com `backup_service._backup_lock` e `background_enricher`.
   - Alinhados todos os campos da UI administrativa em `static/index.html` e `static/js/app.js` (`window_start`, `window_end`, `timezone`, `adaptive_use_window`, `max_storage_mb` até 50 GB, `storage_used_bytes`, `storage_files_count`).

---

## 2. Validação da Imagem Docker Candidata e QPDF Real

Construída na VPS em diretório temporário isolado (`/tmp/lectoria-p125-staging/src`) como `lectoria:v1.10.0-rc-p125`:

| Propriedade | Valor Verificado |
| :--- | :--- |
| **Tamanho da imagem candidata** | **279 MB** (`+45 MB` sobre os `234 MB` da `v1.9.2`, incluindo Pillow 12.3.0 + WebP + QPDF 12.2.0 + `libqpdf30`) |
| **QPDF na imagem (Debian)** | `qpdf version 12.2.0` (`Version: 12.2.0-1`, `Installed-Size: 341 KB`, `libqpdf30`) |
| **Pillow WebP** | `PIL.features.check("webp") == True` |
| **Execução `bounded_exec.py`** | Validada em `--read-only`, `--tmpfs /tmp:rw,noexec,nosuid`, `--cap-drop ALL`, `no-new-privileges:true`, `UID/GID 1000` |
| **Hardening runtime (`lectoria-p125-staging`)** | `ReadonlyRootfs=true`, `CapDrop=[ALL]`, `SecurityOpt=[no-new-privileges:true]`, `Memory=768 MiB`, `User=1000:1000` |
| **Consumo em repouso** | Staging: `52,33 MiB / 768 MiB` (`0,20% CPU`) · Produção intacta: `78,66 MiB / 768 MiB` (`0,25% CPU`) |
| **Healthcheck** | `{"status":"healthy","app_name":"Lectoria","version":"1.9.2"}` (validado antes do bump de release) |

---

## 3. Experimentos Reais com QPDF e Investigação do `DOC-PDF-L`

Executados sobre cópias temporárias de PDFs reais do acervo (originais montados `:ro`, zero alteração):

| Amostra (Anonimizada) | Original | Derivado | Razão | Tempo | Pico RSS | Status / Observação |
| :--- | ---: | ---: | ---: | ---: | ---: | :--- |
| `small_nonlin` | 2,03 MB | 1,95 MB | 0,96× | 0,08s | 19,0 MB | Linearizado válido (`rc=0`) |
| `med_nonlin` (patológico) | 15,97 MB | 28,69 MB | **1,71×** | 1,27s | 51,0 MB | **Rejeitado pela guarda > +15%** (`size_regression_exceeded`) |
| `med_pdf` (staging 12.2.0) | 15,07 MB | ~14,7 MB | ~0,98× | < 1,0s | — | `state=optimized`, `-11,1%` em `range_bytes` na 1ª página |
| `large_nonlin` (61 MB) | 61,11 MB | 61,11 MB | 1,00× | 1,01s | 14,3 MB | Linearizado válido (`rc=0`) |
| `large_nonlin_2` (97,5 MB, 66k objs) | 97,47 MB | 88,38 MB (`generate`) | **0,91×** | 4,10s | 340,3 MB | Dentro do limite de 384 MB (`rc=3` por warnings preexistentes do original) |
| `encrypted` | 14,92 MB | — | — | — | — | Ignorado automaticamente (`encrypted_pdf_unsupported`) |
| **`DOC-PDF-L` (99,16 MB, 453 pp.)** | 99,16 MB | 99,19 MB | 1,00× | **0,23s** | **18,0 MB** | **Causa raiz identificada (ver abaixo)** |

### Causa Raiz do `DOC-PDF-L` (99,16 MB)
O `qpdf --check-linearization` no arquivo original revelou **linearização estruturalmente defeituosa** (*`linearized file contains an uncompressed object after a compressed one in a cross-reference stream`*, *`first shared object offset mismatch: hint table = 103959144; computed = 103957609`*, *`page 0 has shared identifier entries`*). No benchmark do leitor PDF.js no staging, o original exigiu **126 requisições `Range` e 65,7 MB transferidos** antes de renderizar a 1ª página (`7,17s` em túnel SSH; > 90s no baseline remoto). A relinearização limpa com `qpdf` levou apenas **0,23s** e **18,0 MB de RAM**, eliminando todos os warnings (`rc=0`). Ainda assim, a otimização permanece **opcional e `disabled` por padrão** para controle explícito do administrador.

---

## 4. Segurança, Backup e Suíte Final

- **9 cenários de pinagem de representação** testados e aprovados em `tests/test_v1100_phase125_validation.py`.
- **Inspeção real de backup (`backup_service.create_backup()`):** confirmado que `covers.tar.gz` inclui `*_custom.*` e **exclui integralmente** `covers/thumbs/*.webp` e `optimized/pdf/*.pdf`.
- **Restauração e estado:** `lectoria.db` e capas personalizadas são restaurados integralmente; `data/optimized/` (estado e derivados) é regenerável e intencionalmente excluído do backup obrigatório.
- **Dependências:** `pip check` sem pendências (`No broken requirements found`).
- **Suíte final sobre `v1.10.0`:** consolidada no relatório `14-release-readiness-and-deployment-plan.md` após conclusão da execução integral.
