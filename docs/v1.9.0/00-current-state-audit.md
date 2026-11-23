# Auditoria do Estado Atual (v1.8.0) e Pontos de Extensão para a v1.9.0

- **Versão Auditada**: Lectoria `v1.8.0` (Schema SQLite `v12`)
- **Branch de Trabalho**: `feature/v1.9.0-connected-knowledge`
- **Natureza do Documento**: Fatos verificados no código-fonte, inferências arquiteturais e classificação técnica de componentes para o domínio de **Conhecimento Conectado**.

---

## 1. Metodologia e Escopo da Auditoria

Esta auditoria foi conduzida em modo estritamente de leitura sobre o código-fonte da versão `1.8.0` (commit de referência `ddffdaa4` em `main`). Nenhum arquivo de código, schema ou configuração foi alterado.

Os 16 eixos solicitados foram inspecionados diretamente nos módulos Python do backend, nos scripts JavaScript Vanilla do frontend, nas folhas de estilo e nos relatórios de validação das versões `v1.7.0` e `v1.8.0`.

---

## 2. Inspeção Sistemática dos 16 Eixos Arquiteturais

### 2.1. Organização Arquitetural do Backend
- **Fatos verificados no código**:
  - O backend segue uma arquitetura em camadas enxuta, servida por **FastAPI** e **Uvicorn** em processo único (`main.py`, 4.229 linhas).
  - O ponto de entrada `main.py` concentra:
    - Ciclo de vida (`lifespan`, linhas 188–203): executa `init_db()`, `automation_service.recover_interrupted_jobs_on_startup()`, `scan_library()` e inicia `automation_service.scheduler_loop`.
    - Middleware HTTP (`csrf_and_security_middleware`, linhas 216–281): validação de `Origin`/`Referer` contra `Host`, verificação Double Submit Cookie (`lectoria_csrf` vs. cabeçalho `x-csrf-token` ou query param `csrf_token`) e cabeçalhos `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN` e `Referrer-Policy: strict-origin-when-cross-origin`.
    - Injeção de autorização (`get_current_user`, `require_user`, `require_admin`, linhas 806–846).
    - Schemas Pydantic v2 de entrada com `model_config = ConfigDict(extra="forbid")` nas rotas de leitura compartilhada e anotações colaborativas.
  - A lógica de domínio é desacoplada em módulos especializados sem dependência reversa de `main.py`:
    - `database.py` (1.607 linhas): conexão SQLite em modo WAL e migrações `v1` a `v12`.
    - `library.py` (1.698 linhas): varredura de arquivos, resolução segura de caminhos (`resolve_book_path`, `resolve_book_file_for_user`), catálogo (`get_books`, `get_book`), índice FTS5 (`books_fts`) e prateleiras (`get_discovery_shelves`).
    - `annotations_service.py` (1.332 linhas): CRUD e visibilidade (`private`/`public`) de `highlights`, `notes`, `page_drawings` e `bookmarks`.
    - `export_service.py` (666 linhas): exportação `.md` e `.json` (`scope='mine'`) e importação transacional com preview.
    - `taxonomy_service.py` (1.448 linhas): árvore taxonômica canônica, facetas (`subjects`/`book_subjects`) e categorias administráveis (`categories`/`book_categories`).
    - `collections_service.py` (272 linhas): coleções pessoais de livros por usuário (`collections`/`collection_books`).
    - `social_service.py` (1.223 linhas): perfis (`user_profiles`), privacidade (`user_privacy_settings`), avaliações (`book_reviews`), citações no mural comunitário (`shared_annotations`, `annotation_comments`), recomendações (`book_recommendations`) e atividades (`reading_activity`).
    - `clubs_service.py` e `shared_reading_service.py`: clubes de leitura, leituras coletivas, tópicos, respostas, metas e citações de grupo.
    - `metadata_service.py`, `background_enricher.py` e `automation_service.py`: enriquecimento bibliográfico e fila persistente de jobs.
    - `auth_service.py`, `sources_service.py`, `covers_service.py`, `backup_service.py`, `progress.py`.
- **Inferência arquitetural**:
  - A regra de unidirecionalidade (`main.py` → `*_service.py` → `database.py`) está consolidada e permite introduzir um novo módulo de domínio (ex.: `knowledge_service.py`) sem acoplamento circular, embora `main.py` já esteja extenso (4.229 linhas) e se beneficie de organização clara de rotas ou `APIRouter`.

