# Lectoria — Fase 11.3: Segurança HTTP, Proxies Confiáveis, CSP e Dependências

- **Status**: **`PHASE_11_3_COMPLETED`** *(integrada em `main`, publicada em `:stable`, implantada de forma controlada na VPS e validada via testes automatizados, HTTPS real e checklist funcional manual do proprietário; `APP_VERSION = "1.9.1"`, `SCHEMA_VERSION = 13`)*
- **Data**: `2026-11-24` (implementação e CI) · `2026-11-25` (merge, publicação, deploy controlado e encerramento)
- **Branch / PR / Commit em `main`**: `refactor/v1.9.2-http-security` @ `d207614` → [PR #2](https://github.com/Nosremekk/Lectoria/pull/2) integrado em `main` no commit `4d682e3d2c611b3e67062a51f07c922a20aadecf` (sem tag Git nem release)

---

## 1. Problemas Concretos Identificados (Base: Fase 11.1 / 11.2.2)

1. **Cookies sem atributo `Secure`**: `main.py` não lia `LECTORIA_SECURE_COOKIES` e nunca aplicava `secure=...` em `lectoria_session` nem em `lectoria_csrf` (`/api/auth/setup`, `/api/auth/login`, `/api/auth/logout`, `/api/auth/csrf` e middleware).
2. **Emissão espúria de `Set-Cookie: lectoria_csrf` em `/static/*`**: o middleware emitia cookie CSRF em CSS/JS/imagens estáticas, prejudicando cacheabilidade.
3. **Confiança indiscriminada em proxies (`--forwarded-allow-ips "*"`)**: `Dockerfile:76` confiava em `X-Forwarded-Proto` e `X-Forwarded-For` de qualquer cliente; `main.py` lia `X-Forwarded-For` em `/api/sources/*` sem validar se o peer era confiável.
4. **Ausência de `Content-Security-Policy` e `Permissions-Policy` em `/` e `/reader`**: apenas capas SVG possuíam CSP; 2 event handlers inline (`onsubmit="return false;"`) existiam em `static/index.html`.
5. **Dependências abertas sem constraints**: `requirements.txt` não possuía arquivo de constraints para builds reproduzíveis entre Docker, CI e instalação independente.

---

## 2. Alterações Implementadas e Justificativas Técnicas

| Arquivo | Alteração | Justificativa |
| :--- | :--- | :--- |
| [`config.py`](../../config.py) | Adicionados `parse_secure_cookies_mode`, `parse_csp_mode`, `parse_bool_env`, `parse_forwarded_allow_ips`, `is_trusted_proxy_ip` e constantes `SECURE_COOKIES_MODE`, `FORWARDED_ALLOW_IPS`, `CSP_MODE`, `HSTS_ENABLED`. | Validação fail-fast de configuração com padrões conservadores e suporte a IPs e CIDRs (ex.: bridge Docker `172.16.0.0/12`). |
| [`main.py`](../../main.py) | Adicionados `is_effective_https_request`, `should_use_secure_cookies`, `get_effective_client_ip`, `DEFAULT_HTML_CSP`, `DEFAULT_PERMISSIONS_POLICY`; removido import morto `CORSMiddleware`; `Set-Cookie` de `lectoria_csrf` restrito a páginas HTML (nunca `/static/*`); `secure=` consistente em login/setup/logout/csrf; HSTS condicional somente em HTTPS efetivo; validação de proxy confiável para `X-Forwarded-Proto` e `X-Forwarded-For`. | Elimina spoofing de esquema HTTPS e IP, ativa defesa em profundidade contra XSS sem quebrar PDF.js/EPUB.js e preserva 100% HTTP Range (206), downloads e capas SVG. |
| [`static/index.html`](../../static/index.html) + [`static/js/app.js`](../../static/js/app.js) | Removidos os 2 `onsubmit="return false;"` inline (`#auth-form`, `#details-edit-form`) e migrados para `addEventListener("submit", e => e.preventDefault())`. | Viabiliza `script-src 'self'` (sem `'unsafe-inline'` nem `'unsafe-eval'` para scripts). |
| [`constraints.txt`](../../constraints.txt) | Criado arquivo de constraints com versões exatas das dependências Python de produção (`fastapi==0.162.0`, `starlette==1.10.0`, `pydantic==2.14.2`, `uvicorn==0.54.0`, `pypdf==6.21.4`, `anyio==4.15.1`, `click==8.5.0`, `h11==0.16.0`, `idna==3.20`). | Reprodutibilidade determinística no `Dockerfile`, CI e instalações standalone sem introduzir gerenciador complexo. |
| [`Dockerfile`](../../Dockerfile) + [`.github/workflows/_test-suite.yml`](../../.github/workflows/_test-suite.yml) | Adicionado `-c constraints.txt`; substituído `--forwarded-allow-ips "*"` por `${LECTORIA_FORWARDED_ALLOW_IPS:-127.0.0.1,::1}`; definidos `ENV` padrão conservadores. | Elimina confiança aberta `*` por padrão em imagens Docker e alinha CI + build. |
| [`.env.example`](../../.env.example) + [`docker-compose.yml`](../../docker-compose.yml) | Documentadas as 4 novas variáveis de segurança HTTP com padrões conservadores e aviso sobre confidencialidade em HTTP. | Facilita adoção segura em qualquer ambiente self-hosted. |

---

## 3. Configurações Disponíveis e Valores Padrão

| Variável | Valores Permitidos | Padrão | Comportamento |
| :--- | :--- | :---: | :--- |
| `LECTORIA_SECURE_COOKIES` | `auto` \| `true` \| `false` | `auto` | `auto`: `Secure=True` apenas em HTTPS efetivo (direto ou via proxy confiável). `true`: sempre `Secure`. `false`: sempre sem `Secure`. |
| `LECTORIA_FORWARDED_ALLOW_IPS` | IPs, CIDRs separados por vírgula ou `*` | `127.0.0.1,::1` | Proxies autorizados a fornecer `X-Forwarded-Proto` e `X-Forwarded-For`. |
| `LECTORIA_CSP_MODE` | `off` \| `report-only` \| `enforce` | `report-only` | Aplica CSP nas páginas HTML (`/` e `/reader`) sem alterar o CSP `default-src 'none'` das capas SVG. |
| `LECTORIA_HSTS_ENABLED` | `true` \| `false` | `false` | Quando `true`, emite `Strict-Transport-Security: max-age=31536000; includeSubDomains` **somente** em requisições HTTPS efetivas (nunca em HTTP). |

### Política CSP Aplicada (`DEFAULT_HTML_CSP`)
```http
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data: blob:; connect-src 'self' blob:; worker-src 'self' blob:; frame-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'self'; form-action 'self'
```
- **Exceções justificadas**: `style-src 'unsafe-inline'` (necessário para atributos `style="..."` na UI e regras injetadas pelo EPUB.js); `blob:`/`data:` em `img-src`, `font-src`, `connect-src`, `worker-src` e `frame-src` (necessários para PDF.js worker, EPUB.js iframes/recursos embutidos e `canvas.toBlob`/`sendBeacon`). Zero `'unsafe-inline'` ou `'unsafe-eval'` em `script-src`.

---

## 4. Matriz de Compatibilidade de Implantação

| Cenário | Configuração Recomendada | Status de Validação |
| :--- | :--- | :--- |
| **1. Localhost HTTP (`http://127.0.0.1:8765`)** | Padrão (`auto`, `127.0.0.1,::1`, `report-only`, HSTS `false`) | **Efetivamente testado** (TestClient + Playwright Chromium) |
| **2. Rede Local HTTP (`http://192.168.x.x:8765`)** | Padrão (`auto`) ou `LECTORIA_SECURE_COOKIES=false` *(aviso: HTTP não cifra transporte)* | **Analisado + lógica de cookie/host testada** (`Secure=False` em HTTP) |
| **3. Tailscale Privado HTTP (`http://100.x.y.z:8765`)** | Padrão (`auto`) *(WireGuard cifra rede, mas navegador vê HTTP)* | **Analisado** (mesmo comportamento de cookies HTTP) |
| **4. Tailscale Serve HTTPS (`https://*.ts.net`)** | `LECTORIA_SECURE_COOKIES=true` (ou `auto` + `LECTORIA_FORWARDED_ALLOW_IPS=127.0.0.1,::1,172.16.0.0/12` em Docker) | **Lógica testada** (CIDR `172.16.0.0/12` + `X-Forwarded-Proto: https` → `Secure=True`) |
| **5. Reverse Proxy HTTPS (Caddy / Nginx / Traefik)** | `LECTORIA_SECURE_COOKIES=auto` ou `true` + `LECTORIA_FORWARDED_ALLOW_IPS=<IP/CIDR do proxy>` + HSTS opcional | **Lógica testada** (proxy confiável vs. spoofing de cliente não confiável) |
| **6. Docker Compose Genérico** | `docker-compose.yml` atualizado com padrões seguros | **Analisado + validado estruturalmente** |
| **7. Instalação Independente (venv/systemd)** | `.env.example` + `pip install -r requirements.txt -c constraints.txt` | **Efetivamente testado** (local Python/Uvicorn) |

---

## 5. Resultados dos Testes, Revisão EPUB e Auditoria de Dependências

- **Novos testes criados**:
  - [`tests/test_v192_http_security.py`](../../tests/test_v192_http_security.py): 5 grupos de testes cobrindo validadores, anti-spoofing (`X-Forwarded-Proto` e `X-Forwarded-For`), cookies (`auto`/`true`/`false`), login/logout/CSRF, cabeçalhos, CSP (`off`/`report-only`/`enforce`), HSTS condicional, ausência de `Set-Cookie` em `/static/*` e invariantes de `Dockerfile`/`constraints.txt`.
  - [`tests/e2e/test_v192_csp_and_http_security.py`](../../tests/e2e/test_v192_csp_and_http_security.py): teste E2E real no navegador Chromium sob **`LECTORIA_CSP_MODE=enforce`** confirmando **zero violações `securitypolicyviolation`** na Biblioteca (`/`), Atlas, leitor PDF (PDF.js + worker) e leitor EPUB (EPUB.js + JSZip + iframe), além de HTTP Range `206`, download (`Content-Disposition`) e capa SVG (`default-src 'none'`).
- **Revisão de isolamento do leitor EPUB (`static/js/reader.js:4789-4794`)**:
  - Confirmado `allowScriptedContent: false` no `epubBook.renderTo(...)` (aplica `sandbox` sem `allow-scripts` no iframe do capítulo) + interceptação bloqueando `javascript:`, `data:` e `vbscript:` em links internos. *Nota de risco residual*: ausência de violações CSP não substitui o `sandbox` do iframe; manter `allowScriptedContent: false` obrigatório e nunca injetar HTML de EPUB diretamente no documento principal.
- **Resultado da suíte local**: **389 passed, 1 skipped (Docker Engine ausente no host local), 0 failed** — `333` unitários/API + `56` E2E Playwright (`253,73 s`), além de `py_compile` e sintaxe JS (V8) aprovados.
- **Resultado do CI remoto (GitHub Actions `ubuntu-latest`, Python 3.12 + `-c constraints.txt`)**:
  - **[Run #45488808181](https://github.com/Nosremekk/Lectoria/actions/runs/45488808181)** (`CI` · `push` @ `d207614`): **`completed / success`** (`702 s`) — **`390 passed, 3 warnings in 664.75s (0:11:04)`**, incluindo `test_v192_docker_real_integration_end_to_end PASSED`, `test_v192_csp_enforce_zero_violations_across_library_atlas_pdf_epub PASSED` e 5/5 de `test_v192_http_security.py`.
  - **[Run #45488816924](https://github.com/Nosremekk/Lectoria/actions/runs/45488816924)** (`CI` · `pull_request` @ `d207614`): **`completed / success`** (`846 s`, `390 passed, 0 failed`).
  - **[Run #45679963000](https://github.com/Nosremekk/Lectoria/actions/runs/45679963000)** (`Publish Docker Image (GHCR)` · `push` em `main` @ `4d682e3`): **`completed / success`** (`18:55:01Z` → `19:05:52Z`) — `Preflight` (`success`), `Mandatory Test Suite` (**`390 passed, 3 warnings in 557.01s`**), build `:sha-4d682e3`, smoke test no digest imutável (`/api/health` = `healthy`, `1.9.1`) e promoção para `:stable` (`sha256:1eac54772ffeb00e60aed00adb41b2597d7fd0950495fb299fe862e2f9fd51cd`). Tags versionadas `:v1.9.1` e `:1.9.1` permaneceram intactas (`sha256:da4dca9cac06d28817d62b0d48224c5063a3e993e115b39682d2e766c1b6a253`).
- **Auditoria de dependências (`2026-11-24`, venv temporário descartável em `/tmp`)**:
  - **`pip check`**: `No broken requirements found.`
  - **`pip-audit 2.10.1`** (`-r requirements.txt` com `-c constraints.txt`, auditando 14 pacotes fixados + `pydantic-core==2.50.2`): **`No known vulnerabilities found`** (`exit 0`).
  - **Distinção e limitação**: `constraints.txt` é um arquivo de **constraints parciais** (fixa as 5 dependências diretas/principais + 9 transitivas puras Python, deixando `pydantic-core` vinculado a `pydantic==2.14.2` para compatibilidade multiplataforma Python 3.12/3.14), não sendo um lockfile com hashes SHA-256 por plataforma.

---

## 6. Integração Controlada em Produção e Encerramento (`2026-11-25`)

### 6.1 Verificações Automatizadas (CI, GHCR, Deploy Controlado e HTTPS Real)
- **Momento A (Preparação na VPS, `2026-11-24`)**: `lectoria-auto-update.timer` pausado (`inactive`), backup de `compose.yaml` em `/root/lectoria-compose-backup-20261124T213055Z/compose.yaml`, backup preventivo em `/srv/nosreme/lectoria/backups/2026-11-24T21-30-56Z` (`integrity=ok`, `schema=13`, `covers.tar.gz` com `213` entradas), gateway real identificado (`lectoria_default`, `172.19.0.1`) e 4 variáveis de segurança configuradas no `compose.yaml` (`LECTORIA_SECURE_COOKIES="true"`, `LECTORIA_FORWARDED_ALLOW_IPS="127.0.0.1,::1,172.19.0.1"`, `LECTORIA_CSP_MODE="report-only"`, `LECTORIA_HSTS_ENABLED="false"`).
- **Momento B (Merge, Publicação e Deploy Controlado, `2026-11-25`)**:
  - PR #2 integrado em `main` no commit `4d682e3d2c611b3e67062a51f07c922a20aadecf` (sem tag Git nem release).
  - `publish.yml` (`Run #45679963000`): `390 passed`, smoke test `@digest` aprovado, `:stable` e `:sha-4d682e3` em `sha256:1eac54772ffeb00e60aed00adb41b2597d7fd0950495fb299fe862e2f9fd51cd`, `:v1.9.1` e `:1.9.1` intactas (`sha256:da4dca9c...`).
  - `lectoria-auto-update.service` (`19:14:01–19:14:21 UTC`): `SVC_EXIT_CODE=0`, `EXECUTABLE_CHANGE_DETECTED` (`51fc906f...` → `9ee4b453...`), backup obrigatório `/srv/nosreme/lectoria/backups/2026-11-25T19-14-08Z` (`41 MB`, `integrity=ok`, `schema=13`, `238` livros, `5` usuários, `213` capas), `UPDATE_SUCCESS`, novo container `c49acdfd29f4ae75857f8438bacb6e5c66e27e825a2fc9d6587ebd2123d67071`, `OCI_REVISION_MATCH=YES`, `Health=healthy`, `Restarts=0`, `BLOCKED_AFTER=NO`.
  - Integridade SQLite (WAL / `mode=ro`) preservada com contagens idênticas antes/depois (`books=238`, `users=5`, `reading_progress=54`, `highlights=0`, `notes=19`, `reading_clubs=1`, `concepts=4`, `reflections=1`, `knowledge_links=4`).
  - Cabeçalhos HTTPS reais via Tailscale Serve (`https://srv2137600.tailf5c3ce.ts.net/`): `Content-Security-Policy-Report-Only`, `Permissions-Policy`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: strict-origin-when-cross-origin`, `Set-Cookie: lectoria_csrf=...; Path=/; SameSite=lax; Secure` em `/`, **sem `Set-Cookie`** em `/static/js/app.js?v=1.9.1`, **sem HSTS** (`LECTORIA_HSTS_ENABLED=false`).
- **Momento C (Execução Automática Pós-Reativação, `2026-11-25`)**:
  - `lectoria-auto-update.timer` reativado (`enabled/active`), disparou automaticamente em `19:29:43–19:29:44 UTC` (`Result=success`, `ExecMainStatus=0`), confirmando `NO_EXECUTABLE_CHANGE`, preservação do container `c49acdfd...`, ausência de backup extra desnecessário, `lectoria-backup.timer` `enabled/active` e `nosreme-restic-backup.timer` `disabled/inactive`.

### 6.2 Verificações Manuais Confirmadas pelo Proprietário (Navegador Real)
Confirmadas pelo proprietário em `https://srv2137600.tailf5c3ce.ts.net` (escopo restrito aos fluxos efetivamente exercitados):
- Login e logout funcionais.
- Cookies `lectoria_session` (`Secure`, `HttpOnly`, `SameSite=Lax`) e `lectoria_csrf` (`Secure`, `SameSite=Lax`) presentes com os atributos esperados.
- Biblioteca, capas, Descoberta e Atlas funcionais.
- Leitores PDF e EPUB operacionais, com preservação de progresso e anotações.
- Zero violações CSP observadas no Console durante os cenários testados.

---

## 7. Procedimento de Reversão e Riscos Residuais

- **Reversão**: `git revert` dos commits da Fase 11.3 ou override imediato via variáveis de ambiente (`LECTORIA_SECURE_COOKIES=false`, `LECTORIA_CSP_MODE=off`, `LECTORIA_HSTS_ENABLED=false`), ou retorno à imagem imutável anterior `ghcr.io/nosremekk/lectoria@sha256:6659b146d3205d9b5543eeb58b973a1c8c7cae1b3a0691533a7c0ad4cd200cce` com `/root/lectoria-compose-backup-20261124T213055Z/compose.yaml`, sem qualquer impacto em Schema v13.
- **Riscos residuais**: (1) EPUBs de terceiros com recursos embutidos não cobertos pelos cenários testados ainda devem ser observados em `report-only` no uso cotidiano antes de promover o padrão para `enforce` (não se declara ausência universal de violações CSP fora dos cenários efetivamente testados); (2) `style-src 'unsafe-inline'` permanece necessário até eventual refatoração futura dos atributos `style="..."`; (3) `pip-audit` confirmou ausência de vulnerabilidades conhecidas na data da auditoria (`2026-11-24`), o que não garante ausência universal de vulnerabilidades futuras ou não catalogadas.
