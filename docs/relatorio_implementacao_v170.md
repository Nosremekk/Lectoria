# Relatório Técnico Consolidado de Implementação — Lectoria v1.7.0 (Leitura Compartilhada)

- **Projeto**: Lectoria — Biblioteca Digital e Leitura Colaborativa *Self-Hosted*
- **Versão**: `1.7.0` (base anterior estável: `v1.6.3`)
- **Branch de Desenvolvimento**: `feature/v1.7.0-shared-reading`
- **Schema SQLite**: `11` (migração incremental `v10 → v11`)
- **Suíte Automatizada**: **248 testes aprovados** (`211` unitários/integração + `37` E2E no Chromium headless)

---

## 1. Resumo Executivo da v1.7.0

A versão **v1.7.0 — Leitura Compartilhada** expande a infraestrutura de Clubes de Leitura e Leituras Coletivas introduzida na v1.6.0, transformando cada leitura coletiva em um espaço editorial estruturado para debate, organização de cronograma e troca de trechos literários, sem descaracterizar o Lectoria como biblioteca pessoal sóbria e sem introduzir mecânicas de rede social genérica.

Os principais marcos entregues na v1.7.0 incluem:
1. **Fórum de Discussões por Leitura Coletiva**: tópicos temáticos ou por capítulo (`chapter_or_section`, `page_reference`), respostas encadeadas (`parent_reply_id`), controle de spoilers (`has_spoilers`), fixação (`is_pinned`), trancamento (`is_locked`) e exclusão lógica (`is_deleted = 1`).
2. **Metas Coletivas e Progresso Voluntário**: cronograma de marcos criado por `owner`/`moderator` e acompanhamento individual estritamente voluntário (`planned`, `in_progress`, `completed`), mantendo 100% de isolamento em relação ao progresso privado (`reading_progress`).
3. **Mural de Citações Compartilhadas e Integração com PDF.js / EPUB.js**: compartilhamento voluntário de trechos selecionados diretamente nos leitores PDF e EPUB ou a partir de notas privadas do próprio usuário, com salto reverso direto (*"Abrir no leitor"*) para `#page=N` ou `#cfi=epubcfi(...)`.
4. **Política Somente-Leitura para Leituras Encerradas/Arquivadas**: preservação integral do histórico para consulta (`GET 200`) e bloqueio estrito de mutações (`HTTP 400 Bad Request`) quando a leitura está `completed`, `cancelled` ou vinculada a clube com `is_archived = 1`.
5. **Consolidação Arquitetural e Correção dos Filtros Taxonômicos da Biblioteca**: documentação técnica modular (`AGENTS.md`, `docs/*.md`, `ADR-001..006`) e correção definitiva da paridade entre os contadores de `/api/taxonomy/tree` e a listagem `/api/books`, desacoplando facetas ortogonais (`genre`, `origin`, `period`) e corrigindo a invalidação do cache no frontend.

---

## 2. Arquitetura Geral Implementada

A implementação preservou integralmente os pilares arquiteturais do Lectoria:
- **Backend**: Python 3.10+ / FastAPI em processo único, validação de contratos com Pydantic v2 (`ConfigDict(extra="forbid")`), autenticação por sessão com hash SHA-256 e proteção CSRF via cabeçalho `x-csrf-token`.
- **Camada de Serviço Isolada**: Toda a lógica de domínio e autorização da Leitura Compartilhada reside em `shared_reading_service.py`, sem sobrecarregar `main.py` ou misturar responsabilidades com `clubs_service.py`.
- **Persistência**: SQLite 3 em modo WAL (`PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`), evoluído da versão `10` para a versão `11` com *triggers* nativos de integridade relacional cruzada.
- **Frontend Vanilla**: Módulo dedicado `static/js/shared_reading.js` acoplado a `static/index.html`, além de extensões cirúrgicas em `static/reader.html` e `static/js/reader.js`, sem adição de frameworks ou etapas de build.

---

## 3. Fase 1 — Persistência e Migração SQLite v10 → v11

