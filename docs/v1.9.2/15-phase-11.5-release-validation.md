# Lectoria — Fase 11.5: Validação Final e Release `v1.9.2`

- **Status**: **`PHASE_11_5_IN_PROGRESS`** *(preparação da release na branch `release/v1.9.2`; aguardando conclusão da suíte local, PR e CI remoto antes de declarar `PHASE_11_5_READY_FOR_RELEASE`)*
- **Data**: `2026-11-26`
- **Branch**: `release/v1.9.2` (baseada em `origin/main` @ `acfc42364e6ed60d78b202191db1f97f0c2d05a8`)
- **Versão alvo**: `APP_VERSION = "1.9.2"` · `SCHEMA_VERSION = 13` (sem migração de banco)

---

## 1. Escopo da Release `v1.9.2`

Encerramento do ciclo de estabilização de infraestrutura, segurança HTTP, backup/recuperação e hardening residual de container (Fases 11.1 a 11.4), sem introdução de novas funcionalidades de produto, sem redesign e sem otimizações prematuras de desempenho:
- **Fase 11.1**: Auditorias de infraestrutura, segurança, baseline de performance e avaliação Python/SQLite/VPS (`01`–`05`).
- **Fase 11.2 (`PHASE_11_2_COMPLETED`)**: Reformulação do CI/CD (`_test-suite.yml`, `ci.yml`, `publish.yml`), imutabilidade de tags GHCR, rastreabilidade OCI, smoke tests `@digest` e atualizador Docker/OCI (`deployment/docker-auto-update.sh`) baseado em `exec_fp` com backup obrigatório e *fail-closed*.
- **Fase 11.3 (`PHASE_11_3_COMPLETED`)**: Segurança HTTP configurável (`LECTORIA_SECURE_COOKIES`, `LECTORIA_FORWARDED_ALLOW_IPS`, `LECTORIA_CSP_MODE` com padrão `report-only`, `LECTORIA_HSTS_ENABLED`, `Permissions-Policy`), remoção de `Set-Cookie` em `/static/*`, remoção de `onsubmit` inline e `constraints.txt`.
- **Fase 11.4 (`PHASE_11_4_COMPLETED`)**: Validação SQLite `mode=ro` sem downgrade automático, extração segura de `covers.tar.gz` em staging (incluindo normalização de `data/covers/`), invalidação padrão de sessões na restauração, container não-root (`lectoria:lectoria` `1000:1000`), `no-new-privileges:true`, `cap_drop: [ALL]`, `read_only: true`, `tmpfs /tmp` e `/app/books:ro`.

---

## 2. Alterações Realizadas na Fase 11.5

- Bump coordenado de `1.9.1` → `1.9.2` em:
  - [`config.py`](../../config.py) (`APP_VERSION = "1.9.2"`)
  - [`static/index.html`](../../static/index.html) e [`static/reader.html`](../../static/reader.html) (`data-app-version="1.9.2"` e cache-busting `?v=1.9.2`)
  - [`static/js/app.js`](../../static/js/app.js) (`window.LECTORIA_FRONTEND_VERSION = "1.9.2"`)
  - Testes de alinhamento de versão (`tests/test_v151_social_contracts.py`, `tests/test_v192_ci_cd_architecture.py`, `tests/test_v192_http_security.py`)
- Consolidação documental em [`CHANGELOG.md`](../../CHANGELOG.md), [`README.md`](../../README.md), [`docs/v1.9.2/05-implementation-roadmap.md`](./05-implementation-roadmap.md) e preservação do encerramento da Fase 11.4 ([`14-phase-11.4-backup-and-container-hardening.md`](./14-phase-11.4-backup-and-container-hardening.md)).
- Referências históricas (`v1.9.1`, relatórios anteriores, testes de compatibilidade) e `SCHEMA_VERSION = 13` preservados integralmente.

---

## 3. Evidências de Testes

- **Suíte local completa (`pytest -q`, Python 3.14.9 + Playwright Chromium)**: **`398 passed, 2 skipped, 0 failed, 3 warnings in 597.89s (0:09:57)`** (`APP_VERSION=1.9.2`, `SCHEMA_VERSION=13`). Os 2 testes ignorados localmente são os testes Docker reais (`test_v192_docker_auto_update_integration.py` e `test_v192_docker_hardening_integration.py`, por ausência de Docker Engine no host local).
- **CI remoto (`push` + `pull_request` em `ubuntu-latest` com Docker Engine real + Playwright Chromium)**: *(validado via PR de release)*
- **Validações manuais**: confirmadas pelo proprietário ao final das Fases 11.3 e 11.4 em ambiente real HTTPS (login/logout, cookies, CSRF, Biblioteca/capas, Descoberta, Atlas, leitores PDF/EPUB, escrita SQLite e drill real de recuperação).

