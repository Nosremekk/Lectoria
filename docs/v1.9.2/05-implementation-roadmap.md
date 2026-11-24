# Lectoria — Fase 11.1: Roadmap de Implementação (Fases 11.2 a 12.5)

Planejamento sequencial e mensurável derivado da Auditoria de Infraestrutura ([`01-infrastructure-audit.md`](./01-infrastructure-audit.md)), Segurança ([`02-security-audit.md`](./02-security-audit.md)), Baseline de Performance ([`03-performance-baseline.md`](./03-performance-baseline.md)) e Avaliação Técnica ([`04-python-sqlite-vps-assessment.md`](./04-python-sqlite-vps-assessment.md)).

> **Política de Execução**: Nenhuma fase abaixo foi iniciada. Cada etapa exige autorização explícita do proprietário antes da execução. O backup remoto Restic/Rclone permanece descontinuado (Fase 8.D).

---

## Ciclo `v1.9.2` — Estabilização de Infraestrutura, Segurança e Deploy Resiliente

### Fase 11.2 — CI/CD e Versionamento — **`PHASE_11_2_COMPLETED`** *(2026-11-24)*
- **Concluída integralmente (Fases 11.2 a 11.2.6)**: PR #1 integrado em `main` (`9527470`), desduplicação do CI e imutabilidade GHCR validados em execução real ([Run #45461385371](https://github.com/Nosremekk/Lectoria/actions/runs/45461385371)), atualizador Docker/OCI (`deployment/docker-auto-update.sh`) validado com 384/384 testes + Docker Engine real e instalado na VPS com validação real dos 3 cenários (`NO_EXECUTABLE_CHANGE` no incidente OCI Index, `UPDATE_SUCCESS` no deploy controlado e `NO_EXECUTABLE_CHANGE` no disparo automático do timer). Relatório final: [`12-phase-11.2.6-controlled-install-and-merge.md`](./12-phase-11.2.6-controlled-install-and-merge.md).
- **Escopo**:
  - Extrair job de testes para workflow reutilizável `_test-suite.yml` eliminando execução duplicada entre `ci.yml` e `publish.yml` em pushes na `main`.
  - Garantir imutabilidade de tags Docker (`push` em `main` publica `:stable` + `:sha-<shortsha>`; `push` de tag `vX.Y.Z` valida igualdade com `config.APP_VERSION`, bloqueia sobrescrita de tag existente e publica/promove `:vX.Y.Z`, `:X.Y.Z` e `:stable` com o mesmo digest).
  - Injetar rótulos OCI (`org.opencontainers.image.revision`, `org.opencontainers.image.version`) no `Dockerfile` e corrigir o `docker-compose.yml` genérico (`:stable` + `/api/health`).
- **Dependências**: Aprovação da Fase 11.1.
- **Complexidade**: Baixa–Média.
- **Riscos**: Erro de sintaxe em YAML de workflow bloquear publicação GHCR.
- **Critérios de Aceitação**: `ci.yml` + `publish.yml` executam suíte 1 vez por push; tentativa simulada de tag divergente de `APP_VERSION` falha; `GITHUB_STEP_SUMMARY` exibe SHA + digest.
- **Plano de Reversão**: `git revert` dos arquivos em `.github/workflows/` e `Dockerfile`.

### Fase 11.3 — Segurança HTTP e Dependências
- **Escopo**:
  - Implementar `LECTORIA_SECURE_COOKIES` (`auto` | `true` | `false`, padrão `auto` respeitando `x-forwarded-proto == https`) em `config.py` e aplicá-lo em `lectoria_session` e `lectoria_csrf`.
  - Restringir emissão do cookie `lectoria_csrf` a páginas HTML (`/` e `/reader`) e `/api/auth/csrf`, removendo `Set-Cookie` de `/static/*`.
  - Remover os 2 `onsubmit="return false;"` de `static/index.html` e ativar `Content-Security-Policy-Report-Only` compatível com PDF.js, EPUB.js e Atlas.
  - Criar lockfile determinístico (`requirements.in` + `requirements.lock` / `requirements.txt` pinado para Python 3.12).
  - Remover import morto `CORSMiddleware` (ou documentar política Same-Origin).
- **Dependências**: Fase 11.2.
- **Complexidade**: Média.
- **Riscos**: Cookie `Secure` quebrar testes/HTTP local se modo `auto` não detectar corretamente `http://127.0.0.1`; CSP gerar violações inesperadas em EPUBs específicos.
- **Critérios de Aceitação**: 360+ testes verdes; zero violações CSP em todos os 55 testes E2E (PDF, EPUB, Atlas); `curl -I /static/js/app.js` sem `Set-Cookie`; `Set-Cookie` com `Secure` atrás de HTTPS/proxy; build Docker reproduzível a partir de versões fixadas.
- **Plano de Reversão**: Reverter commit da Fase 11.3 ou definir `LECTORIA_SECURE_COOKIES=false` e remover header `CSP-Report-Only`.