### 2.2. Organização Arquitetural do Frontend
- **Fatos verificados no código**:
  - O frontend é construído em HTML5, CSS3 e JavaScript ES6+ puro, sem etapas de compilação ou bundlers:
    - `static/index.html` (1.772 linhas) e `static/js/app.js` (5.339 linhas): shell principal organizado em abas de navegação (`#tab-nav-discovery`, `#tab-nav-library`, `#tab-nav-social`, `#tab-nav-clubs`, `#tab-nav-admin`, linhas 103–136 de `index.html`).
    - `static/js/shared_reading.js`: módulo encapsulado em IIFE que controla `#shared-reading-view` dentro da seção de clubes, usando `AbortController` e contador incremental `generation`.
    - `static/reader.html` (871 linhas) e `static/js/reader.js` (4.859 linhas): aplicação do leitor adaptativo (PDF.js e EPUB.js), barra de seleção `#selection-toolbar`, popovers `#highlight-popover` e `#page-note-popover`, e gaveta `#annotations-drawer` (Caderno de Estudos).
    - `static/css/style.css`: folha de estilo única com variáveis CSS para três temas (`dark`, `light`, `sepia`) e regras responsivas de `320px` a `1920px`.
  - Ambos `app.js` (linhas 38–59) e `reader.js` (linhas 14–35) sobrescrevem `window.fetch` para injetar automaticamente o cabeçalho `X-CSRF-Token` a partir do cookie `lectoria_csrf` em métodos mutáveis.

### 2.3. Modelo de Dados Atual (Schema SQLite v12)
- **Fatos verificados no código (`database.py`)**:
  - O banco SQLite opera com `PRAGMA journal_mode=WAL;`, `PRAGMA synchronous=NORMAL;` e `PRAGMA foreign_keys=ON;` em cada conexão aberta por `get_connection()` (`database.py:17-29`).
  - O schema `v12` contém 31 tabelas (incluindo a tabela virtual `books_fts`):
    1. Núcleo e autenticação: `schema_migrations`, `users`, `sessions`, `library_sources`, `books`, `reading_progress`.
    2. Organização de acervo: `collections`, `collection_books`, `categories`, `book_categories`, `subjects`, `book_subjects`, ` books_fts`.
    3. Metadados e automação: `metadata_candidates`, `metadata_api_cache`, `metadata_provenance`, `metadata_provider_stats`, `scheduled_jobs`, `background_jobs`, `admin_audit_log`, `system_settings`.
    4. Estudo e leitura no livro: `highlights`, `notes`, `page_drawings`, `bookmarks`.
    5. Social e clubes: `user_profiles`, `user_privacy_settings`, `book_reviews`, `book_recommendations`, `shared_annotations`, `annotation_comments`, `reading_activity`, `reading_clubs`, `reading_club_members`, `reading_club_invites`, `collective_reads`, `collective_read_members`, `collective_discussion_topics`, `collective_discussion_posts`, `collective_reading_goals`, `collective_goal_progress`, `collective_shared_quotes`.

### 2.4. Identidade Canônica dos Livros
- **Fatos verificados no código (`library.py:189-214, 347`, `annotations_service.py:157-189`)**:
  - Cada livro possui um identificador primário textual (`books.id TEXT PRIMARY KEY`), gerado como UUID v4 (`str(uuid.uuid4())`) para novos livros (ou hash hexadecimal de 16 caracteres em registros legados).
  - Quando um arquivo é movido ou renomeado dentro da mesma fonte (`library.py:192-213`), `scan_library()` reconcilia o registro comparando `filesize` e `file_hash` (`"{size}:{mtime}"`), atualizando `rel_path` e `filename` **sem alterar `books.id`**.
  - Todas as tabelas de anotações e leitura (`highlights`, `notes`, `page_drawings`, `bookmarks`, `reading_progress`, `collective_reads`) referenciam `books(id) ON DELETE CASCADE`.
  - A função `_verify_book_access(cursor, book_id, user_id)` (`annotations_service.py:157-189`) exige que `books.id` exista, tenha `is_missing = 0` e pertença a uma fonte ativa (`library_sources.is_active = 1`).

