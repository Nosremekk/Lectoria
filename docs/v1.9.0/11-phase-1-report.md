# 11 — Relatório Consolidado da Fase 1 (Lectoria v1.9.0 — Conhecimento Conectado / Atlas)

> **Fase**: 1 (Consolidação das Decisões, Fundação do Domínio e Migração SQLite v13)  
> **Branch**: `feature/v1.9.0-connected-knowledge`  
> **Commit de Referência Base (`main`)**: `ddffdaa437a7e884c1a474926181371c047aefdf`  
> **Versão da Aplicação (`config.APP_VERSION`)**: `"1.8.0"` (preservada para compatibilidade até a fase de release)  
> **Schema SQLite**: `12 → 13` (`SCHEMA_VERSION = 13`)  
> **Seção Principal Oficial (`KD-010`)**: **Atlas**  
> **Data**: 2026-11-18  
> **Classificação Final da Fase 1**: **`PHASE_1_COMPLETE`**

---

## 1. Resumo Executivo

A **Fase 1 da Lectoria v1.9.0 — Conhecimento Conectado** foi concluída integralmente dentro das fronteiras autorizadas pelo proprietário:

1. **Consolidação Documental das 11 Decisões do Proprietário (`KD-001` a `KD-011`)**:
   - Todas as 11 decisões foram registradas como `APPROVED` em [09-owner-decisions.md](file:///home/vini/Jellyfin%20livros/docs/v1.9.0/09-owner-decisions.md), incluindo o nome oficial da nova seção principal (**Atlas**, `KD-010`) e as ressalvas técnicas obrigatórias da Seção 13 nos documentos `02` a `10` de `docs/v1.9.0/`.
2. **Migração Incremental Aditiva do Schema SQLite (`v12 → v13`)**:
   - Implementada a função `_apply_migration_v13(cursor)` em [database.py](file:///home/vini/Jellyfin%20livros/database.py#L1606-L2006), elevando `SCHEMA_VERSION` de `12` para `13`.
   - Criadas as tabelas `concepts`, `reflections`, `knowledge_links`, a tabela virtual `knowledge_fts` (SQLite FTS5 com `tokenize='unicode61 remove_diacritics 2'`), 10 índices compostos iniciados por `user_id` e 13 triggers SQLite de sincronização FTS e integridade referencial polimórfica.
3. **Camada de Serviço de Domínio (`knowledge_service.py`)**:
   - Implementado o módulo [knowledge_service.py](file:///home/vini/Jellyfin%20livros/knowledge_service.py) com CRUD completo, paginação e ordenação determinística para Conceitos (`concepts`), Reflexões Autorais (`reflections`) e Conexões/Backlinks (`knowledge_links`), resolução centralizada de entidades com autorização em tempo de leitura (`resolve_knowledge_entity`) e pesquisa de candidatos FTS5 com dupla barreira relacional (`search_knowledge_candidates`).
4. **Validação Automatizada e Regressão Completa**:
   - Criadas 6 novas suítes de testes (`test_v190_knowledge_migration.py`, `test_v190_knowledge_concepts.py`, `test_v190_knowledge_reflections.py`, `test_v190_knowledge_links.py`, `test_v190_knowledge_authorization.py`, `test_v190_knowledge_fts.py`) totalizando **31 novos testes**.
   - **Suíte Unitária e de Integração**: **262 aprovados**, 0 falhas, 0 ignorados (`41,68s`).
   - **Suíte E2E (Playwright Chromium)**: **40 aprovados**, 0 falhas, 0 ignorados (`156,32s`).
   - **Total Geral**: **302 testes aprovados** (contra 271 da referência v1.8.0).

---

## 2. Consolidação das 11 Decisões Aprovadas pelo Proprietário (Seção 17.1)

| ID | Decisão | Alternativa Aprovada | Estado | Reflexo Técnico na Fase 1 |
| :--- | :--- | :--- | :---: | :--- |
| **KD-001** | Escopo Funcional da v1.9.0 | Alternativa 2 — Escopo B (Equilibrado) | `APPROVED` | Fundação completa para conceitos, conexões, backlinks, reflexões e busca FTS5. |
| **KD-002** | Modelo de Conceitos | Alternativa 1 — Entidade própria `concepts`, privada por `user_id` | `APPROVED` | Tabela `concepts` com UUID, `normalized_name` (NFC + `.casefold()`) e índice único `(user_id, normalized_name)`. |
| **KD-003** | Tipos de Conexão Semântica | Alternativa 1 — 8 tipos fixos direcionados + `rationale` opcional | `APPROVED` | Constraint `CHECK` em `knowledge_links.relation_type` e consulta bidirecional sem duplicar arestas. |
| **KD-004** | Referências Internas e Backlinks | Alternativa 1 — Referências por UUIDs estáveis em `knowledge_links` | `APPROVED` | `knowledge_links` como fonte única de verdade para vínculos entre `concept`, `reflection`, `highlight`, `note` e `book`. |
| **KD-005** | Reflexões Autorais | Alternativa 1 — Entidade independente `reflections` com Markdown seguro | `APPROVED` | Tabela `reflections` com coluna `body_markdown` (até 50.000 caracteres) e `primary_book_id` opcional (`ON DELETE SET NULL`). |
| **KD-006** | Visualização de Conexões | Alternativa 1 — Grafo local SVG (1–2 saltos, <= 80 nós) + Lista Textual | `APPROVED` | Documentado na arquitetura; primitivas de vizinhança prontas em `knowledge_service.py` (UI reservada para a Fase 6). |
| **KD-007** | Trilhas de Estudo vs. Coleções | Alternativa 1 — Reutilizar conceitos e coleções; não criar `study_trails` | `APPROVED` | Nenhuma tabela `study_trails` criada; `collections` preservada intacta. |
| **KD-008** | Motor de Busca Unificada | Alternativa 1 — SQLite FTS5 `knowledge_fts` na migração v13 + dupla barreira | `APPROVED` | Tabela virtual `knowledge_fts` com `unicode61 remove_diacritics 2`, triggers e validação relacional obrigatória. |
| **KD-009** | Exportação Aberta | Alternativa 1 — ZIP Markdown + JSON v2.0 + CSV + opção Obsidian | `APPROVED` | Especificado nos documentos de arquitetura e roadmap para implementação na Fase 6/7. |
| **KD-010** | Nome da Nova Seção Principal | Alternativa 5 — **Atlas** | `APPROVED` | Consolidado nos documentos de produto, UX, arquitetura e roadmap (mantendo `knowledge` nos identificadores técnicos). |
| **KD-011** | Migração do Schema SQLite | Alternativa 1 — Migração incremental aditiva `v12 → v13` | `APPROVED` | Implementada em `_apply_migration_v13` com backup automático e rollback transacional. |

---

## 3. Especificação Técnica da Migração SQLite v13 (Seção 17.2)

### 3.1. Tabelas Criadas em `_apply_migration_v13` ([database.py](file:///home/vini/Jellyfin%20livros/database.py#L1606-L2006))

1. **`concepts`**:
   - Colunas: `id TEXT PRIMARY KEY`, `user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE`, `name TEXT NOT NULL`, `normalized_name TEXT NOT NULL`, `description TEXT NOT NULL DEFAULT ''`, `tags_json TEXT NOT NULL DEFAULT '[]'`, `color TEXT NOT NULL DEFAULT '#d4a853'`, `is_archived INTEGER NOT NULL DEFAULT 0`, `created_at TIMESTAMP`, `updated_at TIMESTAMP`.
   - Constraints: `length(trim(name)) BETWEEN 1 AND 160`, `length(trim(normalized_name)) BETWEEN 1 AND 160`, `length(description) <= 10000`, `is_archived IN (0, 1)`.
2. **`reflections`**:
   - Colunas: `id TEXT PRIMARY KEY`, `user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE`, `title TEXT NOT NULL`, `body_markdown TEXT NOT NULL DEFAULT ''`, `primary_book_id TEXT NULL REFERENCES books(id) ON DELETE SET NULL`, `tags_json TEXT NOT NULL DEFAULT '[]'`, `is_pinned INTEGER NOT NULL DEFAULT 0`, `is_archived INTEGER NOT NULL DEFAULT 0`, `created_at TIMESTAMP`, `updated_at TIMESTAMP`.
   - Constraints: `length(trim(title)) BETWEEN 1 AND 300`, `length(body_markdown) <= 50000`, `is_pinned IN (0, 1)`, `is_archived IN (0, 1)`.
3. **`knowledge_links`**:
   - Colunas: `id TEXT PRIMARY KEY`, `user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE`, `source_type TEXT NOT NULL`, `source_id TEXT NOT NULL`, `target_type TEXT NOT NULL`, `target_id TEXT NOT NULL`, `relation_type TEXT NOT NULL DEFAULT 'relates_to'`, `rationale TEXT NOT NULL DEFAULT ''`, `created_at TIMESTAMP`, `updated_at TIMESTAMP`.
   - Constraints:
     - `source_type IN ('concept', 'reflection', 'highlight', 'note', 'book')`
     - `target_type IN ('concept', 'reflection', 'highlight', 'note', 'book')`
     - `relation_type IN ('complements', 'contradicts', 'exemplifies', 'grounds', 'develops', 'references', 'questions', 'relates_to')`
     - `length(rationale) <= 2000`
     - `CHECK (NOT (source_type = target_type AND source_id = target_id))`
4. **`knowledge_fts` (Virtual Table FTS5)**:
   - Colunas não indexadas de metadados: `entity_type UNINDEXED`, `entity_id UNINDEXED`, `user_id UNINDEXED`, `book_id UNINDEXED`.
   - Colunas textuais indexadas: `title`, `body`, `tags`.
   - Tokenizador: `tokenize='unicode61 remove_diacritics 2'`.

### 3.2. Índices Compostos Criados
- `uq_concepts_user_norm_name ON concepts(user_id, normalized_name)`
- `idx_concepts_user_updated ON concepts(user_id, is_archived, updated_at DESC, id ASC)`
- `idx_concepts_user_created ON concepts(user_id, created_at DESC, id ASC)`
- `idx_reflections_user_updated ON reflections(user_id, is_archived, is_pinned DESC, updated_at DESC, id ASC)`
- `idx_reflections_user_created ON reflections(user_id, created_at DESC, id ASC)`
- `idx_reflections_user_book ON reflections(user_id, primary_book_id)`
- `uq_knowledge_links_tuple ON knowledge_links(user_id, source_type, source_id, target_type, target_id, relation_type)`
- `idx_klinks_user_source ON knowledge_links(user_id, source_type, source_id, created_at DESC, id ASC)`
- `idx_klinks_user_target ON knowledge_links(user_id, target_type, target_id, created_at DESC, id ASC)`
- `idx_klinks_user_rel ON knowledge_links(user_id, relation_type, updated_at DESC, id ASC)`

### 3.3. Triggers de Integridade Polimórfica e Sincronização FTS5
- `trg_concepts_ai_fts`, `trg_concepts_au_fts`, `trg_concepts_ad_cleanup`
- `trg_reflections_ai_fts`, `trg_reflections_au_fts`, `trg_reflections_ad_cleanup`
- `trg_highlights_ai_fts`, `trg_highlights_au_fts`, `trg_highlights_ad_cleanup`
- `trg_notes_ai_fts`, `trg_notes_au_fts`, `trg_notes_ad_cleanup`
- `trg_klinks_ai_fts`, `trg_klinks_au_fts`, `trg_klinks_ad_fts`
- `trg_users_ad_knowledge_fts`

---

## 4. Serviços Implementados em `knowledge_service.py` (Seção 17.3)

O módulo [knowledge_service.py](file:///home/vini/Jellyfin%20livros/knowledge_service.py) expõe as seguintes funções públicas de domínio:

| Grupo | Função | Assinatura e Responsabilidade |
| :--- | :--- | :--- |
| **Normalização Unicode** | `normalize_unicode_nfc(value)` | Normaliza strings em Unicode NFC e rejeita bytes nulos (`\x00`). |
| **Normalização Unicode** | `normalize_concept_name(name)` | Limpa espaços e retorna `(name_nfc, normalized_name_casefold)`. |
| **Normalização Unicode** | `strip_diacritics_casefold(text)` | Decompõe em NFD, remove marcas diacríticas (`Mn`) e aplica `.casefold()`. |
| **Resolução e Autorização** | `resolve_knowledge_entity(entity_type, entity_id, user_id, *, cursor=None, for_link_endpoint=False)` | Valida usuário ativo, propriedade (`concept`/`reflection`), acessibilidade do livro (`is_missing = 0`, `source.is_active = 1`) e visibilidade (`user_id == viewer OR visibility == 'public'`). Em modo `for_link_endpoint=True`, mascara itens inacessíveis com `available=False`. |
| **Conceitos** | `create_concept(user_id, name, description="", tags=None, color=None, is_archived=False)` | Cria conceito privado com unicidade por `(user_id, normalized_name)`. |
| **Conceitos** | `get_concept(concept_id, user_id)` | Obtém conceito do próprio usuário com `links_count`. |
| **Conceitos** | `list_concepts(user_id, *, search=None, tag=None, include_archived=False, sort_by="updated_desc", limit=50, offset=0)` | Lista paginada com contagem de conexões em lote (zero N+1) e filtro insensível a acentos. |
| **Conceitos** | `update_concept(concept_id, user_id, *, name=None, description=None, tags=None, color=None, is_archived=None)` | Atualiza conceito preservando UUID e conexões. |
| **Conceitos** | `delete_concept(concept_id, user_id)` | Exclui conceito próprio e limpa seus vínculos e FTS. |
| **Conceitos** | `search_concepts_for_lookup(user_id, query="", *, limit=50)` | Busca rápida limitada a <= 50 itens para seletores assistidos. |
| **Reflexões** | `create_reflection(user_id, title, body_markdown="", *, primary_book_id=None, tags=None, is_pinned=False, is_archived=False)` | Cria reflexão autoral privada validando acessibilidade de `primary_book_id`. |
| **Reflexões** | `get_reflection(reflection_id, user_id)` | Obtém reflexão com `references_count`, `backlinks_count` e estado do livro principal. |
| **Reflexões** | `list_reflections(user_id, *, search=None, tag=None, primary_book_id=None, include_archived=False, sort_by="updated_desc", limit=50, offset=0)` | Lista paginada ordenada por fixação (`is_pinned DESC`) e data/título. |
| **Reflexões** | `update_reflection(reflection_id, user_id, ...)` | Atualiza título, `body_markdown`, livro principal, tags, fixação e arquivamento. |
| **Reflexões** | `delete_reflection(reflection_id, user_id)` | Exclui reflexão própria e limpa vínculos e FTS. |
| **Reflexões** | `get_reflection_references(reflection_id, user_id)` | Lista referências que saem da reflexão com resolução em tempo real. |
| **Reflexões** | `get_reflection_backlinks(reflection_id, user_id)` | Lista backlinks que apontam para a reflexão. |
| **Reflexões** | `search_reflections_for_lookup(user_id, query="", *, limit=50)` | Busca rápida limitada a <= 50 itens para seletores assistidos. |
| **Conexões e Backlinks** | `create_knowledge_link(user_id, source_type, source_id, target_type, target_id, relation_type="relates_to", rationale="")` | Cria vínculo validando autorização em tempo real dos dois extremos, bloqueando auto-loop e duplicatas. |
| **Conexões e Backlinks** | `get_knowledge_link(link_id, user_id)` | Obtém vínculo próprio com extremos hidratados em tempo de leitura. |
| **Conexões e Backlinks** | `update_knowledge_link(link_id, user_id, *, relation_type=None, rationale=None)` | Atualiza apenas `relation_type` e `rationale`; bloqueia troca de extremos ou proprietário. |
| **Conexões e Backlinks** | `delete_knowledge_link(link_id, user_id)` | Remove vínculo próprio. |
| **Conexões e Backlinks** | `list_outgoing_links(entity_type, entity_id, user_id, ...)` | Lista conexões de saída (`source`). |
| **Conexões e Backlinks** | `list_incoming_links(entity_type, entity_id, user_id, ...)` | Lista backlinks recebidos (`target`) na rede privada do usuário. |
| **Conexões e Backlinks** | `get_entity_connections_and_backlinks(entity_type, entity_id, user_id, ...)` | Retorna pacote consolidado de saídas e entradas de uma entidade. |
| **FTS5** | `sync_entity_fts(entity_type, entity_id)` / `delete_entity_fts(...)` | Sincroniza ou remove individualmente uma entidade em `knowledge_fts`. |
| **FTS5** | `rebuild_knowledge_fts()` | Reconstrói e otimiza integralmente `knowledge_fts` a partir das tabelas relacionais. |
| **FTS5** | `search_knowledge_candidates(user_id, query, *, entity_types=None, book_id=None, limit=50, offset=0)` | Localiza candidatos via `knowledge_fts MATCH` (BM25) e revalida 100% dos candidatos nas tabelas relacionais originais antes de retornar qualquer dado. |

---

## 5. Modelo de Segurança e Autorização em Tempo de Leitura (Seção 17.4)

1. **Isolamento Total por Usuário (`user_id`)**:
   - Conceitos (`concepts`), reflexões (`reflections`) e conexões (`knowledge_links`) possuem `user_id` obrigatório e todas as queries filtram por `WHERE user_id = ?`.
   - Acesso por outro usuário (inclusive administrador) levanta `KnowledgeNotFoundError` (`status_code = 404`), impedindo enumeração de UUIDs.
2. **Dupla Barreira na Resolução de Extremos (`resolve_knowledge_entity`)**:
   - Nenhum texto de anotação de terceiro é copiado para dentro de `knowledge_links`.
   - Em toda leitura de conexão ou backlink (`for_link_endpoint=True`), `resolve_knowledge_entity` verifica o estado atual do item:
     - Se uma anotação pública de terceiro tiver sua visibilidade alterada para `private` (`public → private`), for excluída, ou se seu livro ficar ausente (`is_missing = 1`) ou em fonte inativa (`library_sources.is_active = 0`), a função retorna exclusivamente o objeto sentinela `_unavailable_entity_sentinel` (`available = False`, `label = "Referência indisponível"`, todos os campos de conteúdo `None`), preservando intacta a justificativa autoral (`rationale`) de quem criou a conexão.
     - Se o autor original republicar a anotação (`private → public`), a resolução volta a funcionar imediatamente sem perda de vínculo.
3. **FTS5 como Localizador Não-Confiável para Autorização**:
   - Testado explicitamente em `test_deliberately_desynchronized_fts5_index_never_leaks_unauthorized_records`: mesmo injetando registros falsos ou obsoletos diretamente em `knowledge_fts` com o `user_id` da vítima, `search_knowledge_candidates` descarta 100% dos itens não autorizados durante a validação relacional.

---

## 6. Testes Executados e Verificação Inicial de Desempenho (Seção 17.5)

### 6.1. Resumo das Suítes Executadas

| Comando Executado | Resultado | Tempo |
| :--- | :--- | :---: |
| `./.venv/bin/python -m py_compile *.py scripts/*.py` | `Código de saída 0` (zero erros de sintaxe) | `~1,2s` |
| `./.venv/bin/pytest tests/test_v190_knowledge_*.py -v` | **`31 passed`** (0 falhas, 0 ignorados) | `14,2s` |
| `./.venv/bin/pytest --ignore=tests/e2e -q` | **`262 passed`** (231 legados + 31 novos, 0 falhas, 0 ignorados) | `41,68s` |
| `./.venv/bin/pytest tests/e2e -q` | **`40 passed`** (0 falhas, 0 ignorados) | `156,32s` |
| **Total Geral da Suíte** | **`302 passed`, `0 failed`, `0 skipped`** | **`198,00s`** |

### 6.2. Planos de Execução (`EXPLAIN QUERY PLAN`) e Escala Sintética (100, 1.000 e 10.000 Anotações)

Validado no teste automatizado `test_explain_query_plan_and_synthetic_scale_100_1000_10000` ([tests/test_v190_knowledge_fts.py](file:///home/vini/Jellyfin%20livros/tests/test_v190_knowledge_fts.py#L204-L320)):
- **Listagem de conceitos**: `SEARCH concepts USING INDEX idx_concepts_user_updated (user_id=? AND is_archived=?)` — zero *full scan*, zero `USE TEMP B-TREE FOR ORDER BY`.
- **Listagem de reflexões**: `SEARCH reflections USING INDEX idx_reflections_user_updated (user_id=? AND is_archived=?)` — zero *full scan*.
- **Conexões de saída (`outgoing_links`)**: `SEARCH knowledge_links USING INDEX idx_klinks_user_source (user_id=? AND source_type=? AND source_id=?)` — zero *full scan*.
- **Backlinks de entrada (`incoming_links`)**: `SEARCH knowledge_links USING INDEX idx_klinks_user_target (user_id=? AND target_type=? AND target_id=?)` — zero *full scan*.
- **Desempenho medido nos 3 degraus sintéticos (100, 1.000 e 10.000 notas indexadas em FTS5 + 50 backlinks hidratados)**:
  - Consulta e hidratação completa de 30 backlinks (`list_incoming_links`): `< 5 ms` nos 3 cenários.
  - Pesquisa FTS5 (`search_knowledge_candidates` com `unicode61 remove_diacritics 2` e validação relacional): `< 25 ms` mesmo com 10.000 notas indexadas.

---

## 7. Handoff Técnico para a Fase 2 — API REST e Segurança (Seções 17.6 e 19)

A Fase 1 deixa a camada de persistência (`database.py`) e a camada de domínio (`knowledge_service.py`) 100% prontas para que a **Fase 2** exponha os endpoints REST em `main.py`:

1. **Mapeamento Direto de Exceções de Domínio para Status HTTP em `main.py`**:
   - `ks.KnowledgeValidationError` → `HTTP 400 Bad Request`
   - `ks.KnowledgePermissionError` → `HTTP 403 Forbidden`
   - `ks.KnowledgeNotFoundError` → `HTTP 404 Not Found`
   - `ks.KnowledgeConflictError` → `HTTP 409 Conflict`
2. **Injeção de Dependências e Cabeçalhos Obrigatórios na Fase 2**:
   - Todas as rotas `/api/knowledge/*` e `/api/books/{book_id}/knowledge` deverão exigir `current_user: Dict[str, Any] = Depends(require_user)`.
   - Rotas de escrita (`POST`, `PUT`, `PATCH`, `DELETE`) já são protegidas automaticamente pelo `csrf_and_security_middleware` (`x-csrf-token`).
   - Todas as respostas de `/api/knowledge/*` deverão definir `Cache-Control: private, no-store`.
   - Todos os payloads de entrada deverão usar modelos Pydantic v2 com `model_config = ConfigDict(extra="forbid")`.
3. **Contratos de Serviço Prontos para Consumo Direto na Fase 2**:
   - `POST /api/knowledge/concepts` → `ks.create_concept(...)`
   - `GET /api/knowledge/concepts` → `ks.list_concepts(...)` e `ks.search_concepts_for_lookup(...)`
   - `GET /api/knowledge/concepts/{id}` → `ks.get_concept(...)` + `ks.get_entity_connections_and_backlinks("concept", id, user_id)`
   - `PUT /api/knowledge/concepts/{id}` → `ks.update_concept(...)`
   - `DELETE /api/knowledge/concepts/{id}` → `ks.delete_concept(...)`
   - `POST /api/knowledge/reflections` → `ks.create_reflection(...)`
   - `GET /api/knowledge/reflections` → `ks.list_reflections(...)` e `ks.search_reflections_for_lookup(...)`
   - `GET /api/knowledge/reflections/{id}` → `ks.get_reflection(...)` + `ks.get_reflection_references(...)` + `ks.get_reflection_backlinks(...)`
   - `PUT /api/knowledge/reflections/{id}` → `ks.update_reflection(...)`
   - `DELETE /api/knowledge/reflections/{id}` → `ks.delete_reflection(...)`
   - `POST /api/knowledge/links` → `ks.create_knowledge_link(...)`
   - `GET /api/knowledge/links` → `ks.get_entity_connections_and_backlinks(...)` / `ks.list_outgoing_links(...)` / `ks.list_incoming_links(...)`
   - `PUT /api/knowledge/links/{id}` → `ks.update_knowledge_link(...)`
   - `DELETE /api/knowledge/links/{id}` → `ks.delete_knowledge_link(...)`
   - `GET /api/knowledge/search` → `ks.search_knowledge_candidates(...)`