### Fase 11.4 — Revisão Operacional Complementar e Hardening do Compose na VPS
- **Itens já antecipados e concluídos na Fase 11.2 (não repetir)**:
  - Atualizador Docker/OCI versionado (`deployment/docker-auto-update.sh`) com comparação por `exec_fp`, lock `flock`, backup preventivo obrigatório (`lectoria-backup.service`), escrita atômica de `blocked` e `last-good.json` com referências imutáveis e tratamento *fail-closed* preservando schema SQLite.
  - Resolução do incidente de OCI Index e validação operacional do `lectoria-auto-update.timer` na VPS.
- **Escopo remanescente para a Fase 11.4**:
  - Aplicar endurecimento do `compose.yaml` na VPS preservando 100% os bind mounts `/storage/*`: `mem_limit: 1g`, `pids_limit: 128`, `no-new-privileges:true`, `LECTORIA_SECURE_COOKIES: "true"`, `LECTORIA_FORWARDED_ALLOW_IPS` restrito, rotação `json-file` (`10m × 3`) e `healthcheck` em `/api/health`.
  - Executar teste controlado de restauração completa de backup em ambiente descartável/isolado (além da verificação estrutural `PRAGMA integrity_check`).
  - Documentar procedimento operacional consolidado de rollback assistido para releases com migração de schema SQLite.
- **Dependências**: Fase 11.3 (`LECTORIA_SECURE_COOKIES` e `LECTORIA_FORWARDED_ALLOW_IPS`).
- **Complexidade**: Baixa–Média (reduzida após antecipação do atualizador na Fase 11.2).
- **Riscos**: Erro de permissão ou sintaxe no `compose.yaml` derrubar o container momentaneamente.
- **Critérios de Aceitação**: Container sobe `healthy` com Compose endurecido preservando 238 livros, 5 usuários, Schema v13 e `lectoria-backup.timer` ativo; zero processos Restic/Rclone; restauração de backup validada em ambiente isolado.
- **Plano de Reversão**: Restaurar cópia `compose.yaml.bak` + `docker compose up -d`.

### Fase 11.5 — Validação e Release `v1.9.2`
- **Escopo**:
  - Bump de versão para `1.9.2` (`config.py`, HTML, cache-busting, `CHANGELOG.md`), suíte completa verde, merge em `main`, tag imutável `v1.9.2`, acompanhamento de CI/GHCR, verificação pós-deploy na VPS e relatório final.
- **Dependências**: Fases 11.2, 11.3 e 11.4.
- **Complexidade**: Baixa.
- **Riscos**: Regressão de versão ou CSP em navegador real.
- **Critérios de Aceitação**: `DEPLOY_SUCCESS`, `/api/health == 1.9.2`, zero violações CSP no navegador real do proprietário, digests `:v1.9.2 == :1.9.2 == :stable`.
- **Plano de Reversão**: Rollback do container para o digest imutável `v1.9.1` (`sha256:36af447d...`, compatível com Schema v13).

---

## Ciclo `v1.10.0` — Profiling Aprofundado e Otimização Real de Performance

### Fase 12.1 — Profiling Aprofundado (Baseline Completo)
- **Escopo**:
  - Executar a matriz de 13 cenários de referência ([`03-performance-baseline.md`](./03-performance-baseline.md)) em ambiente isolado com emulação de rede Tailscale (`60 ms` RTT) usando Playwright Performance API / trace + `cProfile` / `EXPLAIN QUERY PLAN` em cópia sintética do banco.
  - Medir especificamente: abertura de PDFs (`1 MB`, `16 MB`, `125 MB`), abertura de EPUBs (`1 MB`, `30 MB`), carregamento de 36 capas, `GET /api/books` (238 e 1.000 livros), *Long Tasks* > 50 ms e uso de heap JS.
  - Definir metas numéricas finais baseadas em dados medidos (mediana e p95).
- **Dependências**: Fase 11.5 (`v1.9.2`).
- **Complexidade**: Média.
- **Riscos**: Nenhum risco em produção (100% isolado).
- **Critérios de Aceitação**: Relatório quantitativo com mediana/p95 dos 13 cenários e ranking comprovado dos gargalos.
- **Plano de Reversão**: Não aplicável (somente medição).