No módulo `database.py`, foi implementada a migração incremental `_apply_migration_v11(conn)` elevando `SCHEMA_VERSION` de `10` para `11`:

- **Tabelas Criadas**:
  1. `discussion_topics`: tópicos vinculados a `collective_reads(id)` (`ON DELETE CASCADE`) e `users(id)` (`ON DELETE SET NULL`), com `title`, `body`, `chapter_or_section`, `page_reference`, `has_spoilers`, `is_pinned`, `is_locked`, `is_deleted`, `created_at` e `updated_at`.
  2. `discussion_replies`: respostas vinculadas a `discussion_topics(id)` (`ON DELETE CASCADE`), `users(id)` (`ON DELETE SET NULL`) e auto-referência opcional `parent_reply_id` (`ON DELETE SET NULL`), com `body`, `has_spoilers`, `is_deleted`, `created_at` e `updated_at`.
  3. `collective_reading_goals`: metas vinculadas a `collective_reads(id)` (`ON DELETE CASCADE`) e `created_by_user_id` (`ON DELETE SET NULL`), com `title`, `description`, `target_section`, `target_page`, `target_percent`, `due_date`, `sort_order`, `created_at` e `updated_at`.
  4. `collective_goal_progress`: progresso voluntário por participante (`UNIQUE(goal_id, user_id)`), com `status IN ('planned', 'in_progress', 'completed')`, `note`, `updated_at` e `completed_at`.
  5. `shared_quotes`: citações compartilhadas vinculadas a `collective_reads(id)` (`ON DELETE CASCADE`), `users(id)` (`ON DELETE SET NULL`) e opcionalmente `notes(id)` (`ON DELETE SET NULL`), com `quote_text`, `comment_text`, `page_number`, `section_label`, `position_ref`, `has_spoilers`, `is_deleted`, `created_at` e `updated_at`.
- **Integridade Entre Entidades (Triggers SQLite)**:
  - `trg_discussion_replies_topic_integrity_insert` e `update`: garantem que o tópico exista em `discussion_topics`.
  - `trg_discussion_replies_parent_integrity_insert` e `update`: garantem que `parent_reply_id` pertença exatamente ao mesmo `topic_id`.
  - `trg_collective_goal_progress_goal_integrity_insert` e `update`: garantem que a meta exista em `collective_reading_goals`.
- **Backup Pré-Migração Não-Destrutivo**:
  - `_create_pre_migration_backup_v11(db_path)` cria cópia consistente via `sqlite3.Connection.backup()` em `backups/` (ou junto ao DB) com sufixo incremental (`.1`, `.2`, ...) caso o arquivo de destino já exista, nunca sobrescrevendo backups anteriores.
  - Em caso de falha durante a migração, a transação sofre rollback integral e `schema_migrations` permanece na versão `10`.

---

## 4. Fase 2 — `shared_reading_service.py` e Regras de Negócio

O módulo `shared_reading_service.py` implementa todas as operações e invariantes de domínio:

- **Resolução de Contexto e Controle de Acesso (`_get_read_context`)**:
  - Verifica existência da leitura coletiva e do clube associado.
  - Aplica isolamento anti-enumeração: se o clube for `private` e o usuário não for membro ativo, lança `SharedReadingNotFoundError` (`HTTP 404`).
  - Verifica se a leitura está em modo somente-leitura (`status in ('completed', 'cancelled')` ou `club_is_archived == 1`).
- **Discussões e Respostas**:
  - Criação restrita a participantes ativos da leitura coletiva (que também sejam membros do clube).
  - Edição restrita ao próprio autor; exclusão lógica (`is_deleted = 1`) permitida ao autor ou `owner`/`moderator`.
  - Fixação (`is_pinned`) e trancamento (`is_locked`) restritos a `owner`/`moderator`.
  - Tópicos trancados ou excluídos rejeitam novas respostas (`SharedReadingValidationError`).
