# Lectoria v1.9.1 — Auditoria Geral de Qualidade (Fase 10.1 — Prioridade P1)

- **Escopo**: Auditoria técnica, funcional, de segurança, desempenho e engenharia sobre a base `v1.9.0` (`main` @ `9ff89cd`) + correção P0 do Markdown.
- **Natureza**: **Exclusivamente documental** — nenhuma melhoria secundária foi implementada nesta fase, preservando o escopo mínimo da correção crítica.
- **Base de evidências**: Inspeção direta do código-fonte, workflows CI/CD, Dockerfile, Compose candidato, banco SQLite, métricas reais observadas na VPS (`srv2137600`, `1 vCPU`, `3,8 GiB RAM`) em `2026-11-23` e suíte de testes automatizados.

---

## A. Experiência do Usuário e Acessibilidade do Atlas

### Pontos fortes verificados
- Interface editorial limpa e consistente com os 3 temas (Claro, Escuro, Sépia) e responsividade validada em Desktop, Tablet e Mobile.
- Renderização segura de reflexões via DOM puro (zero `innerHTML`), proteção contra saída com alterações não salvas (`#atlas-unsaved-modal`) e detecção de concorrência entre abas.
- Estados vazios descritivos presentes nas abas do Atlas (Conceitos, Reflexões, Conexões, Pesquisa e Mapa).

### Lacunas identificadas
1. **Acessibilidade (A11y) limitada no Atlas**: apenas 18 ocorrências de `aria-*` / `role=` / `tabindex` em 4.159 linhas de [`static/js/knowledge.js`](../../static/js/knowledge.js) (8 em `book-knowledge.js`, 7 em `reader-knowledge.js`). Modais customizados não implementam *focus trap* completo nem retorno automático de foco ao botão acionador ao fechar via `Escape`.
2. **Atalhos de teclado e histórico Desfazer no editor Markdown**: a barra de formatação (`insertMarkdownSyntax` em `static/js/knowledge.js:1917`) atribui diretamente `ta.value = ...`, o que limpa a pilha nativa de *Undo/Redo* (`Ctrl+Z`) do `<textarea>` em navegadores Chromium; faltam atalhos comuns (`Ctrl+S` para salvar, `Ctrl+B` / `Ctrl+I` para negrito/itálico) e `aria-label` descritivo nos botões curtos da toolbar.
3. **Preview síncrono em textos muito longos**: embora o loop infinito tenha sido corrigido e textos de ~250 títulos renderizem rapidamente, reflexões próximas ao limite de 50.000 caracteres reprocessam todo o documento sincronamente a cada tecla. Um *debounce* leve (120–150 ms) apenas para documentos acima de ~5.000 caracteres evitaria *jank* em CPUs modestas.
4. **Cobertura Markdown limitada no preview**: o parser atual não renderiza listas de tarefas (`- [ ]`), negrito+itálico aninhado (`***texto***`) ou tabelas simples.

---

## B. Integração Entre Módulos

### Pontos fortes verificados
- Integração ponta a ponta validada por testes E2E entre Biblioteca (`app.js`), Leitores PDF/EPUB (`reader.js` + `reader-knowledge.js`), Caderno de Estudos (`book-knowledge.js`), Atlas (`knowledge.js`) e Exportação (`.zip` Markdown, JSON v2.0, CSV).
- Navegação profunda segura (`buildSafeNavigationSpec`) para páginas PDF (`#page=N`) e posições EPUB (`#cfi=...`) com tratamento gracioso de referências indisponíveis (*Read-Time Authorization*).
- Isolamento estrito entre anotações privadas, leitura compartilhada (Clubes) e Atlas preservado.

### Lacunas identificadas
1. **Acoplamento implícito via estado global de janela (`window.Lectoria*`)**: a comunicação entre `app.js`, `knowledge.js`, `book-knowledge.js` e `reader.js` depende de objetos globais e eventos customizados sem um contrato tipado/documentado único de eventos, aumentando o risco de regressão silenciosa ao evoluir abas.
2. **Cache-busting manual acoplado**: qualquer atualização de versão exige sincronia manual entre `config.APP_VERSION`, `data-app-version` em `static/index.html` e `static/reader.html`, e query strings `?v=X.Y.Z` em todos os `<script>`/`<link>` (hoje protegido por `test_v151_frontend_backend_version_alignment`).

