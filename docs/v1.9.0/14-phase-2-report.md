# 14 — Relatório Consolidado da Fase 2 (Lectoria v1.9.0 — Conhecimento Conectado / Atlas)

> **Fase**: 2 (Revisão da Fundação, API REST e Segurança)  
> **Branch**: `feature/v1.9.0-connected-knowledge`  
> **Commit de Referência Base (`main`)**: `ddffdaa437a7e884c1a474926181371c047aefdf`  
> **Versão da Aplicação (`config.APP_VERSION`)**: `"1.8.0"` (preservada até a fase de release)  
> **Schema SQLite**: `v13` (`SCHEMA_VERSION = 13`)  
> **Seção Principal Oficial (`KD-010`)**: **Atlas**  
> **Data**: 2026-11-18  
> **Classificação Final da Fase 2**: **`PHASE_2_COMPLETE`**

---

## 1. Estado Inicial do Repositório

- **Branch ativa**: `feature/v1.9.0-connected-knowledge`.
- **Estado ao iniciar a Fase 2**: Fase 1 concluída (`PHASE_1_COMPLETE`), com `SCHEMA_VERSION = 13` em [database.py](file:///home/vini/Jellyfin%20livros/database.py), serviço de domínio em [knowledge_service.py](file:///home/vini/Jellyfin%20livros/knowledge_service.py), 31 testes da Fase 1 aprovados e 302 testes aprovados no total (262 unitários/integração + 40 E2E).
- Nenhuma operação Git proibida (`commit`, `merge`, `push`, `tag`) ou deploy havia sido executada.

---

## 2. Resultado da Revisão Preliminar da Fase 1 (Fase 2.0)

A revisão técnica preliminar completa foi documentada em [12-phase-1-technical-review.md](file:///home/vini/Jellyfin%20livros/docs/v1.9.0/12-phase-1-technical-review.md). Resumo dos 6 eixos auditados:

1. **Autorização por Livro (4.1)**: Confirmado que na Lectoria a autorização de acesso a livros baseia-se em usuário ativo (`users.is_active == 1`), livro existente em `books`, arquivo disponível no disco (`books.is_missing == 0`) e fonte de biblioteca ativa (`library_sources.is_active == 1`), implementada canonicamente por `annotations_service._verify_book_access`.
2. **Pesquisa FTS5 e Vazamentos Indiretos (4.2)**: Auditada a função `search_knowledge_candidates`, incluindo paginação, contagens, ranking `bm25()` e pré-filtragem por usuário.
3. **Referências Indisponíveis (4.3)**: Auditado `_unavailable_entity_sentinel` e todas as funções de hidratação de conexões.
4. **Atomicidade da Migração `v12 → v13` (4.4)**: Confirmado que `_apply_migration_v13` executa exclusivamente `cursor.execute(...)` individuais dentro de `BEGIN ... COMMIT / ROLLBACK`, precedido por backup automático via SQLite Online Backup API.
5. **Compatibilidade com Docker (4.5)**: Inspecionado o [Dockerfile](file:///home/vini/Jellyfin%20livros/Dockerfile) (`python:3.12-slim` Debian Bookworm com `sqlite3`, onde FTS5 e `unicode61 remove_diacritics 2` são nativos e já utilizados por `books_fts` em produção).
6. **Cobertura dos Testes (4.6)**: Construída matriz completa relacionando todos os requisitos críticos aos testes existentes e adicionados 2 novos testes de regressão na fundação.

---

## 3. Problemas e Oportunidades de Hardening Encontrados na Revisão (Fase 2.0)

1. **A-01 (Autorização por Livro)**: `_check_book_accessible_row` em `knowledge_service.py` executava uma query SQL própria equivalente a `_verify_book_access` em vez de chamar diretamente `annotations_service._verify_book_access`.
2. **A-02 (Exaustão Potencial de Candidatos FTS5 por Outros Usuários)**: Em `search_knowledge_candidates`, a query `SELECT ... FROM knowledge_fts WHERE knowledge_fts MATCH ? LIMIT 500` não filtrava `user_id` na cláusula `WHERE` do FTS5, o que permitiria que centenas de conceitos/reflexões privados de outros usuários consumissem slots do `LIMIT` interno antes da filtragem relacional em Python.
3. **A-03 (Desempate Determinístico no Ranking FTS5 e `has_more`)**: Faltava critério de desempate secundário explícito após `bm25(knowledge_fts)` e o campo booleano `has_more` nas respostas paginadas de listagens.
4. **A-04 (Padronização Estrita de Chaves em `_unavailable_entity_sentinel`)**: `_unavailable_entity_sentinel` omitia algumas chaves opcionais (`author_display_name`, `book_format`, `color`, `tags`, `is_own`, `user_id`, `created_at`, `updated_at`), que convém manter explícitas como `None` / `[]` para serialização Pydantic uniforme.

---

## 4. Correções Realizadas na Fundação (`knowledge_service.py`)

1. **Delegação Canônica a `annotations_service._verify_book_access`**:
   - `_check_book_accessible_row(cursor, book_id, user_id)` agora invoca obrigatoriamente `annotations_service._verify_book_access(cursor, clean_bid, user_id=user_id)`.
   - Adicionado o teste `test_own_annotation_subject_to_book_authorization_on_source_deactivation` provando que até mesmo anotações próprias ficam indisponíveis (`available = False`) e bloqueadas para novos vínculos (`404`) quando a fonte do livro é desativada.
2. **Pré-filtragem por `user_id` na Query FTS5 e Desempate Determinístico**:
   - A consulta em `knowledge_fts` agora executa `WHERE knowledge_fts MATCH ? AND (user_id = ? OR entity_type IN ('highlight', 'note')) ORDER BY rank ASC, entity_type ASC, entity_id ASC LIMIT 1000`.
   - Scores brutos de `bm25()` jamais são expostos na resposta.
   - Adicionado o teste `test_fts5_private_concepts_of_other_users_do_not_exhaust_candidate_pagination`.
3. **Adição de `has_more`, `list_knowledge_links` e `lookup_referencable_entities`**:
   - Todas as listagens paginadas (`list_concepts`, `list_reflections`, `list_knowledge_links`, `get_entity_connections_and_backlinks`, `search_knowledge_candidates`) incluem `has_more` e validação estrita de parâmetros de ordenação.
   - Implementada `lookup_referencable_entities` para localização assistida nos 5 tipos de entidade aprovados (`concept`, `reflection`, `highlight`, `note`, `book`).

---

## 5. Arquitetura REST Adotada

Seguindo o padrão arquitetural consolidado da Lectoria (onde todas as rotas REST residem em [main.py](file:///home/vini/Jellyfin%20livros/main.py) e delegam 100% das regras de negócio e SQL aos módulos `*_service.py`), a camada HTTP de `/api/knowledge/*` foi implementada em `main.py` com separação estrita de responsabilidades:

- **Validação HTTP e Contratos**: Modelos Pydantic v2 com `ConfigDict(extra="forbid")` e `Query(...)` tipados com limites explícitos.
- **Autenticação e Sessão**: `require_user(request)` validando sessão ativa e `users.is_active == 1`.
- **Proteção CSRF**: `csrf_and_security_middleware` exigindo cabeçalho `x-csrf-token` e bloqueando `?csrf_token=...` na URL em `/api/knowledge/*`.
- **Serviço de Domínio e Autorização**: Delegação integral a [knowledge_service.py](file:///home/vini/Jellyfin%20livros/knowledge_service.py), sem uma única linha de SQL na camada HTTP.
- **Mapeamento de Erros**: Função centralizada `_handle_knowledge_error(exc)` traduzindo exceções de domínio para `400`, `403`, `404` e `409` sem expor mensagens internas do SQLite ou stack traces.

---

## 6. Inventário de Endpoints Implementados

| # | Método | Endpoint | Status HTTP de Sucesso | Função de Serviço Delegada |
| :-: | :---: | :--- | :---: | :--- |
| 1 | `GET` | `/api/knowledge/concepts` | `200 OK` | `knowledge_service.list_concepts` |
| 2 | `POST` | `/api/knowledge/concepts` | `201 Created` | `knowledge_service.create_concept` |
| 3 | `GET` | `/api/knowledge/concepts/{concept_id}` | `200 OK` | `knowledge_service.get_concept` |
| 4 | `PATCH` | `/api/knowledge/concepts/{concept_id}` | `200 OK` | `knowledge_service.update_concept` |
| 5 | `DELETE` | `/api/knowledge/concepts/{concept_id}` | `200 OK` | `knowledge_service.delete_concept` |
| 6 | `GET` | `/api/knowledge/reflections` | `200 OK` | `knowledge_service.list_reflections` |
| 7 | `POST` | `/api/knowledge/reflections` | `201 Created` | `knowledge_service.create_reflection` |
| 8 | `GET` | `/api/knowledge/reflections/{reflection_id}` | `200 OK` | `get_reflection` + `get_reflection_references` + `get_reflection_backlinks` |
| 9 | `PATCH` | `/api/knowledge/reflections/{reflection_id}` | `200 OK` | `knowledge_service.update_reflection` |
| 10 | `DELETE` | `/api/knowledge/reflections/{reflection_id}` | `200 OK` | `knowledge_service.delete_reflection` |
| 11 | `GET` | `/api/knowledge/links` | `200 OK` | `knowledge_service.list_knowledge_links` |
| 12 | `POST` | `/api/knowledge/links` | `201 Created` | `knowledge_service.create_knowledge_link` |
| 13 | `GET` | `/api/knowledge/links/{link_id}` | `200 OK` | `knowledge_service.get_knowledge_link` |
| 14 | `PATCH` | `/api/knowledge/links/{link_id}` | `200 OK` | `knowledge_service.update_knowledge_link` |
| 15 | `DELETE` | `/api/knowledge/links/{link_id}` | `200 OK` | `knowledge_service.delete_knowledge_link` |
| 16 | `GET` | `/api/knowledge/entities/{entity_type}/{entity_id}/connections` | `200 OK` | `knowledge_service.get_entity_connections_and_backlinks` |
| 17 | `GET` | `/api/knowledge/entities/{entity_type}/{entity_id}/backlinks` | `200 OK` | `knowledge_service.get_entity_connections_and_backlinks(direction="incoming")` |
| 18 | `GET` | `/api/knowledge/lookup` | `200 OK` | `knowledge_service.lookup_referencable_entities` |

---

## 7. Contratos de Validação Pydantic v2

Implementados em [main.py](file:///home/vini/Jellyfin%20livros/main.py#L841-L1121):
- `ConceptCreatePayload`, `ConceptUpdatePayload`, `ConceptResponseModel`, `ConceptListResponseModel`
- `ReflectionCreatePayload`, `ReflectionUpdatePayload`, `ReflectionResponseModel`, `ReflectionListResponseModel`
- `KnowledgeEntityEndpointModel`
- `KnowledgeLinkCreatePayload`, `KnowledgeLinkUpdatePayload`, `KnowledgeLinkResponseModel`, `KnowledgeLinkListResponseModel`
- `KnowledgeEntityConnectionsResponseModel`
- `KnowledgeLookupItemModel`, `KnowledgeLookupResponseModel`

Todos os modelos de entrada utilizam `model_config = ConfigDict(extra="forbid")` e compartilham exatamente as mesmas constantes de limite definidas em `knowledge_service.py` (`MAX_CONCEPT_NAME_LEN = 160`, `MAX_CONCEPT_DESC_LEN = 10000`, `MAX_REFLECTION_TITLE_LEN = 300`, `MAX_REFLECTION_BODY_LEN = 50000`, `MAX_LINK_RATIONALE_LEN = 2000`, `MAX_TAGS_COUNT = 30`, `MAX_TAG_LEN = 64`, `DEFAULT_PAGE_LIMIT = 50`, `MAX_PAGE_LIMIT = 200`, `AUX_SEARCH_LIMIT = 50`, `MAX_SEARCH_QUERY_LEN = 300`).

---

## 8. Garantias de Autorização e Anti-IDOR

1. **Identidade Obtida Exclusivamente da Sessão**: `user_id` é sempre extraído de `user = require_user(request)` (`user["id"]`). Qualquer tentativa de enviar `user_id` ou `owner_user_id` no payload JSON é rejeitada com `HTTP 422`.
2. **Isolamento Estrito de Proprietário (Incluindo Administradores)**: Conceitos, reflexões e conexões são filtrados por `WHERE user_id = ?`. Administradores (`role == 'admin'`) não possuem acesso implícito às redes privadas de outros usuários e recebem `HTTP 404 Not Found`.
3. **Validação Prévia da Entidade Focal em Conexões e Backlinks**: Ao consultar `/api/knowledge/entities/{entity_type}/{entity_id}/connections`, `/backlinks` ou `GET /api/knowledge/links?entity_type=...&entity_id=...`, o serviço valida primeiro se o usuário tem permissão de leitura sobre `{entity_type}/{entity_id}`; caso contrário, retorna `HTTP 404 Not Found` (anti-enumeração).

---

## 9. Estratégia de CSRF

- Todas as rotas mutáveis (`POST`, `PATCH`, `DELETE`) em `/api/knowledge/*` exigem o cabeçalho `x-csrf-token` correspondente ao cookie `lectoria_csrf`.
- No middleware `csrf_and_security_middleware` ([main.py](file:///home/vini/Jellyfin%20livros/main.py#L266-L290)), qualquer requisição mutável para `/api/knowledge/*` que tente fornecer `csrf_token` via query string (`?csrf_token=...`) é explicitamente rejeitada com `HTTP 403 Forbidden`.
- O fallback legado para o salvamento de progresso do leitor (`/api/books/{id}/progress`) permanece inalterado para não afetar os leitores nesta fase.

---

## 10. Estratégia de Cache

- O middleware HTTP e todos os handlers de `/api/knowledge/*` aplicam obrigatoriamente `Cache-Control: private, no-store` tanto nas respostas de sucesso (`200`, `201`) quanto nas respostas de erro (`400`, `401`, `403`, `404`, `409`, `422`).

---

## 11. Comportamento de Referências Indisponíveis

- Quando um destaque ou nota pública de terceiro referenciada por uma conexão privada tem sua visibilidade revogada (`public → private`), é excluída fisicamente, ou pertence a um livro cuja fonte foi desativada (`is_active = 0`) ou cujo arquivo ficou ausente (`is_missing = 1`), a conexão **não falha** nem é destruída:
  - O extremo correspondente é retornado com `available: false`, `status: "unavailable"`, `label: "Referência indisponível"` e todos os metadados protegidos (`title`, `excerpt`, `author_display_name`, `user_id`, `book_id`, `book_title`, `page`, `epub_cfi`, `chapter_label`, `visibility`) definidos como `null`.
  - A justificativa autoral (`rationale`) escrita pelo dono da conexão permanece 100% preservada e acessível.
  - Se o autor original republicar a anotação (`private → public`) ou a fonte do livro for reativada, a resolução volta a exibir os metadados automaticamente.

---

## 12. Testes Executados e Contabilização Detalhada (Seção 18)

### 12.1. Novas Suítes Criadas na Fase 2
1. [tests/test_v190_knowledge_concepts_api.py](file:///home/vini/Jellyfin%20livros/tests/test_v190_knowledge_concepts_api.py) (3 testes abrangentes de CRUD, Unicode, paginação, filtros e semântica de `null` vs. vazio)
2. [tests/test_v190_knowledge_reflections_api.py](file:///home/vini/Jellyfin%20livros/tests/test_v190_knowledge_reflections_api.py) (2 testes abrangentes de CRUD, preservação de Markdown bruto, autorização de livro na escrita/leitura e paginação)
3. [tests/test_v190_knowledge_links_api.py](file:///home/vini/Jellyfin%20livros/tests/test_v190_knowledge_links_api.py) (2 testes abrangentes dos 8 tipos de relação, padrão `relates_to`, imutabilidade dos extremos e conflitos)
4. [tests/test_v190_knowledge_backlinks_api.py](file:///home/vini/Jellyfin%20livros/tests/test_v190_knowledge_backlinks_api.py) (2 testes cobrindo o ciclo completo de 9 passos de revogação/republicação/exclusão via API e filtro de direções)
5. [tests/test_v190_knowledge_lookup_api.py](file:///home/vini/Jellyfin%20livros/tests/test_v190_knowledge_lookup_api.py) (1 teste cobrindo localização assistida nos 5 tipos de entidade, isolamento, filtros e limites)
6. [tests/test_v190_knowledge_api_security.py](file:///home/vini/Jellyfin%20livros/tests/test_v190_knowledge_api_security.py) (4 testes cobrindo autenticação em todas as 18 rotas, usuário inativo, CSRF em cabeçalho vs. URL em todas as 9 rotas mutáveis, isolamento multiusuário + admin e payloads adversariais/SQLi)
7. Expansão de [tests/test_v190_knowledge_authorization.py](file:///home/vini/Jellyfin%20livros/tests/test_v190_knowledge_authorization.py) (+2 testes adicionados durante a Revisão Fase 2.0).

---

## 13. Resultados Efetivamente Obtidos

| Métrica | Contagem / Resultado |
| :--- | :--- |
| **Testes anteriores (Referência ao final da Fase 1)** | **302** (262 unitários/integração + 40 E2E) |
| **Testes adicionados durante a Revisão Preliminar (Fase 2.0)** | **+2** (`test_v190_knowledge_authorization.py`) |
| **Testes adicionados pela API REST (Fase 2)** | **+14** (em 6 novos arquivos `test_v190_knowledge_*_api.py`) |
| **Total de testes unitários e de integração executados** | **`278 passed, 0 failed, 0 skipped`** (`47,10s`, 3 warnings de depreciação do Starlette TestClient) |
| **Total de testes E2E Playwright executados** | **`40 passed, 0 failed, 0 skipped`** (`~156s`, 1 warning do Starlette TestClient) |
| **Total Geral Executado na Fase 2** | **`318 passed, 0 failed, 0 skipped`** |

---

## 14. Limitações Ambientais Registradas

1. **Verificação de Build Docker Local (Seção 4.5)**: O binário `docker` não está instalado no ambiente host local (`docker: comando não encontrado`), impossibilitando a execução de `docker build` local nesta sessão. A compatibilidade do SQLite FTS5 com a imagem `python:3.12-slim` (Debian Bookworm) foi verificada por inspeção do `Dockerfile` e pelo funcionamento comprovado de `books_fts` na imagem de produção.
2. **Risco Estatístico Residual de IDF Global no `bm25()` do FTS5 (Seção 4.2)**: Documentado em [12-phase-1-technical-review.md](file:///home/vini/Jellyfin%20livros/docs/v1.9.0/12-phase-1-technical-review.md); mitigado pela pré-filtragem de `user_id` na cláusula `WHERE` do FTS5, pela não exposição de scores numéricos brutos e pelo desempate determinístico.

---

## 15. Arquivos Modificados e Criados na Fase 2

### Arquivos Modificados
- [knowledge_service.py](file:///home/vini/Jellyfin%20livros/knowledge_service.py): Reutilização canônica de `annotations_service._verify_book_access`, pré-filtragem de `user_id` no FTS5, adição de `has_more`, `list_knowledge_links` e `lookup_referencable_entities`.
- [main.py](file:///home/vini/Jellyfin%20livros/main.py): Modelos Pydantic v2 (`ConfigDict(extra="forbid")`), proteção CSRF sem token em URL para `/api/knowledge/*`, cabeçalho `Cache-Control: private, no-store` e os 18 endpoints REST `/api/knowledge/*`.
- [tests/test_v190_knowledge_authorization.py](file:///home/vini/Jellyfin%20livros/tests/test_v190_knowledge_authorization.py): +2 testes da Revisão Preliminar Fase 2.0.
- [docs/v1.9.0/08-roadmap.md](file:///home/vini/Jellyfin%20livros/docs/v1.9.0/08-roadmap.md): Atualizado com a conclusão validada da Fase 2.

### Arquivos Novos Criados na Fase 2
- [docs/v1.9.0/12-phase-1-technical-review.md](file:///home/vini/Jellyfin%20livros/docs/v1.9.0/12-phase-1-technical-review.md)
- [docs/v1.9.0/13-knowledge-api-contracts.md](file:///home/vini/Jellyfin%20livros/docs/v1.9.0/13-knowledge-api-contracts.md)
- [docs/v1.9.0/14-phase-2-report.md](file:///home/vini/Jellyfin%20livros/docs/v1.9.0/14-phase-2-report.md)
- [tests/test_v190_knowledge_concepts_api.py](file:///home/vini/Jellyfin%20livros/tests/test_v190_knowledge_concepts_api.py)
- [tests/test_v190_knowledge_reflections_api.py](file:///home/vini/Jellyfin%20livros/tests/test_v190_knowledge_reflections_api.py)
- [tests/test_v190_knowledge_links_api.py](file:///home/vini/Jellyfin%20livros/tests/test_v190_knowledge_links_api.py)
- [tests/test_v190_knowledge_backlinks_api.py](file:///home/vini/Jellyfin%20livros/tests/test_v190_knowledge_backlinks_api.py)
- [tests/test_v190_knowledge_lookup_api.py](file:///home/vini/Jellyfin%20livros/tests/test_v190_knowledge_lookup_api.py)
- [tests/test_v190_knowledge_api_security.py](file:///home/vini/Jellyfin%20livros/tests/test_v190_knowledge_api_security.py)

---

## 16. Estado Final do Git

- Nenhum commit, merge, push, tag ou deploy foi realizado.
- Todos os arquivos modificados e não rastreados pertencem estritamente às Fases 0, 1 e 2 da v1.9.0 na branch `feature/v1.9.0-connected-knowledge`.

---

## 17. Handoff Técnico para a Fase 3 — Integração com os Leitores PDF e EPUB (Seção 22)

Esta seção prepara exclusivamente a especificação técnica para a futura **Fase 3 (Integração de Conceitos, Conexões e Backlinks nos Leitores PDF e EPUB)**, sem qualquer implementação antecipada no frontend:

1. **Endpoints Consumíveis pelo Leitor (`static/js/reader.js`)**:
   - **Localização rápida de conceitos e alvos**: `GET /api/knowledge/lookup?q=<termo>&entity_type=concept&limit=20` (ou sem `entity_type` para buscar conceitos, notas, destaques, livros e reflexões).
   - **Criação rápida de conceito durante a leitura**: `POST /api/knowledge/concepts` com `{"name": "...", "description": "...", "color": "#d4a853"}` e cabeçalho `X-CSRF-Token`.
   - **Associação de destaque/nota a um conceito ou outra anotação**: `POST /api/knowledge/links` com `{"source_type": "highlight"|"note", "source_id": "<uuid>", "target_type": "concept"|"highlight"|"note"|"book"|"reflection", "target_id": "<id>", "relation_type": "relates_to"|..., "rationale": "..."}`.
   - **Consulta de conceitos associados, conexões e backlinks ao abrir um popover de destaque/nota**: `GET /api/knowledge/entities/{highlight|note}/{id}/connections?direction=both&limit=50`.
   - **Remoção de vínculo**: `DELETE /api/knowledge/links/{link_id}` com cabeçalho `X-CSRF-Token`.
2. **Fluxo de Associação de Conceitos no Leitor**:
   - No `#highlight-popover`, `#page-note-popover` ou `#modal-note-editor`, o usuário aciona `+ Conceito`, pesquisa via `GET /api/knowledge/lookup?entity_type=concept&q=...` (com debounce de 200 ms e `AbortController`), seleciona um conceito existente (ou cria um novo via `POST /api/knowledge/concepts`) e registra o vínculo via `POST /api/knowledge/links` (`relation_type: "relates_to"`).
3. **Fluxo de Criação de Conexões Semânticas e Localização de Alvos**:
   - O usuário aciona `+ Conectar` em um destaque ou nota, escolhe um dos 8 tipos de relação (`complements`, `contradicts`, `exemplifies`, `grounds`, `develops`, `references`, `questions`, `relates_to`), busca o alvo via `GET /api/knowledge/lookup?q=...`, preenche opcionalmente o campo `rationale` (máx. 2.000 caracteres) e envia `POST /api/knowledge/links` protegido por `withSubmitLock`.
4. **Consulta de Backlinks e Comportamento após Revogação**:
   - Ao exibir as conexões de uma anotação (`outgoing_links` e `incoming_backlinks`), o leitor verifica `link.counterpart.available`:
     - Se `available === true`: exibe o rótulo (`relation_label`), o título/trecho escapado com `escapeHtml()`, o `rationale` e link clicável para `/reader/{book_id}#page={page}` (PDF) ou `/reader/{book_id}#cfi={encodeURIComponent(epub_cfi)}` (EPUB).
     - Se `available === false`: exibe o selo neutro *"Referência indisponível"* sem link de navegação, preservando a exibição do `rationale` autoral e o botão para excluir a conexão órfã se o usuário desejar.
5. **Tratamento de Erros, Acessibilidade e Carregamento Assíncrono**:
   - Tratar `409 Conflict` exibindo toast informativo (*"Este vínculo ou conceito já existe"*), `404 Not Found` (*"Item não encontrado ou indisponível"*) e `403` (*"Sessão ou token CSRF expirado"*).
   - Carregar conexões do popover sob demanda (*lazy load* ao abrir o popover) com `AbortController` para cancelar requisições se o leitor fechar o popover ou mudar de página rapidamente.
   - Garantir navegação por teclado (`Escape` fecha modal/popover, `aria-label` nos chips de conceitos e botões de ação) e zero overflow horizontal entre `320px` e `1920px`.
