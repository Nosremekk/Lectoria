# 13 — Contratos REST da API de Conhecimento Conectado (Seção Atlas — Lectoria v1.9.0)

> **Fase**: 2 (Revisão da Fundação, API REST e Segurança)  
> **Branch**: `feature/v1.9.0-connected-knowledge`  
> **Namespace HTTP**: `/api/knowledge`  
> **Seção Principal Oficial (`KD-010`)**: **Atlas**  
> **Data**: 2026-11-18

---

## 1. Convenções Gerais, Autenticação, CSRF e Cache

### 1.1. Autenticação Obrigatória
- Todos os endpoints sob `/api/knowledge/*` exigem sessão autenticada e usuário ativo (`require_user(request)`).
- Requisições sem sessão, com token de sessão expirado/inválido ou pertencentes a usuários desativados (`users.is_active == 0`) recebem **`HTTP 401 Unauthorized`**:
  ```json
  {
    "detail": "Autenticação necessária para acessar este recurso."
  }
  ```

### 1.2. Proteção CSRF (`x-csrf-token`)
- Todos os métodos HTTP mutáveis (`POST`, `PATCH`, `DELETE`) em `/api/knowledge/*` passam pelo middleware `csrf_and_security_middleware` e exigem:
  1. Correspondência de `Origin` / `Referer` com `Host` (quando presentes);
  2. Cabeçalho HTTP `x-csrf-token` idêntico ao cookie `lectoria_csrf`.
- **Proibição de Token CSRF em URL nas Novas Rotas**: Diferentemente do fallback legado do leitor (`sendBeacon`), os endpoints `/api/knowledge/*` **rejeitam explicitamente** (`HTTP 403 Forbidden`) qualquer tentativa de enviar `?csrf_token=...` na query string da URL.

### 1.3. Política de Cache e Privacidade
- Todas as respostas de `/api/knowledge/*` (incluindo respostas `200`, `201` e erros sensíveis `403`/`404`) definem obrigatoriamente o cabeçalho:
  ```http
  Cache-Control: private, no-store
  ```
- Nenhum conteúdo privado de reflexões (`body_markdown`), descrições de conceitos ou justificativas (`rationale`) é registrado em logs de aplicação.

### 1.4. Validação Pydantic v2 (`ConfigDict(extra="forbid")`)
- Todos os payloads de entrada (`ConceptCreatePayload`, `ConceptUpdatePayload`, `ReflectionCreatePayload`, `ReflectionUpdatePayload`, `KnowledgeLinkCreatePayload`, `KnowledgeLinkUpdatePayload`) utilizam `ConfigDict(extra="forbid")`.
- Qualquer campo não previsto no contrato (como `id`, `user_id`, `owner_user_id`, `created_at`, `updated_at`, `is_own`) resulta imediatamente em **`HTTP 422 Unprocessable Entity`**, prevenindo *Mass Assignment*.
- **Semântica de Atualização Parcial (`PATCH`)**:
  - **Campo omitido**: o valor atual no banco de dados é preservado intacto.
  - **Campo enviado como string vazia (`""`)**: permitido apenas em campos textuais opcionais (`description`, `body_markdown`, `rationale`) para limpar o texto; rejeitado (`422`) em campos obrigatórios (`name`, `title`).
  - **Campo enviado explicitamente como `null`**: permitido exclusivamente em `ReflectionUpdatePayload.primary_book_id` para desassociar o livro principal da reflexão; rejeitado (`422`) nos demais campos (`name`, `description`, `title`, `body_markdown`, `tags`, `color`, `is_pinned`, `is_archived`, `relation_type`, `rationale`).

### 1.5. Mapeamento Padronizado de Códigos HTTP