- **Metas Coletivas e Progresso Voluntário**:
  - CRUD de metas restrito a `owner`/`moderator` do clube.
  - Registro de progresso individual (`upsert_goal_progress`) restrito a participantes ativos da leitura coletiva, sem qualquer leitura ou escrita automática na tabela privada `reading_progress`.
- **Citações Compartilhadas**:
  - Criação restrita a participantes ativos; se `source_note_id` for informado, valida que a nota pertence ao próprio usuário autenticado e ao mesmo `book_id` da leitura coletiva (proteção anti-IDOR).

---

## 5. Fase 3 — Endpoints REST, Validação e Segurança

Em `main.py`, foram expostos **19 endpoints REST** (18 de leitura compartilhada + 1 de descoberta de leituras ativas por livro):

1. `GET /api/collective-reads/{read_id}/overview`
2. `GET /api/collective-reads/{read_id}/topics`
3. `POST /api/collective-reads/{read_id}/topics`
4. `GET /api/collective-reads/{read_id}/topics/{topic_id}`
5. `PATCH /api/collective-reads/{read_id}/topics/{topic_id}`
6. `DELETE /api/collective-reads/{read_id}/topics/{topic_id}`
7. `POST /api/collective-reads/{read_id}/topics/{topic_id}/replies`
8. `PATCH /api/collective-reads/{read_id}/replies/{reply_id}`
9. `DELETE /api/collective-reads/{read_id}/replies/{reply_id}`
10. `GET /api/collective-reads/{read_id}/goals`
11. `POST /api/collective-reads/{read_id}/goals`
12. `PATCH /api/collective-reads/{read_id}/goals/{goal_id}`
13. `DELETE /api/collective-reads/{read_id}/goals/{goal_id}`
14. `PUT /api/collective-reads/{read_id}/goals/{goal_id}/my-progress`
15. `GET /api/collective-reads/{read_id}/my-goal-progress`
16. `GET /api/collective-reads/{read_id}/quotes`
17. `POST /api/collective-reads/{read_id}/quotes`
18. `PATCH /api/collective-reads/{read_id}/quotes/{quote_id}`
19. `DELETE /api/collective-reads/{read_id}/quotes/{quote_id}`
20. `GET /api/books/{book_id}/active-collective-reads`

Todos os modelos Pydantic utilizam `ConfigDict(extra="forbid")` e limites estritos de tamanho (`title <= 200`, `body <= 10000`, `quote_text <= 3000`, `comment_text <= 3000`, `note <= 1000`).

---

## 6. Fase 4 — Interface Web da Leitura Compartilhada

O módulo `static/js/shared_reading.js` e os estilos em `static/css/style.css` adicionaram o painel completo da Leitura Compartilhada dentro da seção de Clubes de Leitura:
- **4 Subabas Integradas**: *Visão Geral*, *Discussões*, *Metas* e *Citações*.
- **Proteção de Spoilers**: conteúdo marcado com `has_spoilers` permanece oculto sob máscara `.sr-spoiler-box` até clique explícito em *"Revelar spoiler"*.
- **Controle de Concorrência**: uso de `AbortController` na troca rápida entre leituras coletivas ou subabas.
- **Responsividade e Temas**: compatibilidade verificada em `320px`, `360px`, `768px`, `1280px` e `1920px` nos temas Claro, Escuro e Sépia, com `scrollWidth <= clientWidth`.

---

## 7. Fase 5 — Integração com Leitores PDF.js e EPUB.js

Nos arquivos `static/reader.html` e `static/js/reader.js`:
- **Compartilhamento Direto**:
  - Ao selecionar texto no PDF ou EPUB, a barra `#selection-floating-bar` exibe o botão *"Compartilhar com leitura coletiva"*.
  - No Caderno de Estudos, cada nota privada do próprio leitor exibe ação para compartilhamento voluntário no modal `#modal-share-collective-quote`.
  - O modal consulta `GET /api/books/{book_id}/active-collective-reads` e preenche automaticamente o trecho, número da página (`page_number`) e referência técnica (`#page=N` ou `epubcfi(...)`).
