# Lectoria — Fase 12.4.1: Otimização Automática de Documentos em Segundo Plano

**Data:** 2026-11-27  
**Branch:** `perf/v1.10.0-phase-12.4.1-doc-optimization` (a partir de `2680964` — Fase 12.4)  
**Versão da aplicação preservada:** `APP_VERSION = 1.9.2`  
**Schema SQLite preservado:** `SCHEMA_VERSION = 13` (zero migração de banco)  
**Produção:** Intacta na `v1.9.2` (nenhum push, merge, deploy ou alteração na VPS)

---

## 1. Objetivo e Princípios Arquiteturais

A Fase 12.4.1 introduz um subsistema leve, seguro e configurável de **preparação automática de documentos para leitura remota em segundo plano**, focado em reduzir latência inicial e desperdício de banda em PDFs não linearizados servidos por `HTTP Range` (206), respeitando estritamente hardware modesto (VPS 1 vCPU, 3,8 GiB RAM, Uvicorn `--workers 1`):

- **Preservação absoluta dos originais:** `/storage/books` permanece somente leitura (`:ro`); nenhum arquivo original é modificado, substituído ou truncado. `/api/books/{id}/download` entrega **sempre** o documento original.
- **Derivados regeneráveis fora de backups obrigatórios:** derivados linearizados são gravados em `<DATA_DIR>/optimized/pdf/` e o estado da fila/política é persistido atomicamente em `<DATA_DIR>/optimized/state.json`. O `backup_service.py` (que inclui apenas `lectoria.db` e `data/covers/*_custom.*`) exclui automaticamente esses derivados.
- **Padrão conservador (`disabled`):** nenhuma otimização automática é disparada sem habilitação explícita pelo administrador.
- **Degradação graciosa:** na ausência do binário `qpdf`, a aplicação funciona normalmente sem falhar, marcando tarefas como `waiting_resources` e servindo o PDF original sem interrupção.

---

## 2. Auditoria Real do Acervo de Produção (Somente Leitura)

Antes da implementação, foi realizada inspeção somente leitura no acervo real da VPS (`/srv/nosreme/lectoria/books`) para calibrar elegibilidade, estratégia de ferramenta e decisão sobre EPUBs:

### 2.1 PDFs (184 arquivos)
| Categoria | Quantidade | Observação |
| :--- | ---: | :--- |
| **Total de PDFs** | **184** | Acervo completo em produção |
| Já linearizados (`/Linearized` válido) | **42** (22,8%) | Detectados automaticamente e marcados como `already_optimized` |
| Não linearizados (candidatos) | **142** (77,2%) | **5.135,53 MB** totais (`96` ≥ 5 MB; `63` ≥ 20 MB) |
| Criptografados | **8** | Ignorados automaticamente (`ignored` / motivo `encrypted`) |
| Assinados digitalmente (`/ByteRange` / `/Sig`) | **0** | Protegidos por detecção explícita (nunca reescritos) |

- **Inspeção específica de `DOC-PDF-L` (99,16 MB, 453 páginas):** confirmado como **já linearizado** (`/Linearized` com `/L == 103974726` exato e `/E = 399932`), utilizando tabela de referência híbrida (*classic xref* + *xref stream*). Isso confirma que o ganho da Fase 12.4.1 incide prioritariamente sobre os **142 PDFs não linearizados** do acervo (77,2%), onde o PDF.js hoje precisa buscar a tabela `xref` ao final do arquivo antes de exibir a primeira página.

### 2.2 EPUBs (9 arquivos) e Decisão Técnica
| Métrica | Valor Real |
| :--- | ---: |
| Total de EPUBs | **9** (`9/9` ZIPs válidos) |
| Tamanho total combinado | **8,51 MB** |
| Maior EPUB | **1,66 MB** |
| Média de entradas ZIP por EPUB | **46,4** |
| Total de imagens embutidas | **3,38 MB** |

- **Decisão:** para EPUBs, o sistema executa **apenas análise e validação estrutural** (`mimetype`, `META-INF/container.xml`, integridade ZIP), **sem reempacotamento ou recompressão destrutiva**. Reempacotar EPUBs traria ganho desprezível (< 2 MB no acervo inteiro) e introduziria risco desnecessário de invalidação de CFIs (`epubcfi(...)`), destaques, notas privadas e referências do Atlas.

---

## 3. Arquitetura Implementada

### 3.1 Novo Serviço: `document_optimization_service.py`
Módulo desacoplado de `main.py`, estruturado em camadas claras:

1. **Detecção e execução isolada de `qpdf`:**
   - Localiza `qpdf` via `shutil.which("qpdf")`, verifica versão e degrada graciosamente se indisponível.
   - Invoca subprocesso com `shell=False`, lista explícita de argumentos (`qpdf --linearize --object-streams=preserve`), `timeout` configurável e limites POSIX (`RLIMIT_AS` e `RLIMIT_CPU` via `preexec_fn` quando suportado).
