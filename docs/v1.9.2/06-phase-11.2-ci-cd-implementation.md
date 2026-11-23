# Lectoria — Fase 11.2: Implementação de CI/CD, Imutabilidade de Releases e Rastreabilidade

- **Branch de trabalho**: `refactor/v1.9.2-ci-cd-hardening` (derivada de `main` @ `dda6f27`)
- **Versão da aplicação preservada**: `APP_VERSION = "1.9.1"` · `SCHEMA_VERSION = 13`
- **Escopo**: Exclusivamente Fase 11.2 (CI/CD, imutabilidade GHCR, rastreabilidade OCI e alinhamento do Compose genérico). Nenhuma alteração na VPS, nenhuma tag criada/movida e nenhum push/merge realizado.

---

## 1. Diagnóstico Inicial

Conforme documentado em [`01-infrastructure-audit.md`](./01-infrastructure-audit.md):
- **Duplicação de CI**: `ci.yml` e `publish.yml` executavam a suíte completa (Playwright + Chromium + testes de escala) em duplicidade a cada `push` em `main` (~20–28 min de runner por push, chegando a 4 execuções em um ciclo de release com tag).
- **Mutabilidade de tags versionadas**: `publish.yml` publicava `:v<APP_VERSION>`, `:<APP_VERSION>` e `:stable` em todo `push` na `main`, sobrescrevendo silenciosamente tags versionadas já lançadas caso um hotfix entrasse sem bump de versão.
- **Risco em falha de smoke test**: `:stable` era enviado no mesmo `docker/build-push-action` **antes** do smoke test de `/api/health`.
- **Rastreabilidade OCI incompleta**: `Dockerfile` não injetava `org.opencontainers.image.revision`, `version` nem `created`.
- **Compose genérico desalinhado**: `docker-compose.yml` referenciava `:latest` (não publicada) e `healthcheck` em `/api/books` (`401` sem sessão).

---

## 2. Arquivos Alterados

| Arquivo | Tipo | Resumo da Alteração |
| :--- | :--- | :--- |
| `.github/workflows/_test-suite.yml` | Criado | Workflow reutilizável (`workflow_call`) com Python 3.12, cache pip, Playwright Chromium, `py_compile`, `pytest -v tests/`, `scripts/diagnose.py --json`, `timeout-minutes: 30` e `permissions: contents: read`. |
| `.github/workflows/ci.yml` | Refatorado | Delega integralmente a `_test-suite.yml`; dispara em `push` (`feat/*`, `fix/*`, `refactor/*`) e `pull_request` (`main`), removendo `push` em `main` para eliminar duplicação. |
| `.github/workflows/publish.yml` | Refatorado | Separa `build-and-publish-continuous` (`main` → `:sha-*` → smoke test → `:stable`) de `promote-release-tag` (`tags: v*` → validação SemVer/`APP_VERSION`/ancestralidade → recusa de sobrescrita → localização de `:sha-*` sem rebuild → smoke test → promoção atômica `:vX.Y.Z`, `:X.Y.Z`, `:stable`), com concorrência global e `GITHUB_STEP_SUMMARY`. |
| `Dockerfile` | Atualizado | Adicionados `ARG VCS_REF`, `ARG APP_VERSION`, `ARG BUILD_DATE` e labels OCI `revision`, `version`, `created` (mantendo `source` e `licenses`). |
| `docker-compose.yml` | Corrigido | Imagem atualizada de `:latest` para `:stable`; healthcheck corrigido de `/api/books` para `/api/health` (`timeout=4`). |
| `tests/test_v192_ci_cd_architecture.py` | Criado | 8 testes automatizados validando sintaxe YAML, expressões GitHub Actions, rótulos OCI, Compose genérico e os Cenários A–J. |

---

## 3. Arquitetura Anterior vs. Nova Arquitetura