| Situação / Exceção de Domínio | Código HTTP | Comportamento |
| :--- | :---: | :--- |
| Leitura, listagem, atualização ou exclusão bem-sucedida | `200 OK` | Retorna payload JSON validado. |
| Criação de conceito, reflexão ou conexão | `201 Created` | Retorna `{"status": "success", "<entidade>": {...}}`. |
| `KnowledgeValidationError` (ex.: auto-referência `source == target`) | `400 Bad Request` | Mensagem descritiva limpa, sem detalhes internos de SQLite. |
| Ausência de sessão válida ou usuário desativado | `401 Unauthorized` | Sessão exigida. |
| `KnowledgePermissionError` ou falha/ausência de `x-csrf-token` | `403 Forbidden` | Operação bloqueada. |
| `KnowledgeNotFoundError` (item inexistente, privado de terceiro ou livro inacessível) | `404 Not Found` | Resposta uniforme anti-enumeração. |
| `KnowledgeConflictError` (conceito com mesmo nome normalizado ou relação duplicada) | `409 Conflict` | Conflito de unicidade no Atlas do usuário. |
| Falha estrutural de payload Pydantic / query param fora dos limites | `422 Unprocessable Entity` | Validação estrutural do FastAPI/Pydantic v2. |

### 1.6. Ajustes e Evoluções em Relação à Previsão da Fase 1
1. **Verbo HTTP `PATCH` para Atualizações Parciais**: Conforme especificado nas Seções 6, 7 e 8 da Fase 2, as rotas de atualização de conceitos, reflexões e conexões utilizam `PATCH` (`/api/knowledge/concepts/{id}`, `/api/knowledge/reflections/{id}`, `/api/knowledge/links/{id}`) com suporte estrito a atualização parcial (`model_fields_set`).
2. **Endpoints Dedicados de Conexões e Backlinks por Entidade**: Além de `GET /api/knowledge/links`, foram implementados `GET /api/knowledge/entities/{entity_type}/{entity_id}/connections` (parametrizável por `direction=outgoing|incoming|both`) e `GET /api/knowledge/entities/{entity_type}/{entity_id}/backlinks`.
3. **Endpoint Unificado de Localização Assistida (`GET /api/knowledge/lookup`)**: Implementado para permitir que os seletores assistidos das Fases 3 a 5 localizem `concept`, `reflection`, `highlight`, `note` e `book` com limite máximo de 50 resultados e normalização Unicode.
4. **Campo `has_more` em Todas as Listagens Paginadas**: Todas as listagens paginadas retornam `total`, `limit`, `offset` e `has_more: bool`.

---

## 2. Inventário Completo de Endpoints (`/api/knowledge/*`)

