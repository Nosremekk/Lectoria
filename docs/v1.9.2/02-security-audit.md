# Lectoria — Fase 11.1: Auditoria de Segurança HTTP, Cookies e CSP

- **Escopo**: Revisão estática do código (`main.py`, `auth_service.py`, `config.py`, `static/index.html`, `static/reader.html`, `static/js/*.js`) e inspeção real dos cabeçalhos HTTP retornados em produção (`https://srv2137600.tailf5c3ce.ts.net`).
- **Restrições respeitadas**: Exclusivamente documental; nenhuma alteração de código, cabeçalhos, cookies ou proxy aplicada nesta fase.

---

## 1. Matriz de Controles de Segurança Atuais (Evidências Verificadas)

| Controle | Estado Atual Verificado | Evidência | Risco |
| :--- | :--- | :--- | :--- |
| **Cookie de sessão (`lectoria_session`)** | `HttpOnly=True`, `SameSite=lax`, `Path=/`, `max_age=7d`, **`Secure` AUSENTE** | `main.py:1272-1279` e `1292-1299` (`set_cookie` não passa `secure=...`). Variável `LECTORIA_SECURE_COOKIES` não existe em `config.py` nem `main.py`. | **Alto** (se trafegar fora de HTTPS) / **Médio** (sob Tailscale HTTPS) |
| **Cookie CSRF (`lectoria_csrf`)** | `HttpOnly=False` (necessário para Double Submit), `SameSite=lax`, `Path=/`, **`Secure` AUSENTE**; **emitido também em `/static/*`** | `curl -I https://srv2137600.tailf5c3ce.ts.net/static/js/app.js?v=1.9.1` retornou `set-cookie: lectoria_csrf=...; Path=/; SameSite=lax` (`main.py:316-328`). | **Médio** (segurança + prejuízo ao cache estático) |
| **Token de sessão no corpo JSON** | `/api/auth/login` e `/api/auth/setup` retornam `{"token": token}` além do cookie `HttpOnly` | `main.py:1280` e `1300`. | **Baixo/Médio** (desnecessário para clientes browser se não houver consumidor Bearer ativo; verificar testes antes de mudar) |
| **Proteção CSRF** | Valida `Origin`/`Referer` vs `Host` + Double Submit (`secrets.compare_digest`). Bloqueia `csrf_token` na URL em `/api/knowledge`, mas **ainda aceita via query string em rotas legadas** | `main.py:259-304`. | **Médio** (vazamento potencial em logs/histórico nas rotas legadas) |
| **Content-Security-Policy (CSP)** | **Ausente** em `/` e `/reader`; presente **apenas** em capas SVG (`default-src 'none'; style-src 'unsafe-inline'`) | `main.py:1897` e `curl -I` em produção. | **Médio/Alto** (falta defesa em profundidade contra XSS) |
| **Headers defensivos básicos** | `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: strict-origin-when-cross-origin` ativos; `Cache-Control: private, no-store` em `/api/knowledge` | `main.py:309-313` e `curl -I`. | **Adequado** (falta `Permissions-Policy` opcional e `HSTS` no proxy/HTTPS) |
| **CORS** | `CORSMiddleware` importado (`main.py:19`), mas **não registrado** → política Same-Origin estrita do navegador | `grep add_middleware main.py` (vazio). | **Adequado** (remover import morto em limpeza futura) |
| **Proteção XSS no Frontend** | **Atlas (`knowledge*.js`, `book-knowledge.js`, `reader-knowledge.js`)**: 100% DOM explícito (`createElement`/`createTextNode`), **zero `innerHTML`**. **Legado (`app.js`, `reader.js`)**: usa `innerHTML` extensivamente com `escapeHtml(...)` | Auditoria `grep innerHTML static/js/`. | **Baixo** no Atlas / **Médio** no legado (frágil a omissões futuras de `escapeHtml`) |
| **Autorização & Isolamento** | `require_user`/`require_admin`, Read-Time Authorization no Atlas, clubes privados retornando `404`, `ConfigDict(extra="forbid")` contra Mass Assignment, `scrypt` + sessões SHA-256 + rate limit com `threading.Lock` | Validado por 360 testes automatizados. | **Forte** |
| **Uploads e Arquivos** | Capas passam por validação de tamanho/formato (`covers_service.py`); resolução de livros usa validação de caminho contra path traversal (`library.py`, `SecurityError`); exportações usam `BoundedSemaphore(4)` + streaming em disco temporário + proteção contra CSV formula injection | `covers_service.py`, `library.py`, `knowledge_export_service.py`. | **Forte** |
| **Container Root** | Container Docker executa `uvicorn` como `root` (`UID 0`) porque `./data` na VPS tem ownership `root:root` | `Dockerfile` e `ps -ef` na VPS. | **Médio** (mitigável futuramente com `no-new-privileges:true` já no Compose candidato e eventual migração de UID) |