- **Navegação Reversa (*"Abrir no leitor"*)**:
  - A partir da aba *Citações*, clicar em *"Abrir no leitor"* navega para `/reader?id={book_id}#page={N}` (PDF) ou `/reader?id={book_id}#cfi={encoded_cfi}` (EPUB).
  - O leitor interpreta o hash após carregar o documento e posiciona o usuário no ponto citado, exibindo aviso não-bloqueante (`#reader-toast`) caso a referência seja inválida, sem quebrar a sessão de leitura.

---

## 8. Fase 6 — Validação, Endurecimento e Regressões

Durante a Fase 6, foi realizada auditoria completa das Fases 1–5 e implementado endurecimento adicional:
- **Unificação da Política de Leituras Concluídas (`status = 'completed'`)**:
  - Leituras coletivas nos estados `completed`, `cancelled` ou pertencentes a clubes arquivados (`is_archived = 1`) foram padronizadas como **estritamente somente-leitura** em todos os métodos de escrita de `shared_reading_service.py` e na interface `static/js/shared_reading.js` (exibindo banner informativo `#sr-readonly-banner` e ocultando formulários/botões de mutação).
- **Novos Testes de Endurecimento (`tests/test_v170_phase6_hardening.py`)**:
  - Cobertura de bloqueio de escrita em leituras concluídas/canceladas/arquivadas, preservação de histórico em exclusão de usuário (`ON DELETE SET NULL`), livro ausente (`is_missing = 1`), paginação nos limites (`limit=100`) e payloads malformados.

---

## 9. Fase 6.5 — Consolidação Arquitetural e Documentação Técnica

Foi criada a base documental estruturada do repositório para orientar desenvolvedores humanos e agentes de IA:
- `AGENTS.md` (reescrito e atualizado para a v1.7.0 / Schema v11).
- `docs/ARCHITECTURE.md`, `docs/DATABASE.md`, `docs/AUTHORIZATION.md`, `docs/READER.md`, `docs/FRONTEND.md`, `docs/API.md`, `docs/TESTING.md` e `docs/DEPLOYMENT.md`.
- 6 *Architecture Decision Records* em `docs/DECISIONS/`:
  - `ADR-001-vanilla-frontend.md`
  - `ADR-002-sqlite-persistence.md`
  - `ADR-003-pdf-range-streaming.md`
  - `ADR-004-privacy-by-default.md`
  - `ADR-005-completed-collective-reads-readonly.md`
  - `ADR-006-soft-delete-and-history-preservation.md`

---

## 10. Investigação e Correção dos Filtros Taxonômicos da Biblioteca

Em resposta ao bug reportado na biblioteca (onde selecionar *"Filosofia (33)"* exibia *"Nenhum livro correspondente"*), foi conduzida investigação de ponta a ponta e aplicadas 5 correções definitivas:
1. **Paridade SQL em `taxonomy_service.get_taxonomy_tree()`**:
   - Os contadores da árvore taxonômica passaram a filtrar `b.is_missing = 0` e `JOIN library_sources s ON b.source_id = s.id WHERE s.is_active = 1`, além de calcular `total_books` com `COUNT(DISTINCT b.id)` por categoria para não inflar a soma quando um livro possui mais de uma subcategoria na mesma categoria.
2. **Isolamento de Facetas Ortogonais em `taxonomy_service.sync_book_taxonomy()`**:
   - Na implementação legada, `genre`, `origin` e `period` eram gravados diretamente no registro compartilhado de `subjects` da subcategoria/categoria, fazendo com que livros da mesma subcategoria sobrescrevessem mutuamente essas facetas. Agora cada faceta ortogonal é persistida em seu próprio registro de assunto (`"{categoria} — Gênero — {genre}"`, `"{categoria} — Origem — {origin}"`, `"{categoria} — Período — {period}"`).