2. **Elegibilidade de PDFs (`analyze_pdf_eligibility`):**
   - Verifica tamanho mínimo/máximo configuráveis (`min_document_mb`, `max_document_mb`), tamanho absoluto mínimo (`>= 1024` bytes), cabeçalho `%PDF-`, presença de `/Linearized` + `/L` compatível com o tamanho do arquivo, criptografia (`/Encrypt` ou `pypdf.is_encrypted`) e assinatura digital (`/Sig`, `/ByteRange`, `/DocMDP`).
   - Ignora automaticamente arquivos já linearizados (`already_optimized`), criptografados, assinados ou fora dos limites.
3. **Validação estrita pré-publicação e atomicidade:**
   - Executa `qpdf --check` no derivado temporário.
   - Confirma cabeçalho `/Linearized` com `/L` exato igual ao tamanho do derivado.
   - Compara via `pypdf` a **contagem exata de páginas** e a **geometria por página (`MediaBox` + rotação `/Rotate`)** entre original e derivado — garantindo 100% de preservação de coordenadas de destaques, notas, desenhos vetoriais e páginas salvas (`reading_progress`).
   - Recheca o *fingerprint* do arquivo original (`mtime_ns`, `size`, `ino`) imediatamente antes de publicar o derivado por `os.replace` atômico. Qualquer divergência descarta o temporário sem publicar.
4. **Identidade do derivado e invalidação automática:**
   - Cada derivado vincula-se ao `source_fingerprint` do original. Se o arquivo original mudar em disco ou o derivado ficar ausente/corrompido, `resolve_pdf_for_reader()` faz *fallback* imediato e transparente para o original.
5. **Estados suportados (11 estados completos):**
   `disabled`, `pending`, `queued`, `running`, `optimized`, `already_optimized`, `ignored`, `failed`, `cancelled`, `paused`, `waiting_resources`.
6. **4 Modos de execução e coordenação de recursos:**
   - `disabled` (padrão seguro): nenhuma tarefa é executada automaticamente.
   - `immediate`: permite execução imediata de itens enfileirados.
   - `scheduled`: respeita janela horária (`schedule_window_start`–`schedule_window_end`), fuso (`timezone`, padrão `America/Sao_Paulo`, suportando virada de meia-noite) e dias da semana (`schedule_weekdays`).
   - `adaptive`: adia processamento quando há atividade recente de leitura (`mark_reading_activity()`, janela de ociosidade configurável) ou quando um backup está em andamento (`backup_service._backup_lock`).
   - Concorrência limitada a **1 worker serial** (`max_concurrency = 1`) e controle de orçamento de disco (`max_storage_mb`) com evicção **LRU** automática de derivados antigos + proteção de espaço livre mínimo (`min_free_disk_mb`).

### 3.2 Entrega HTTP no Leitor e Download (`main.py`)
- **`GET`/`HEAD /api/books/{book_id}/file`**:
  - Registra atividade de leitura (`document_optimization_service.mark_reading_activity()`).
  - Aceita parâmetro opcional `representation: Literal["auto", "original"] = Query("auto")`.
  - No modo `auto` (padrão do leitor PDF), resolve o derivado otimizado validado quando disponível e compatível; caso contrário, faz *fallback* transparente ao original.
  - **Segurança de cache e HTTP Range (RFC 9110):** o `ETag` inclui o prefixo da representação servida (`"{served_rep}-{mtime_ns}-{size}-{ino}"`), garantindo que `If-Range` e `If-None-Match` nunca misturem *byte ranges* do original com os do derivado.
  - Emite cabeçalho diagnóstico `X-Document-Representation: optimized|original`.
- **`GET /api/books/{book_id}/download`**:
  - Permanece **inalterado** — entrega sempre o arquivo original do acervo.

### 3.3 API Administrativa e Interface Mínima
Foram adicionados 8 endpoints sob `/api/admin/optimization/*`, todos protegidos por `require_admin`, CSRF (em escrita), `ConfigDict(extra="forbid")`, `Cache-Control: private, no-store` e trilha de auditoria (`log_admin_action`):

- `GET /api/admin/optimization/status`
- `PUT /api/admin/optimization/config`
- `POST /api/admin/optimization/enqueue`
- `GET /api/admin/optimization/books/{book_id}`
- `POST /api/admin/optimization/pause`
- `POST /api/admin/optimization/resume`
- `POST /api/admin/optimization/cancel`
- `POST /api/admin/optimization/cleanup`

No frontend (`static/index.html` e `static/js/app.js`), foi adicionado o card compacto `#admin-doc-optimization-card` na aba **Manutenção** do painel administrativo, exibindo disponibilidade do `qpdf`, contadores da fila, uso de disco dos derivados e controles de política/ação sem polling contínuo.