---

## 2. Proposta de Content-Security-Policy (CSP) Compatível (Seção 3.1)

### 2.1 Requisitos Reais Levantados no Código
Inspeção completa de `static/index.html`, `static/reader.html` e `static/js/*.js` confirmou:
- **Zero CDNs externos**: 100% dos scripts, folhas de estilo e bibliotecas (`pdf.min.js`, `pdf.worker.min.js`, `epub.min.js`, `jszip.min.js`) são servidos de `/static/`.
- **Zero blocos `<script>` inline** em `index.html` e `reader.html`.
- **Apenas 2 event handlers inline** em toda a aplicação:
  - `static/index.html:814` (`<form id="auth-form" onsubmit="return false;">`)
  - `static/index.html:1150` (`<form id="details-edit-form" onsubmit="return false;">`)
- **Uso extensivo de estilos inline** (`style="..."` no HTML e `element.style.*` em JS, além do EPUB.js injetando regras no iframe) → **`style-src 'self' 'unsafe-inline'` é obrigatório** para não quebrar a interface.
- **Requisitos específicos de PDF.js e EPUB.js**:
  - `pdf.worker.min.js` é carregado de `/static/vendor/pdfjs/pdf.worker.min.js` (`worker-src 'self' blob:`).
  - `epub.min.js` renderiza capítulos em `<iframe>` usando `blob:` / `srcdoc` e carrega imagens/fontes embutidas do EPUB via `blob:` e `data:`.
  - `reader.js` usa `offCanvas.toBlob(...)` para cache de capa e `navigator.sendBeacon(..., new Blob(...))` para salvar progresso.

### 2.2 Política CSP Recomendada (Adoção Progressiva em 2 Etapas)

**Pré-requisito mínimo (Fase 11.3)**: remover os 2 `onsubmit="return false;"` em `static/index.html` (substituindo por `e.preventDefault()` nos listeners já existentes em `app.js`).

**Política alvo para `/` e `/reader`**:
```http
default-src 'self';
script-src 'self';
style-src 'self' 'unsafe-inline';
img-src 'self' data: blob:;
font-src 'self' data: blob:;
connect-src 'self' blob:;
worker-src 'self' blob:;
frame-src 'self' blob:;
object-src 'none';
base-uri 'self';
frame-ancestors 'self';
form-action 'self';
```

> **Atenção especial ao leitor EPUB e PDF.js**: alguns arquivos EPUB com scripts/styles internos ou versões de PDF.js/EPUB.js podem ocasionalmente exigir `script-src 'self' blob:` (ou `child-src 'self' blob:` para Safari legado). Por isso, **NUNCA ativar em modo bloqueante diretamente**.

