# Lectoria — Fase 11.2.2: Auditoria de Pré-Integração Segura e Compatibilidade Self-Hosted

- **Status da Revisão**: **`PREMERGE_READY`** *(condicionado à execução do plano seguro da Seção 9 e à inspeção pelo proprietário da falha recente do `lectoria-auto-update.service`)*
- **Data**: `2026-11-23`
- **Pull Request**: [PR #1 — `refactor(ci): harden GHCR releases and deduplicate workflows`](https://github.com/Nosremekk/Lectoria/pull/1)
- **Branch**: `refactor/v1.9.2-ci-cd-hardening` (`8698175`) → `main` (`dda6f27`)
- **Produção**: `v1.9.1`, Schema SQLite `13`, **100% intacta** (nenhum merge, push, tag, publicação GHCR, `sudo` ou reinício executado).

---

## 1. Estado Atual do PR #1

| Campo | Fato Verificado |
| :--- | :--- |
| **Estado / Mergeable** | `open`, `draft: false`, `mergeable: true`, marcado com **`DO NOT MERGE — Pending production deployment safety review.`** |
| **Head / Base** | `refactor/v1.9.2-ci-cd-hardening` @ `8698175` ← `main` @ `dda6f27` (`v1.9.1`) |
| **Diff (`main..HEAD`)** | 9 arquivos (`+934 / -89`): `.github/workflows/_test-suite.yml`, `ci.yml`, `publish.yml`, `Dockerfile`, `docker-compose.yml`, `tests/test_v192_ci_cd_architecture.py` e `docs/v1.9.2/05..07*.md`. |
| **`actionlint 1.7.12`** | `Found 0 errors in 3 files` (`_test-suite.yml`, `ci.yml`, `publish.yml`). |
| **Testes locais** | `368/368` aprovados (`313` unitários/API + `55` E2E Playwright). |
| **Condições que atualizam `:stable` no novo `publish.yml`** | (1) `push` em `main` após `preflight` + `_test-suite.yml` + build `:sha-*` + smoke test `/api/health`; (2) `push` de tag `vX.Y.Z` (ou `workflow_dispatch` com `promote_tag`) **somente se** `COMMIT_SHA == HEAD(origin/main)` (`is_main_head == true`). |

---

## 2. Resultado Final do GitHub Actions

| Run ID | Workflow | Evento | Commit | Estado | Duração | Observação |
| :---: | :--- | :---: | :---: | :---: | :---: | :--- |
| [`#45259058204`](https://github.com/Nosremekk/Lectoria/actions/runs/45259058204) | `CI` (`_test-suite.yml`) | `pull_request` | `fb4ac5d` | **`completed / success`** | `547 s` | Último commit de código/workflows (`368 passed`) |
| [`#45258918710`](https://github.com/Nosremekk/Lectoria/actions/runs/45258918710) | `CI` (`_test-suite.yml`) | `push` | `fb4ac5d` | **`completed / success`** | `756 s` | Último commit de código/workflows (`368 passed`) |
| [`#45261598147`](https://github.com/Nosremekk/Lectoria/actions/runs/45261598147) | `CI` (`_test-suite.yml`) | `pull_request` | `8698175` | **`completed / success`** | `648 s` | Commit puramente documental (`07-phase-11.2.1...md`) |
| [`#45261586365`](https://github.com/Nosremekk/Lectoria/actions/runs/45261586365) | `CI` (`_test-suite.yml`) | `push` | `8698175` | **`completed / success`** | `539 s` | Commit puramente documental |

Nenhum workflow `Publish Docker Image (GHCR)` foi disparado pelo PR #1.

---

## 3. Investigação dos Digests Docker (Divergência `36af447d...` vs `da4dca9c...`)

### Fatos Verificados Diretamente no GHCR (API OCI v2)

| Referência / Run | OCI Image Index Digest (`Docker-Content-Digest`) | Sub-manifesto `linux/amd64` | Config Digest (`Image ID`) | Timestamp `created` |
| :--- | :--- | :--- | :--- | :--- |
| Run `#45210832288` (`main` @ `dda6f27`, `15:56 UTC`) | `sha256:36af447d240272bab0cefb97bc0972534b77c87fe98d0d3cd383de9b3e4d3982` | `sha256:b3ca97d1ba4c7fac50356e97e26fd0c2bd1644526926cd8796781541c3df0af9` | `sha256:e4607f2adb7ee9179dd7db87204a6f12129151a4383f1b76b42bd6bf5eb346dd` | `2026-11-23T16:09:27.735162071Z` |
| Run `#45248554299` (`tag: v1.9.1` @ `dda6f27`, `19:24 UTC`) — **tags atuais `v1.9.1`, `1.9.1`, `stable`** | `sha256:da4dca9cac06d28817d62b0d48224c5063a3e993e115b39682d2e766c1b6a253` | `sha256:b3ca97d1ba4c7fac50356e97e26fd0c2bd1644526926cd8796781541c3df0af9` | `sha256:e4607f2adb7ee9179dd7db87204a6f12129151a4383f1b76b42bd6bf5eb346dd` | `2026-11-23T16:09:27.735162071Z` |

### Explicação Técnica da Divergência (Sem Comprometimento)
1. **A imagem executável `linux/amd64` é 100% IDÊNTICA bit a bit** nos dois digests: o manifesto da plataforma (`b3ca97d1...`), o `Image ID` / config (`e4607f2a...`), todas as camadas e o timestamp `created` (`16:09:27Z`) são exatamente os mesmos (100% cache hit do Buildx).
2. **Por que o digest externo do OCI Image Index mudou?** No workflow antigo da `v1.9.1`, o push da tag `v1.9.1` disparou o run `#45248554299` (`docker/build-push-action@v6`), que anexa um segundo sub-manifesto `unknown/unknown` (BuildKit *provenance attestation*) contendo metadados do novo run. A alteração desse sub-manifesto de atestação gerou um novo hash para o OCI Index (`da4dca9c...`), sem mudar um único byte da imagem `linux/amd64`.
3. **Rótulos OCI atualmente presentes na `v1.9.1`**: apenas `title`, `description`, `source`, `licenses` (ainda sem `revision`/`version`/`created`, que entram com o PR #1).
4. **Limitação na VPS (sem `sudo`)**: o usuário `emerson` recebeu `permission denied` em `/var/run/docker.sock`, não sendo possível ler o `Image ID` local do container sem elevação; porém `/api/health` confirma `{"status":"healthy","app_name":"Lectoria","version":"1.9.1"}`.

---

## 4. Diagnóstico do Auto-Update e Risco de Deploy Inesperado

### Fatos Verificados na VPS (Somente-Leitura, Sem `sudo`)
- `lectoria-auto-update.timer`: **`enabled` / `active (waiting)`**, dispara a cada 15 min (`OnUnitInactiveSec=15min`).
- `/srv/nosreme/lectoria/compose.yaml`: referencia **`ghcr.io/nosremekk/lectoria:stable`**.
- **Achado operacional relevante**: `lectoria-auto-update.service` apresentou **`Active: failed (Result: exit-code)`** em `2026-11-23 20:37:48 UTC` (`Process 847313, status=1/FAILURE, CPU: 5ms`).
- **O que permanece desconhecido (restrição de permissão respeitada)**: o corpo de `/usr/local/sbin/lectoria-auto-update` (`0700 root:root`) e o `journalctl` completo do serviço não são legíveis sem `sudo` (usuário `emerson` fora dos grupos `adm`/`systemd-journal`).

### Resposta Objetiva: Um merge do PR #1 poderia atualizar automaticamente a VPS mesmo sem alterar `APP_VERSION`?
- **SIM.**
- **Mecanismo exato**:
  1. Ao fazer merge do PR #1 em `main`, `publish.yml` rodará em modo `continuous`.
  2. O `Dockerfile` atualizado adiciona novos `LABEL` OCI (`revision`, `version`, `created`), produzindo um **novo `Image ID` e novo digest** para o mesmo `APP_VERSION = "1.9.1"`.
  3. Após passar nos 368+ testes e no smoke test `/api/health`, `publish.yml` promoverá esse novo digest para **`ghcr.io/nosremekk/lectoria:stable`** (preservando `:v1.9.1` intacta).
  4. Assim que `lectoria-auto-update.service` voltar a executar com sucesso, ele verá o novo digest em `:stable`, fará pull e recriará o container `lectoria` (sem migração de banco, mantendo Schema v13 e `1.9.1`, mas causando um reinício do container).
- **Mitigação**: pausar temporariamente `lectoria-auto-update.timer` durante a janela de merge do PR #1 (ou diagnosticar antes a falha de `20:37:48 UTC`) — detalhado na Seção 9.

---

## 5. Matriz de Compatibilidade Self-Hosted (Cenários A–F)

Auditoria no código confirmou **zero referências hardcoded** a `nosreme.cloud`, `tailf5c3ce`, `100.82.105.34` ou `/srv/nosreme` em `*.py`, `static/`, `scripts/`, `deployment/`, `Dockerfile` e `docker-compose.yml`.

| Cenário | Transporte | Comportamento Atual (`v1.9.1`) | Requisitos para Compatibilidade Permanente (Fase 11.3+) |
| :--- | :---: | :--- | :--- |
| **A. Localhost** (`http://127.0.0.1:8765`) | HTTP | **100% funcional** (login, sessão `HttpOnly/Lax`, CSRF, PDF/EPUB, APIs). | Nunca exigir `Secure=True` em HTTP puro; `LECTORIA_SECURE_COOKIES=auto` deve emitir `Secure=False` aqui. |
| **B. Rede Local (LAN)** (`http://192.168.x.x:8765`) | HTTP | **Funcional** hoje porque `Secure` não está ativo. **Atenção**: navegadores **NÃO** tratam IPs LAN como *secure context* (diferente de `localhost`); se `Secure=True` for forçado em HTTP LAN, o navegador **descarta** `lectoria_session` e `lectoria_csrf` e o login quebra! | `LECTORIA_SECURE_COOKIES=auto` (padrão) **deve** detectar `scheme == "http"` e manter `Secure=False` (ou permitir `false` explícito). Documentar que HTTP na LAN não protege o transporte contra interceptação na rede local. |
| **C. Tailscale Privado (IP / MagicDNS HTTP)** | HTTP (túnel WireGuard) | **Funcional** hoje. Embora o túnel WireGuard criptografe o tráfego entre nós, para o navegador a URL `http://100.x.y.z:8765` é **HTTP não-seguro** — cookies `Secure=True` seriam descartados! | Tratar igual ao Cenário B: `auto` → `Secure=False` quando `http://`. Documentar que WireGuard protege a camada de rede, mas não equivale a HTTPS fim a fim no navegador (sem HSTS/Secure Context). |
| **D. Tailscale Serve (`https://*.ts.net`)** | HTTPS (TLS no `tailscaled` → HTTP local `127.0.0.1:8765`) | Funcional, mas cookies atualmente **sem flag `Secure`**. | Com `LECTORIA_SECURE_COOKIES=auto` (ou `true` no Compose de produção) + `X-Forwarded-Proto: https` de proxy confiável (`127.0.0.1`), emitir `Secure=True` em `lectoria_session` e `lectoria_csrf`. |
| **E. Reverse Proxy HTTPS (Caddy, Nginx, Traefik)** | HTTPS → HTTP | `Dockerfile:76` usa `--proxy-headers --forwarded-allow-ips *`, o que **confia indiscriminadamente** em `X-Forwarded-Proto`/`X-Forwarded-For` de qualquer cliente. CSRF valida `Origin`/`Referer` vs `Host` (exige proxy preservando `Host`). | **Risco identificado**: substituir `*` por variável configurável `LECTORIA_FORWARDED_ALLOW_IPS` (padrão restrito, ex.: `127.0.0.1`, configurável para a rede do proxy Docker). Documentar envio de `Host` e `X-Forwarded-Proto` no Caddy/Nginx/Traefik. |
| **F. Docker de Terceiros** | HTTP ou HTTPS | 100% independente de domínios/caminhos do desenvolvedor. `docker-compose.yml` genérico corrigido no PR #1 para `:stable` + `/api/health`. | Padrões seguros out-of-the-box: `LECTORIA_SECURE_COOKIES=auto`, volumes padrão `/app/data`, `/app/books`, `/app/backups`, zero dependência externa. |

---

## 6. Política Recomendada de Cookies (Requisitos para a Fase 11.3)

`LECTORIA_SECURE_COOKIES=auto|true|false` (padrão: `auto`), aplicada **uniformemente** a `lectoria_session` (`HttpOnly=True`, `SameSite=lax`) e `lectoria_csrf` (`HttpOnly=False`, `SameSite=lax`, emitido **apenas** em `/`, `/reader` e `/api/auth/csrf`, **nunca** em `/static/*`):
- **`auto`**: `Secure=True` somente quando `request.url.scheme == "https"` (já resolvido pelo Uvicorn `--proxy-headers` **exclusivamente** a partir de IPs confiáveis em `LECTORIA_FORWARDED_ALLOW_IPS`).
- **`true`**: `Secure=True` sempre (recomendado para instalações 100% HTTPS como Tailscale Serve / Caddy HTTPS).
- **`false`**: `Secure=False` sempre (override explícito para ambientes HTTP).
- **Testes obrigatórios na Fase 11.3**: (1) `http://127.0.0.1` com `auto` → sem `Secure`; (2) `http://192.168.x.x` (simulado via `Host`) com `auto` → sem `Secure`; (3) `X-Forwarded-Proto: https` de proxy confiável com `auto` → com `Secure`; (4) `X-Forwarded-Proto: https` de cliente **não** confiável → ignorado; (5) modo `true` → sempre `Secure`; (6) `/static/*` nunca recebe `Set-Cookie`.

---

## 7. Estratégia de Publicação para Terceiros

O novo `publish.yml` atende plenamente tanto o desenvolvedor quanto terceiros self-hosted:
- **Tags versionadas imutáveis (`:vX.Y.Z` e `:X.Y.Z`)**: recomendadas para instalações de terceiros que desejam estabilidade previsível e upgrades voluntários.
- **Canal `:stable`**: adequado para instalações que optam explicitamente por atualização contínua validada (testes + smoke test), mas **não** deve ser a única opção recomendada para terceiros conservadores — documentar fixação por `:vX.Y.Z` ou `@sha256:...` no `README.md` / `docs/DEPLOYMENT.md`.
- **Tags por commit (`:sha-<shortsha>`) e digests (`@sha256:...`)**: garantem rastreabilidade exata e rollback determinístico.

---

## 8. Riscos Identificados

1. **`lectoria-auto-update.service` em estado `failed` na VPS** (`20:37:48 UTC`, `exit 1` em `5ms`) — causa desconhecida sem acesso administrativo; precisa ser inspecionada pelo proprietário antes da Fase 11.4.
2. **Merge do PR #1 gera novo digest em `:stable`** — causará restart automático do container se o auto-update estiver ativo e funcional (mesmo com `APP_VERSION = "1.9.1"`).
3. **`--forwarded-allow-ips "*"` no `Dockerfile:76`** — permite spoofing de `X-Forwarded-Proto`/`X-Forwarded-For` se a porta `8765` for exposta diretamente sem proxy reverso (corrigir na Fase 11.3 junto com `LECTORIA_SECURE_COOKIES`).
4. **Fluxo `publish.yml` ainda não executado contra o GHCR real** — será validado pela primeira vez no merge controlado do PR #1 (modo `continuous`) e depois na tag `v1.9.2` (modo `release_tag`).

---

## 9. Plano Seguro de Integração do PR #1 (Não Executado)

1. **Pré-condições**: confirmar CI verde no commit `8698175` (PR #1), confirmar `main` em `dda6f27` e VPS saudável em `1.9.1`.
2. **Evitar deploy inesperado**: antes do merge, o proprietário pausa temporariamente `lectoria-auto-update.timer` na VPS (`sudo systemctl stop lectoria-auto-update.timer`) e inspeciona o motivo da falha de `20:37:48 UTC` (`sudo journalctl -u lectoria-auto-update.service -n 50`).
3. **Preservar a imagem atual**: registrar na VPS o `Image ID` e `RepoDigests` atuais (`docker inspect lectoria --format '{{.Image}} {{.RepoDigests}}'`), e confirmar backup SQLite recente íntegro em `/srv/nosreme/lectoria/backups/`.
4. **Executar merge do PR #1 em `main`** (sem criar tags Git).
5. **Validar a primeira execução real de `publish.yml` (`continuous`)**: confirmar que `ci.yml` **não** dispara em duplicidade, `publish.yml` executa `preflight` → `_test-suite.yml` → build `:sha-<short>` → smoke test → promove `:stable`, e conferir em `GITHUB_STEP_SUMMARY` que `:v1.9.1` e `:1.9.1` **permaneceram** no digest anterior `sha256:da4dca9c...`.
6. **Verificar container em produção (atualização controlada)**: executar pull manual ou reativar o timer após validação do digest; confirmar `/api/health == 1.9.1`, `schema_migrations == 13`, `PRAGMA integrity_check == ok`, `books == 238`, `users == 5`, zero OOM.
7. **Detecção de regressões e recuperação**: em caso de falha do container, fixar temporariamente `image: ghcr.io/nosremekk/lectoria@sha256:da4dca9cac06d28817d62b0d48224c5063a3e993e115b39682d2e766c1b6a253` no `compose.yaml` e subir com `docker compose up -d`. *(Nota: como o PR #1 não altera schema SQLite — permanece v13 — o rollback de imagem é 100% compatível com o banco atual; em releases futuras com migração de schema, o rollback exigirá restauração coordenada do snapshot SQLite pré-update).*
8. **Liberar auto-update**: reativar `lectoria-auto-update.timer` somente após confirmar estabilidade do container e resolução da falha de `20:37:48 UTC`.

---

## 10. Pendências para Fases Seguintes e Recomendação Final

- **Fase 11.3 (Segurança HTTP e Dependências)**: implementar `LECTORIA_SECURE_COOKIES=auto|true|false`, `LECTORIA_FORWARDED_ALLOW_IPS`, remover `Set-Cookie` de `/static/*`, remover 2 `onsubmit` inline, ativar `CSP-Report-Only`, criar lockfile Python 3.12.
- **Fase 11.4 (Deploy Resiliente na VPS)**: inspecionar e corrigir `/usr/local/sbin/lectoria-auto-update` (incluindo a falha de `20:37:48 UTC`), implementar rollback automático ciente de schema + snapshot SQLite pré-update e aplicar hardening do `compose.yaml`.
- **Recomendação final sobre o merge do PR #1**: **Pronto para merge controlado** (`CI` verde em `fb4ac5d` e `8698175`), seguindo rigorosamente o plano seguro da Seção 9 (pausar temporariamente o `lectoria-auto-update.timer` e inspecionar a falha de `20:37:48 UTC` antes do merge) assim que o proprietário autorizar expressamente.
