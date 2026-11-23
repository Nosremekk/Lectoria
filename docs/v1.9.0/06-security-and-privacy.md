# Segurança, Autorização e Privacidade — Lectoria v1.9.0

- **Versão Planejada**: `v1.9.0 — Conhecimento Conectado`
- **Natureza do Documento**: Modelo estrutural de segurança, matriz de autorização por entidade, análise de vetores de ameaça (IDOR, XSS, CSRF, vazamentos indiretos, revogações) e registro de manutenção da v1.8.0.

---

## 1. Princípios Estruturais de Privacidade no Domínio de Conhecimento

1. **Privacidade Estrita por Padrão**:
   - Todo **Conceito** (`concepts`), **Reflexão Autoral** (`reflections`) e **Conexão de Conhecimento** (`knowledge_links`) pertence exclusivamente ao usuário que o criou (`user_id == viewer_id`) e nasce estritamente privado.
   - Nem mesmo administradores (`role == 'admin'`) possuem acesso de leitura, listagem ou busca aos conceitos, reflexões ou conexões privadas de outros usuários.
2. **Separação entre a Privacidade da Conexão e a Privacidade dos Elementos Conectados**:
   - Uma conexão (`knowledge_links`) possui seu próprio proprietário (`link.user_id`).
   - Os elementos conectados (`source` e `target`) possuem seus próprios proprietários e regras de visibilidade independentes.
   - **Regra de ouro**: a existência de uma conexão privada criada pelo Usuário B apontando para uma anotação do Usuário A **nunca** concede ao Usuário B direito perpétuo de leitura sobre a anotação de A, e **nunca** revela ao Usuário A que B referenciou sua anotação.
3. **Verificação em Tempo de Consulta (*Read-Time Authorization*), Nunca Apenas na Escrita**:
   - Validar permissões apenas no momento do `POST /api/knowledge/links` é insuficiente, pois o mundo muda após a criação do vínculo: uma anotação pública pode voltar a ser privada, um livro pode ser removido do disco (`is_missing = 1`) ou uma fonte de biblioteca pode ser desativada (`library_sources.is_active = 0`).
   - Portanto, **toda leitura** de conceitos, conexões, backlinks, grafos, pesquisas FTS5 e exportações deve revalidar a autorização atual do `viewer_id` sobre ambos os extremos (`source` e `target`).

---

## 2. Análise Detalhada dos 13 Vetores de Ameaça e Mitigações

### 2.1. IDOR (Insecure Direct Object Reference)
- **Ameaça**: Usuário B tenta ler, editar, excluir ou conectar um `concept_id`, `reflection_id`, `link_id`, `note_id` privado ou `highlight_id` privado pertencente ao Usuário A.
- **Mitigação**:
  - Todas as consultas a `concepts`, `reflections` e `knowledge_links` incluem obrigatoriamente `WHERE id = ? AND user_id = ?`.
  - Se o registro pertencer a outro usuário, o backend retorna estritamente **`HTTP 404 Not Found`** (anti-enumeração, impedindo confirmar se o UUID existe).
  - Ao criar um vínculo em `POST /api/knowledge/links`, o serviço verifica ambos os extremos:
    - Se `type IN ('concept', 'reflection')`: exige `entity.user_id == viewer_id` (retorna `404` caso contrário).
    - Se `type IN ('highlight', 'note')`: exige que o livro exista e esteja ativo (`_verify_book_access`) **e** que `(entity.user_id == viewer_id OR entity.visibility == 'public')` (retorna `404` se for privada de terceiro).