| Método | Rota | Descrição |
| :---: | :--- | :--- |
| `GET` | `/api/knowledge/concepts` | Lista paginada de conceitos privados do usuário autenticado. |
| `POST` | `/api/knowledge/concepts` | Cria um novo conceito privado (`201 Created`). |
| `GET` | `/api/knowledge/concepts/{concept_id}` | Consulta um conceito privado do usuário. |
| `PATCH` | `/api/knowledge/concepts/{concept_id}` | Atualiza parcialmente nome, descrição, tags, cor ou arquivamento do conceito. |
| `DELETE` | `/api/knowledge/concepts/{concept_id}` | Exclui um conceito próprio e limpa seus vínculos em `knowledge_links` e `knowledge_fts`. |
| `GET` | `/api/knowledge/reflections` | Lista paginada de reflexões autorais privadas do usuário. |
| `POST` | `/api/knowledge/reflections` | Cria uma nova reflexão autoral privada (`201 Created`). |
| `GET` | `/api/knowledge/reflections/{reflection_id}` | Consulta uma reflexão própria com suas `references` (saídas) e `backlinks` (entradas). |
| `PATCH` | `/api/knowledge/reflections/{reflection_id}` | Atualiza parcialmente título, `body_markdown`, `primary_book_id`, tags, fixação ou arquivamento. |
| `DELETE` | `/api/knowledge/reflections/{reflection_id}` | Exclui uma reflexão própria e limpa seus vínculos em `knowledge_links` e `knowledge_fts`. |
| `GET` | `/api/knowledge/links` | Lista paginada de conexões pessoais do usuário com filtros opcionais. |
| `POST` | `/api/knowledge/links` | Cria uma conexão direcionada entre duas entidades autorizadas (`201 Created`). |
| `GET` | `/api/knowledge/links/{link_id}` | Consulta uma conexão própria com extremos hidratados em tempo de leitura. |
| `PATCH` | `/api/knowledge/links/{link_id}` | Atualiza exclusivamente `relation_type` e/ou `rationale` de uma conexão própria. |
| `DELETE` | `/api/knowledge/links/{link_id}` | Exclui uma conexão própria. |
| `GET` | `/api/knowledge/entities/{entity_type}/{entity_id}/connections` | Lista conexões de saída (`outgoing_links`) e/ou backlinks (`incoming_backlinks`) de uma entidade autorizada. |
| `GET` | `/api/knowledge/entities/{entity_type}/{entity_id}/backlinks` | Lista especificamente os backlinks (`incoming_backlinks`) que apontam para uma entidade autorizada. |
| `GET` | `/api/knowledge/lookup` | Localização assistida de entidades referenciáveis (`concept`, `reflection`, `highlight`, `note`, `book`). |
| `GET` | `/api/knowledge/books/{book_id}/summary` | Resumo agregado pessoal de conceitos, conexões e anotações conectadas de um livro acessível (Fase 4). |
| `POST` | `/api/knowledge/entities/summary` | Resumo pessoal em lote (até 200 entidades `highlight`/`note`/`book`, sem N+1) com anti-enumeração (Fase 4). |
| `GET` | `/api/knowledge/summary` | Resumo estatístico geral do Atlas pessoal e itens recentes (Fase 5). |

---

## 3. Especificação Detalhada dos Contratos e Exemplos JSON

### 3.1. Conceitos (`/api/knowledge/concepts`)

#### `POST /api/knowledge/concepts`
- **Limites**: `name` (`1..160` caracteres após `trim`), `description` (`0..10000` caracteres), `tags` (`0..30` itens, cada um com `1..64` caracteres), `color` (hexadecimal `#rgb` a `#rrggbbaa`, padrão `#d4a853`).
- **Payload de Requisição**:
  ```json
  {
    "name": "Autonomia da Técnica",
    "description": "Fenômeno pelo qual o sistema técnico passa a determinar seus próprios fins.",
    "tags": ["filosofia", "modernidade"],
    "color": "#d4a853",
    "is_archived": false
  }
  ```
- **Resposta (`201 Created`)**:
  ```json
  {
    "status": "success",
    "concept": {
      "id": "a7c4b1e0-3f2a-4d91-8b11-1029384756ab",
      "user_id": 10,
      "name": "Autonomia da Técnica",
      "normalized_name": "autonomia da técnica",
      "description": "Fenômeno pelo qual o sistema técnico passa a determinar seus próprios fins.",
      "tags": ["filosofia", "modernidade"],
      "color": "#d4a853",
      "is_archived": false,
      "links_count": 0,
      "created_at": "2026-11-18 21:30:00",
      "updated_at": "2026-11-18 21:30:00"
    }
  }
  ```

#### `GET /api/knowledge/concepts`
- **Query Parameters**:
  - `search` (`string`, opcional, máx. `300`): busca insensível a acentos e caixa em `name`, `description` e `tags`.
  - `tag` (`string`, opcional, máx. `64`): filtra por etiqueta exata (case-insensitive).
  - `include_archived` (`bool`, padrão `false`): quando `false`, retorna apenas conceitos ativos.
  - `sort` (`"updated_desc" | "created_desc" | "name_asc"`, padrão `"updated_desc"`).
  - `limit` (`int`, padrão `50`, intervalo `1..200`).
  - `offset` (`int`, padrão `0`, `>= 0`).