### 2.5. Identidade e Ciclo de Vida das Anotações
- **Fatos verificados no código (`database.py:1438-1597`, `annotations_service.py:231-1148`)**:
  - Existem quatro entidades na camada de estudo do leitor, todas com `id TEXT PRIMARY KEY` gerado via `str(uuid.uuid4())`:
    - `highlights`: marcações de trecho (`book_id`, `user_id`, `page`, `color`, `rects_json`, `selected_text`, `text_context`, `visibility`, `epub_cfi`, `chapter_label`, `created_at`, `updated_at`).
    - `notes`: anotações textuais (`book_id`, `user_id`, `page`, `highlight_id`, `content`, `color`, `visibility`, `epub_cfi`, `chapter_label`, `created_at`, `updated_at`). A FK `highlight_id` usa `REFERENCES highlights(id) ON DELETE SET NULL`, ou seja, excluir um destaque preserva a nota como nota avulsa da página/CFI.
    - `page_drawings`: desenhos vetoriais por página (`book_id`, `page`, `user_id`, `strokes_json`, `visibility`, `created_at`, `updated_at`, com `UNIQUE(book_id, page, user_id)`).
    - `bookmarks`: marcadores rápidos (`book_id`, `user_id`, `page`, `epub_cfi`, `chapter_label`, `label`, sempre privados).
  - **Observação crítica sobre exclusão**: diferentemente de `collective_discussion_topics`, `collective_discussion_posts` e `collective_shared_quotes` (que usam `is_deleted = 1`), as tabelas `highlights`, `notes`, `page_drawings` e `bookmarks` utilizam **exclusão física (`DELETE FROM ...`)** (`annotations_service.py:468-475, 785-792, 1016-1019, 1138-1146`).

### 2.6. Representação de Páginas PDF
- **Fatos verificados no código (`annotations_service.py:41-109`, `static/js/reader.js:619-637, 800-950`)**:
  - Páginas PDF são inteiros baseados em 1 (`page >= 1`).
  - Destaques PDF armazenam retângulos normalizados `[0.0..1.0]` em `rects_json` (`x`, `y`, `w`, `h` relativos às dimensões da página renderizada).
  - Desenhos PDF armazenam traços normalizados `[0.0..1.0]` em `strokes_json` e são renderizados em camadas `<svg>` com `viewBox="0 0 1000 ${vbHeight}"` isotrópico.
  - Navegação direta por URL utiliza `/reader/{book_id}#page=N` (`parseHashLocator()` em `reader.js:310-329`).

### 2.7. Representação de Posições EPUB por CFI
- **Fatos verificados no código (`annotations_service.py:131-143`, `static/js/reader.js:321-326, 1781-1801, 3200-3550`)**:
  - Localizadores EPUB utilizam o padrão **EPUB CFI** (`epubcfi(...)`), validado por `_validate_epub_cfi()` (`annotations_service.py:131-143`: comprimento máximo de 1.024 caracteres, prefixo `epubcfi(`, sufixo `)`, proibido `<` ou `>`).
  - Em `highlights`, `notes` e `bookmarks`, `epub_cfi` coexiste com `page` (inteiro aproximado de progresso/capítulo `>= 1`) e `chapter_label` (rótulo textual do capítulo até 300 caracteres).
  - A ordenação lógica de CFIs no frontend é realizada por `compareEpubCfi(cfiA, cfiB)` (`reader.js:1781-1801`), que delega para `new ePub.CFI().compare(cfiA, cfiB)` ou aplica comparação numérica passo a passo após remover asserções `[...]`.
  - Navegação direta por URL utiliza `/reader/{book_id}#cfi=epubcfi(...)` e `navigateToEpubCfi(cfi, fallbackChapterLabel)`.

### 2.8. Organização de Usuários e Permissões
- **Fatos verificados no código (`auth_service.py`, `main.py:806-846`, `social_service.py:42-125`)**:
  - Dois papéis globais em `users.role`: `'admin'` e `'reader'`, mais flag `users.is_active` (`1` ou `0`).
  - Três papéis locais em clubes (`reading_club_members.role`): `'owner'`, `'moderator'`, `'member'`.
  - Dados públicos de perfil residem em `user_profiles` (`display_name`, `avatar_color`, `bio`).
  - Configurações globais de privacidade residem em `user_privacy_settings` (`share_current_reading`, `share_reading_history`, `share_reviews`, `share_lists`, `share_annotations_default`), todas iniciadas com `0` (privado por padrão).

