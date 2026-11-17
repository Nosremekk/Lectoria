# Autorização, Segurança e Permissões do Lectoria

Este documento descreve os mecanismos de autenticação, proteção contra ataques (CSRF, IDOR, Enumeração, Mass Assignment), hierarquia de papéis e a matriz de permissões do **Lectoria**.

---

## 1. Autenticação e Sessões

- **Armazenamento de Senhas**: Utiliza o algoritmo `hashlib.scrypt` da biblioteca padrão do Python com parâmetros seguros e salt individual criptográfico de 16 bytes.
- **Tokens de Sessão**:
  - Na criação da sessão, um token pseudo-aleatório seguro de 32 bytes em Base64 é gerado (`auth_service.create_session`).
  - O banco SQLite armazena apenas o hash criptográfico **SHA-256** do token (`sessions.token_hash`), impedindo que vazamentos de banco comprometam sessões ativas.
  - O token trafega no cookie HTTP `lectoria_session` com atributos de segurança `HttpOnly`, `SameSite=Lax` e `Path=/`.
- **Proteção contra Força Bruta**: Rate-limiting em memória para tentativas consecutivas incorretas de login (`/api/auth/login`).

---

## 2. Proteção CSRF e Mass Assignment

- **Double-Submit Cookie / Header CSRF**:
  - Requisições mutáveis (`POST`, `PUT`, `PATCH`, `DELETE`) exigem a presença do cabeçalho `x-csrf-token`.
  - A rota `GET /api/auth/csrf` fornece o token vinculado à sessão ativa.
  - Chamadas sem o cabeçalho ou com token incompatível retornam imediatamente `HTTP 403 Forbidden`.
- **Prevenção de Mass Assignment**:
  - Todos os modelos Pydantic de entrada da API REST configuram:
    ```python
    model_config = ConfigDict(extra="forbid")
    ```
  - Qualquer tentativa de enviar campos não mapeados (como `user_id`, `role`, `is_pinned`, `is_deleted`) resulta em rejeição estrita com `HTTP 422 Unprocessable Entity`.

---

## 3. Papéis de Usuário (*Roles*)

O Lectoria opera com dois níveis de governança:

### 3.1 Nível Global da Instância (`users.role`)
- **`admin`**: Acesso irrestrito a configurações do servidor, fontes de livros, fila de automação, gestão de usuários e criação de categorias.
- **`reader`**: Acesso à biblioteca, leitura de documentos, anotações privadas, clubes de leitura e participação colaborativa.

### 3.2 Nível de Clube de Leitura (`reading_club_members.role`)
- **`owner`**: Criador ou titular do clube. Pode gerenciar metadados do clube, transferir posse, arquivar o clube, criar leituras coletivas, criar/editar/arquivar metas coletivas e moderar discussões.
- **`moderator`**: Membro com privilégios de moderação. Pode fixar (`pin`), trancar (`lock`) ou ocultar (`soft-delete`) tópicos e respostas, além de criar e gerenciar metas coletivas.
- **`member`**: Participante ativo. Pode abrir tópicos, responder debates, marcar progresso voluntário em metas e compartilhar citações.

---

## 4. Políticas de Isolamento e Segurança

### 4.1 Anti-Enumeração de Clubes Privados
- Em clubes com `visibility = 'private'`, qualquer tentativa de acesso por um usuário não membro (seja via `/api/clubs/{id}`, `/api/collective-reads/{id}` ou listagens) retorna estritamente **`HTTP 404 Not Found`**.
- Isso impede que usuários descubram a existência de clubes privados ou leituras restritas por força bruta de IDs.
- Em clubes públicos com `visibility = 'public'`, usuários não membros visualizam metadados públicos, mas operações restritas a membros retornam **`HTTP 403 Forbidden`**.

### 4.2 Anti-IDOR e Proteção de Anotações Privadas
- Quando um usuário compartilha uma nota privada na leitura coletiva (`POST /api/collective-reads/{read_id}/quotes` com `note_id`), o backend valida obrigatoriamente:
  1. Que a nota existe e pertence estritamente ao usuário autenticado (`notes.user_id == current_user.id`).
  2. Que a nota pertence ao mesmo livro da leitura coletiva (`notes.book_id == collective_reads.book_id`).
- Caso outro usuário tente passar o `note_id` de terceiros, a operação é sumariamente rejeitada com **`HTTP 403 Forbidden`**.
- O compartilhamento cria um *snapshot* textual independente. Alterações ou exclusões posteriores na nota privada **não modificam** a citação já publicada na leitura coletiva.

### 4.3 Desacoplamento entre Progresso Privado e Metas Coletivas
- O progresso de leitura individual do usuário (`reading_progress.current_page`) é confidencial e nunca é compartilhado compulsoriamente com o clube.
- O progresso nas metas coletivas (`collective_goal_progress`) é gravado de maneira voluntária e autônoma pelo participante ao selecionar seu estado (*Pendente*, *Em andamento* ou *Concluída*).