**Rollout seguro em 2 passos**:
1. **Passo 1 — `Content-Security-Policy-Report-Only`** (Fase 11.3): emitir em modo somente-observação com coleta de violações em todos os 55 testes E2E Playwright (incluindo PDF, EPUB, Atlas, desenhos SVG e exportações) + validação em uso real no navegador do proprietário.
2. **Passo 2 — Enforcement (`Content-Security-Policy`)**: somente após confirmar **zero** violações reais em `/`, `/reader` (PDF e EPUB), Atlas e uploads de capa.

---

## 3. Política de Cookies em Produção (Seção 3.2)

### 3.1 Topologia Real Verificada
- Produção opera atrás de **`tailscale serve`** (`https://srv2137600.tailf5c3ce.ts.net` → proxy interno `http://127.0.0.1:8765`), com Uvicorn iniciado com `--proxy-headers --forwarded-allow-ips *`.
- Desenvolvimento local e testes E2E/TestClient acessam via `http://127.0.0.1:<porta>` (HTTP puro).
- **Consequência**: forçar `Secure=True` incondicionalmente quebraria o acesso em `http://127.0.0.1` em ambientes locais e testes sem HTTPS (embora navegadores modernos tratem `localhost` como *secure context*, TestClient/httpx e IPs LAN não-HTTPS descartam cookies `Secure`).

### 3.2 Estratégia Recomendada (Fase 11.3)
1. Implementar suporte explícito em `config.py` para `LECTORIA_SECURE_COOKIES` com 3 modos claros:
   - `"true"` / `"1"`: sempre `Secure=True` (recomendado no Compose de produção com Tailscale HTTPS).
   - `"false"` / `"0"`: sempre `Secure=False` (dev/LAN HTTP explícito).
   - `"auto"` (padrão): aplica `Secure=True` automaticamente quando `request.url.scheme == "https"` ou `x-forwarded-proto == "https"` (já confiado via `--proxy-headers`), mantendo compatibilidade transparente em `http://127.0.0.1`.
2. Aplicar essa mesma regra tanto a **`lectoria_session`** quanto a **`lectoria_csrf`**.
3. **Corrigir escopo de emissão do cookie CSRF** (`main.py:316-320`): emitir `lectoria_csrf` **apenas em páginas HTML** (`/` e `/reader`) e `/api/auth/csrf`, **nunca** em `/static/*` — eliminando `Set-Cookie` espúrio em CSS/JS/imagens e restaurando cacheabilidade total dos estáticos.

---

## 4. Auditoria de Dependências (Seção 3.3)

| Camada | Arquivo | Pacotes Diretos | Versões Resolvidas (.venv) | Transitivas Críticas | Risco |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Produção** | `requirements.txt` | `fastapi>=0.110.0`, `uvicorn>=0.28.0`, `pypdf>=4.0.0` | `fastapi 0.162.0`, `uvicorn 0.54.0`, `pypdf 6.21.4` | `starlette 1.10.0`, `pydantic 2.14.2`, `pydantic-core`, `anyio`, `h11`, `click` | **Alto** (limites abertos sem teto superior; builds não determinísticos) |
| **Dev/Testes** | `requirements-dev.txt` | `pytest>=8.0.0`, `httpx>=0.27.0`, `playwright>=1.40.0`, `pytest-playwright>=0.5.0` | `pytest 9.2.0`, `playwright 0.10.0` | `httpx`, `pluggy` | **Médio** (warnings de deprecação `httpx`→`httpx2` no Starlette 1.10) |
| **Frontend Vendor** | `static/vendor/` | PDF.js (`pdf.min.js` + `pdf.worker.min.js`), EPUB.js (`epub.min.js`), JSZip (`jszip.min.js`) | Estáticos commitados no repositório | Nenhuma rede externa | **Baixo** (totalmente determinístico; revisar CVEs periodicamente) |

**Recomendação (Fase 11.3)**: gerar lockfile exato para Python 3.12 (`requirements.lock` ou pinagem `==` em `requirements.txt` com `requirements.in` como fonte), validado no `Dockerfile` e no CI.