### 2.2. XSS (Cross-Site Scripting) em Reflexões, Conceitos e Markdown (Seção 13.5)
- **Ameaça**: Injeção de `<script>`, `<img src=x onerror=...>`, `<svg onload=...>`, atributos `on*`, `style` malicioso ou links `[clique](javascript:alert(1))` / `data:text/html,...` / `vbscript:...` dentro do corpo Markdown (`body_markdown`) de uma reflexão, descrição de conceito ou justificativa de conexão.
- **Mitigação Estrutural e Separação entre Armazenamento e Renderização**:
  1. **Na Persistência (`knowledge_service.py` — Fase 1)**:
     - O backend armazena `body_markdown` como texto Markdown puro normalizado em Unicode NFC (validando limites de tamanho `title <= 300`, `body_markdown <= 50000`, `name <= 160`, `description <= 10000`, `rationale <= 2000` e rejeitando bytes nulos `\x00`), preservando a fidelidade autoral para edição e exportação sem executar nem confiar em HTML embutido.
  2. **Na Renderização Frontend (`knowledge.js`, `reader.js`, `app.js` — Fases 3 a 5)**:
     - **Apenas `escapeHtml()` isolado não constitui uma política completa de Markdown seguro** quando links e imagens ou atributos são gerados após o escape.
     - Portanto, antes de qualquer renderização visual de Markdown na Fase 5, o renderizador deverá obrigatoriamente implementar uma política completa de sanitização:
       - Bloqueio integral de HTML bruto (escapando `&`, `<`, `>`, `"`, `'` antes do parsing de tokens Markdown).
       - Whitelist estrita de elementos gerados (`p`, `h4`, `h5`, `h6`, `strong`, `em`, `code`, `pre`, `blockquote`, `ul`, `ol`, `li`, `a`).
       - Proibição absoluta de atributos de evento (`on*`), `style`, `srcdoc`, `formaction` e tags ativas (`script`, `iframe`, `object`, `embed`, `svg`, `math`, `style`, `link`, `meta`).
       - Validação rigorosa de esquemas de URL em links (`href`): permitir exclusivamente caminhos internos relativos seguros (`/reader/...`, `#...`) ou URLs `https://` / `http://` explícitas com `rel="noopener noreferrer" target="_blank"`, bloqueando `javascript:`, `data:`, `vbscript:`, `file:` e variações com entidades HTML, caracteres de controle ou espaços codificados.

### 2.3. CSRF (Cross-Site Request Forgery)
- **Ameaça**: Site externo malicioso tenta induzir o navegador autenticado a criar, editar ou apagar conceitos, reflexões ou conexões.
- **Mitigação**:
  - Todas as rotas mutáveis (`POST`, `PUT`, `PATCH`, `DELETE`) em `/api/knowledge/*` passam pelo `csrf_and_security_middleware` (`main.py:216-281`), exigindo correspondência de `Origin`/`Referer` com `Host` e cabeçalho `X-CSRF-Token` idêntico ao cookie `lectoria_csrf`.

### 2.4. Acesso Indireto a Anotações Privadas
- **Ameaça**: Usuário B tenta criar uma conexão com um `note_id` chutado ou obtido anteriormente para ler o texto da nota privada através do endpoint de detalhes da conexão ou do grafo.
- **Mitigação**:
  - Como `create_knowledge_link` exige acesso de leitura no momento da criação e `resolve_link_endpoints` revalida `(n.user_id == viewer_id OR n.visibility == 'public')` em toda leitura, anotações privadas de terceiros nunca podem ser vinculadas nem resolvidas.

### 2.5. Vazamentos por Pesquisa Integrada (FTS5)
- **Ameaça**: Uma busca em `/api/knowledge/search?q=termo` consultar a tabela virtual `knowledge_fts` sem filtrar `user_id` ou `visibility`, retornando trechos de notas privadas ou reflexões de outros usuários.
- **Mitigação**:
  - Toda consulta a `knowledge_fts` aplica filtro obrigatório por `user_id` e faz `JOIN` de verificação com as tabelas reais (`concepts`, `reflections`, `highlights`, `notes` e `books` + `library_sources`), descartando qualquer linha cujo item base não seja próprio nem público em livro ativo.

### 2.6. Vazamentos por Backlinks
- **Ameaça**: Alice cria uma anotação pública `H_1`. Bob cria uma nota privada `N_priv` ou um conceito privado `C_priv` e o conecta a `H_1`. Se Alice consultar os backlinks de `H_1` e o sistema mostrar `N_priv` ou `C_priv` (ou mesmo informar `"1 backlink privado de Bob"`), a privacidade de Bob foi violada.
- **Mitigação**:
  - A tabela `knowledge_links` é isolada por `knowledge_links.user_id = :viewer_id`.
  - Quando Alice consulta os backlinks de `H_1`, o sistema busca apenas `WHERE knowledge_links.user_id = :alice_id`. As conexões privadas de Bob são 100% invisíveis para Alice (nem o conteúdo nem a existência/contagem da conexão de Bob são revelados).