### 3.4 Integração Docker (`Dockerfile`)
- Adicionado `qpdf` em `apt-get install --no-install-recommends sqlite3 qpdf`.
- Criados `/app/data/covers/thumbs`, `/app/data/optimized` e `/app/data/optimized/pdf` com propriedade `lectoria:lectoria`, preservando integralmente o invariante de hardening da v1.9.2 (`USER lectoria:lectoria`, `ReadonlyRootfs=true`, UID/GID `1000`).

---

## 4. Validação Automatizada Completa

A suíte dedicada `tests/test_v1100_phase1241_doc_optimization.py` cobre todos os **24 cenários obrigatórios** (elegibilidade, PDFs linearizados/criptografados/assinados, validação EPUB, disponibilidade/ausência de `qpdf`, ciclo de geração/validação/preservação byte a byte do original, preservação de geometria/anotações, invalidação ao alterar original, falha e timeout de subprocesso, pausa/retomada/cancelamento, orçamento LRU, modos `disabled`/`immediate`/`scheduled`/`adaptive`, coordenação com `backup_service._backup_lock`, limpeza, RBAC/CSRF/anti-IDOR, HTTP Range + ETag/If-Range vinculado à representação, `representation=original`, download sempre do original e fallback transparente).

### Resultados da Execução Final (sobre o código final)
| Suíte | Comando | Resultado |
| :--- | :--- | :--- |
| Fase 12.4.1 (dedicada) | `.venv/bin/pytest -v tests/test_v1100_phase1241_doc_optimization.py` | **4 passed**, 0 failed (0,31s) |
| Unitários + Integração (API) | `.venv/bin/pytest -q --ignore=tests/e2e` (+ verificação pós-ajuste `test_v192_backup_restore_and_hardening.py`: 9/9) | **359 passed**, 2 skipped (Docker daemon), 0 failed |
| E2E Playwright (Chromium) | `.venv/bin/pytest -q tests/e2e` | **56 passed**, 0 failed (245,78s) |
| **Total Geral** | — | **415 passed, 2 skipped, 0 failed** |

*(Observação: durante a validação completa, o teste de regressão `test_v192_dockerfile_and_compose_hardening_invariants` detectou uma divergência textual na linha `chown` do `Dockerfile`, prontamente corrigida mantendo a string exata `chown lectoria:lectoria /app/data /app/data/covers /app/backups` seguida dos novos subdiretórios, revalidada em 9/9 testes.)*

---

## 5. Limitações Conhecidas e Procedimento de Rollback

### Limitações Conhecidas
1. **Ambiente local sem binário `qpdf`:** o ambiente local de desenvolvimento (Flatpak/Fedora) não possui o executável `qpdf` instalado; os testes exercitam tanto o caminho real de degradação graciosa quanto o pipeline completo de subprocesso/validação com mocks fiéis de `qpdf`. O teste com `qpdf` real em contêiner ocorrerá na validação de imagem da Fase 12.5/release.
2. **Estado em arquivo JSON:** adequado para acervos domésticos de centenas a poucos milhares de livros sem migração de schema (`SCHEMA_VERSION=13`). Se o acervo crescer para dezenas de milhares de documentos, poderá migrar para tabela SQLite em uma futura release de schema.
3. **PDFs criptografados ou assinados:** intencionalmente ignorados para não quebrar senhas ou assinaturas digitais.

### Rollback Seguro
- **Desativação imediata em runtime:** alterar o modo para `disabled` via `PUT /api/admin/optimization/config` (ou UI) e executar `POST /api/admin/optimization/cleanup` (`scope="all"`). Os derivados em `data/optimized/pdf/` são removidos sem qualquer impacto nos originais ou no banco SQLite.
- **Rollback de código:** como `SCHEMA_VERSION=13` foi mantido intacto, o retorno para qualquer commit/imagem anterior (`v1.9.2` ou Fase 12.4) é imediato e sem perda de dados.

---

## 6. Recomendações para a Fase 12.5

1. **Benchmark em contêiner Docker com `qpdf` real:** medir tempo de linearização, pico de RAM/CPU e ganho real de TTFB/primeira página em PDFs não linearizados representativos (ex.: 5 MB, 20 MB, 80 MB) sob limite de 1 vCPU.
2. **Verificação ponta a ponta da v1.10.0:** consolidar comparativo completo **Antes (v1.9.2 baseline) × Depois (Fases 12.2 + 12.3 + 12.4 + 12.4.1)** em bytes transferidos, requisições, tempo de abertura de Descoberta/Acervo/Leitor PDF/Leitor EPUB.
3. **Política sugerida para produção após validação:** habilitar inicialmente no modo `scheduled` (janela noturna `02:00–05:00`, `America/Sao_Paulo`, `max_storage_mb=1024`) antes de considerar `adaptive`.