---

## C. Segurança

### Pontos fortes verificados
- **Zero `innerHTML` em todo o subsistema Atlas** (`knowledge-api.js`, `knowledge.js`, `book-knowledge.js`, `reader-knowledge.js`).
- **Proteção CSRF robusta** em [`main.py`](../../main.py) (validação de `Origin`/`Referer` + *Double Submit Cookie* `lectoria_csrf` com `secrets.compare_digest` + bloqueio explícito de `csrf_token` na query string em `/api/knowledge`).
- **Headers defensivos básicos presentes**: `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: strict-origin-when-cross-origin` e `Cache-Control: private, no-store` nas rotas `/api/knowledge`.
- **Anti-IDOR e isolamento multiusuário**: *Read-Time Authorization* no Atlas, clubes privados retornando `404` para não-membros, `ConfigDict(extra="forbid")` contra *Mass Assignment*, sessões SHA-256 com `httponly=True` e hash `scrypt` para senhas.
- **Exportação protegida**: streaming em disco temporário com `BoundedSemaphore(4)` em `knowledge_export_service.py` e bloqueio de imagens externas/URLs `javascript:` no preview Markdown.

### Lacunas identificadas
1. **Ausência de `Content-Security-Policy` (CSP) global nas páginas HTML** (`/` e `/reader`): atualmente `main.py` aplica CSP apenas em capas SVG (linha 1897). Uma política CSP restritiva (`default-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'`, ajustada aos workers/blobs do PDF.js/EPUB.js) adicionaria defesa em profundidade contra XSS.
2. **Cookie `lectoria_csrf` sem flag `secure=config.SECURE_COOKIES`**: em `main.py:322-328` (e `/api/auth/csrf`), o cookie CSRF não propaga `secure=config.SECURE_COOKIES`, ao contrário do cookie de sessão.
3. **Token CSRF ainda aceito via query string em rotas legadas fora de `/api/knowledge`** (`main.py:295-297`): deve ser descontinuado gradualmente para evitar exposição acidental em logs ou históricos.
4. **Uso extensivo de `innerHTML` nos módulos legados (`static/js/app.js` e `static/js/reader.js`)**: embora utilize `escapeHtml(...)` sistematicamente, a abordagem baseada em concatenação de strings HTML é mais suscetível a regressões futuras de XSS do que construção DOM explícita como no Atlas.

---

## D. Desempenho em Hardware Real da VPS (`1 vCPU`, `3,8 GiB RAM`)

### Estado real observado na VPS (`2026-11-23`)
- Processo único `uvicorn --workers 1`, consumo observado em repouso/pós-deploy: **`73,9 MiB` / `768 MiB`**, zero eventos de pressão ou OOM (`low 0 high 0 max 0 oom 0 oom_kill 0`).
- Acervo atual: **238 livros** (`6,9 GiB`), banco SQLite **`5,5 MB`** em modo `WAL`, `PRAGMA integrity_check = ok`, `fts_rows = 9`.
- Consultas de grafo do Atlas otimizadas com `UNION ALL` indexado e FTS5 (`unicode61 remove_diacritics 2`).

### Oportunidades identificadas
1. **PRAGMAs repetitivos por conexão SQLite** (`database.py:26-28`): `PRAGMA journal_mode=WAL;` é executado em toda abertura de conexão (`get_connection`), embora seja persistente no cabeçalho do arquivo do banco. Definir `WAL` apenas na inicialização (`init_db`) e avaliar `PRAGMA temp_store=MEMORY;` + `PRAGMA cache_size=-8000;` (8 MiB) reduz syscalls em 1 vCPU.
2. **Adoção pendente de `docker-compose.v190-candidate.yml` na VPS**: o ambiente remoto ainda opera com `mem_limit: 768m` e sem rotação de logs Docker; o arquivo candidato já preparado no repositório propõe `mem_limit: 1g`, `pids_limit: 128`, `no-new-privileges:true`, `LECTORIA_SECURE_COOKIES: "true"`, `start_period: 30s` e rotação `json-file` (`10m × 3`).
3. **Debounce no preview Markdown de reflexões muito grandes** (citado no eixo A) para manter baixa carga no navegador cliente em dispositivos móveis/modestos.