3. **Filtragem e Cache no Frontend (`static/js/app.js`)**:
   - Adicionada normalização Unicode NFC case-insensitive (`normTax`) em `getFilteredAndSortedBooksFromCache()` e `populateSubcategorySelect()`, além de invalidação imediata de `libraryCache` ao editar metadados, reclassificar obras ou gerenciar categorias.
4. **Layout Mobile dos Seletores (`static/css/style.css`)**:
   - Corrigida especificidade CSS mobile (`.taxonomy-filters-collapsible .taxonomy-select`) para manter `width: 100%` em telas de `360px`.
5. **Ferramenta de Diagnóstico Não-Destrutiva (`scripts/diagnose_taxonomy.py`)**:
   - Script CLI estritamente *read-only* (`mode=ro`, `PRAGMA query_only=ON`) para inspecionar paridade de contadores e detectar eventuais registros legados compartilhados em bancos de produção.

---

## 11. Resultados de Testes Automatizados

A execução completa da suíte automatizada na branch `feature/v1.7.0-shared-reading` atingiu **100% de aprovação**:

| Suíte | Comando | Resultado |
| :--- | :--- | :--- |
| **Unitários, Serviços, Persistência e API** | `./.venv/bin/pytest --ignore=tests/e2e -q` | **211 passed** |
| **End-to-End (Playwright Chromium)** | `./.venv/bin/pytest tests/e2e -q` | **37 passed** |
| **Total Geral** | — | **248 / 248 aprovados (0 falhas)** |

---

## 12. Auditoria de Segurança e Privacidade

- **Proteção CSRF**: Todas as rotas mutantes exigem sessão válida e cabeçalho `x-csrf-token` pareado ao cookie `lectoria_csrf`, além de verificação `Origin`/`Referer`.
- **Proteção contra Mass Assignment**: Todos os payloads de entrada utilizam Pydantic `ConfigDict(extra="forbid")`.
- **Anti-Enumeração de Clubes Privados**: Qualquer acesso a leituras coletivas, tópicos, respostas, metas ou citações de clubes privados por não-membros retorna `HTTP 404 Not Found`.
- **Anti-IDOR em Notas Privadas**: `create_shared_quote` valida que `source_note_id` pertence estritamente ao `user_id` autenticado e ao `book_id` da leitura coletiva.
- **Prevenção de XSS**: Todo texto renderizado em `static/js/shared_reading.js`, `static/js/app.js` e `static/js/reader.js` passa por `escapeHtml()`.
- **Higiene de Repositório**: Nenhum arquivo `.env`, banco `.db`/`-wal`/`-shm`, chave privada ou credencial está rastreado pelo Git.

---

## 13. Auditoria de Desempenho e Compatibilidade com Hardware Modesto

- **Leitor PDF**: Mantidos `disableStream: true`, `rangeChunkSize: 524288` (512 KB) e `disableAutoFetch: true` em `static/js/reader.js`, garantindo abertura imediata via HTTP 206 Partial Content sem download integral do PDF.
- **Biblioteca**: Mantida renderização progressiva em lotes de 36 livros (`BOOKS_CHUNK_SIZE = 36`) e cache local `libraryCache` com invalidação cirúrgica.
- **Agendador (`automation_service.py`)**: Limpeza de `_dir_snapshots` em `start()`/`stop()` evita varredura duplicada no boot; o monitoramento de diretórios inspeciona apenas `mtime` de diretórios (sem `stat` individual em milhares de arquivos).
- **Zero Polling Contínuo**: Nenhuma chamada `setInterval` de rede foi introduzida na Leitura Compartilhada; dados são carregados sob demanda com cancelamento via `AbortController`.

---

## 14. Arquivos Criados e Modificados

