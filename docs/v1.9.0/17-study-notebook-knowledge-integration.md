# 17 — Integração do Atlas com o Caderno de Estudos e a Biblioteca (Fase 4 — Lectoria v1.9.0)

> **Fase**: 4 (Integração do Atlas com o Caderno de Estudos e a Biblioteca)  
> **Branch**: `feature/v1.9.0-connected-knowledge`  
> **Seção Principal Oficial (`KD-010`)**: **Atlas**  
> **Data**: 2026-11-19

---

## 1. Arquitetura Compartilhada e Reutilização

Para evitar duplicação entre os leitores PDF/EPUB (Fase 3), o Caderno de Estudos (Fase 4), o modal de Detalhes da Obra (Fase 4) e a futura interface principal do Atlas (Fase 5), foi extraído um módulo núcleo leve em Vanilla JS sem dependências externas:

| Módulo | Responsabilidade | Consumidores |
| :--- | :--- | :--- |
| [knowledge-api.js](file:///home/vini/Jellyfin%20livros/static/js/knowledge-api.js) | Cliente HTTP autenticado (`same-origin`, `cache: "no-store"`), cabeçalho estrito `X-CSRF-Token`, timeout (`15 s`), `AbortController`, mapeamento de erros HTTP, constantes `RELATION_TYPES` (8 tipos fixos) e `ENTITY_TYPE_LABELS`, helpers seguros de DOM (`createEl`, `clearNode`, `sanitizeHexColor`) e `buildSafeNavigationSpec`. | `reader-knowledge.js`, `reader.js`, `book-knowledge.js` e futura Fase 5 |
| [reader-knowledge.js](file:///home/vini/Jellyfin%20livros/static/js/reader-knowledge.js) | Modal contextual `#modal-reader-knowledge` para associação/criação de conceitos, conexões semânticas, backlinks e navegação segura. Estendido na Fase 4 com callback `onMutated` para atualização imediata do Caderno de Estudos após mutações. | Leitores PDF/EPUB e Caderno de Estudos |
| [book-knowledge.js](file:///home/vini/Jellyfin%20livros/static/js/book-knowledge.js) | Controlador contextual sob demanda da subaba **Conhecimento** dentro de `#modal-book-details` na Biblioteca. | `index.html` e `app.js` |

---

## 2. Integração com o Caderno de Estudos (`#annotations-drawer`)

### 2.1. Preservação de Categorias e Elegibilidade
- Todas as categorias existentes (**Destaques**, **Notas**, **Desenhos** e **Marcadores**) e todos os filtros de escopo (`Minhas`, `Públicas`, `Todas visíveis`), pesquisa textual, ordenação (`Posição no livro`, `Mais recentes`), paginação (`Carregar mais`) e exportação/importação foram preservados integralmente.
- Apenas **Destaques (`highlight`)** e **Notas (`note`)** autorizados exibem a faixa discreta `.drawer-knowledge-strip` e a ação contextual **Conhecimento** (`.btn-drawer-knowledge`), utilizando exclusivamente seus UUIDs canônicos.
- **Desenhos** e **Marcadores** permanecem fora das entidades referenciáveis do Atlas, sem botões nem badges de conhecimento.

### 2.2. Estratégia Anti-N+1 e Carregamento Sob Demanda
- **Zero requisições no carregamento inicial do leitor**: nenhuma chamada a `/api/knowledge/*` é disparada ao abrir um PDF ou EPUB enquanto o Caderno de Estudos permanece fechado.
- **Ao abrir o Caderno de Estudos**: são executadas no máximo **2 requisições agregadas** independentemente do número de cartões na página (até 50 destaques e 50 notas por página):
  1. `GET /api/knowledge/books/{book_id}/summary` — retorna os indicadores pessoais agregados da obra.
  2. `POST /api/knowledge/entities/summary` — retorna em lote (até 200 entidades por requisição) os conceitos associados e as contagens de saídas/backlinks de todos os cartões visíveis na página carregada.
- **Ao clicar em "Carregar mais"**: é executada apenas **1 requisição em lote** para os novos itens acrescentados.
- **Degradação graciosa**: qualquer falha de rede ou servidor em `/api/knowledge/*` não impede a listagem, busca ou navegação nas anotações convencionais do Caderno.

### 2.3. Semântica das Contagens e Filtros de Conhecimento
- Filtro complementar `#drawer-knowledge-filter-group`:
  - **Todas (`all`)**: comportamento padrão preservado (todas as anotações, desenhos e marcadores do escopo).
  - **Conectadas (`connected`)**: destaques e notas autorizados que possuem ao menos uma conexão pessoal (`knowledge_links.user_id = current_user.id`) como origem ou destino — avaliado em SQL sobre todo o conjunto do livro (compatível com paginação multi-página). Desenhos e marcadores não elegíveis retornam 0 neste modo.
  - **Sem conexões (`unconnected`)**: destaques e notas autorizados sem nenhum vínculo pessoal do usuário autenticado (avaliado em SQL sobre todo o livro).
- Indicadores pessoais em `#drawer-knowledge-summary` (`GET /api/knowledge/books/{book_id}/summary`):
  - **Conceitos (`distinct_concepts_count`)**: conceitos distintos pertencentes ao usuário autenticado associados diretamente à obra ou a qualquer destaque/nota acessível da obra.
  - **Conexões (`personal_links_count`)**: conexões distintas (`knowledge_links.id`) pertencentes ao usuário que tocam a obra ou suas anotações acessíveis (sem dupla contagem).
  - **Anotações conectadas (`connected_annotations_count`)**: soma exata de destaques acessíveis conectados e notas acessíveis conectadas na rede pessoal do usuário.

---

## 3. Integração Contextual Mínima com a Biblioteca (`#modal-book-details`)

- Adicionada a subaba **Conhecimento** (`data-subtab="knowledge"`) dentro de `#modal-book-details` em [index.html](file:///home/vini/Jellyfin%20livros/static/index.html), carregada estritamente sob demanda apenas quando selecionada pelo usuário.
- Exibe indicadores pessoais da obra, atalho direto **Abrir Caderno de Estudos** (`/reader/{book_id}?drawer=1`, que abre o leitor com `#annotations-drawer` ativo), conceitos pessoais associados à entidade `book`, conexões de saída e backlinks recebidos diretamente na obra.
- Conceitos privados do Atlas permanecem completamente separados da taxonomia e categorias da biblioteca.

---

## 4. Segurança, Privacidade, Acessibilidade e Limitações

- **Isolamento multiusuário**: filtros, contagens agregadas, chips e conexões consideram exclusivamente `knowledge_links.user_id = user_id` e `concepts.user_id = user_id`. Conexões de terceiros sobre anotações públicas nunca alteram contagens, filtros ou cartões do autor da anotação.
- **Anti-enumeração no lote**: `POST /api/knowledge/entities/summary` valida cada identificador em SQL contra acesso ao livro e propriedade/visibilidade pública, omitindo silenciosamente itens privados alheios ou inexistentes.
- **Anti-XSS e CSRF**: renderização estrita via `textContent` / criação explícita de nós DOM, cabeçalho `X-CSRF-Token` obrigatório em métodos mutáveis (bloqueado na URL), `Cache-Control: private, no-store` e zero uso de `localStorage` para dados privados do Atlas.
- **Referências indisponíveis**: preservam o contrato seguro `"Referência indisponível"` sem vazamento de metadados protegidos e sem botão de navegação, mantendo a justificativa do proprietário e a opção de exclusão.
- **Responsividade e acessibilidade**: validado em desktop (`1366x860`), tablet (`768x1024`) e mobile (`360x800`) sem overflow horizontal, com `Escape` e devolução de foco.
- **Limitações ambientais residuais**: `node` e `docker` não estão instalados neste host local; a validação JavaScript foi executada integralmente no Chromium real via Playwright.