| Aspecto | Arquitetura Anterior (`v1.9.1`) | Nova Arquitetura (Fase 11.2) |
| :--- | :--- | :--- |
| **Push em `main`** | `ci.yml` (360 testes) + `publish.yml` (360 testes + build + push `:vX.Y.Z`, `:X.Y.Z`, `:stable` + smoke) | Apenas `publish.yml`: `preflight` → `_test-suite.yml` (1x) → build `:sha-*` → smoke test `@digest` → promove `:stable` (nunca toca `:vX.Y.Z`) |
| **Push de tag `vX.Y.Z`** | Reexecuta 360 testes + rebuild completo + sobrescreve `:vX.Y.Z`, `:X.Y.Z`, `:stable` | `preflight` (SemVer + `APP_VERSION` + ancestral `origin/main`) → bloqueia se `:vX.Y.Z`/`:X.Y.Z` existir → localiza `:sha-*` validado → smoke test `@digest` → promove sem rebuild via `imagetools create` |
| **Falha no smoke test** | `:stable` já havia sido atualizado no GHCR antes do teste | `:stable` **não** é atualizado; apenas `:sha-*` permanece para diagnóstico |
| **Tempo estimado de CI/CD por release** | ~40–50 min de runner (4 suítes completas + 2 builds) | ~15–22 min de runner (1 suíte no PR/branch + 1 suíte na `main` + 1 build + promoção de tag em ~1–2 min; **redução estimada de ~55%–65%**) |

---

## 4. Fluxo de Eventos GitHub Actions

```
feat/* | fix/* | refactor/* (push) ──► ci.yml ──► _test-suite.yml (sem publicação)
pull_request → main               ──► ci.yml ──► _test-suite.yml (sem publicação)
main (push)                       ──► publish.yml [preflight → _test-suite.yml → build :sha-* → smoke → promove :stable]
tags: v* (push)                   ──► publish.yml [preflight → checa imutabilidade → localiza :sha-* → smoke → promove :vX.Y.Z, :X.Y.Z, :stable]
workflow_dispatch                 ──► publish.yml [vazio: somente refs/heads/main; promote_tag: mesmas regras de tag imutável]
```

> **Regras de proteção de branch (documentação)**: caso o repositório remoto exija um status check nominal de `ci.yml` em `pull_request`, o nome do job `test` foi preservado em `ci.yml` (`Test Suite`). Nenhuma configuração remota do GitHub foi alterada.

---

## 5. Estratégia de Imutabilidade de Tags

1. **Bloqueio por gatilho**: o job contínuo (`main`) jamais referencia `raw_version` ou `clean_version` nas tags publicadas — envia apenas `:sha-<shortsha>` e, após smoke test, `:stable`.
2. **Validação estrita na tag**: `preflight` exige SemVer `^v[0-9]+\.[0-9]+\.[0-9]+$`, igualdade exata `TAG == v${APP_VERSION}` e `git merge-base --is-ancestor "$GITHUB_SHA" origin/main`.
3. **Recusa de sobrescrita**: `promote-release-tag` executa `docker buildx imagetools inspect` para `:vX.Y.Z` e `:X.Y.Z`; se qualquer uma já existir no GHCR, encerra com `exit 1` (`Sobrescrita recusada`).
4. **Promoção sem rebuild**: utiliza `docker buildx imagetools create` sobre `ghcr.io/nosremekk/lectoria@<digest>` (localizado via `:sha-<shortsha>` após confirmar `org.opencontainers.image.revision == GITHUB_SHA`), garantindo que `:vX.Y.Z`, `:X.Y.Z` e `:stable` apontem para o **mesmo digest** validado.

---

## 6. Estratégia de Concorrência

- **`ci.yml`**: `group: ci-${{ github.workflow }}-${{ github.ref }}`, `cancel-in-progress: true` (economiza runners em pushes sucessivos na mesma branch de desenvolvimento/PR).
- **`publish.yml`**: `group: ghcr-publish-lectoria`, `cancel-in-progress: false` (grupo único serializado para todo o repositório).
  - Se um `push` em `main` e um `push` de tag `vX.Y.Z` ocorrem em sequência, o run da tag aguarda em fila o término do run da `main`, garantindo que `:sha-<shortsha>` já exista no GHCR quando a promoção iniciar.
  - Nenhum workflow de publicação é cancelado no meio da promoção de tags.