### Arquivos de Código e Configuração Criados/Modificados
- `config.py`: atualização de `APP_VERSION = "1.7.0"`.
- `database.py`: `SCHEMA_VERSION = 11`, migração `_apply_migration_v11`, backup pré-migração único e 6 triggers de integridade.
- `shared_reading_service.py` *(novo)*: serviço completo da Leitura Compartilhada.
- `main.py`: 19 rotas REST da Leitura Compartilhada, schemas Pydantic v2 e tratamento de erros HTTP.
- `taxonomy_service.py`: paridade de contadores em `get_taxonomy_tree()` e isolamento de facetas em `sync_book_taxonomy()`.
- `library.py`: limpeza do join de `is_active` na listagem de livros.
- `automation_service.py`: limpeza de `_dir_snapshots` em `start()` e `stop()`.
- `static/index.html`: containers da Leitura Compartilhada e cache-busting `?v=1.7.0`.
- `static/reader.html`: botão e modal de compartilhamento de citação e cache-busting `?v=1.7.0`.
- `static/js/shared_reading.js` *(novo)*: controlador frontend da Leitura Compartilhada.
- `static/js/app.js`: integração com `shared_reading.js`, correção dos filtros taxonômicos, normalização Unicode e `LECTORIA_FRONTEND_VERSION = "1.7.0"`.
- `static/js/reader.js`: compartilhamento de seleção/nota com leitura coletiva e navegação por hash (`#page=N`, `#cfi=...`).
- `static/css/style.css`: estilos da Leitura Compartilhada, máscaras de spoiler, banners somente-leitura e correção mobile de `.taxonomy-select`.
- `scripts/diagnose_taxonomy.py` *(novo)*: diagnóstico read-only de integridade taxonômica.
- `scripts/package.py`: atualização da versão padrão de empacotamento para `1.7.0`.

### Arquivos de Testes Criados/Modificados
- `tests/test_v170_shared_reading_schema.py` *(novo)*
- `tests/test_v170_shared_reading_service.py` *(novo)*
- `tests/test_v170_shared_reading_api.py` *(novo)*
- `tests/test_v170_phase6_hardening.py` *(novo)*
- `tests/test_v170_library_filters_regression.py` *(novo)*
- `tests/e2e/test_v170_shared_reading_ui_e2e.py` *(novo)*
- `tests/e2e/test_v170_reader_shared_reading_e2e.py` *(novo)*
- `tests/e2e/test_v170_library_filters_e2e.py` *(novo)*
- `tests/test_v151_social_contracts.py` e `tests/test_v163_cover_rendering.py` *(atualizados para v1.7.0 / Schema v11)*

### Documentação Criada/Atualizada
- `AGENTS.md`, `README.md`, `CHANGELOG.md`
- `docs/ARCHITECTURE.md`, `docs/DATABASE.md`, `docs/AUTHORIZATION.md`, `docs/READER.md`, `docs/FRONTEND.md`, `docs/API.md`, `docs/TESTING.md`, `docs/DEPLOYMENT.md`
- `docs/DECISIONS/ADR-001..006.md`
- `docs/v1.7.0-progress.md`, `docs/v1.7.0-validation-report.md`, `docs/relatorio_implementacao_v170.md`, `docs/v1.7.0-release-readiness.md`

---

## 15. Limitações Conhecidas e Recomendações Futuras

1. **Verificação Pós-Atualização em Bancos Legados com Facetas Acopladas**:
   - Em bancos alimentados por versões anteriores (v1.4.0–v1.6.3) onde múltiplos livros compartilhavam o mesmo registro `subjects` com `genre`/`origin`/`period` acoplados, recomenda-se executar `python3 scripts/diagnose_taxonomy.py` após o upgrade na VPS para inspecionar se algum livro automático requer re-inferência de facetas.
2. **Notificações em Tempo Real**:
   - Por decisão arquitetural voltada a baixo consumo em hardware modesto, a v1.7.0 não utiliza WebSockets ou *polling* em segundo plano para novos tópicos/respostas; a atualização ocorre sob demanda ao abrir ou alternar abas.
3. **Nível de Aninhamento de Respostas**:
   - O encadeamento visual de respostas exibe referência direta ao comentário-pai (`parent_reply_id`) em lista linear cronológica para preservar legibilidade em telas móveis estreitas (`320px–360px`).
