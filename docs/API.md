# Referência da API REST do Lectoria

Este documento descreve os principais grupos de rotas REST, contratos de requisição/resposta, validação Pydantic e códigos de status HTTP do **Lectoria**.

---

## 1. Padrões Globais da API

- **Autenticação**:
  - A maioria dos endpoints exige sessão autenticada (`require_user`), validada via cookie `lectoria_session`.
  - Rotas administrativas exigem `require_admin`.
- **Proteção CSRF**:
  - Métodos mutáveis (`POST`, `PUT`, `PATCH`, `DELETE`) exigem cabeçalho `x-csrf-token` idêntico ao cookie CSRF ativo.
- **Validação de Entrada**:
  - Modelos Pydantic v2 com `ConfigDict(extra="forbid")`. Parâmetros não reconhecidos retornam `422 Unprocessable Entity`.
- **Códigos de Resposta Típicos**:
  - `200 OK`: Consulta ou atualização bem-sucedida.
  - `201 Created`: Criação de recurso com sucesso.
  - `206 Partial Content`: Bloco de streaming HTTP Range (leitor PDF).
  - `304 Not Modified`: Cache HTTP válido via ETag ou Last-Modified.
  - `400 Bad Request`: Operação de negócio inválida (`InvalidOperationError`).
  - `401 Unauthorized`: Usuário sem sessão ativa (`require_user`).
  - `403 Forbidden`: Falha de CSRF, falta de privilégios ou clube público não vinculado.
  - `404 Not Found`: Recurso inexistente ou clube privado (anti-enumeração).
  - `422 Unprocessable Entity`: Validação de esquema/Pydantic rejeitada.

---

## 2. Autenticação e Sessão (`/api/auth/*`)

| Método | Rota | Autenticação | Descrição |
| :--- | :--- | :---: | :--- |
| `POST` | `/api/auth/login` | Pública | Login com `username` e `password`. Cria cookie de sessão. |
| `POST` | `/api/auth/logout` | `require_user` | Destrói a sessão atual no SQLite e limpa o cookie. |
| `GET` | `/api/auth/me` | Pública / Opcional | Retorna o usuário logado ou objeto de convidado. |
| `GET` | `/api/auth/csrf` | `require_user` | Retorna o token CSRF `{ "csrf_token": "..." }`. |

---

## 3. Acervo e Documentos (`/api/books/*`)

| Método | Rota | Autenticação | Descrição |
| :--- | :--- | :---: | :--- |
| `GET` | `/api/books` | `require_user` | Lista livros com paginação, busca e filtros de categoria. |
| `GET` | `/api/books/{id}` | `require_user` | Detalhes completos da obra (sinopse, ISBN, tags, metadados). |
| `GET` | `/api/books/{id}/file` | `require_user` | Streaming do arquivo (`.pdf` ou `.epub`). Suporta `HEAD`, `Accept-Ranges` e códigos `200`/`206`/`304`. |
| `GET` | `/api/books/{id}/collective-reads` | `require_user` | Lista leituras coletivas ativas/elegíveis para o livro onde o usuário é membro. |
| `POST` | `/api/books/{id}/edit` | `require_user` | Edição manual de título, autor, sinopse e travas de campos. |
| `POST` | `/api/books/{id}/cover` | `require_user` | Upload de capa personalizada (JPEG/PNG/WebP até 5 MB). |
| `DELETE` | `/api/books/{id}/cover` | `require_user` | Remove capa personalizada e restaura capa automática. |

---

## 4. Clubes de Leitura (`/api/clubs/*`)

| Método | Rota | Autenticação | Descrição |
| :--- | :--- | :---: | :--- |
| `GET` | `/api/clubs` | `require_user` | Lista clubes visíveis ao usuário (públicos e privados onde é membro). |
| `POST` | `/api/clubs` | `require_user` | Cria um novo clube com visibilidade `public` ou `private`. |
| `GET` | `/api/clubs/{id}` | `require_user` | Detalhes do clube, membros e leituras coletivas associadas. |
| `PUT` | `/api/clubs/{id}` | `require_user` | Edição do clube (exige papel `owner`). |
| `POST` | `/api/clubs/{id}/join` | `require_user` | Entrada voluntária em clube público. |
| `POST` | `/api/clubs/{id}/leave` | `require_user` | Saída voluntária do clube. |
| `POST` | `/api/clubs/{id}/archive` | `require_user` | Arquivamento do clube (exige `owner`). |
| `POST` | `/api/clubs/{id}/reads` | `require_user` | Cria uma leitura coletiva vinculada a uma obra (exige `owner`). |

---

## 5. Leitura Compartilhada (`/api/collective-reads/{read_id}/*`)

Grupo de 18 rotas REST introduzidas na v1.7.0 gerenciadas por `shared_reading_service.py`:

### 5.1 Visão Geral
- **`GET /api/collective-reads/{read_id}/overview`**
  - Retorna metadados da leitura, clube, contadores de tópicos/metas/citações, próximas metas e tópicos recentes.

