# Lectoria — Fase 11.1: Auditoria de Infraestrutura, CI/CD e Operação

- **Escopo**: Auditoria exclusivamente documental sobre o estado confirmado em `v1.9.1` (`main` @ `dda6f27`, Schema SQLite `13`).
- **Restrições respeitadas**: Nenhuma alteração em código, workflows, Dockerfile, Docker Compose, serviços da VPS, banco SQLite ou backups. Acesso à VPS estritamente somente-leitura.

---

## 1. Inventário dos Artefatos Inspecionados

| Artefato | Localização | Situação Atual Observada |
| :--- | :--- | :--- |
| **CI** | `.github/workflows/ci.yml` | Dispara em `push` (`main`, `feat/*`, `fix/*`) e `pull_request` (`main`). Executa `pytest -v tests/` (360 testes) + `scripts/diagnose.py --json` em Python 3.12. |
| **Publicação GHCR** | `.github/workflows/publish.yml` | Dispara em `push` (`main`, `tags: v*`) e `workflow_dispatch`. Reexecuta `py_compile` + `pytest -v tests/` (360 testes), constrói imagem Docker e publica `:vX.Y.Z`, `:X.Y.Z` e `:stable`, validando `/api/health`. |
| **Dockerfile** | `Dockerfile` | Multi-stage `python:3.12-slim`, `MALLOC_ARENA_MAX=2`, instala `sqlite3`, `uvicorn main:app --workers 1 --proxy-headers --forwarded-allow-ips *`, `HEALTHCHECK` em `/api/health` (`--start-period=15s`). Executa como `root`. |
| **Compose em Produção (VPS)** | `/srv/nosreme/lectoria/compose.yaml` | `image: ghcr.io/nosremekk/lectoria:stable`, bind mounts `./books:/storage/books`, `./data:/storage/data`, `./backups:/storage/backups`, porta `127.0.0.1:8765:8765`, `mem_limit: 768m`. **Sem** `healthcheck` customizado, **sem** `logging` rotation, **sem** `no-new-privileges`, **sem** `LECTORIA_SECURE_COOKIES`. |
| **Compose Candidato** | `docker-compose.v190-candidate.yml` | Alinhado à VPS real (`/storage/*`), propõe `mem_limit: 1g`, `mem_reservation: 256m`, `pids_limit: 128`, `no-new-privileges:true`, `start_period: 30s`, rotação `10m × 3` e `LECTORIA_SECURE_COOKIES: "true"` (ainda não aplicado na VPS). |
| **Compose Genérico (Repo)** | `docker-compose.yml` | **Desalinhado**: aponta para `ghcr.io/nosremekk/lectoria:latest` (tag **não publicada** por `publish.yml`) e `healthcheck` consulta `/api/books` (que exige autenticação e retorna `401` após o setup inicial). |
| **Auto-Update (VPS)** | `lectoria-auto-update.timer` / `.service` | Timer `OnUnitInactiveSec=15min`, `TimeoutStartSec=15min`, executa `/usr/local/sbin/lectoria-auto-update` como `root`. Arquivo `-rwx------ root:root` (`2.130 B`): conteúdo interno não legível pelo usuário `emerson` sem elevação. |
| **Backup Local (VPS)** | `lectoria-backup.timer` / `.service` | `OnCalendar=*-*-* 03:00:00`, `enabled`/`active`, executa `/usr/local/sbin/lectoria-backup`. Backup remoto `nosreme-restic-backup.timer` permanece `disabled`/`inactive` (Fase 8.D). |
| **Proxy / Acesso Remoto** | `tailscale serve` | `https://srv2137600.tailf5c3ce.ts.net` (tailnet-only) → `http://127.0.0.1:8765`. |

---

## 2. Imutabilidade das Releases e Rastreabilidade (Seção 2.1)

### 2.1 Achados Confirmados
1. **Sobrescrita silenciosa de tags versionadas em `publish.yml`**:
   - Nas linhas 67–69 e 91–94 de `.github/workflows/publish.yml`, **qualquer `push` em `main`** extrai `config.APP_VERSION` e publica simultaneamente `:v<APP_VERSION>`, `:<APP_VERSION>` e `:stable`.
   - Consequência real já ocorrida na v1.9.0: um hotfix em `main` sem bump de versão sobrescreve a tag Docker `:v1.9.0` no GHCR, gerando divergência entre a tag Git `v1.9.0` (`c919050`) e o artefato publicado (`9ff89cd`).