### 2.9. Lógica de Visibilidade das Anotações
- **Fatos verificados no código (`annotations_service.py:25-27, 111-129, 217-225`)**:
  - Nas tabelas `highlights`, `notes` e `page_drawings`, `visibility` admite exclusivamente `'private'` (default) ou `'public'`.
  - `_build_scope_clause(table_alias, scope, user_id)` filtra consultas em três escopos:
    - `mine`: `{alias}.user_id = ?`
    - `public`: `{alias}.visibility = 'public'`
    - `all_visible`: `({alias}.user_id = ? OR {alias}.visibility = 'public')`
  - **Proteção cruzada já existente em `get_note_by_id` (`annotations_service.py:602-608`)**: se uma nota for lida e referenciar um `highlight_id` cujo destaque seja privado e pertença a outro usuário, `highlight_text` e `highlight_color` são suprimidos (`None`).
  - Todas as rotas de leitura de anotações retornam `Cache-Control: private, no-store` (`main.py:1996, 2033, 2261, 2358`), garantindo efeito imediato quando uma anotação pública é revertida para privada.

### 2.10. Funcionamento do Caderno de Estudos
- **Fatos verificados no código (`static/reader.html:331-408`, `static/js/reader.js:1756-2350`)**:
  - O Caderno de Estudos (`#annotations-drawer`) é uma gaveta lateral **exclusiva da página do leitor (`reader.html`)**, acoplada a um único `bookId`.
  - Possui estado próprio (`drawerState`, linhas 1757–1771): `scope` (`all_visible`, `mine`, `public`), `filter` (`all`, `highlights`, `notes`, `drawings`, `bookmarks`), `sort` (`position`, `recent`), `search`, `limit: 50`, `offset: 0`, `hasMore`, `requestSeq` e `AbortController`.
  - **Limitação atual**: não existe uma visão global do Caderno de Estudos fora do leitor (`index.html`), nem possibilidade de visualizar lado a lado anotações de múltiplos livros.

### 2.11. Sistema Atual de Taxonomias e Coleções
- **Fatos verificados no código (`taxonomy_service.py`, `collections_service.py`, `database.py:288-326, 625-655, 1039-1145`)**:
  - Existem três estruturas distintas hoje:
    1. **Categorias da Biblioteca (`categories` e `book_categories`)**: globais à instância, gerenciadas por administradores (5 eixos padrão: *Filosofia*, *Literatura*, *Ciências humanas*, *Ciência e tecnologia*, *Artes e cultura*, mais categorias customizadas), vinculadas apenas a **livros** (`book_id`).
    2. **Assuntos e Facetas Bibliográficas (`subjects` e `book_subjects`)**: globais à instância, armazenam `name`, `category`, `subcategory`, `genre`, `origin`, `period`, vinculadas apenas a **livros** (`book_id`).
    3. **Coleções Pessoais (`collections` e `collection_books`)**: privadas por usuário (`collections.user_id`), possuem apenas `id`, `name`, `description`, `user_id`, `created_at` e associam apenas **livros** (`collection_books.book_id`) sem ordem explícita, sem notas, sem capítulos e sem conceitos.

### 2.12. Mecanismos de Pesquisa
- **Fatos verificados no código (`database.py:696-717`, `library.py:50-78, 532-544`, `annotations_service.py:388-396, 663-671`)**:
  - **Na Biblioteca (`library.py`)**:
    - Parâmetro `search`: usa `LIKE %term%` sobre `b.title`, `b.author`, `b.isbn13`, `b.isbn10`.
    - Parâmetro `fts_query`: usa a tabela virtual SQLite **FTS5** `books_fts(book_id UNINDEXED, title, author, description, publisher, subjects)` com busca por prefixo (`"termo"*`).
    - Nota técnica: `update_book_fts()` em `library.py:330, 410, 1422` passa `isbn13` no 6º parâmetro (coluna `subjects` de `books_fts`), e `books_fts` usa o tokenizador padrão `unicode61` sem configuração explícita de `remove_diacritics 2`.
  - **No Caderno de Estudos (`annotations_service.py`)**:
    - Usa `LIKE %term% COLLATE NOCASE` restrito a um único `book_id` sobre `selected_text`, `text_context`, `content`, `chapter_label` e `author_display_name`.
  - **No Leitor PDF (`reader.js`)**:
    - Busca incremental página a página via `page.getTextContent()` com cache em memória (`pdfPageTextCache`).
  - **Limitação atual**: não existe pesquisa global que atravesse simultaneamente livros, destaques, notas de múltiplos livros, conceitos ou reflexões.