- **Resposta (`200 OK`)**:
  ```json
  {
    "items": [
      {
        "id": "a7c4b1e0-3f2a-4d91-8b11-1029384756ab",
        "user_id": 10,
        "name": "Autonomia da Técnica",
        "normalized_name": "autonomia da técnica",
        "description": "Fenômeno pelo qual o sistema técnico passa a determinar seus próprios fins.",
        "tags": ["filosofia", "modernidade"],
        "color": "#d4a853",
        "is_archived": false,
        "links_count": 3,
        "created_at": "2026-11-18 21:30:00",
        "updated_at": "2026-11-18 21:30:00"
      }
    ],
    "total": 1,
    "limit": 50,
    "offset": 0,
    "has_more": false
  }
  ```

---

### 3.2. Reflexões Autorais (`/api/knowledge/reflections`)

#### `POST /api/knowledge/reflections`
- **Limites**: `title` (`1..300` caracteres), `body_markdown` (`0..50000` caracteres), `primary_book_id` (opcional, deve referenciar um livro existente, presente no disco e pertencente a uma fonte ativa), `tags` (`0..30` itens).
- **Nota de Segurança sobre `body_markdown`**: O texto é armazenado e retornado pela API exatamente como string JSON não confiável. Nenhuma conversão para HTML ocorre na camada REST; o futuro renderizador visual (Fase 5) aplicará sanitização estrita no cliente.
- **Payload de Requisição**:
  ```json
  {
    "title": "Ellul e Heidegger: Duas Leituras sobre a Técnica Moderna",
    "body_markdown": "# Introdução\n\nComparação entre a autonomia sociológica em Ellul e o conceito de *Gestell* em Heidegger.",
    "primary_book_id": "book-ellul-01",
    "tags": ["ensaio", "filosofia"],
    "is_pinned": true,
    "is_archived": false
  }
  ```
- **Resposta (`201 Created`)**:
  ```json
  {
    "status": "success",
    "reflection": {
      "id": "f3b91c22-8a44-4e19-9c01-998877665544",
      "user_id": 10,
      "title": "Ellul e Heidegger: Duas Leituras sobre a Técnica Moderna",
      "body_markdown": "# Introdução\n\nComparação entre a autonomia sociológica em Ellul e o conceito de *Gestell* em Heidegger.",
      "primary_book_id": "book-ellul-01",
      "primary_book_title": "A Técnica e o Desafio do Século",
      "primary_book_available": true,
      "tags": ["ensaio", "filosofia"],
      "is_pinned": true,
      "is_archived": false,
      "references_count": 0,
      "backlinks_count": 0,
      "created_at": "2026-11-18 21:35:00",
      "updated_at": "2026-11-18 21:35:00"
    }
  }
  ```

#### `GET /api/knowledge/reflections/{reflection_id}`
- Retorna a reflexão acompanhada de suas `references` (conexões que partem da reflexão) e `backlinks` (conexões que apontam para a reflexão), ambos hidratados com *Read-Time Authorization*.

---

### 3.3. Conexões Semânticas (`/api/knowledge/links`)

#### `POST /api/knowledge/links`
- **Campos Permitidos**:
  - `source_type`: `"concept" | "reflection" | "highlight" | "note" | "book"`
  - `source_id`: identificador canônico da entidade de origem
  - `target_type`: `"concept" | "reflection" | "highlight" | "note" | "book"`
  - `target_id`: identificador canônico da entidade de destino
  - `relation_type` (opcional, padrão `"relates_to"`):
    - `"complements"` (*Complementa* / *Complementado por*)
    - `"contradicts"` (*Contradiz* / *Contradito por*)
    - `"exemplifies"` (*Exemplifica* / *Exemplificado por*)
    - `"grounds"` (*Fundamenta* / *Fundamentado por*)
    - `"develops"` (*Desenvolve* / *Desenvolvido por*)
    - `"references"` (*Referencia* / *Referenciado por*)
    - `"questions"` (*Questiona* / *Questionado por*)
    - `"relates_to"` (*Relaciona-se com* / *Relacionado a*)
  - `rationale` (opcional, `0..2000` caracteres): justificativa autoral do vínculo.