### 4.4 Política Estrita de Leitura Somente-Leitura
A função central `_require_collective_read_access` em `shared_reading_service.py` aplica as seguintes restrições para qualquer operação de escrita (`is_write_op = True`):
- **Clube Arquivado (`is_archived = 1`)**: Rejeita com `HTTP 400 Bad Request` ("Este clube está arquivado e em modo somente leitura.").
- **Leitura Cancelada (`status = 'cancelled'`)**: Rejeita com `HTTP 400 Bad Request` ("Esta leitura coletiva foi cancelada e está em modo somente leitura.").
- **Leitura Concluída (`status = 'completed'`)**: Rejeita com `HTTP 400 Bad Request` ("Esta leitura coletiva foi concluída e está em modo somente leitura.").
- **Preservação de Leitura**: Todas as consultas (`GET`) continuam retornando `HTTP 200 OK`, preservando integralmente o histórico de debates, metas e citações para os membros autorizados.

---

## 5. Matriz Completa de Permissões (v1.7.0)

A tabela abaixo reflete as regras efetivamente aplicadas em [shared_reading_service.py](file:///home/vini/Jellyfin%20livros/shared_reading_service.py) e [main.py](file:///home/vini/Jellyfin%20livros/main.py):

| Operação / Recurso | Sem Sessão | Carlos (Não Membro) | Bob (Membro) | Moderador | Alice (Owner) | Leitura Encerrada* |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `GET /api/collective-reads/{id}/overview` (Público) | 401 | 403 | 200 | 200 | 200 | 200 |
| `GET /api/collective-reads/{id}/overview` (Privado) | 401 | 404 | 200 | 200 | 200 | 200 |
| `GET /api/collective-reads/{id}/topics` | 401 | 403/404 | 200 | 200 | 200 | 200 |
| `POST /api/collective-reads/{id}/topics` | 401 | 403/404 | 201 | 201 | 201 | 400 |
| `PUT /api/collective-reads/{id}/topics/{tid}` (Próprio) | 401 | 403/404 | 200 | 200 | 200 | 400 |
| `PUT /api/collective-reads/{id}/topics/{tid}` (De Outro) | 401 | 403/404 | 403 | 403 | 403 | 400 |
| `PATCH /api/collective-reads/{id}/topics/{tid}/moderate` | 401 | 403/404 | 403 | 200 | 200 | 400 |
| `DELETE /api/collective-reads/{id}/topics/{tid}` (Autor/Mod) | 401 | 403/404 | 200 (se autor) | 200 | 200 | 400 |
| `POST /api/collective-reads/{id}/topics/{tid}/posts` | 401 | 403/404 | 201 | 201 | 201 | 400 |
| `POST /api/collective-reads/{id}/topics/{tid}/posts` (Trancado) | 401 | 403/404 | 400 | 400 | 400 | 400 |
| `PUT /api/collective-reads/{id}/topics/{tid}/posts/{pid}` (Próprio) | 401 | 403/404 | 200 | 200 | 200 | 400 |
| `DELETE /api/collective-reads/{id}/topics/{tid}/posts/{pid}` | 401 | 403/404 | 200 (se autor) | 200 | 200 | 400 |
| `GET /api/collective-reads/{id}/goals` | 401 | 403/404 | 200 | 200 | 200 | 200 |
| `POST /api/collective-reads/{id}/goals` | 401 | 403/404 | 403 | 201 | 201 | 400 |
| `PUT /api/collective-reads/{id}/goals/{gid}` | 401 | 403/404 | 403 | 200 | 200 | 400 |
| `DELETE /api/collective-reads/{id}/goals/{gid}` | 401 | 403/404 | 403 | 200 | 200 | 400 |
| `PUT /api/collective-reads/{id}/goals/{gid}/progress` | 401 | 403/404 | 200 | 200 | 200 | 400 |
| `GET /api/collective-reads/{id}/quotes` | 401 | 403/404 | 200 | 200 | 200 | 200 |
| `POST /api/collective-reads/{id}/quotes` | 401 | 403/404 | 201 | 201 | 201 | 400 |
| `DELETE /api/collective-reads/{id}/quotes/{qid}` (Autor/Mod) | 401 | 403/404 | 200 (se autor) | 200 | 200 | 400 |
| `POST .../quotes` com `note_id` de outro leitor | 401 | 403/404 | 403 | 403 | 403 | 400 |
| `POST .../quotes` com texto > 1500 caracteres | 401 | 403/404 | 422 | 422 | 422 | 400 |

*\*Leitura Encerrada: leitura com status `completed`, `cancelled` ou pertencente a clube arquivado (`is_archived = 1`).*

---

## 6. Autorização e Isolamento de Anotações Colaborativas no Livro (v1.8.0 — Fases 1 e 2)

As regras implementadas em [annotations_service.py](file:///home/vini/Jellyfin%20livros/annotations_service.py), [export_service.py](file:///home/vini/Jellyfin%20livros/export_service.py) e [main.py](file:///home/vini/Jellyfin%20livros/main.py) garantem:

1. **Visibilidade Estrita (`private` | `public`)**:
   - Existem apenas dois estados de visibilidade: `private` (padrão obrigatório para todas as anotações antigas, novas e importadas) e `public` (ativado somente por ação explícita do próprio autor via `PATCH .../visibility` ou payload explícito).
   - Valores fora de `('private', 'public')` (como `club`) são rejeitados pelo Pydantic (`422`) e pelo serviço (`400`).
2. **Associação Canônica por `book_id` e Acesso ao Livro**:
   - Toda consulta ou escrita verifica `get_book(book_id, user_id)` e `_verify_book_access(cursor, book_id, user_id)`: o livro deve existir pelo seu `books.id` canônico (`404` se inexistente), não estar ausente (`is_missing = 0`, `404` se ausente), pertencer a uma fonte ativa (`library_sources.is_active = 1`, `403` se fonte inativa) e o usuário deve estar ativo (`users.is_active = 1`, `401` se desativado).
3. **Escopos de Consulta (`mine`, `public`, `all_visible`) e Cache-Control**:
   - `mine`: retorna apenas anotações do próprio usuário (`user_id == viewer_id`), sejam privadas ou públicas.
   - `public`: retorna apenas anotações com `visibility = 'public'` no livro autorizado.
   - `all_visible`: combina anotações próprias (`user_id == viewer_id`) com anotações públicas (`visibility = 'public'`) de outros leitores, sem duplicação.
   - Respostas de leitura incluem `Cache-Control: private, no-store` para que revogações (`public -> private`) reflitam imediatamente sem retenção em cache HTTP.
4. **Anti-IDOR, Imutabilidade por Terceiros e Identidade Pública Segura**:
   - Consultar ou tentar alterar por ID uma anotação `private` de outro usuário retorna `HTTP 404 Not Found` (anti-enumeração).
   - Tentar alterar a visibilidade de uma anotação `public` pertencente a outro usuário retorna `HTTP 403 Forbidden`; tentar editar ou excluir destaque/nota/desenho/marcador de outro usuário retorna `HTTP 404 Not Found`.
   - Operações em rotas com `{book_id}` validam que a anotação alvo pertence efetivamente ao `book_id` informado (`404` se pertencer a outro livro).
   - A autoria pública expõe apenas `author_user_id`, `author_display_name`, `author_avatar_color`, `is_own`, `can_edit`, `can_delete` e `can_toggle_visibility`, jamais expondo `password_hash`, `token_hash`, `session_token` ou caminhos internos de arquivos.
5. **Marcadores Pessoais (`bookmarks`) e Importação/Exportação**:
   - Marcadores são 100% privados (`WHERE book_id = ? AND user_id = ?`) e inacessíveis a outros usuários.
   - Exportações pessoais (`.md` e `.json`) utilizam estritamente `scope='mine'`.
   - Importações `.json` ignoram qualquer `user_id` ou `visibility='public'` contido no arquivo, forçando `visibility='private'` sob o `user_id` autenticado e rejeitando vínculo com `highlight_id` de terceiros.

### 6.1 Matriz de Permissões da API de Anotações Colaborativas (v1.8.0)

| Endpoint / Ação | Sem Sessão | Sem CSRF | Fonte Inativa | Autor (Item Privado) | Autor (Item Público) | Outro Leitor (Item Privado de A) | Outro Leitor (Item Público de A) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `GET /api/books/{id}/annotations?scope=mine` | 401 | — | 403 | 200 (vê) | 200 (vê) | 200 (oculto) | 200 (oculto) |
| `GET /api/books/{id}/annotations?scope=public` | 401 | — | 403 | 200 (oculto) | 200 (vê) | 200 (oculto) | 200 (vê, `is_own=false`) |
| `GET /api/books/{id}/annotations?scope=all_visible` | 401 | — | 403 | 200 (vê 1x) | 200 (vê 1x) | 200 (oculto) | 200 (vê, `is_own=false`) |
| `GET /api/books/{id}/pages/{p}/drawing` | 401 | — | 403 | 200 (`own_drawing`) | 200 (`own_drawing`) | 200 (oculto) | 200 (`public_drawings`) |
| `POST /api/books/{id}/highlights` / `notes` / `bookmarks` | 401 | 403 | 403 | 201 (`private`) | 201 (`public`) | — | — |
| `PUT /api/books/{id}/notes/{note_id}` | 401 | 403 | 403 | 200 | 200 | 404 | 404 |
| `PATCH .../highlights/{hid}/visibility` | 401 | 403 | 403 | 200 | 200 | 404 | 403 |
| `PATCH .../notes/{nid}/visibility` | 401 | 403 | 403 | 200 | 200 | 404 | 403 |
| `PATCH .../pages/{p}/drawing/visibility` | 401 | 403 | 403 | 200 | 200 | 404 | — (escopo próprio) |
| `DELETE .../highlights/{hid}` / `notes/{nid}` / `bookmarks/{bid}` | 401 | 403 | 403 | 200 | 200 | 404 | 404 |
