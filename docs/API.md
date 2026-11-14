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

## 6. Anotações Pessoais (`/api/books/{id}/*`)

- `GET /api/books/{id}/notes`: Lista notas privadas do usuário ativo na obra.
- `POST /api/books/{id}/notes`: Criação de nota textual privada vinculada a página ou destaque.
- `PUT /api/books/{id}/notes/{note_id}`: Edição de nota privada.
- `DELETE /api/books/{id}/notes/{note_id}`: Exclusão física de nota privada.
- `GET /api/books/{id}/highlights`: Lista marcações coloridas do usuário no leitor.
- `POST /api/books/{id}/highlights`: Salva destaque com coordenadas normalizadas.