- **Exemplo — Conectando uma Nota PDF a um Destaque EPUB CFI (`201 Created`)**:
  ```json
  {
    "source_type": "note",
    "source_id": "note-pdf-ellul-p42",
    "target_type": "highlight",
    "target_id": "hl-epub-heidegger-cfi1",
    "relation_type": "complements",
    "rationale": "A descrição da técnica como Gestell aprofunda ontologicamente a autonomia descrita na página 42."
  }
  ```
- **Resposta (`201 Created`)**:
  ```json
  {
    "status": "success",
    "link": {
      "id": "e1d2c3b4-5a6f-4789-8012-3456789abcde",
      "user_id": 10,
      "source_type": "note",
      "source_id": "note-pdf-ellul-p42",
      "source": {
        "type": "note",
        "id": "note-pdf-ellul-p42",
        "available": true,
        "status": "available",
        "label": "Ellul define aqui a autonomia técnica...",
        "is_own": true,
        "user_id": 10,
        "author_display_name": "Helena Costa",
        "title": "A Técnica e o Desafio do Século",
        "excerpt": "Ellul define aqui a autonomia técnica...",
        "color": "#fef08a",
        "tags": [],
        "book_id": "book-ellul-01",
        "book_title": "A Técnica e o Desafio do Século",
        "book_format": "pdf",
        "page": 42,
        "epub_cfi": null,
        "chapter_label": "Capítulo 2",
        "visibility": "private",
        "created_at": "2026-11-18 20:00:00",
        "updated_at": "2026-11-18 20:00:00"
      },
      "target_type": "highlight",
      "target_id": "hl-epub-heidegger-cfi1",
      "target": {
        "type": "highlight",
        "id": "hl-epub-heidegger-cfi1",
        "available": true,
        "status": "available",
        "label": "A essência da técnica moderna é a armação (Gestell).",
        "is_own": true,
        "user_id": 10,
        "author_display_name": "Helena Costa",
        "title": "A Questão da Técnica",
        "excerpt": "A essência da técnica moderna é a armação (Gestell).",
        "color": "#fef08a",
        "tags": [],
        "book_id": "book-heidegger-02",
        "book_title": "A Questão da Técnica",
        "book_format": "epub",
        "page": 1,
        "epub_cfi": "epubcfi(/6/4[chap1]!/4/2/8,/1:0,/1:52)",
        "chapter_label": "Parte I",
        "visibility": "private",
        "created_at": "2026-11-18 20:10:00",
        "updated_at": "2026-11-18 20:10:00"
      },
      "relation_type": "complements",
      "relation_label": "Complementa",
      "outgoing_label": "Complementa",
      "incoming_label": "Complementado por",
      "direction": "outgoing",
      "counterpart": {
        "type": "highlight",
        "id": "hl-epub-heidegger-cfi1",
        "available": true,
        "status": "available",
        "label": "A essência da técnica moderna é a armação (Gestell).",
        "is_own": true,
        "user_id": 10,
        "author_display_name": "Helena Costa",
        "title": "A Questão da Técnica",
        "excerpt": "A essência da técnica moderna é a armação (Gestell).",
        "color": "#fef08a",
        "tags": [],
        "book_id": "book-heidegger-02",
        "book_title": "A Questão da Técnica",
        "book_format": "epub",
        "page": 1,
        "epub_cfi": "epubcfi(/6/4[chap1]!/4/2/8,/1:0,/1:52)",
        "chapter_label": "Parte I",
        "visibility": "private",
        "created_at": "2026-11-18 20:10:00",
        "updated_at": "2026-11-18 20:10:00"
      },
      "rationale": "A descrição da técnica como Gestell aprofunda ontologicamente a autonomia descrita na página 42.",
      "created_at": "2026-11-18 21:40:00",
      "updated_at": "2026-11-18 21:40:00"
    }
  }
  ```

---

### 3.4. Representação Padronizada de Referências Indisponíveis

