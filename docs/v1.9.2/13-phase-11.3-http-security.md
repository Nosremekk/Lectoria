# Lectoria — Fase 11.3: Segurança HTTP, Proxies Confiáveis, CSP e Dependências

- **Status**: **`PHASE_11_3_READY_FOR_REVIEW`** *(implementado e validado localmente na branch `refactor/v1.9.2-http-security`; sem push, sem merge em `main`, sem alteração na VPS, `APP_VERSION = "1.9.1"`, `SCHEMA_VERSION = 13`)*
- **Data**: `2026-11-24`
- **Branch**: `refactor/v1.9.2-http-security` (derivada de `origin/main` @ `9527470`)

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
- **Auditoria de dependências (`2026-11-24`, venv temporário descartável em `/tmp`)**:
  - **`pip check`**: `No broken requirements found.`
  - **`pip-audit 2.10.1`** (`-r requirements.txt` com `-c constraints.txt`, auditando 14 pacotes fixados + `pydantic-core==2.50.2`): **`No known vulnerabilities found`** (`exit 0`).
  - **Distinção e limitação**: `constraints.txt` é um arquivo de **constraints parciais** (fixa as 5 dependências diretas/principais + 9 transitivas puras Python, deixando `pydantic-core` vinculado a `pydantic==2.14.2` para compatibilidade multiplataforma Python 3.12/3.14), não sendo um lockfile com hashes SHA-256 por plataforma.

---

## 6. Topologia Real da VPS e Plano Operacional Conciso de Integração (NÃO EXECUTAR AGORA)

### Topologia Observada na VPS (`lectoria-vps`, leitura sem `sudo` em `2026-11-24`)
- **Porta publicada**: `127.0.0.1:8765` (escuta **exclusiva em loopback**, sem exposição direta em `eth0` `187.41.126.101` ou `tailscale0` `100.82.105.34`).
- **Único caminho de acesso externo**: `tailscale serve` (`100.82.105.34:443` / `https://srv2137600.tailf5c3ce.ts.net`, tailnet-only) → proxy `http://127.0.0.1:8765`.
- **Bridges Docker `UP` no host**: `br-f037bd487a0f` (`172.18.0.1/16`), `br-a3631ebb936e` (`172.19.0.1/16`), `br-beac29fe2d58` (`172.20.0.1/16`).
- **Limitação (sem `sudo`)**: para identificar exatamente qual gateway pertence ao container `lectoria` (evitando adotar `172.16.0.0/12` amplo), executar antes da integração:
  ```bash
  sudo docker inspect lectoria --format '{{range .NetworkSettings.Networks}}Gateway={{.Gateway}} IP={{.IPAddress}}{{end}}'
  ```
- **Configuração recomendada para a VPS**: `LECTORIA_SECURE_COOKIES: "true"` (adequado porque o único acesso é HTTPS via Tailscale Serve, preservando `auto` como padrão genérico da aplicação), `LECTORIA_FORWARDED_ALLOW_IPS: "127.0.0.1,::1,<GATEWAY_EXATO>"` (ex.: `127.0.0.1,::1,172.18.0.1`), `LECTORIA_CSP_MODE: "report-only"`, `LECTORIA_HSTS_ENABLED: "false"`.

### Plano Conciso de Integração Controlada (11 Passos — Aguardar Autorização)
1. Pausar temporariamente `lectoria-auto-update.timer` na VPS (`sudo systemctl stop lectoria-auto-update.timer`).
2. Fazer backup de `/srv/nosreme/lectoria/compose.yaml` (`compose.yaml.bak.<TS>`).
3. Executar backup preventivo dos dados (`sudo systemctl start lectoria-backup.service`) e validar `PRAGMA integrity_check = ok`.
4. Identificar `<GATEWAY_EXATO>` via `docker inspect` e configurar `LECTORIA_SECURE_COOKIES=true`, `LECTORIA_FORWARDED_ALLOW_IPS=127.0.0.1,::1,<GATEWAY_EXATO>` e `LECTORIA_CSP_MODE=report-only` no `compose.yaml` **antes** do merge.
5. Confirmar compatibilidade das novas variáveis com a imagem atual (`docker compose config` sem erros; a `v1.9.1` atual apenas ignora as novas variáveis).
6. Executar merge autorizado do PR #2 em `main` (sem criar tag).
7. Acompanhar `publish.yml` no GHCR (`preflight` → 390 testes → `:sha-*` → smoke test → `:stable`), confirmando `:v1.9.1` intacta.
8. Executar deploy controlado (`sudo systemctl start lectoria-auto-update.service`) e confirmar `UPDATE_SUCCESS`.
9. Validar no navegador real via `https://srv2137600.tailf5c3ce.ts.net`: login, logout, CSRF, cookies com `Secure`, PDF, EPUB, Atlas e zero violações CSP no console.
10. Reativar `lectoria-auto-update.timer` e confirmar execução automática com `NO_EXECUTABLE_CHANGE`.
11. **Recuperação em caso de falha**: restaurar `compose.yaml.bak.<TS>` e fixar o digest anterior `ghcr.io/nosremekk/lectoria@sha256:6659b146d3205d9b5543eeb58b973a1c8c7cae1b3a0691533a7c0ad4cd200cce` (`docker compose up -d`), mantendo o timer pausado (sem impacto em Schema v13).

---

## 7. Procedimento de Reversão e Riscos Residuais

- **Reversão**: `git revert` dos commits da Fase 11.3 ou override imediato via variáveis de ambiente (`LECTORIA_SECURE_COOKIES=false`, `LECTORIA_CSP_MODE=off`, `LECTORIA_HSTS_ENABLED=false`) sem qualquer impacto em schema SQLite (`13`).
- **Riscos residuais**: (1) EPUBs de terceiros com scripts/recursos exóticos embutidos ainda devem ser observados em `report-only` no uso real antes de mudar o padrão para `enforce`; (2) `style-src 'unsafe-inline'` permanece necessário até eventual refatoração futura dos atributos `style="..."`; (3) auditoria formal de CVEs com `pip-audit` pendente.
