# Lectoria v1.9.0 — Fase 6: Pesquisa Integrada do Atlas e Motor SQLite FTS5

## 1. Objetivo e Escopo (`KD-008`)

A **Pesquisa Integrada do Atlas** (`GET /api/knowledge/search`) permite localizar, em uma única interface e contrato REST, conceitos pessoais (`concept`), reflexões autorais (`reflection`), destaques (`highlight`), notas (`note`), justificativas de conexões semânticas (`link`) e metadados de livros acessíveis (`book`).

O motor reutiliza a tabela virtual SQLite FTS5 `knowledge_fts` criada na migração do schema `v13` (`tokenize='unicode61 remove_diacritics 2'`), aplicando **Dupla Barreira** com validação relacional integrada antes da paginação e revalidação ao vivo em tempo de leitura.

---

## 2. Entidades e Campos Indexados vs. Fontes Complementares

| Tipo de Entidade | `index_source` | Fonte Primária de Índice | Campos Pesquisados e Revalidados | Regra de Autorização Relacional |
| :--- | :--- | :--- | :--- | :--- |
| `concept` | `knowledge_fts` | `knowledge_fts` + `concepts` | `name`, `description`, `tags_json` | Estritamente privado (`c.user_id = :uid`) |
| `reflection` | `knowledge_fts` | `knowledge_fts` + `reflections` | `title`, `body_markdown`, `tags_json` | Estritamente privada (`r.user_id = :uid`) |
| `highlight` | `knowledge_fts` | `knowledge_fts` + `highlights` | `selected_text`, `text_context`, `chapter_label` | Livro presente (`b.is_missing = 0`), fonte ativa (`COALESCE(s.is_active, 1) = 1`) e `(h.user_id = :uid OR h.visibility = 'public')` |
| `note` | `knowledge_fts` | `knowledge_fts` + `notes` | `content`, `chapter_label` | Livro presente (`b.is_missing = 0`), fonte ativa (`COALESCE(s.is_active, 1) = 1`) e `(n.user_id = :uid OR n.visibility = 'public')` |
| `link` | `knowledge_fts` | `knowledge_fts` + `knowledge_links` | `relation_type`, `rationale` | Estritamente privada (`kl.user_id = :uid`) com hidratação segura dos extremos |
| `book` | `books_metadata` | Tabela relacional `books` | `title`, `author` | Livro presente (`b.is_missing = 0`) e fonte ativa (`COALESCE(s.is_active, 1) = 1`) |

Quando o fallback relacional controlado é acionado (`force_fallback=True` ou ausência do módulo FTS5), os itens retornam `index_source = "relational_fallback"` e `search_engine = "relational_fallback"`.

---

## 3. Normalização Unicode e Construção Segura de Consultas FTS5

1. **Validação e Normalização (`normalize_unicode_nfc`)**:
   - Converte a string de entrada para a forma canônica composta **Unicode NFC**.
   - Rejeita bytes nulos (`\x00`), tipos não textuais e codepoints substitutos UTF-16 órfãos (`0xD800..0xDFFF`) com `KnowledgeValidationError` (`HTTP 400`).
   - Impõe comprimento máximo de `MAX_SEARCH_QUERY_LEN = 300` caracteres.
2. **Construção de Expressão FTS5 (`_build_safe_fts_query`)**:
   - Extrai apenas tokens alfanuméricos Unicode via `_FTS_TOKEN_RE = re.compile(r"\w+", re.UNICODE)` (limitados a `MAX_SEARCH_TOKENS = 16`).
   - Envolve cada token em aspas duplas com operador de prefixo (`"token"*`) e combina múltiplos termos com `AND`.
   - Neutraliza integralmente operadores especiais FTS5 (`NEAR`, `NOT`, `OR`, `AND`, `^`, `:`, `*`), aspas desbalanceadas e parênteses malformados, jamais concatenando entrada bruta na cláusula `MATCH`.

---

## 4. Dupla Barreira, Paginação sem Esgotamento e Isolamento de Relevância (Sem Vazamento BM25 IDF)

### 4.1. Autorização Relacional Integrada Antes da Paginação
Na consulta SQL sobre `knowledge_fts`, cada candidato precisa satisfazer simultaneamente um predicado `EXISTS (...)` contra sua tabela relacional canônica (`concepts`, `reflections`, `knowledge_links`, `highlights JOIN books LEFT JOIN library_sources`, `notes JOIN books LEFT JOIN library_sources`).
- Isso impede que um volume dominante de registros inacessíveis de outros usuários (ex.: milhares de conceitos ou notas privadas de terceiros contendo o mesmo termo) ocupe os slots de candidatos e esgote ou distorça a paginação do usuário autenticado.

### 4.2. Revalidação Ao Vivo Contra Índices FTS5 Obsoletos
Para cada candidato retornado pelo FTS5, o serviço lê a linha relacional atualizada e verifica com `_text_matches_all_folded_tokens(live_text, folded_tokens)` se todos os tokens da busca realmente constam nos dados relacionais vigentes. Entradas obsoletas ou fantasmas no FTS5 são descartadas silenciosamente.

### 4.3. Relevância Determinística sem Vazamento Estatístico entre Usuários
A função nativa `bm25(knowledge_fts)` do SQLite utiliza a frequência inversa de documentos (IDF) calculada sobre **todas** as linhas da tabela virtual global. Para evitar que documentos privados de um usuário alterem a pontuação ou a ordem de classificação dos resultados de outro usuário, a Fase 6 calcula a relevância exclusivamente sobre os campos relacionais do próprio item autorizado (`_compute_authorized_relevance_score`):
- Correspondência exata ou por prefixo do termo completo no título/nome;
- Ocorrências exatas e por prefixo de cada token no título, nas etiquetas e no corpo/descrição;
- Bônus leve de autoria própria (`is_own`), seguido de desempate determinístico `(-score, entity_type ASC, entity_id ASC)`.
- Nenhum score numérico interno é exposto na resposta da API.

---

## 5. Geração Segura de Snippets (`_build_safe_snippet_segments`)

- Os snippets são construídos em Python exclusivamente a partir do texto relacional já autorizado (`max_len = 220` caracteres).
- A API retorna:
  - `snippet`: string em texto puro com reticências contextuais (`…`);
  - `snippet_segments`: array estruturado `[{"text": "...", "highlight": true|false}]`.
- No frontend (`static/js/knowledge.js`), `buildSafeSnippetDom` constrói apenas `document.createTextNode` e elementos `<mark class="atlas-search-mark">` com `textContent`, sem jamais utilizar `innerHTML`.
- Referências indisponíveis em conexões (`available = false`) nunca têm títulos ou trechos incluídos em snippets.

---

## 6. Fallback Relacional Controlado vs. Corrupção Estrutural

- Se o SQLite reportar indisponibilidade do módulo/tabela FTS5 (`no such module: fts5` ou `no such table: knowledge_fts`), `search_knowledge` aciona `_search_knowledge_relational_fallback`, mantendo as mesmas regras de permissão, paginação, relevância e geração de `snippet_segments`.
- Se o SQLite reportar corrupção estrutural (`malformed` ou `corrupt`), o erro **não** é mascarado pelo fallback e é propagado para diagnóstico operacional.