### 5.2 Discussões e Moderação
- **`GET /api/collective-reads/{read_id}/topics`**
  - Query params: `page` (default 1), `limit` (default 15, máx 50), `filter` (`all`, `pinned`, `spoilers`).
  - Retorna lista paginada de tópicos com prévias seguras de spoiler e contadores de respostas.
- **`POST /api/collective-reads/{read_id}/topics`**
  - Payload: `{ "title": str, "content": str, "chapter_ref": Optional[str], "page_start": Optional[int], "page_end": Optional[int], "is_spoiler": bool }`.
  - Retorna `201 Created` com o tópico criado.
- **`GET /api/collective-reads/{read_id}/topics/{topic_id}`**
  - Query params: `page` (default 1), `limit` (default 50).
  - Retorna detalhe do tópico e árvore de respostas encadeadas (nível 1).
- **`PUT /api/collective-reads/{read_id}/topics/{topic_id}`**
  - Edição do tópico pelo próprio autor (`200 OK`).
- **`PATCH /api/collective-reads/{read_id}/topics/{topic_id}/moderate`**
  - Payload: `{ "is_pinned": Optional[bool], "is_locked": Optional[bool] }`.
  - Exige papel `owner` ou `moderator` do clube.
- **`DELETE /api/collective-reads/{read_id}/topics/{topic_id}`**
  - Exclusão lógica (`soft-delete`) permitida ao autor ou à moderação.

### 5.3 Respostas Encadeadas
- **`POST /api/collective-reads/{read_id}/topics/{topic_id}/posts`**
  - Payload: `{ "content": str, "parent_post_id": Optional[str], "is_spoiler": bool }`.
  - Valida profundidade máxima de aninhamento 1 e integridade de tópico via triggers SQLite.
- **`PUT /api/collective-reads/{read_id}/topics/{topic_id}/posts/{post_id}`**
  - Edição do conteúdo da resposta pelo autor.
- **`DELETE /api/collective-reads/{read_id}/topics/{topic_id}/posts/{post_id}`**
  - Exclusão lógica da resposta.

### 5.4 Metas e Cronogramas Coletivos
- **`GET /api/collective-reads/{read_id}/goals`**
  - Retorna metas da leitura ordenadas por `display_order`, contagem de concluídos e status do usuário atual.
- **`POST /api/collective-reads/{read_id}/goals`**
  - Payload: `{ "title": str, "description": Optional[str], "target_date": Optional[str], "target_page": Optional[int], "chapter_ref": Optional[str], "display_order": Optional[int] }`.
  - Exige papel `owner` ou `moderator`.
- **`PUT /api/collective-reads/{read_id}/goals/{goal_id}`**
  - Edição dos parâmetros da meta (exige moderação/owner).
- **`DELETE /api/collective-reads/{read_id}/goals/{goal_id}`**
  - Arquivamento lógico da meta (`is_archived = 1`).
- **`PUT /api/collective-reads/{read_id}/goals/{goal_id}/progress`**
  - Payload: `{ "status": "pending" | "in_progress" | "completed" }`.
  - Atualização voluntária de progresso pelo participante autenticado.

### 5.5 Citações Compartilhadas
- **`GET /api/collective-reads/{read_id}/quotes`**
  - Query params: `page` (default 1), `limit` (default 20).
  - Retorna citações paginadas com autor, livro, página/CFI e máscara de spoiler.
- **`POST /api/collective-reads/{read_id}/quotes`**
  - Payload: `{ "quote_text": Optional[str], "comment_text": Optional[str], "page_number": Optional[int], "epub_cfi": Optional[str], "is_spoiler": bool, "note_id": Optional[str] }`.
  - Valida limite de 1.500 caracteres para `quote_text`. Se `note_id` for informado, valida posse da nota e mesmo livro anti-IDOR.
- **`DELETE /api/collective-reads/{read_id}/quotes/{quote_id}`**
  - Exclusão lógica da citação pelo autor ou pela moderação.

---

## 6. Anotações Colaborativas, Desenhos e Marcadores (`/api/books/{id}/*` — v1.8.0)

Todas as rotas exigem sessão autenticada (`require_user`) e verificam o acesso autorizado ao livro (`books.id` canônico, `is_missing = 0`, fonte `is_active = 1`). Rotas de leitura incluem `Cache-Control: private, no-store` para garantir que revogações de visibilidade (`public -> private`) tenham efeito imediato. Rotas de escrita exigem cabeçalho `x-csrf-token` e utilizam modelos Pydantic v2 com `ConfigDict(extra="forbid")`.

### 6.1 Resumo e Consulta por Página/Posição
- **`GET /api/books/{book_id}/annotations`**
  - Query params: `scope` (`mine` [default], `public`, `all_visible`), `search` (opcional, máx. 300 chars; busca em conteúdo, texto destacado, capítulo e nome público do autor), `sort` (`position` [default], `recent`), `epub_cfi` (opcional), `limit` (`1..500`), `offset` (`>= 0`).
  - Retorna `{ "scope", "highlights", "notes", "drawing_pages", "bookmarks", "has_more", "total_counts", "counts" }`.
