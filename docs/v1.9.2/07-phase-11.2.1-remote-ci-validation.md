# Lectoria — Fase 11.2.1: Relatório de Validação Remota Controlada do CI/CD

- **Status Final**: **`REMOTE_CI_VALIDATED`**
- **Data**: `2026-11-23`
- **Branch**: `refactor/v1.9.2-ci-cd-hardening`
- **Pull Request**: [PR #1 — `refactor(ci): harden GHCR releases and deduplicate workflows`](https://github.com/Nosremekk/Lectoria/pull/1) (`DO NOT MERGE — Pending production deployment safety review.`)
- **Restrições preservadas**: `main` intacta em `dda6f27`, `APP_VERSION = "1.9.1"`, `SCHEMA_VERSION = 13`, zero tags criadas/movidas, zero publicações GHCR, zero alterações na VPS.

---

## 1. Estado Inicial Verificado

- Branch base: `refactor/v1.9.2-ci-cd-hardening` com os 3 commits locais da Fase 11.2 (`2ff94f1`, `47a0391`, `fc7816c`).
- Repositório remoto confirmado: `origin https://github.com/Nosremekk/Lectoria.git`.
- Nenhum PR aberto anteriormente (`OPEN_PRS: []`) e nenhum workflow em execução no momento do push.
- Arquivos pré-existentes não rastreados preservados (`docs/v1.9.1/05-release-deployment-report.md`, `docs/v1.9.2/01..04*.md`).

---

## 2. Branch e Commits na Branch de Trabalho

| Commit | Descrição |
| :--- | :--- |
| `2ff94f1` | `refactor(ci): deduplicate test workflows and enforce release immutability` |
| `47a0391` | `fix(compose): align image tag and healthcheck` |
| `fc7816c` | `docs(v1.9.2): document phase 11.2 validation` |
| `fb4ac5d` | `fix(ci): harden tag immutability check (fail-closed) and prevent stable regression` *(hardening aplicado durante a revisão de segurança da Seção 8 antes do push)* |

---

## 3. Resultado do `actionlint` (Binário Oficial Verificado)

- **Ferramenta**: `actionlint 1.7.12` (baixado do release oficial `rhysd/actionlint`, checksum SHA-256 validado com sucesso).
- **Arquivos inspecionados**:
  - `.github/workflows/_test-suite.yml`: **0 parse errors, 0 total errors**
  - `.github/workflows/ci.yml`: **0 parse errors, 0 total errors**
  - `.github/workflows/publish.yml`: **0 parse errors, 0 total errors**
- **Resultado consolidado**: **`Found 0 errors in 3 files`**.
- *Nota de transparência*: `shellcheck` e `pyflakes` não estavam instalados no `$PATH` local, portanto essas duas sub-regras opcionais do `actionlint` ficaram desabilitadas durante o lint local; toda a sintaxe YAML, expressões `${{ ... }}`, contextos, `workflow_call`, `needs`, `outputs`, `if`, `permissions` e `concurrency` foram validados com 0 erros.

---

## 4. Pull Request Aberto

- **URL**: https://github.com/Nosremekk/Lectoria/pull/1
- **Título**: `refactor(ci): harden GHCR releases and deduplicate workflows`
- **Base ← Head**: `main ← refactor/v1.9.2-ci-cd-hardening`
- **Sinalização de bloqueio**: `**DO NOT MERGE — Pending production deployment safety review.**` · automerge desativado.

---

## 5. Execução Real no GitHub Actions

| Workflow | Run ID | Evento | Commit | Resultado | Duração | Testes | Link |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **CI** (`Test Suite / Automated Test Suite (Python 3.12)`) | `#45259058204` | `pull_request` | `fb4ac5d` | **`completed / success`** | `547 s` (`9m07s`) | **`368 passed`** (`514,22 s`, 3 warnings legados) | [Run #45259058204](https://github.com/Nosremekk/Lectoria/actions/runs/45259058204) |
| **CI** (`Test Suite / Automated Test Suite (Python 3.12)`) | `#45258918710` | `push` | `fb4ac5d` | **`completed / success`** | `756 s` (`12m36s`) | **`368 passed`** | [Run #45258918710](https://github.com/Nosremekk/Lectoria/actions/runs/45258918710) |

### Verificações confirmadas nos steps do Run `#45259058204`
1. Workflow correto acionado (`CI`, zero `Publish Docker Image (GHCR)`).
2. Workflow reutilizável `_test-suite.yml` invocado via `workflow_call` (job `Test Suite / Automated Test Suite (Python 3.12)`).
3. Python `3.12.16` configurado com sucesso.
4. Dependências (`sqlite3`, `requirements-dev.txt`) e Playwright Chromium instalados com sucesso.
5. `python -m py_compile *.py scripts/*.py`: `success`.
6. `pytest -v tests/`: **`368 passed, 3 warnings in 514.22s (0:08:34)`**.
7. `python scripts/diagnose.py --json`: `success`.

---

## 6. Falhas Encontradas e Correções

- **Nenhuma falha no CI remoto** (aprovado na primeira execução).
- **Melhorias preventivas identificadas durante a revisão de segurança da Seção 8 (aplicadas no commit `fb4ac5d` antes do push)**:
  1. **Checagem de imutabilidade *fail-closed*** em `publish.yml`: se `docker buildx imagetools inspect` falhar por erro de rede/503/401 (sem mensagem `not found|manifest unknown`), o workflow agora **aborta com erro** em vez de assumir ausência da tag no GHCR.
  2. **Proteção contra regressão de `:stable`**: `preflight` agora verifica se o commit da tag é exatamente `HEAD` de `origin/main` (`is_main_head`). Se uma tag apontar para um commit ancestral mais antigo, promove apenas `:vX.Y.Z` e `:X.Y.Z` sem regredir `:stable`.

---

## 7. Revisão de Segurança de `publish.yml` (Seção 8)

- **8.1 Imutabilidade**:
  - `build-and-publish-continuous` (`main`) jamais referencia `raw_version`/`clean_version`; envia apenas `:sha-<shortsha>` e promove `:stable`.
  - `promote-release-tag` bloqueia sobrescrita de `:vX.Y.Z` e `:X.Y.Z` de forma *fail-closed* antes de qualquer escrita.
  - Condição de corrida entre inspeção e criação mitigada pelo grupo global `concurrency: { group: ghcr-publish-lectoria, cancel-in-progress: false }`.
- **8.2 Promoção de digest**:
  - Localiza `:sha-<shortsha>` sem `docker/build-push-action` (zero rebuild), valida `org.opencontainers.image.revision == GITHUB_SHA`, executa smoke test `/api/health` contra o `@digest` exato e só então executa `imagetools create`.
  - No fluxo contínuo, `:stable` só é promovido após aprovação do smoke test.
- **8.3 Concorrência e regressão**:
  - Push em `main` + push de tag simultâneos são serializados na fila única `ghcr-publish-lectoria` sem cancelamento.
  - Reexecução manual (`workflow_dispatch`) recusa branches fora de `refs/heads/main` no modo contínuo e aplica todas as travas de imutabilidade no modo `promote_tag`.
  - `:stable` não regride se uma tag apontar para commit anterior ao `HEAD` de `origin/main`.
- **8.4 Credenciais e permissões**:
  - Permissão global mínima `contents: read`; `packages: write` restrito apenas aos 2 jobs de publicação em `publish.yml`.
  - `publish.yml` **não** dispara em `pull_request` (evitando execução de código de PR com permissão `packages: write`).

---

## 8. Confirmações de Isolamento (GHCR, `main` e VPS)

- **Ausência de publicação GHCR**: nenhum workflow `Publish Docker Image (GHCR)` foi disparado após o push da `v1.9.1` (`#45248554299`). As tags `:v1.9.1`, `:1.9.1` e `:stable` permanecem apontando exatamente para o digest da `v1.9.1` (`sha256:da4dca9cac06d28817d62b0d48224c5063a3e993e115b39682d2e766c1b6a253`).
- **`main` intacta**: permanece no commit `dda6f27` (`tag: v1.9.1`).
- **Ausência de deploy / VPS intacta**: nenhum comando foi executado na VPS nesta etapa; nenhum container reiniciado ou dado alterado.

---

## 9. Riscos Residuais e Procedimento Recomendado para Integração Futura

> **Nota obrigatória**: o sucesso do CI no Pull Request comprova o funcionamento real de `ci.yml` + `_test-suite.yml` no GitHub Actions, **mas NÃO valida em execução real os passos de login/push/promoção de `publish.yml` contra o GHCR**. Essa validação ocorrerá somente após aprovação explícita do proprietário para integração em `main`.

### Procedimento recomendado para integração futura (após autorização)
1. Revisar e aprovar o [PR #1](https://github.com/Nosremekk/Lectoria/pull/1).
2. Realizar o merge em `main` em janela controlada e acompanhar o primeiro run real de `publish.yml` (`continuous`: `:sha-*` → smoke test → `:stable`), confirmando que `:v1.9.1` permanece intacta.
3. Prosseguir para a **Fase 11.3 (Segurança HTTP e Dependências)** e, na **Fase 11.5 (`v1.9.2`)**, validar pela primeira vez em produção o fluxo `promote-release-tag` sem rebuild.