### Fase 12.2 — Otimizações de Backend e SQLite
- **Escopo**:
  - Ativar `GZipMiddleware(minimum_size=500)` no FastAPI (excluindo ou evitando recompressão inútil de binários PDF/EPUB/JPEG/ZIP já comprimidos).
  - Enviar `Cache-Control: public, max-age=31536000, immutable` para `/static/*` com query `?v=X.Y.Z` (e `max-age=86400` + ETag forte para `/api/books/{id}/cover`).
  - Executar `PRAGMA journal_mode=WAL` apenas em `init_db()` e validar `PRAGMA temp_store=MEMORY` / `cache_size` em benchmark isolado.
  - Revisar as 9 rotas `async def` restantes para garantir zero I/O bloqueante no event loop e avaliar paginação/campos enxutos em `GET /api/books` se o profiling da Fase 12.1 justificar.
- **Dependências**: Fase 12.1.
- **Complexidade**: Média.
- **Riscos**: `GZipMiddleware` interferir em `StreamingResponse` / `Range 206` de PDFs (mitigação obrigatória: validar `206 Partial Content` + `Accept-Ranges: bytes` nos testes de PDF!).
- **Critérios de Aceitação**: Redução `>= 65%` em bytes transferidos de HTML/CSS/JS/JSON; `0` regressões em HTTP Range 206 (PDF) e exportações `.zip`; 360+ testes verdes.
- **Plano de Reversão**: Reverter middleware GZip/headers mantendo compatibilidade total com Schema v13.

### Fase 12.3 — Otimizações de Frontend
- **Escopo**:
  - Carregamento sob demanda (`lazy script load`) de `knowledge.js` (`164 KB`) e `shared_reading.js` (`52 KB`) somente ao abrir as abas **Atlas** ou **Clubes**.
  - Debounce (120–150 ms) no preview Markdown do Atlas apenas para reflexões > 5.000 caracteres.
  - Ativar enforcement do `Content-Security-Policy` (promovendo de `Report-Only` após validação na `v1.9.2`).
  - Eliminar *long tasks* > 50 ms identificadas na Fase 12.1 (ex.: batching com `DocumentFragment` / `requestAnimationFrame` em `renderBooks`).
- **Dependências**: Fases 12.1 e 12.2.
- **Complexidade**: Média.
- **Riscos**: Race condition ao carregar scripts sob demanda antes do clique na aba.
- **Critérios de Aceitação**: Redução mensurável de FCP/LCP e *Long Tasks* nos traces Playwright; zero erros de console em todos os testes E2E.
- **Plano de Reversão**: Voltar tags `<script>` estáticas em `static/index.html`.

### Fase 12.4 — Otimizações de Leitores PDF e EPUB
- **Escopo**:
  - Garantir cache imutável e compressão GZip para `pdf.min.js` (`313 KB`), `pdf.worker.min.js` (`1,1 MB`), `epub.min.js` (`219 KB`) e `jszip.min.js` (`96 KB`) com versionamento `?v=X.Y.Z`.
  - Otimizar sequência de inicialização do leitor (carregar shell + progresso + primeira página em paralelo com anotações/conhecimento, evitando serialização em cascata).
  - Avaliar pré-carregamento da próxima página no PDF e feedback progressivo de download em EPUBs grandes (sem alterar arquivos do usuário).
- **Dependências**: Fases 12.1 e 12.2.
- **Complexidade**: Média–Alta.
- **Riscos**: Regressão em navegação `#page=N` / `#cfi=...` ou desenhos vetoriais SVG.
- **Critérios de Aceitação**: Ganho comprovado no tempo até a 1ª página renderizada (PDF e EPUB) sob 60 ms RTT; 100% dos testes E2E de PDF/EPUB/Anotações/Atlas verdes.
- **Plano de Reversão**: `git revert` cirúrgico em `static/reader.html` e `static/js/reader.js`.

### Fase 12.5 — Benchmarks Comparativos e Release `v1.10.0`
- **Escopo**:
  - Reexecutar a matriz exata de 13 cenários da Fase 12.1 e publicar relatório comparativo Antes (`v1.9.2`) vs. Depois (`v1.10.0`).
  - Bump de versão para `1.10.0`, validação completa, merge, tag imutável `v1.10.0`, publicação GHCR e verificação não destrutiva na VPS.
- **Dependências**: Fases 12.2, 12.3 e 12.4.
- **Complexidade**: Baixa.
- **Riscos**: Baixo (validado por benchmarks e suíte E2E completa).
- **Critérios de Aceitação**: Ganho mensurável comprovado sobre o baseline da Fase 12.1; `DEPLOY_SUCCESS` em produção; zero regressões de funcionalidade, segurança ou memória.
- **Plano de Reversão**: Rollback automático/manual para o digest imutável `v1.9.2`.