---

## E. Engenharia, Manutenção, CI/CD e Operação

### Pontos fortes verificados
- Suíte robusta com **360 testes automatizados** (305 unitários/API + 55 E2E Playwright) executando com sucesso no GitHub Actions (Python 3.12) e localmente (Python 3.14).
- Smoke test automatizado da imagem Docker publicada em `publish.yml` validando `/api/health` e versão antes de concluir o workflow.
- Backup SQLite local diário operacional (`lectoria-backup.timer`) + backup automático pré-migração (`lectoria_pre_migration_v12_to_v13.db.bak`). Backup remoto Restic/Rclone descontinuado voluntariamente conforme Fase 8.D.

### Lacunas e riscos operacionais identificados
1. **Divergência entre a tag Git `v1.9.0` e o commit publicado em `main`**:
   - A tag anotada `v1.9.0` (`f84d6dd` → commit `c919050`) aponta para o merge inicial que falhou no CI (caminhos absolutos nos testes E2E).
   - A imagem publicada em GHCR (`:v1.9.0`, `:1.9.0`, `:stable`) e em execução na VPS corresponde ao commit `9ff89cd` (2 hotfixes de CI à frente da tag `v1.9.0`).
   - **Impacto**: auditoria de rastreabilidade (`git checkout v1.9.0`) não reproduz exatamente o artefato em produção.
   - **Resolução recomendada**: ao publicar a correção do Markdown, promover formalmente a release **`v1.9.1`** (`APP_VERSION = "1.9.1"`) com tag Git `v1.9.1` apontando para o commit exato validado no CI — sem reescrever histórico nem mover a tag histórica `v1.9.0`.
2. **Sobrescrita silenciosa de tags versionadas no `publish.yml`**:
   - Em [`.github/workflows/publish.yml`](../../.github/workflows/publish.yml) (linhas 67–69 e 91–94), qualquer push em `main` gera `:v<APP_VERSION>`, `:<APP_VERSION>` e `:stable`. Se um commit cair em `main` sem incremento de `APP_VERSION`, a tag Docker `:v1.9.0` é sobrescrita silenciosamente, violando imutabilidade de artefatos.
   - **Recomendação**: separar claramente o trigger de tag `:stable` (push em `main`) da publicação de tags versionadas imutáveis (somente quando `refs/tags/v*` ou quando `APP_VERSION` muda).
3. **Execução duplicada de CI em pushes na `main`**:
   - Tanto `ci.yml` quanto `publish.yml` executam os 360 testes completos em paralelo a cada push na `main` (~8 min × 2 = ~16 min de runner). Encadear `publish.yml` via `workflow_call` / `workflow_run` ou limitar `ci.yml` a PRs e branches `feat/*`/`fix/*` corta pela metade o consumo de minutos de CI.
4. **Dependências não fixadas em [`requirements.txt`](../../requirements.txt)**:
   - Apenas 3 linhas com limites inferiores abertos (`fastapi>=0.110.0`, `uvicorn>=0.28.0`, `pypdf>=4.0.0`). Sem lockfile ou versões fixadas, builds futuros no Docker podem puxar atualizações *breaking* inesperadas.
5. **Monolitos de grande porte**:
   - `knowledge_service.py` (5.644 linhas), `static/js/app.js` (5.422), `main.py` (5.261), `static/js/reader.js` (5.154), `static/js/knowledge.js` (4.159), `static/css/style.css` (6.935). Dificultam revisões cirúrgicas e aumentam o custo cognitivo de manutenção.
6. **Confiabilidade do deploy automático (`lectoria-auto-update.timer`)**:
   - O timer verifica `:stable` a cada 15 minutos, mas não cria snapshot SQLite pré-pull automático nem realiza rollback automático para o digest anterior caso o novo container falhe no `/api/health`. Um script de auto-update com verificação de health pós-start e fallback ao digest anterior eleva significativamente a resiliência operacional.