### 2.7. Vazamentos por Grafos
- **Ameaça**: A travessia de 2 saltos do grafo (`depth = 2`) atravessar uma anotação pública e puxar nós privados de outro usuário conectados a ela.
- **Mitigação**:
  - A query de expansão de vizinhança percorre exclusivamente arestas de `knowledge_links` onde `knowledge_links.user_id = :viewer_id`. Como um usuário só percorre suas próprias arestas e só resolve nós próprios ou públicos ativos, é matematicamente impossível atravessar para a rede privada de outro usuário.

### 2.8. Vazamentos por Contagem de Entidades
- **Ameaça**: Contadores em badges de livros (`"Conceitos associados"`, `"Conexões"`) ou em popovers somarem conexões privadas de outros usuários, permitindo inferir atividade privada alheia.
- **Mitigação**:
  - Todos os contadores de conceitos, reflexões, conexões e backlinks retornados pela API são calculados com `WHERE user_id = :viewer_id`.

### 2.9. Vazamentos por Exportação
- **Ameaça**: O pacote de exportação (`.zip`, `.json`, `.csv`) incluir anotações privadas de terceiros ou manter cópia integral de uma anotação pública de terceiro que já foi revogada para `private`.
- **Mitigação**:
  - A exportação consulta os dados em tempo real aplicando as mesmas regras de autorização: inclui 100% dos conceitos, reflexões, notas e destaques do próprio usuário (`user_id == viewer_id`). Se uma conexão do usuário apontar para uma anotação de terceiro que foi revogada (`visibility == 'private'`) ou excluída, a exportação registra apenas o marcador neutro `[Referência externa indisponível]`, sem expor texto, página ou autor.

### 2.10. Revogação de Acesso a Livros (`is_missing = 1` ou Fonte Desativada)
- **Ameaça**: Um administrador desativa uma fonte (`library_sources.is_active = 0`) ou um arquivo é removido (`books.is_missing = 1`), mas destaques de terceiros naquele livro continuam aparecendo nos vínculos de outros usuários.
- **Mitigação**:
  - Para anotações de terceiros (`entity.user_id != viewer_id`), `_verify_book_access` exige `b.is_missing = 0 AND s.is_active = 1`. Se a fonte for desativada, anotações públicas daquele livro deixam de ser resolvidas para outros usuários.
  - Já os **Conceitos** e **Reflexões Autorais** pertencentes ao próprio usuário permanecem acessíveis para ele na seção Conhecimento (pois o pensamento escrito pelo leitor lhe pertence), apenas sinalizando que o arquivo do livro citado encontra-se temporariamente indisponível para abertura no leitor.

### 2.11. Revogação de Publicação de Anotações (`public → private`)
- **Ameaça**: Alice publica uma nota (`public`), Bob cria uma conexão privada para ela, e depois Alice reverte a nota para `private`. Se o sistema tivesse copiado o texto de Alice para dentro da conexão de Bob, Alice perderia o controle sobre sua nota privada.
- **Mitigação**:
  - `knowledge_links` **não copia nem faz snapshot do texto** da anotação de destino; armazena apenas `(target_type, target_id)` e a justificativa autoral do próprio criador do link (`rationale`).
  - Assim que Alice altera `visibility` para `'private'`, qualquer consulta posterior de Bob vê `target_status = "unavailable"`, ocultando imediatamente o texto, capítulo e autoria de Alice, enquanto preserva o `rationale` escrito por Bob.

### 2.12. Exclusão de Usuários
- **Ameaça**: Quando uma conta de usuário é excluída por um administrador (`DELETE FROM users WHERE id = ?`), dados privados órfãos permanecerem no banco ou no índice FTS5.
- **Mitigação**:
  - `concepts`, `reflections` e `knowledge_links` declaram `user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE`, e triggers `AFTER DELETE` limpam as entradas correspondentes em `knowledge_fts`.