2. **Dupla publicação no fluxo de tag**:
   - Na v1.9.1, o push em `main` (`dda6f27`) disparou `publish.yml` (`#45210832288`) publicando `:v1.9.1`, `:1.9.1`, `:stable`; em seguida, o push da tag Git `v1.9.1` disparou um segundo run (`#45248554299`) sobre o mesmo commit, gerando reconstrução redundante.
3. **Ausência de rótulos OCI de rastreabilidade no `Dockerfile`**:
   - O `Dockerfile` define `org.opencontainers.image.title/source/licenses`, mas não injeta `org.opencontainers.image.revision` (commit SHA) nem `org.opencontainers.image.version` via build-args.

### 2.2 Arquitetura Proposta de Imutabilidade
- **Separação estrita de gatilhos**:
  - `push` em `main`: publica **exclusivamente** `:stable` e `:sha-<shortsha>` (além de verificar `APP_VERSION` sem sobrescrever `:vX.Y.Z`).
  - `push` de tag anotada `vX.Y.Z`: valida que `tag == "v" + config.APP_VERSION` (falhando o workflow em caso de divergência), verifica no registro se `:vX.Y.Z` já existe (bloqueando sobrescrita acidental), e publica `:vX.Y.Z`, `:X.Y.Z` e atualiza `:stable` para o **mesmo digest** (ou promove o digest já construído do commit via `docker buildx imagetools create`, evitando rebuild!).
- **Metadados OCI e registro de digest**:
  - Injetar `org.opencontainers.image.revision=${GITHUB_SHA}` e `org.opencontainers.image.version=${CLEAN_VERSION}` no build.
  - Publicar `GITHUB_STEP_SUMMARY` com commit SHA, tag Git, versão, digest `sha256:...` e resultado do smoke test `/api/health`.
- **Reprodutibilidade histórica**:
  - Combinando tag Git imutável + `requirements.txt` fixado (ou lockfile) + imagem `ghcr.io/nosremekk/lectoria@sha256:...`, qualquer release histórica pode ser reproduzida bit a bit.

---

## 3. Duplicação do CI (Seção 2.2)

### 3.1 Achado Confirmado
- A cada `push` em `main`, `ci.yml` e `publish.yml` executam em paralelo a suíte completa de **360 testes** (Playwright + Chromium + 50k links scale test), levando ~10–14 minutos cada (**~20–28 minutos de runner por push em `main`**).
- Em um ciclo de release com push em branch + merge em `main` + push de tag `vX.Y.Z`, a mesma suíte de 360 testes rodou **4 vezes** (`#45210826123`, `#45210832056`, `#45210832288`, `#45248554299`).

### 3.2 Arquitetura Proposta Sem Perda de Garantias
- **Opção recomendada (Reusable Workflow)**:
  - Extrair o job de testes (`py_compile` + `pytest -v tests/` + `scripts/diagnose.py`) para um workflow reutilizável `.github/workflows/_test-suite.yml` (`workflow_call`).
  - `ci.yml` passa a rodar em `pull_request` para `main` e `push` em `feat/*`/`fix/*` (removendo `push: branches: [main]`, pois `publish.yml` já é o gate obrigatório de `main`).
  - `publish.yml` chama `_test-suite.yml` como job `test` (`needs: [test]` no job `build-and-publish`). Para tags `v*`, valida que a tag aponta para um commit já testado e promove o manifesto via `imagetools` ou executa build único.
- **Ganho estimado**: redução de **~50% a 65%** do tempo total de runners GitHub Actions por release, mantendo 100% da obrigatoriedade dos 360 testes antes de qualquer publicação.

---

## 4. Atualização Automática na VPS e Rollback Seguro (Seção 2.3)