### 2.13. Exportações Existentes
- **Fatos verificados no código (`export_service.py:34-192`, `main.py:2495-2536`)**:
  - Existem dois formatos de exportação, ambos **por livro individual** (`/api/books/{book_id}/export/markdown` e `/api/books/{book_id}/export/json`) e restritos a `scope="mine"`:
    - Markdown (`.md`): documento linear agrupado por `## Página N`, contendo citações (`> "..."`) e notas (`- 📝 **Nota:** ...`).
    - JSON (`.json`): schema `"format": "lectoria_study_export"`, `"version": "1.0"`, com `book`, `highlights`, `notes`, `drawings` e `bookmarks`.
  - **Limitação atual**: não há exportação global do conhecimento do usuário (todos os livros estudados, conceitos, reflexões ou arquivo ZIP de notas interligadas).

### 2.14. Eventos e Fluxos de Navegação
- **Fatos verificados no código (`static/js/app.js`, `static/js/reader.js`, `static/js/shared_reading.js`)**:
  - A navegação entre a biblioteca (`/`) e o leitor (`/reader/{book_id}`) ocorre por carregamento de página completa (`window.location.href` ou `<a href="/reader/{id}#page=N">`).
  - Dentro de `index.html`, a navegação entre seções ocorre via `setMainTab(view)` (`discovery`, `library`, `social`, `clubs`, `admin`), persistindo a aba ativa em `localStorage.getItem("lectoria_main_tab")`.
  - Dentro de `reader.html`, a navegação interna utiliza hash locators (`#page=N` e `#cfi=epubcfi(...)`), `goToPage(num)` e `navigateToEpubCfi(cfi, fallbackChapterLabel)` com realce visual temporário de 1,8 segundo no elemento de destino.

### 2.15. Pontos de Extensão Disponíveis
- **Fatos verificados no código**:
  1. **Barra de seleção do leitor (`#selection-toolbar` em `reader.html:225-239`)**: já acomoda botões contextuais compactos (`Nota`, `Compartilhar na Leitura Coletiva`) e pode receber um gatilho discreto de vínculo conceitual/conexão ou integrá-lo ao próprio fluxo do modal/popover.
  2. **Popovers de destaque e nota (`#highlight-popover` e `#page-note-popover` em `reader.html:276-307`)**: possuem contêineres de metadados e ações (`#hl-pop-actions`, `#note-pop-actions`) ideais para exibir chips de conceitos associados, conexões e botão de vinculação.
  3. **Modal de edição de nota (`#modal-note-editor` em `reader.html:411-440`)**: estrutura limpa de formulário onde seletores opcionais de conceitos e referências podem ser inseridos sem poluir a leitura.
  4. **Gaveta do Caderno de Estudos (`#annotations-drawer` em `reader.html:331-408`)**: estrutura de abas `.drawer-tabs` e cartões de anotação que já exibem badges de tipo, autor e localizador.
  5. **Barra de navegação principal (`nav.main-view-nav .main-view-tabs` em `index.html:103-136`)**: comporta uma nova seção principal ao lado de *Descoberta*, *Biblioteca*, *Nossa Biblioteca* e *Clubes de Leitura*, já contando com rolagem horizontal suave e máscara de gradiente no mobile (`style.css`).
  6. **Modal de detalhes do livro (`#modal-book-details` em `index.html`)**: possui abas internas onde indicadores discretos de conhecimento associado à obra podem ser exibidos.