Quando um extremo (`source` ou `target`) de uma conexão existente deixa de estar acessível ao proprietário da conexão (porque o autor de uma anotação pública revogou sua visibilidade para `private`, excluiu a anotação, ou porque o livro ficou ausente / teve sua fonte desativada), o extremo é serializado com o contrato seguro abaixo, preservando o `rationale` do dono da conexão e ocultando 100% dos metadados protegidos:

```json
{
  "type": "highlight",
  "id": "hl-terceiro-revogado",
  "available": false,
  "status": "unavailable",
  "label": "Referência indisponível",
  "is_own": null,
  "user_id": null,
  "author_display_name": null,
  "title": null,
  "excerpt": null,
  "color": null,
  "tags": [],
  "book_id": null,
  "book_title": null,
  "book_format": null,
  "page": null,
  "epub_cfi": null,
  "chapter_label": null,
  "visibility": null,
  "created_at": null,
  "updated_at": null
}
```

---

### 3.5. Referências e Backlinks por Entidade (`/api/knowledge/entities/{entity_type}/{entity_id}/connections` e `/backlinks`)

- **Ordem de Autorização**: Antes de consultar `knowledge_links`, o endpoint executa `resolve_knowledge_entity(entity_type, entity_id, user_id, for_link_endpoint=False)`. Se a entidade consultada pertencer a outro usuário e for privada (ou não existir), retorna imediatamente **`HTTP 404 Not Found`**, impedindo enumeração.
- **Query Parameters**:
  - `direction` (apenas em `/connections`, padrão `"both"`): `"outgoing" | "incoming" | "both"`.
  - `relation_type` (opcional): filtra por um dos 8 tipos semânticos.
  - `limit` (`1..200`, padrão `50`) e `offset` (`>= 0`, padrão `0`).
- **Resposta (`200 OK`)**:
  ```json
  {
    "entity": {
      "type": "concept",
      "id": "a7c4b1e0-3f2a-4d91-8b11-1029384756ab",
      "available": true,
      "status": "available",
      "label": "Autonomia da Técnica",
      "is_own": true,
      "user_id": 10,
      "author_display_name": null,
      "title": "Autonomia da Técnica",
      "excerpt": "Fenômeno pelo qual o sistema técnico passa a determinar seus próprios fins.",
      "color": "#d4a853",
      "tags": ["filosofia", "modernidade"],
      "book_id": null,
      "book_title": null,
      "book_format": null,
      "page": null,
      "epub_cfi": null,
      "chapter_label": null,
      "visibility": "private",
      "created_at": "2026-11-18 21:30:00",
      "updated_at": "2026-11-18 21:30:00"
    },
    "direction": "both",
    "outgoing_links": [],
    "incoming_backlinks": [],
    "total_outgoing": 0,
    "total_incoming": 0,
    "limit": 50,
    "offset": 0,
    "has_more_outgoing": false,
    "has_more_incoming": false
  }
  ```

---

### 3.6. Localização Assistida (`/api/knowledge/lookup`)

- **Finalidade**: Alimentar os seletores rápidos de vinculação de conceitos, criação de conexões entre anotações e inserção assistida de referências nos leitores PDF/EPUB, no Caderno de Estudos e no Atlas.
- **Query Parameters**:
  - `q` (`string`, padrão `""`, máx. `300`): termo de busca normalizado em Unicode NFC e comparado sem diacríticos.
  - `entity_type` (`"concept" | "reflection" | "highlight" | "note" | "book"`, opcional): restringe a busca a um único tipo de entidade.
  - `book_id` (`string`, opcional, máx. `128`): restringe os resultados às entidades associadas ao livro indicado (valida primeiramente se o livro está acessível).
  - `limit` (`int`, padrão `20`, intervalo `1..50`).