---

## 7. Metadados OCI e Resumo de Execução

- `Dockerfile` e `publish.yml` injetam: `org.opencontainers.image.source`, `org.opencontainers.image.revision` (`${GITHUB_SHA}`), `org.opencontainers.image.version` (`${APP_VERSION}`) e `org.opencontainers.image.created` (UTC ISO-8601), sem credenciais ou segredos.
- Ambos os jobs de publicação escrevem tabela consolidada em `GITHUB_STEP_SUMMARY` com Commit SHA, Ref Git, `APP_VERSION`, tags Docker, digest `sha256:...` e status da validação/smoke test.

---

## 8. Resultados dos Testes

- **Validação local executada** (branch `refactor/v1.9.2-ci-cd-hardening`):
  - `py_compile *.py scripts/*.py`: **APROVADO (`exit 0`)**
  - `tests/test_v192_ci_cd_architecture.py` (Cenários A–J + YAML + OCI + Compose): **8/8 aprovados**
  - Suíte completa (unitários/API + E2E Playwright): **368/368 aprovados (`0` falhas)** — `313` unitários/API (`327,95 s`) + `55` E2E Playwright (`239,17 s`).
- **Validações NÃO realizadas localmente (somente confirmáveis após autorização e execução real no GitHub Actions)**:
  - Execução real do `workflow_call` nos runners `ubuntu-latest` do GitHub.
  - Login real no GHCR, push de `:sha-*`, `docker buildx imagetools inspect` e `imagetools create` contra o registro remoto.
  - Inspeção com `actionlint` e `PyYAML` (não instalados no ambiente local; validação estrutural equivalente executada via `tests/test_v192_ci_cd_architecture.py`).

---

## 9. Riscos e Limitações

- **Dependência de `:sha-<shortsha>` pré-existente**: se alguém criar e enviar uma tag `vX.Y.Z` apontando para um commit antigo de `main` publicado antes da Fase 11.2 (que não possui `:sha-<shortsha>`), o workflow falhará explicitamente (comportamento intencional para priorizar integridade histórica; basta disparar o `workflow_dispatch` contínuo na `main` se necessário, ou criar a tag apenas após um commit publicado pelo novo pipeline).
- **Ausência de `actionlint` local**: expressões GitHub Actions foram revisadas manualmente e por regex nos testes, mas a validação final definitiva ocorrerá no primeiro run real em PR/branch após autorização de push.

---

## 10. Procedimento Seguro para Validação Remota Futura (Após Autorização)

1. Enviar `refactor/v1.9.2-ci-cd-hardening` ao GitHub (`git push -u origin refactor/v1.9.2-ci-cd-hardening`) → confirmar que `ci.yml` executa `_test-suite.yml` e **não** dispara `publish.yml` (Cenário A).
2. Abrir PR para `main` → confirmar execução única de `ci.yml` sem publicação (Cenário B).
3. Após revisão e merge em `main` → confirmar execução única de `publish.yml` (`preflight` → `test` → build `:sha-*` → smoke → promove `:stable`), verificando que `:v1.9.1` **não** foi sobrescrita (Cenário C).
4. Na release `v1.9.2` (Fase 11.5), testar a promoção da tag `v1.9.2` sem rebuild (Cenário D).

---

## 11. Plano de Reversão

Qualquer regressão nos workflows pode ser revertida instantaneamente via `git revert` dos commits da Fase 11.2, restaurando os arquivos `.github/workflows/ci.yml`, `.github/workflows/publish.yml`, `Dockerfile` e `docker-compose.yml` anteriores sem qualquer impacto em banco de dados ou produção.

---

## 12. Pendências para a Fase 11.3

- Implementar `LECTORIA_SECURE_COOKIES` (`auto` | `true` | `false`) em `lectoria_session` e `lectoria_csrf`.
- Remover emissão de `Set-Cookie: lectoria_csrf` em `/static/*`.
- Remover os 2 `onsubmit="return false;"` em `static/index.html` e ativar `Content-Security-Policy-Report-Only`.
- Gerar lockfile determinístico de dependências Python 3.12.
