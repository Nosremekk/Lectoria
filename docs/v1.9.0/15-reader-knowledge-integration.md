# 15 — Integração do Atlas com os Leitores PDF e EPUB (Fase 3 — Lectoria v1.9.0)

> **Fase**: 3 (Integração do Atlas com os Leitores PDF e EPUB)  
> **Branch**: `feature/v1.9.0-connected-knowledge`  
> **Seção Principal Oficial (`KD-010`)**: **Atlas**  
> **Data**: 2026-11-19

---

## 1. Visão Geral Arquitetural

A Fase 3 integra o domínio de Conhecimento Conectado (**Atlas**) diretamente aos leitores **PDF** e **EPUB** da Lectoria sem introduzir frameworks frontend, sem ferramentas de build e sem sobrecarregar a barra superior permanente (`.reader-header`).

A integração segue o padrão **Contextual sob Demanda** em um módulo Vanilla JS compartilhado:

| Arquivo | Papel na Integração |
| :--- | :--- |
| [reader-knowledge.js](file:///home/vini/Jellyfin%20livros/static/js/reader-knowledge.js) | Módulo compartilhado (`window.LectoriaReaderKnowledge`) responsável pela comunicação com `/api/knowledge/*`, estado transitório em memória, busca assistida com debounce e `AbortController`, renderização segura via DOM explícito, navegação segura e controle de acessibilidade. |
| [reader.html](file:///home/vini/Jellyfin%20livros/static/reader.html) | Declara o modal acessível `#modal-reader-knowledge` (`role="dialog"`, `aria-modal="true"`) e carrega `/static/js/reader-knowledge.js?v=1.8.0` antes de `/static/js/reader.js?v=1.8.0` (alinhado a `APP_VERSION = "1.8.0"`). |
| [reader.js](file:///home/vini/Jellyfin%20livros/static/js/reader.js) | Inicializa o adaptador de leitura (`LectoriaReaderKnowledge.init(...)`), adiciona o botão contextual **Conhecimento** em `#hl-pop-actions` e `#note-pop-actions` (para anotações próprias e anotações públicas autorizadas de terceiros), protege atalhos globais de teclado enquanto o modal está aberto e dispara `cleanup()` em `beforeunload`/`pagehide`. |
| [style.css](file:///home/vini/Jellyfin%20livros/static/css/style.css) | Estilização editorial discreta (`.rk-*`), adaptada aos temas Claro, Escuro e Sépia, com responsividade validada de `360px` a `1366px` sem overflow horizontal. |

---

## 2. Ponto de Entrada Contextual e Isolamento de Escopo

Tanto o leitor **PDF** (`renderHighlightsForPage` e `renderNotePinsForPage`) quanto o leitor **EPUB** (`renderEpubHighlights` e `renderEpubNotePins`) compartilham os popovers `#highlight-popover` e `#page-note-popover`.

- O botão contextual **Conhecimento** (`.btn-open-knowledge`) aparece exclusivamente em anotações já persistidas com identificador canônico (`highlight` ou `note`).
- Seleções de texto efêmeras não criam anotações silenciosamente.
- Em anotações públicas de terceiros, o popover mantém o selo explícito de somente leitura (sem botões de edição ou exclusão da anotação alheia), permitindo ao leitor autenticado associar **seus próprios** conceitos privados e criar **suas próprias** conexões semânticas no Atlas.

---

## 3. Fluxos Funcionais Implementados

### 3.1. Conceitos Associados
- **Listagem**: Derivada das conexões entre a anotação atual e entidades do tipo `concept` retornadas por `GET /api/knowledge/entities/{entity_type}/{entity_id}/connections`.
- **Visualização rápida**: Clicar no chip do conceito alterna um resumo inline com descrição e etiquetas autorizadas.
- **Busca assistida**: O campo `#rk-concept-search` consulta `GET /api/knowledge/lookup?entity_type=concept&q=...&limit=12` com debounce de `220 ms`, limpeza imediata de resultados obsoletos durante a digitação, `AbortController` e contador sequencial contra respostas fora de ordem.
- **Associação**: `POST /api/knowledge/links` com `relation_type: "relates_to"`.
- **Criação contextual em duas etapas**:
  1. `POST /api/knowledge/concepts` cria o conceito no Atlas do usuário.
  2. `POST /api/knowledge/links` vincula o conceito recém-criado à anotação atual.
  - Em conflito `HTTP 409`, o painel informa que o conceito já existe e aciona a busca assistida para facilitar a associação.
  - Se a etapa 1 concluir e a etapa 2 falhar, o banner distingue claramente o sucesso da criação do conceito da falha na vinculação.
- **Remoção de associação**: O botão `×` no chip executa `DELETE /api/knowledge/links/{link_id}`, removendo apenas o vínculo com aquela anotação e preservando o conceito no Atlas.

### 3.2. Conexões Semânticas e Backlinks
- **Abas direcionais**: `Conexões de saída (N)` e `Backlinks recebidos (M)` com paginação incremental (`Carregar mais`) orientada por `has_more_outgoing` e `has_more_incoming`.
- **Criação de nova conexão**:
  - Exibe claramente a origem (`Destaque atual` ou `Nota atual`), filtros por tipo (`concept`, `reflection`, `highlight`, `note`, `book`) e escopo opcional (`Filtrar apenas neste livro`).
  - Suporta integralmente os 8 tipos fixos de relação (`relates_to` padrão, `complements`, `contradicts`, `exemplifies`, `grounds`, `develops`, `references`, `questions`) com pré-visualização explícita de direção (`Origem — Relação → Destino`) e justificativa opcional de até `2000` caracteres.
- **Edição e exclusão**:
  - Edição inline de `relation_type` e `rationale` via `PATCH /api/knowledge/links/{id}`.
  - Exclusão com confirmação inline em dois cliques (`Excluir` → `Confirmar` / `Cancelar`) via `DELETE /api/knowledge/links/{id}`, sem diálogos nativos bloqueantes (`window.confirm`).

### 3.3. Navegação Segura entre Trechos e Obras
- Antes de saltar para outra posição ou livro, o controlador executa `await adapter.persistProgress()` para preservar o progresso atual de leitura.
- **Mesmo livro**: fecha o modal e invoca `goToPage(page)` (PDF) ou `navigateToEpubCfi(epubCfi, chapterLabel)` (EPUB).
- **Livro distinto**: valida `book_id` com expressão segura (`SAFE_BOOK_ID_RE`), valida `epub_cfi` (`/^epubcfi\(.+\)$/i`) ou número inteiro de página (`>= 1`) e navega para `/reader/{encodeURIComponent(bookId)}#page=N` ou `/reader/{encodeURIComponent(bookId)}#cfi=...`.
- **Entidades do Atlas (`concept` e `reflection`)**: exibem resumo inline com indicação `"Entidade do seu Atlas"`, sem links quebrados para telas futuras da Fase 5.

---

## 4. Segurança, Privacidade e Robustez

1. **Carregamento estritamente sob demanda**: nenhuma chamada a `/api/knowledge/*` ocorre durante o carregamento inicial do PDF ou EPUB até que o usuário abra o painel **Conhecimento**.
2. **Zero `innerHTML` com dados da API**: todos os rótulos, trechos, nomes de conceitos, descrições, etiquetas, justificativas e mensagens de erro são inseridos exclusivamente via `document.createElement` e `textContent`. Cores hexadecimais passam por validação `/^#[0-9a-fA-F]{3,8}$/`.
3. **Zero persistência local de dados privados**: nenhum conceito, reflexão, conexão ou justificativa é gravado em `localStorage` ou `sessionStorage`.
4. **Proteção CSRF**: todas as requisições mutáveis (`POST`, `PATCH`, `DELETE`) enviam `X-CSRF-Token` exclusivamente por cabeçalho HTTP, nunca na query string da URL.
5. **Referências Indisponíveis (`available === false`)**: exibem apenas o estado genérico `"Referência indisponível"`, sem qualquer metadado protegido de terceiros e sem botão de navegação, preservando a justificativa do proprietário da conexão e permitindo sua exclusão.
6. **Concorrência e erros**: travas por operação (`submitLocks`) bloqueiam cliques duplicados; falhas `400`, `401`, `403`, `404`, `409`, `422`, timeout (`15 s`) e quedas de rede exibem feedback claro em `#rk-status-banner` sem interromper a leitura.
7. **Acessibilidade e responsividade**: `role="dialog"`, `aria-modal="true"`, fechamento via `Escape` com devolução de foco ao elemento acionador, bloqueio de atalhos globais do leitor enquanto o modal está aberto e zero overflow horizontal em desktop (`1366px`), tablet (`768px`) e mobile (`360px`).