---

## 4. Compatibilidade, Atualização e Migração de Permissões

- **Compatibilidade**: Docker Compose genérico, instalação independente (venv/systemd), Localhost HTTP, LAN HTTP, Tailscale privado HTTP, Tailscale Serve HTTPS e reverse proxy HTTPS.
- **Instalações novas**: funcionam diretamente sob `lectoria:lectoria` (`1000:1000`) com volumes nomeados e `books:ro`.
- **Instalações anteriores que executavam como `root`**: exigem migração controlada de posse (`root` → `1000:1000`) **exclusivamente** nos arquivos persistentes graváveis sob `data/` (e `backups/`, se necessário), com o container parado e sem seguir symlinks — **nunca** usar `chmod 777` e **nunca** alterar permissões de `books/`. Ver procedimento completo em [`docs/DEPLOYMENT.md`](../DEPLOYMENT.md).
- **Instalações já migradas (Fase 11.4 concluída)**: atualização direta via atualizador automático padrão (`lectoria-auto-update.service` com timer pausado), sem repetir migração de permissões nem alterar `compose.yaml`.

---

## 5. Procedimento Final de Publicação (Compatível com `.github/workflows/publish.yml`)

Revisão de [`.github/workflows/publish.yml`](../../.github/workflows/publish.yml):
- Push em `main` executa `preflight` → `_test-suite.yml` (400 testes) → build `:sha-<shortsha>` → smoke test `@digest` (`/api/health == 1.9.2`) → promoção para `:stable` (preservando `:v1.9.1` e `:1.9.1`).
- Push de tag `v1.9.2` sobre **exatamente o mesmo commit de `main`** executa `preflight` (validando `v1.9.2 == v${config.APP_VERSION}` e ancestralidade em `origin/main`) → `promote-release-tag` (promove o digest exato de `:sha-<shortsha>` para `:v1.9.2`, `:1.9.2` e `:stable` **sem reconstruir a imagem** e recusando sobrescrita se a tag já existir).

**Sequência oficial (17 passos — executar somente após autorização do proprietário)**:
1. Aprovação do PR de release (`release/v1.9.2` → `main`).
2. Pausa controlada do `lectoria-auto-update.timer` na VPS.
3. Backup preventivo local (`lectoria-backup.service`).
4. Merge autorizado do PR em `main`.
5. Acompanhamento integral de `publish.yml` (modo `continuous`).
6. Publicação da imagem `:sha-<commit>` com `APP_VERSION=1.9.2`.
7. Smoke test sobre o digest imutável (`/api/health` retornando `healthy` e `1.9.2`).
8. Promoção automática para `:stable`.
9. Verificação dos metadados OCI (`revision`, `version=1.9.2`) e preservação de `:v1.9.1` e `:1.9.1`.
10. Deploy controlado na VPS via `lectoria-auto-update.service` (com timer pausado, sem nova migração de permissões).
11. Validação técnica e funcional em produção (`/api/health == 1.9.2`, `SCHEMA_VERSION=13`, hardening preservado, navegador real).
12. Criação autorizada da tag Git anotada `v1.9.2` exatamente sobre o commit de merge em `main` e push da tag.
13. Acompanhamento de `publish.yml` (modo `release_tag` — promoção sem rebuild).
14. Confirmação de que `:v1.9.2`, `:1.9.2`, `:sha-<commit>` e `:stable` apontam para o **mesmo digest imutável**.
15. Publicação das notas da release no GitHub Release (`v1.9.2`).
16. Reativação do `lectoria-auto-update.timer` e confirmação de `NO_EXECUTABLE_CHANGE`.
17. Encerramento documental final da Fase 11.5 (`PHASE_11_5_COMPLETED`).

---

## 6. Procedimento de Recuperação e Limitações Conhecidas

- **Rollback de imagem**: retornar para a referência imutável anterior (`ghcr.io/nosremekk/lectoria@sha256:130be101114b8845892a169a9a601744d78fd9cdbf849a2221ab440f1261a3fd`, compatível com Schema `13`), mantendo o timer pausado e sem restauração automática do banco.
- **Limitações conhecidas**:
  1. `LECTORIA_CSP_MODE` permanece `report-only` por padrão (compatibilidade com EPUBs variados); não se declara ausência universal de violações fora dos cenários testados.
  2. Backups locais protegem contra erro operacional/corrupção lógica, mas **não** substituem cópia externa (off-site) em caso de perda física completa do servidor.
  3. Nenhuma medida de segurança torna a aplicação invulnerável; manutenção contínua de dependências e boas práticas operacionais permanecem responsabilidade do administrador.
  4. Testes Docker reais dependem do Docker Engine (executados no CI remoto `ubuntu-latest`, ignorados localmente quando Docker não está instalado).