- **Resposta (`200 OK`)**:
  ```json
  {
    "items": [
      {
        "type": "concept",
        "id": "a7c4b1e0-3f2a-4d91-8b11-1029384756ab",
        "label": "Autonomia da Técnica",
        "title": "Autonomia da Técnica",
        "subtitle": "#filosofia, #modernidade",
        "excerpt": "Fenômeno pelo qual o sistema técnico passa a determinar seus próprios fins.",
        "color": "#d4a853",
        "book_id": null,
        "book_title": null,
        "page": null,
        "epub_cfi": null,
        "chapter_label": null,
        "is_own": true,
        "visibility": "private"
      }
    ],
    "count": 1,
    "limit": 20,
    "query": "tecnica",
    "entity_type": "concept",
    "book_id": null
  }
  ```

---

### 3.7. Resumo Geral do Atlas (`GET /api/knowledge/summary`)

- **Finalidade**: Alimentar a subaba inicial **Visão Geral** da seção principal do Atlas, fornecendo indicadores agregados estritamente pessoais em tempo constante/otimizado e listagens rápidas dos itens modificados recentemente.
- **Query Parameters**:
  - `recent_limit` (`int`, padrão `6`, intervalo `1..20`): quantidade máxima de conceitos e reflexões recentes a retornar.
- **Resposta (`200 OK`)**:
  ```json
  {
    "counts": {
      "total_concepts": 12,
      "total_reflections": 5,
      "total_links": 28,
      "total_outgoing_references": 20,
      "total_incoming_backlinks": 28
    },
    "recent_reflections": [
      {
        "id": "b9c3f4e2-...",
        "title": "Ellul e Heidegger: Duas Leituras sobre a Técnica",
        "updated_at": "2026-11-19 14:30:00",
        "tags": ["ensaio", "filosofia"],
        "is_pinned": 1
      }
    ],
    "recent_concepts": [
      {
        "id": "a7c4b1e0-...",
        "name": "Autonomia da Técnica",
        "color": "#d4a853",
        "updated_at": "2026-11-19 14:20:00"
      }
    ]
  }
  ```

---

### 3.8. Pesquisa Integrada do Atlas (`GET /api/knowledge/search` — Fase 6 / `KD-008`)

- **Finalidade**: Pesquisar de forma integrada conceitos, reflexões autorais, destaques, notas, justificativas de conexões semânticas (via SQLite FTS5 `knowledge_fts`) e metadados de obras acessíveis (`books`), com validação relacional integrada antes da paginação e snippets estruturados seguros.
- **Query Parameters**:
  - `q` (`string`, padrão `""`, máx. `300`): termo de pesquisa normalizado em Unicode NFC.
  - `entity_types` (`string`, opcional): lista CSV de tipos filtrados (`concept`, `reflection`, `highlight`, `note`, `link`, `book`).
  - `book_id` (`string`, opcional, máx. `128`): filtra resultados associados a um livro acessível específico.
  - `include_archived` (`bool`, padrão `false`): inclui conceitos e reflexões arquivados.
  - `limit` (`int`, padrão `24`, intervalo `1..100`) e `offset` (`int`, padrão `0`, `>= 0`).
- **Resposta (`200 OK`, `Cache-Control: private, no-store`)**:
  ```json
  {
    "items": [
      {
        "type": "concept",
        "id": "a7c4b1e0-3f2a-4d91-8b11-1029384756ab",
        "type_label": "Conceito",
        "type_prefix": "[C]",
        "title": "Autonomia da Técnica",
        "label": "Autonomia da Técnica",
        "subtitle": "#filosofia, #civilização",
        "snippet": "Conceito central sobre evolução tecnológica autônoma e organização social.",
        "snippet_segments": [
          {"text": "Conceito central sobre evolução tecnológica autônoma e ", "highlight": false},
          {"text": "organização", "highlight": true},
          {"text": " social.", "highlight": false}
        ],
        "index_source": "knowledge_fts",
        "is_own": true,
        "visibility": "private",
        "color": "#d4a853",
        "tags": ["filosofia", "civilização"],
        "book_id": null,
        "book_title": null,
        "book_format": null,
        "page": null,
        "epub_cfi": null,
        "chapter_label": null,
        "author_display_name": null,
        "relation_type": null,
        "relation_label": null,
        "source": null,
        "target": null,
        "can_open_in_graph": true,
        "created_at": "2026-11-21 15:00:00",
        "updated_at": "2026-11-21 15:00:00"
      }
    ],
    "total": 1,
    "limit": 24,
    "offset": 0,
    "has_more": false,
    "query": "organizacao",
    "entity_types": ["book", "concept", "highlight", "link", "note", "reflection"],
    "book_id": null,
    "search_engine": "fts5"
  }
  ```