### 4.1 Estado Atual Verificado
- **Fatos verificados**:
  - `lectoria-auto-update.timer` dispara a cada 15 minutos (`OnUnitInactiveSec=15min`).
  - `/srv/nosreme/lectoria/compose.yaml` referencia `ghcr.io/nosremekk/lectoria:stable`.
  - O script `/usr/local/sbin/lectoria-auto-update` possui permissão `0700 root:root` (`2.130 B`); sem elevação de privilégio (proibida nesta fase), seu código interno não pôde ser inspecionado diretamente.
- **Lacuna de observabilidade**: na Fase 11.4 (com autorização do proprietário), será necessário inspecionar o script atual com leitura administrativa para confirmar se ele já grava o digest anterior, faz `healthcheck` pós-start e executa fallback automático.

### 4.2 Projeto de Rollback Automático Seguro (Container + Schema SQLite)
Um rollback de container **não** implica rollback seguro do banco SQLite. O desenho recomendado para a Fase 11.4 separa explicitamente **atualizações sem migração** de **atualizações com migração de schema**:

1. **Pré-condição (Antes de baixar/trocar container)**:
   - Capturar `OLD_IMAGE_ID` e `OLD_DIGEST` do container `lectoria` atual.
   - Consultar digest remoto de `ghcr.io/nosremekk/lectoria:stable`; se igual a `OLD_DIGEST`, encerrar sem ação (idempotente).
   - Registrar `PRE_SCHEMA_VERSION` (`SELECT MAX(version) FROM schema_migrations`) e executar **snapshot consistente via SQLite Online Backup API** (`lectoria_pre_update_<timestamp>.db`, retendo últimos 5 snapshots).
2. **Troca e Verificação**:
   - `docker compose pull` → `docker compose up -d`.
   - Polling leve em `http://127.0.0.1:8765/api/health` por até `45 s` (intervalo `3 s`) verificando `status == "healthy"` + `docker inspect` (`Running == true`, `RestartCount == 0`).
3. **Política de Rollback em Caso de Falha**:
   - Ler `POST_SCHEMA_VERSION` do banco (se legível):
     - **Caso A (`POST_SCHEMA_VERSION == PRE_SCHEMA_VERSION`, ex.: hotfix `1.9.0 → 1.9.1`)**: rollback automático seguro — reverter container para `OLD_IMAGE_ID`, revalidar `/api/health` e registrar alerta no `journalctl`.
     - **Caso B (`POST_SCHEMA_VERSION > PRE_SCHEMA_VERSION` — migração ocorreu antes da falha)**: **NÃO** reverter apenas o container sobre um banco migrado (risco de incompatibilidade de schema). Restaurar o snapshot `lectoria_pre_update_<timestamp>.db` (com container parado, validando `PRAGMA integrity_check = ok`) **e** reverter o container para `OLD_IMAGE_ID`, ou travar o auto-update (`lockfile` de erro) exigindo intervenção humana caso a restauração falhe.
     - **Caso C (Falha de rede/pull no GHCR)**: abortar antes de parar o container em produção (zero downtime).

---

## 5. Dependências Python e Reprodutibilidade

- **`requirements.txt` atual (3 linhas abertas)**: `fastapi>=0.110.0`, `uvicorn>=0.28.0`, `pypdf>=4.0.0`.
- **Risco real observado**: no ambiente local atual (`.venv`), `pip` resolveu `fastapi 0.162.0`, `starlette 1.10.0`, `pydantic 2.14.2`, `uvicorn 0.54.0`, `pypdf 6.21.4` — já emitindo `StarletteDeprecationWarning` nos testes (`httpx` vs `httpx2`). Sem lockfile, um build futuro do Dockerfile pode baixar uma major/minor incompatível de `fastapi`, `starlette`, `pydantic` ou `pypdf` e quebrar produção silenciosamente.
- **Estratégia proposta (Fase 11.3)**:
  - Manter `requirements.in` (dependências diretas legíveis) e gerar `requirements.lock` (ou `requirements.txt` com `==` + hashes via `pip-compile` / `uv pip compile` em Python 3.12, mesma versão do `Dockerfile`).
  - Separar `requirements-dev.txt` (testes/Playwright) com pinagem compatível, garantindo builds Docker 100% determinísticos.