### 2.16. Dependências e Limitações Técnicas
- **Fatos verificados no código (`requirements.txt`, `Dockerfile`, `AGENTS.md`)**:
  - `requirements.txt` possui apenas 3 dependências de produção: `fastapi>=0.110.0`, `uvicorn>=0.28.0` e `pypdf>=4.0.0`.
  - O banco SQLite 3 da biblioteca padrão do Python 3.12 suporta nativamente **CTEs recursivas (`WITH RECURSIVE`)**, **triggers**, **chaves estrangeiras**, **índices parciais** e **FTS5** (`CREATE VIRTUAL TABLE ... USING fts5` já utilizado em `database.py:699`).
  - O frontend não utiliza bibliotecas externas além de `PDF.js` e `EPUB.js + JSZip` em `static/vendor/`. Qualquer renderização gráfica (como mapa de conhecimento em SVG/Canvas) ou parser leve de Markdown/referências deve ser implementada em Vanilla JS enxuto, mantendo consumo mínimo de memória RAM e CPU (meta de execução em VPS de 4 GB de RAM compartilhada e mini-PCs modestos).

---

## 3. Classificação dos Componentes Existentes para a v1.9.0

| Componente / Módulo | Classificação | Evidência no Código (`arquivo:função/estrutura`) | Justificativa Técnica |
| :--- | :--- | :--- | :--- |
| **Resolução Canônica e Acesso a Livros** | **Reutilizável diretamente** | `library.py:get_book`, `annotations_service.py:_verify_book_access` | Já valida existência por `books.id`, `is_missing = 0`, fonte ativa (`library_sources.is_active = 1`) e usuário ativo. |
| **Navegação por Localizador no Leitor (`#page=N` e `#cfi=...`)** | **Reutilizável diretamente** | `static/js/reader.js:parseHashLocator`, `goToPage`, `navigateToEpubCfi` | Permite saltar de qualquer conceito, reflexão, backlink ou grafo diretamente para a página PDF ou posição EPUB CFI exata com fallback seguro. |
| **Infraestrutura de Migração e Backup SQLite** | **Reutilizável diretamente** | `database.py:init_db`, `backup_service.py:create_backup` | Realiza `PRAGMA integrity_check`, backup pré-migração único (`.db.bak`) via SQLite Online Backup API e rollback transacional. |
| **Autenticação, Sessões e Middleware CSRF** | **Reutilizável diretamente** | `auth_service.py`, `main.py:csrf_and_security_middleware`, `require_user` | Garante autenticação por cookie `HttpOnly`, validação CSRF e isolamento de sessão. |
| **Serviço de Anotações (`highlights`, `notes`, `bookmarks`)** | **Reutilizável mediante extensão** | `annotations_service.py:get_highlights`, `get_notes`, `get_note_by_id` | Já possui UUIDs estáveis, escopos (`mine`, `public`, `all_visible`), `epub_cfi` e metadados de autoria; precisa ser estendido para expor contadores/resumos de conceitos, conexões e backlinks visíveis ao usuário. |
| **Popovers e Caderno de Estudos no Leitor** | **Reutilizável mediante extensão** | `static/reader.html:#highlight-popover`, `#page-note-popover`, `#annotations-drawer`; `static/js/reader.js:drawerState` | A estrutura de cartões e popovers já existe e tem paginação/busca com `AbortController`; pode ser estendida para exibir e criar vínculos conceituais, relações entre notas e backlinks. |
| **Coleções Pessoais (`collections`)** | **Reutilizável mediante extensão** (para Trilhas Temáticas, se aprovado) | `collections_service.py`, tabelas `collections` e `collection_books` | Já são privadas por usuário e exibidas na biblioteca; mediante extensão simples (ou vinculação a conceitos/notas/ordem de leitura), podem cumprir o papel de trilhas sem duplicar subsistemas. |
| **Serviço de Exportação (`export_service.py`)** | **Reutilizável mediante extensão** | `export_service.py:export_book_annotations_markdown`, `export_book_annotations_json` | Hoje exporta apenas um livro por vez (`scope='mine'`); pode ser estendido para exportar pacotes de conhecimento (Markdown interligado/ZIP, JSON global, CSV). |
| **Indexação Textual FTS5 (`books_fts`)** | **Necessitando adaptação** | `database.py:698-715`, `library.py:update_book_fts` | Atualmente indexa apenas `books` (e grava `isbn13` na coluna `subjects`). Para pesquisa integrada, requer índice FTS5 dedicado ou federado para entidades de conhecimento respeitando rigorosamente permissões por `user_id` e `visibility`. |
| **Política de Exclusão de Destaques e Notas** | **Necessitando adaptação** | `annotations_service.py:delete_highlight`, `delete_note` | Hoje executa `DELETE` físico direto. Quando uma anotação pública é referenciada pela rede privada de outro usuário (ou se torna privada), o sistema precisa limpar ou sinalizar referências inacessíveis sem vazar dados nem quebrar integridade. |
| **Domínio de Conhecimento Conectado (Conceitos, Conexões, Reflexões, Backlinks)** | **Necessitando nova implementação** | Inexistente no código atual (proposto: `knowledge_service.py`) | Não existem tabelas nem serviços para conceitos independentes de livros, conexões semânticas entre anotações, reflexões autorais multi-livro ou consulta de backlinks. |
| **Espaço de Conhecimento e Visualização de Grafo / Alternativa Textual** | **Necessitando nova implementação** | Inexistente no código atual (proposto em `static/index.html` e `static/js/knowledge.js`) | Requer nova seção na interface principal, visualizador de grafo leve em SVG/Canvas com expansão progressiva e explorador textual acessível. |
| **Taxonomia Global de Acervo (`categories`, `subjects`)** | **Inadequado para o novo domínio** (como substituto de Conceitos pessoais) | `taxonomy_service.py`, tabelas `categories`, `book_categories`, `subjects`, `book_subjects` | `categories` e `subjects` são **globais à instância**, voltados à catalogação bibliográfica de **livros inteiros** e gerenciados por administradores ou varredura automática. Usar `subjects` para Conceitos pessoais violaria a privacidade por usuário, misturaria catalogação de livros com síntese intelectual de trechos e impediria descrições autorais privadas. |
| **Mural Social `shared_annotations` (v1.5.0)** | **Inadequado para o novo domínio** | `social_service.py`, tabela `shared_annotations` | É um feed comunitário de snapshots soltos sem posicionamento canônico obrigatório nem grafo de relações; não substitui anotações estruturadas (`highlights`/`notes`) nem conexões conceituais. |