---

### 3.9. Grafo Local de Conhecimento (`GET /api/knowledge/graph` — Fase 6 / `KD-006`)

- **Finalidade**: Construir o subgrafo local centrado em uma entidade autorizada (`concept`, `reflection`, `highlight`, `note`, `book`) limitado a 1 ou 2 saltos, com resolução em lote (zero N+1), tetos rígidos de nós e arestas, sentinelas seguros para referências indisponíveis e lista textual acessível equivalente.
- **Query Parameters**:
  - `entity_type` (`"concept" | "reflection" | "highlight" | "note" | "book"`, obrigatório).
  - `entity_id` (`string`, obrigatório, `1..128`).
  - `depth` (`int`, padrão `1`, valores permitidos: `1` ou `2`).
  - `max_nodes` (`int`, padrão `40`, intervalo `1..80`, incluindo o nó central).
  - `max_edges` (`int`, padrão `120`, intervalo `1..160`).
  - `entity_types` (`string`, opcional): lista CSV de tipos de nós vizinhos (`concept,reflection,highlight,note,book`).
  - `relation_types` (`string`, opcional): lista CSV dos 8 tipos de relação semântica.
- **Resposta (`200 OK`, `Cache-Control: private, no-store`)**:
  ```json
  {
    "center": {
      "key": "concept:a7c4b1e0-...",
      "type": "concept",
      "id": "a7c4b1e0-...",
      "type_label": "Conceito",
      "type_prefix": "[C]",
      "available": true,
      "status": "available",
      "label": "Filosofia da Técnica",
      "short_label": "Filosofia da Técnica",
      "title": "Filosofia da Técnica",
      "subtitle": "Conceito pessoal",
      "excerpt": "Núcleo conceitual sobre técnica, ciência e cultura moderna.",
      "color": "#d4a853",
      "tags": ["técnica", "filosofia"],
      "hop": 0,
      "is_center": true,
      "degree": 5,
      "expandable": true,
      "navigable": true
    },
    "nodes": [],
    "edges": [],
    "accessible_text_list": [],
    "node_count": 5,
    "edge_count": 5,
    "depth": 1,
    "max_nodes": 40,
    "max_edges": 120,
    "truncated": false,
    "truncation_reasons": [],
    "entity_types": ["book", "concept", "highlight", "note", "reflection"],
    "relation_types": ["complements", "contradicts", "develops", "exemplifies", "grounds", "questions", "references", "relates_to"]
  }
  ```

---

### 3.10. Exportação Portátil do Atlas (`GET /api/knowledge/export` — Fase 7 / `KD-009`)

- **Finalidade**: Exportar os dados pessoais autorizados do Atlas do usuário autenticado em formato aberto e portátil via streaming em disco temporário.
- **Query Parameters**:
  - `format` (`"markdown_zip" | "json" | "csv"`, padrão `"markdown_zip"`).
  - `wikilinks` (`bool`, padrão `false`): quando `true` em `markdown_zip`, adiciona `[[...]]` ao lado dos links Markdown padrão.
  - `include_archived` (`bool`, padrão `true`): inclui conceitos e reflexões arquivados.
- **Cabeçalhos de Resposta**:
  - `Cache-Control: private, no-store`
  - `X-Content-Type-Options: nosniff`
  - `Content-Disposition: attachment; filename="lectoria-atlas-..."`
  - `Content-Type`: `application/zip` (`markdown_zip` / `csv`) ou `application/json; charset=utf-8` (`json`).