- **`GET /api/books/{book_id}/pages/{page}/annotations`**
  - Query params: `scope` (`mine` [default], `public`, `all_visible`), `epub_cfi`, `search`, `sort` (`position` | `recent`).
  - Retorna `{ "page", "scope", "highlights", "notes", "drawing", "own_drawing", "public_drawings" }`.

### 6.2 Destaques (`highlights` — PDF & EPUB CFI)
- **`POST /api/books/{book_id}/highlights`** (`201 Created`)
  - Payload (`HighlightCreatePayload`): `{ "page": int, "color": str, "rects": list[dict], "selected_text": str, "text_context": Optional[str], "epub_cfi": Optional[str], "chapter_label": Optional[str], "visibility": "private" | "public" }`.
  - Privado por padrão (`visibility = "private"`). Exige `rects` para PDF ou `epub_cfi` válido (`epubcfi(...)`) para EPUB.
- **`PATCH /api/books/{book_id}/highlights/{highlight_id}/visibility`** (e alias `PATCH /api/highlights/{highlight_id}/visibility`)
  - Payload (`AnnotationVisibilityPayload`): `{ "visibility": "private" | "public" }`.
  - Exclusivo ao autor do destaque (`403`/`404` para terceiros).
- **`DELETE /api/books/{book_id}/highlights/{highlight_id}`** (e alias `DELETE /api/highlights/{highlight_id}`)
  - Remove fisicamente um destaque pertencente ao usuário autenticado.

### 6.3 Notas Textuais (`notes` — PDF & EPUB CFI)
- **`POST /api/books/{book_id}/notes`** (`201 Created`)
  - Payload (`NoteCreatePayload`): `{ "page": int, "content": str, "color": Optional[str], "highlight_id": Optional[str], "epub_cfi": Optional[str], "chapter_label": Optional[str], "visibility": "private" | "public" }`.
  - Privada por padrão (`visibility = "private"`). Se `highlight_id` for informado, valida posse e mesmo livro, herdando `epub_cfi` e `chapter_label` caso omitidos.
- **`PUT /api/books/{book_id}/notes/{note_id}`** (e alias `PUT /api/notes/{note_id}`)
  - Payload (`NoteUpdatePayload`): `{ "content": str, "color": Optional[str], "visibility": Optional["private" | "public"], "chapter_label": Optional[str] }`.
- **`PATCH /api/books/{book_id}/notes/{note_id}/visibility`** (e alias `PATCH /api/notes/{note_id}/visibility`)
  - Payload (`AnnotationVisibilityPayload`): `{ "visibility": "private" | "public" }`.
- **`DELETE /api/books/{book_id}/notes/{note_id}`** (e alias `DELETE /api/notes/{note_id}`)
  - Exclui fisicamente uma nota pertencente ao usuário autenticado.

### 6.4 Desenhos Vetoriais por Página (`page_drawings`)
- **`GET /api/books/{book_id}/pages/{page}/drawing`**
  - Query param: `scope` (`mine`, `public`, `all_visible` [default]).
  - Retorna os campos do próprio desenho no nível raiz (compatibilidade retroativa) mais `"own_drawing"` (editável pelo autor) e `"public_drawings"` (lista somente-leitura de desenhos públicos de outros leitores na página).
- **`PUT /api/books/{book_id}/pages/{page}/drawing`**
  - Payload (`DrawingSavePayload`): `{ "strokes": list[dict], "visibility": Optional["private" | "public"] }`.
  - Novos desenhos nascem como `private`; atualizações de traços preservam a visibilidade existente se `visibility` for omitido. Valida limite de traços, pontos, espessura (`0.5..64`) e coordenadas.
- **`PATCH /api/books/{book_id}/pages/{page}/drawing/visibility`**
  - Payload (`AnnotationVisibilityPayload`): `{ "visibility": "private" | "public" }`.
- **`DELETE /api/books/{book_id}/pages/{page}/drawing`**
  - Limpa apenas os traços pertencentes ao próprio usuário na página, preservando desenhos públicos de outros leitores.

### 6.5 Marcadores Pessoais de Leitura (`bookmarks`)
- **`GET /api/books/{book_id}/bookmarks`**
  - Query params: `limit` (`1..500`), `offset` (`>= 0`).
  - Retorna `{ "book_id", "bookmarks", "count" }` contendo exclusivamente os marcadores privados do usuário autenticado.
- **`POST /api/books/{book_id}/bookmarks`** (`201 Created`)
  - Payload (`BookmarkCreatePayload`): `{ "page": int, "epub_cfi": Optional[str], "chapter_label": Optional[str], "label": Optional[str] }`.
  - Idempotente por `(book_id, user_id, page, COALESCE(epub_cfi, ''))`.
- **`DELETE /api/books/{book_id}/bookmarks/{bookmark_id}`** (e alias `DELETE /api/bookmarks/{bookmark_id}`)
  - Remove um marcador pessoal pertencente ao usuário autenticado.
