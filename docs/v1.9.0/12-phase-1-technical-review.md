# 12 — Revisão Técnica Preliminar da Fundação da Fase 1 (Fase 2.0 — Lectoria v1.9.0)

> **Fase**: 2.0 (Auditoria Preliminar da Fundação da Fase 1 antes da API REST)  
> **Branch**: `feature/v1.9.0-connected-knowledge`  
> **Schema SQLite**: `v13` (`SCHEMA_VERSION = 13`)  
> **Seção Principal Oficial (`KD-010`)**: **Atlas**  
> **Data**: 2026-11-18  
> **Resultado Global da Auditoria Preliminar**: **Fundação íntegra, segura e apta para a implementação da API REST da Fase 2** (nenhum bloqueante identificado; melhorias preventivas aplicadas e testadas).

---

## 1. Objetivo da Revisão Preliminar (Fase 2.0)

Antes de expor o domínio de Conhecimento Conectado (**Atlas**) por meio dos endpoints HTTP `/api/knowledge/*`, foi conduzida uma auditoria crítica sobre o código produzido na Fase 1 ([database.py](file:///home/vini/Jellyfin%20livros/database.py), [knowledge_service.py](file:///home/vini/Jellyfin%20livros/knowledge_service.py) e as 6 suítes `tests/test_v190_knowledge_*.py`), avaliando os seis eixos determinados na Seção 4:

1. **4.1. Autorização por livro e reutilização do mecanismo canônico**
2. **4.2. Pesquisa FTS5, paginação, scores `bm25()` e vazamentos indiretos**
3. **4.3. Mascaramento de referências indisponíveis (`_unavailable_entity_sentinel`)**
4. **4.4. Atomicidade da migração `v12 → v13` e integridade transacional**
5. **4.5. Compatibilidade do SQLite FTS5 com a imagem Docker oficial (`Dockerfile`)**
6. **4.6. Matriz de cobertura de requisitos críticos da Fase 1**

Cada descoberta é classificada estritamente em uma das quatro categorias exigidas:
- **`Verificada e adequada`**
- **`Corrigida nesta fase`**
- **`Limitação documentada`**
- **`Bloqueante`**

---

## 2. Análise Detalhada por Eixo da Auditoria

### 2.1. Autorização por Livro e Reutilização Canônica (Seção 4.1)

#### Inspeção do Código Existente
Foram inspecionados todos os módulos que controlam acesso a livros na Lectoria:
- [library.py](file:///home/vini/Jellyfin%20livros/library.py#L889-L959) (`get_book`, `get_books`, `resolve_book_path`, `resolve_book_file_for_user`): na arquitetura da Lectoria, o acervo bibliográfico pertence às fontes cadastradas (`library_sources`). Não existe tabela de ACL de livros por usuário; um livro está autorizado para leitura e anotação quando:
  1. O usuário autenticado existe e está ativo (`users.is_active == 1`);
  2. O livro existe na tabela `books`;
  3. O livro não está marcado como ausente do disco (`books.is_missing == 0`);
  4. A fonte do livro está ativa (`library_sources.is_active == 1`).
- [annotations_service.py](file:///home/vini/Jellyfin%20livros/annotations_service.py#L157-L189) (`_verify_book_access`): é o verificador canônico utilizado por todos os serviços de destaques, notas, desenhos e marcadores da v1.8.0.

#### Descoberta A-01: Reutilização Direta de `_verify_book_access` em `knowledge_service.py`
- **Observação**: Na Fase 1, `knowledge_service._check_book_accessible_row` executava uma query SQL própria equivalente a `_verify_book_access`, em vez de invocar diretamente `annotations_service._verify_book_access`.
- **Ação Realizada**: Refatorado `_check_book_accessible_row(cursor, book_id, user_id)` em [knowledge_service.py](file:///home/vini/Jellyfin%20livros/knowledge_service.py#L307-L343) para delegar obrigatoriamente ao mecanismo canônico `annotations_service._verify_book_access(cursor, clean_bid, user_id=user_id)`, eliminando qualquer duplicação de regra de autorização.
- **Verificação sobre Anotações Próprias em Livros Desativados**: Confirmado e testado (`test_own_annotation_subject_to_book_authorization_on_source_deactivation` em [tests/test_v190_knowledge_authorization.py](file:///home/vini/Jellyfin%20livros/tests/test_v190_knowledge_authorization.py#L333-L384)) que até mesmo uma anotação própria (`highlight.user_id == user_id` ou `note.user_id == user_id`) permanece sujeita à autorização sobre o livro associado: se a fonte do livro for desativada (`is_active = 0`) ou o livro ficar ausente (`is_missing = 1`), novas conexões para aquela anotação são bloqueadas (`404`) e conexões existentes a mascaram como `available = False`, preservando o conceito e a justificativa autoral do leitor.
- **Classificação**: **`Corrigida nesta fase`**

---

### 2.2. Pesquisa FTS5, Scores `bm25()`, Paginação e Vazamentos Indiretos (Seção 4.2)

#### Inspeção de `search_knowledge_candidates`
Foram auditados os seguintes aspectos em `knowledge_service.search_knowledge_candidates`:

1. **Pré-filtragem por `user_id` no FTS5 para Impedir Exaustão de Paginação**:
   - **Observação (Descoberta A-02)**: Na Fase 1, a query FTS5 selecionava `WHERE knowledge_fts MATCH ? LIMIT 500` sem restringir `user_id` na cláusula `WHERE` do FTS5, descartando os conceitos/reflexões privados de outros usuários apenas no loop Python subsequente. Em uma instância multiusuário onde outro leitor tivesse mais de 500 conceitos privados contendo o termo pesquisado, os candidatos privados alheios poderiam consumir o `LIMIT 500` interno e ocultar resultados legítimos do usuário autenticado.
   - **Ação Realizada**: Adicionada restrição direta na consulta FTS5:
     ```sql
     SELECT entity_type, entity_id, bm25(knowledge_fts) AS rank
     FROM knowledge_fts
     WHERE knowledge_fts MATCH ?
       AND (user_id = ? OR entity_type IN ('highlight', 'note'))
     ORDER BY rank ASC, entity_type ASC, entity_id ASC
     LIMIT 1000;
     ```
     Dessa forma, conceitos, reflexões e conexões privadas de outros usuários são descartados já no motor FTS5 antes do `LIMIT`, jamais consumindo slots de candidatos do usuário autenticado. Validado pelo novo teste `test_fts5_private_concepts_of_other_users_do_not_exhaust_candidate_pagination`.
   - **Classificação**: **`Corrigida nesta fase`**

2. **Não Exposição de Scores Brutos `bm25()` e Risco Residual de IDF Global**:
   - **Análise Técnica (Descoberta A-03)**: No SQLite FTS5, a função `bm25(knowledge_fts)` calcula a relevância usando a frequência inversa de documentos (IDF) global da tabela virtual `knowledge_fts`.
   - **Mitigação Aplicada**:
     - O valor numérico bruto `rank` (`bm25`) **nunca** é incluído nos dicionários retornados por `search_knowledge_candidates` nem exposto pela API REST.
     - Adicionado critério de desempate determinístico `ORDER BY rank ASC, entity_type ASC, entity_id ASC` e controle de tamanho máximo de consulta (`MAX_SEARCH_QUERY_LEN = 300`).
     - Adicionado campo explícito `"has_more": (off + len(page_items)) < total` no retorno paginado.
   - **Risco Residual Documentado**: Como `knowledge_fts` é uma tabela virtual única compartilhada na instância SQLite, em buscas com múltiplos termos ($t_1 \text{ AND } t_2$), a frequência global de documentos (IDF) de $t_1$ vs. $t_2$ na instância influencia o peso relativo entre os dois termos na ordenação `bm25` dos próprios documentos do usuário. Como o score numérico nunca é exposto e nenhum documento não autorizado é retornado ou contabilizado em `total`, esse efeito estatístico residual de segunda ordem na ordenação relativa de termos compostos é inócuo na prática; se no futuro (Fase 6) for desejado isolamento estatístico absoluto de IDF, a ordenação poderá ser combinada com re-ranking local em Python sobre o texto hidratado dos candidatos autorizados.
   - **Classificação**: **`Corrigida nesta fase`** (com risco estatístico residual de IDF documentado).

---

### 2.3. Referências Indisponíveis e Prevenção de Vazamento de Metadados (Seção 4.3)

#### Inspeção de `_unavailable_entity_sentinel` e `_hydrate_link_row`
- **Observação (Descoberta A-04)**: `_unavailable_entity_sentinel(entity_type, entity_id)` retorna `available = False`, `status = "unavailable"` e `label = "Referência indisponível"`, mantendo apenas `type` e `id` (necessários para integridade referencial e para que o proprietário da conexão possa identificar ou excluir a aresta órfã) e o campo `rationale` pertencente ao próprio dono da conexão.
- **Ação Realizada**: Padronizado o dicionário retornado por `_unavailable_entity_sentinel` em [knowledge_service.py](file:///home/vini/Jellyfin%20livros/knowledge_service.py#L346-L375) para conter explicitamente todas as chaves do schema de entidade com valores neutros (`title: None`, `excerpt: None`, `author_display_name: None`, `user_id: None`, `is_own: None`, `book_id: None`, `book_title: None`, `book_format: None`, `page: None`, `epub_cfi: None`, `chapter_label: None`, `visibility: None`, `color: None`, `tags: []`, `created_at: None`, `updated_at: None`).
- **Garantias Verificadas**:
  - Não expõe título protegido, conteúdo, autor, página, CFI, estado atual de publicação (`private` vs. excluído) nem motivo específico da indisponibilidade (impossível distinguir se o item foi tornado privado, excluído ou se seu livro teve a fonte desativada).
  - Nunca copia texto de terceiros para registros privados.
- **Classificação**: **`Verificada e adequada`** (com padronização completa de campos `None` para serialização Pydantic estrita).

---

### 2.4. Atomicidade da Migração `v12 → v13` (Seção 4.4)

#### Inspeção de `database.init_db()` e `_apply_migration_v13()`
- **Descoberta A-05**:
  - Em [database.py](file:///home/vini/Jellyfin%20livros/database.py#L102-L165), `init_db()` inicia explicitamente `conn.execute("BEGIN;")` após criar o backup pré-migração `lectoria_pre_migration_v12_to_v13.db.bak` via SQLite Online Backup API (`conn.backup(bak_conn)`).
  - Dentro de `_apply_migration_v13(cursor)`, todas as 34 instruções DDL/DML utilizam chamadas individuais `cursor.execute(...)` (sem `executescript()`, que emitiria `COMMIT` implícito no driver `sqlite3` do Python).
  - Em caso de qualquer exceção em `_apply_migration_v13`, o bloco `except Exception: conn.rollback(); raise` reverte 100% das tabelas, índices, triggers e a inserção em `schema_migrations`, mantendo o banco na versão 12 com `PRAGMA integrity_check == 'ok'`.
  - Validado pelos testes automatizados em [tests/test_v190_knowledge_migration.py](file:///home/vini/Jellyfin%20livros/tests/test_v190_knowledge_migration.py).
- **Classificação**: **`Verificada e adequada`**

---

### 2.5. Compatibilidade com Docker e SQLite FTS5 (Seção 4.5)

#### Inspeção do `Dockerfile` e do Ambiente Local
- **Descoberta A-06**:
  - O [Dockerfile](file:///home/vini/Jellyfin%20livros/Dockerfile#L8-L43) oficial do projeto utiliza a imagem base oficial `FROM python:3.12-slim` (Debian Bookworm) e instala `apt-get install -y --no-install-recommends sqlite3`.
  - Na distribuição oficial Debian Bookworm (`libsqlite3-0` usada tanto pelo binário `sqlite3` quanto pela biblioteca padrão `sqlite3` do CPython em `python:3.12-slim`), a extensão `ENABLE_FTS5` e o tokenizador padrão `unicode61` (com suporte a `remove_diacritics 2`, introduzido no SQLite 3.27.0) são habilitados por padrão de fábrica — inclusive a tabela `books_fts USING fts5(...)` da Lectoria já roda em produção na imagem Docker desde a v1.5.0.
  - **Verificação de execução local do binário Docker**: Ao testar `docker --version` neste ambiente de desenvolvimento, constatou-se que o daemon/CLI `docker` não está instalado na máquina host local (`bash: docker: comando não encontrado`).
  - Portanto, a construção local da imagem Docker não pôde ser executada nesta sessão por ausência do binário `docker` no host, ficando registrada formalmente como limitação ambiental verificada por inspeção do `Dockerfile` e pelo histórico de `books_fts` em produção.
- **Classificação**: **`Limitação documentada`** (ausência do binário `docker` no host local; compatibilidade da imagem `python:3.12-slim` confirmada por especificação do Debian Bookworm e uso prévio de `books_fts` em produção).

---

### 2.6. Matriz de Cobertura dos Requisitos Críticos da Fase 1 (Seção 4.6)

| Eixo Crítico | Requisito Verificado | Arquivo e Função de Teste Correspondente | Status |
| :--- | :--- | :--- | :---: |
| **Migração** | Migração `v12 → v13` sobre banco populado preservando 100% dos dados legados | `test_v190_knowledge_migration.py::test_migration_v12_to_v13_populated_backup_and_data_preservation` | **Coberto** |
| **Migração** | Inicialização limpa em banco vazio (`v0 → v13`) e idempotência de `init_db` | `test_v190_knowledge_migration.py::test_clean_empty_db_initializes_directly_to_v13` | **Coberto** |
| **Backup** | Criação de backup pré-migração e não sobrescrita de backup existente | `test_v190_knowledge_migration.py::test_migration_v13_does_not_overwrite_existing_backup` | **Coberto** |
| **Rollback** | Rollback transacional completo para `v12` após falha injetada no meio da migração | `test_v190_knowledge_migration.py::test_migration_v13_injected_failure_rolls_back_to_v12` | **Coberto** |
| **Integridade Polimórfica** | Constraints `CHECK`/`UNIQUE`, bloqueio de auto-loop e limpeza via triggers ao excluir conceito, reflexão ou usuário | `test_v190_knowledge_migration.py::test_schema_v13_check_and_unique_constraints_and_triggers` | **Coberto** |
| **Unicode** | Normalização NFC/NFD, unicidade case-insensitive por usuário e equivalência de acentuação em português | `test_v190_knowledge_concepts.py::test_create_concept_valid_and_normalization`, `test_v190_knowledge_fts.py::test_fts5_portuguese_diacritics_and_nfc_nfd_equivalence` | **Coberto** |
| **Concorrência** | Criação concorrente de conceitos e conexões em múltiplas threads sob SQLite WAL | `test_v190_knowledge_links.py::test_concurrent_creation_of_concepts_and_links_integrity` | **Coberto** |
| **Autorização e Isolamento** | Isolamento estrito entre usuários e administrador para conceitos, reflexões, conexões e backlinks | `test_v190_knowledge_concepts.py::test_delete_concept_and_isolation_between_users`, `test_v190_knowledge_reflections.py::test_update_and_delete_reflection_and_multiuser_isolation` | **Coberto** |
| **Autorização por Livro** | Submissão de anotações próprias e de terceiros à autorização canônica sobre o livro (`_verify_book_access`) | `test_v190_knowledge_authorization.py::test_own_annotation_subject_to_book_authorization_on_source_deactivation` | **Coberto (Adicionado na Fase 2.0)** |
| **Revogação e Exclusão** | Ciclo `public → private → public`, desativação de livro/fonte e exclusão física de anotação de terceiro preservando `rationale` | `test_v190_knowledge_authorization.py::test_cross_user_public_annotation_link_revocation_republish_and_isolation`, `test_book_revocation_and_third_party_deletion_preserve_alice_rationale_safely` | **Coberto** |
| **FTS5 e Paginação** | Sincronização de ciclo de vida, índice FTS5 deliberadamente dessincronizado e proteção contra exaustão de paginação por candidatos privados alheios | `test_v190_knowledge_authorization.py::test_deliberately_desynchronized_fts5_index_never_leaks_unauthorized_records`, `test_fts5_private_concepts_of_other_users_do_not_exhaust_candidate_pagination` | **Coberto (Expandido na Fase 2.0)** |
| **Escala e Planos SQL** | Validação `EXPLAIN QUERY PLAN` dos índices compostos e benchmark sintético com 100, 1.000 e 10.000 anotações | `test_v190_knowledge_fts.py::test_explain_query_plan_and_synthetic_scale_100_1000_10000` | **Coberto** |

---

## 3. Quadro Resumo das Descobertas da Fase 2.0

| ID | Eixo | Descrição da Descoberta | Classificação |
| :-: | :--- | :--- | :---: |
| **A-01** | 4.1 Autorização por Livro | `_check_book_accessible_row` refatorado para invocar diretamente o verificador canônico `annotations_service._verify_book_access`; adicionado teste confirmando que anotações próprias também respeitam a desativação da fonte do livro. | **`Corrigida nesta fase`** |
| **A-02** | 4.2 Paginação e Filtro FTS5 | Adicionado filtro `AND (user_id = ? OR entity_type IN ('highlight', 'note'))` diretamente na query `knowledge_fts MATCH`, evitando que conceitos/reflexões privados de outros usuários consumam o limite interno de candidatos. | **`Corrigida nesta fase`** |
| **A-03** | 4.2 Scores `bm25()` e `has_more` | Confirmado que scores brutos `bm25()` nunca são expostos; adicionado desempate determinístico `(rank ASC, entity_type ASC, entity_id ASC)`, metadado `has_more` em todas as listagens e documentado o comportamento de IDF global do FTS5. | **`Corrigida nesta fase`** |
| **A-04** | 4.3 Referências Indisponíveis | `_unavailable_entity_sentinel` verificado e padronizado com todos os campos de conteúdo explícitos como `None`, garantindo zero vazamento de título, texto, página, CFI, autor ou motivo da indisponibilidade. | **`Verificada e adequada`** |
| **A-05** | 4.4 Atomicidade da Migração v13 | `_apply_migration_v13` executa em transação única sem `COMMIT` implícito, com backup pré-migração validado e rollback integral testado. | **`Verificada e adequada`** |
| **A-06** | 4.5 Compatibilidade Docker | `Dockerfile` utiliza `python:3.12-slim` (Debian Bookworm) com suporte nativo a FTS5 (`books_fts` já em produção); construção local não executada por ausência do binário `docker` no host local. | **`Limitação documentada`** |