### 2.13. Referências Órfãs
- **Ameaça**: Exclusão de um destaque, nota, conceito ou reflexão deixar arestas quebradas que causem erro `500 Internal Server Error` ao renderizar um conceito ou grafo.
- **Mitigação**:
  - Triggers SQLite removem automaticamente arestas quando o próprio autor exclui um `concept` ou `reflection`.
  - Para `highlights` e `notes` excluídos (ou revogados), a função `resolve_link_endpoints()` trata graciosamente IDs ausentes retornando um objeto sentinela seguro (`{"id": target_id, "type": target_type, "available": False, "label": "Item removido ou indisponível"}`), permitindo ao usuário visualizar sua própria justificativa e remover o vínculo órfão com um clique se desejar.

---

## 3. Matriz de Autorização por Entidade Proposta (v1.9.0)

| Entidade / Operação | Sem Sessão | Autor / Proprietário (`user_id == viewer`) | Outro Leitor Autenticado | Admin da Instância (`viewer != user_id`) |
| :--- | :---: | :---: | :---: | :---: |
| **`concepts` (Ler / Listar / Pesquisar / Editar / Excluir)** | `401` | `200 / 201` | `404 Not Found` | `404 Not Found` |
| **`reflections` (Ler / Listar / Pesquisar / Editar / Excluir)** | `401` | `200 / 201` | `404 Not Found` | `404 Not Found` |
| **`knowledge_links` (Ler / Criar / Editar / Excluir aresta)** | `401` | `200 / 201` | `404 Not Found` | `404 Not Found` |
| **Vincular `highlight`/`note` própria a `concept`/`link`** | `401` | `201 Created` | `404 Not Found` | `404 Not Found` |
| **Vincular `highlight`/`note` `public` de terceiro (livro ativo)** | `401` | — | `201 Created` (na rede privada do leitor) | `201 Created` (na rede privada do admin) |
| **Vincular `highlight`/`note` `private` de terceiro** | `401` | — | `404 Not Found` (bloqueado) | `404 Not Found` (bloqueado) |
| **Ler extremo de `knowledge_link` após terceiro revogar para `private`** | `401` | — | `200 OK` com alvo mascarado (`available: false`) | `200 OK` com alvo mascarado (`available: false`) |
| **Exportar Conhecimento (`/api/knowledge/export/*`)** | `401` | `200 OK` (apenas dados próprios) | Isolado por sessão | Isolado por sessão |

---

## 4. Registro de Manutenção da v1.8.0: Token CSRF em Query String no `sendBeacon`

Conforme determinado na Seção 9 das instruções da Fase 0, registra-se abaixo a observação técnica identificada na auditoria da versão `1.8.0` (documentada para endereçamento futuro, sem modificação de código nesta fase):

- **Localização no código atual**:
  - Frontend: `static/js/reader.js`, linhas 598–614 (`emergencySave()`):
    ```javascript
    const csrfToken = getCsrfToken();
    const progressUrl = csrfToken
      ? `/api/books/${bookId}/progress?csrf_token=${encodeURIComponent(csrfToken)}`
      : `/api/books/${bookId}/progress`;
    if (navigator.sendBeacon) {
      navigator.sendBeacon(progressUrl, new Blob([payload], { type: "application/json" }));
    }
    ```
  - Backend: `main.py`, linhas 248–252 (`csrf_and_security_middleware`):
    ```python
    provided_csrf = (
        request.headers.get("x-csrf-token")
        or request.query_params.get("csrf_token")
    )
    ```
- **Natureza do risco de manutenção**:
  - A API `navigator.sendBeacon(url, data)` dos navegadores não permite definir cabeçalhos HTTP customizados (como `X-CSRF-Token`), razão pela qual o token foi anexado como query parameter `?csrf_token=...` para salvamento de progresso no evento `beforeunload`/`pagehide`.
  - No entanto, parâmetros de query string na URL são gravados em texto claro nos logs de acesso do servidor HTTP (Uvicorn, Caddy, Nginx) e podem aparecer no histórico de inspeção.
- **Diretriz para saneamento futuro (Seção 13.6 — tarefa de manutenção separada)**:
  - Esta questão é registrada como **tarefa de manutenção independente** do domínio de Conhecimento Conectado (não alterada na Fase 1).
  - Quando for endereçada em fase específica de código, substituir `navigator.sendBeacon` por `fetch("/api/books/{id}/progress", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken }, body: payload, keepalive: true })` ou transporte do token no corpo da requisição **exigirá teste automatizado específico verificando que o salvamento de progresso no fechamento da aba (`pagehide`/`beforeunload`) continua funcionando sem regressão**.