---

## 4. Síntese das Descobertas da Auditoria

1. **Base sólida de ancoragem nas fontes**: A v1.8.0 entregou identificadores UUID estáveis para livros (`books.id`), destaques (`highlights.id`) e notas (`notes.id`), além de posicionamento preciso e testado para páginas PDF (`#page=N`) e trechos EPUB (`#cfi=epubcfi(...)`). Isso significa que o domínio de Conhecimento Conectado da v1.9.0 **não precisa reinventar o leitor nem o sistema de âncoras**.
2. **Distinção clara entre Taxonomia de Biblioteca e Conceitos Intelectuais**: O sistema atual possui `categories` e `subjects` (globais, voltados a classificar livros na estante) e `collections` (agrupamento pessoal de livros). Nenhum deles modela conceitos intelectuais do leitor vinculados a trechos específicos, notas e reflexões com descrição autoral privada.
3. **Três camadas de anotações coexistentes na base atual**:
   - `highlights` / `notes` / `page_drawings` (`annotations_service.py` — v1.8.0): anotações vivas diretamente nas páginas do livro, com visibilidade `'private'` ou `'public'`. **Esta é a camada canônica sobre a qual o Conhecimento Conectado deve operar.**
   - `collective_shared_quotes` (`shared_reading_service.py` — v1.7.0): snapshots compartilhados dentro de uma leitura coletiva de clube.
   - `shared_annotations` (`social_service.py` — v1.5.0): snapshots publicados no mural social "Nossa Biblioteca".
4. **Observação de manutenção de segurança verificada na v1.8.0**:
   - Em `static/js/reader.js:605-610` (`emergencySave()`) e `main.py:249-252`, o token CSRF é enviado na query string (`?csrf_token=...`) quando `navigator.sendBeacon` é acionado no `beforeunload`/`pagehide`. Embora funcional, tokens em URLs ficam expostos em logs de acesso HTTP (Uvicorn/Caddy) e histórico de rede. Essa observação foi documentada na auditoria de segurança (`06-security-and-privacy.md`) conforme instruído.
