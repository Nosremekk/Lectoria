# Lectoria — Roadmap Técnico Priorizado Pós-v1.9.0 (Fase 10.1)

Plano estruturado a partir da correção crítica P0 e da Auditoria Geral de Qualidade ([`02-quality-audit.md`](./02-quality-audit.md)), organizado por prioridade (`P0` → `P3`), dependências, complexidade e critérios de aceitação objetivos.

> **Política de Backup**: o backup SQLite local diário (`lectoria-backup.timer`) permanece como mecanismo principal de recuperação; o backup remoto Restic/Rclone permanece descontinuado (Fase 8.D).

---

## P0 — Crítico (Release imediata `v1.9.1`)

| ID | Item | Complexidade | Dependências | Critérios de Aceitação |
| :--- | :--- | :--- | :--- | :--- |
| **P0-1** | **Correção do travamento/OOM do Markdown (`# ` a `###### `) + suporte H1–H6** | Baixa | Nenhuma (implementado na branch `fix/v1.9.1-markdown-heading-hang`) | Digitar `# `..`###### ` não trava nem cresce memória; H1–H6 funcionam; XSS permanece bloqueado; 360/360 testes aprovados. |
| **P0-2** | **Versionamento limpo da release `v1.9.1` (alinhamento tag Git ↔ GHCR ↔ produção)** | Baixa | Aprovação do proprietário + P0-1 | `APP_VERSION = "1.9.1"`, HTML/cache-busting sincronizados, `test_v151_frontend_backend_version_alignment` verde, tag `v1.9.1` apontando exatamente para o commit publicado em `main`. |

---

## P1 — Alta Prioridade (Próxima janela de estabilização `v1.9.2`)

| ID | Item | Complexidade | Dependências | Critérios de Aceitação |
| :--- | :--- | :--- | :--- | :--- |
| **P1-1** | **Imutabilidade de tags Docker e desduplicação do CI/CD (`ci.yml` + `publish.yml`)** | Baixa | P0-2 | Push em `main` não sobrescreve tags `:vX.Y.Z` antigas; suíte completa roda 1 vez por push em `main` (redução de ~50% no tempo de runner). |
| **P1-2** | **Fixação de versões em `requirements.txt` (builds Docker reproduzíveis)** | Baixa | Nenhuma | Dependências diretas fixadas com faixa segura ou `==` validada no Dockerfile e CI; 360+ testes verdes em Python 3.12. |
| **P1-3** | **Hardening de segurança HTTP: `Content-Security-Policy` global + `secure` no cookie CSRF** | Média | Nenhuma | Páginas `/` e `/reader` servem CSP funcional compatível com PDF.js/EPUB.js (zero violações no console nos testes E2E); `lectoria_csrf` respeita `config.SECURE_COOKIES`. |
| **P1-4** | **Resiliência do `lectoria-auto-update` e adoção do Compose endurecido na VPS** | Média | Aprovação operacional na VPS | `docker-compose` na VPS com `mem_limit: 1g`, `no-new-privileges:true`, rotação de logs `10m × 3` e `LECTORIA_SECURE_COOKIES=true`; auto-update valida `/api/health` pós-start e faz rollback ao digest anterior em caso de falha. |

---

## P2 — Média Prioridade (Qualidade, UX e Manutenibilidade — ciclo `v1.9.x` / `v1.10.0`)

| ID | Item | Complexidade | Dependências | Critérios de Aceitação |
| :--- | :--- | :--- | :--- | :--- |
| **P2-1** | **Acessibilidade (A11y) e UX do editor Markdown do Atlas** | Média | P0-1 | *Focus trap* e restauração de foco via `Escape` nos modais do Atlas; `aria-label` nos botões da toolbar; atalhos `Ctrl+S`, `Ctrl+B`, `Ctrl+I`; preservação de `Ctrl+Z` (`setRangeText` / `execCommand` fallback) ao inserir sintaxe; debounce (150 ms) no preview para textos > 5.000 chars. |
| **P2-2** | **Descontinuação de `csrf_token` via query string em rotas legadas** | Baixa | P1-3 | Todas as chamadas de escrita do frontend enviam `x-csrf-token` exclusivamente via cabeçalho HTTP; fallback de query string removido com testes de regressão. |
| **P2-3** | **Otimização de PRAGMAs SQLite para 1 vCPU** | Baixa | Nenhuma | `PRAGMA journal_mode=WAL` executado apenas em `init_db`; benchmark isolado validando latência de leitura/escrita concorrente sem regressão. |
| **P2-4** | **Modularização incremental de `main.py` em `APIRouter`s e divisão de `knowledge_service.py`** | Alta | Suíte de testes verde | Rotas separadas por domínio (`auth`, `books`, `clubs`, `knowledge`) mantendo 100% compatibilidade de URLs, contratos OpenAPI e cobertura de testes. |

---

## P3 — Baixa Prioridade / Evolução Futura

| ID | Item | Complexidade | Dependências | Critérios de Aceitação |
| :--- | :--- | :--- | :--- | :--- |
| **P3-1** | **Migração gradual de `innerHTML` em `app.js` e `reader.js` para helpers DOM seguros** | Alta | Nenhuma | Eliminação progressiva de interpolações HTML em `app.js`/`reader.js` seguindo o padrão `createEl` do Atlas, reduzindo superfície de XSS. |
| **P3-2** | **Expansão segura do subconjunto Markdown no Atlas** | Média | P2-1 | Suporte seguro (sem `innerHTML`) a listas de tarefas (`- [ ]`/`- [x]`), negrito+itálico (`***`) e tabelas simples, com testes unitários e E2E. |
| **P3-3** | **Divisão modular do CSS (`style.css` com 6.935 linhas) e contrato documentado de eventos JS** | Média | Nenhuma | Folhas organizadas por domínio (ou seções estritamente delimitadas) sem adicionar bundler/framework, mantendo zero build step. |
