# Arquitetura Frontend do Lectoria

Este documento descreve a organização do frontend em **Vanilla JavaScript (ES6+)**, os padrões de gerenciamento de estado, ciclo de vida de requisições assíncronas, barreira de proteção contra spoilers, responsividade e temas visuais editoriais do **Lectoria**.

---

## 1. Princípios de Design Frontend

1. **Zero Ferramentas de Compilação (*No-Build Pipeline*)**:
   - Todo o código HTML, CSS e JavaScript é servido diretamente pelo FastAPI como arquivos estáticos (`/static`).
   - Sem Webpack, Vite, Babel, TypeScript ou Node.js em tempo de execução.
2. **Alta Performance em Hardware Modesto**:
   - O DOM é mantido leve: renderização de livros em lotes progressivos de 36 itens (`BOOKS_CHUNK_SIZE = 36`), delegação centralizada de eventos e alternância puramente CSS entre Grade e Lista.
3. **Sobriedade Estética Editorial**:
   - Tipografia refinada baseada em fontes locais do sistema, ausência de bibliotecas de componentes externas e paletas inspiradas em papelaria clássica.

---

## 2. Organização dos Módulos JavaScript

```
static/
├── css/
│   └── style.css            # Folha de estilo consolidada e responsiva
├── js/
│   ├── app.js               # Catálogo da biblioteca, busca, filtros, abas sob demanda e modais gerais
│   ├── knowledge-api.js     # Cliente REST compartilhado do Atlas e renderizador Markdown seguro (v1.9.0)
│   ├── knowledge.js         # Seção principal Atlas: Visão Geral, Conceitos, Reflexões, Conexões, Pesquisa, Mapa SVG e Exportação (v1.9.0)
│   ├── book-knowledge.js    # Subaba contextual Conhecimento no modal de detalhes da obra (v1.9.0)
│   ├── reader-knowledge.js  # Integração contextual do Atlas nos leitores PDF/EPUB e Caderno de Estudos (v1.9.0)
│   ├── shared_reading.js    # Painel integrado de Leitura Coletiva (v1.7.0)
│   └── reader.js            # Motores de renderização PDF.js e EPUB.js
├── index.html               # Aplicação principal (SPA sem hash router pesado)
└── reader.html              # Interface do leitor adaptativo
```

---

## 3. Gerenciamento de Estado e Ciclo de Vida Assíncrono

### 3.1 Estado Local Encapsulado
Cada módulo mantém seu próprio objeto `state` encapsulado dentro de uma IIFE (*Immediately Invoked Function Expression*):

```javascript
// Exemplo em shared_reading.js
const state = {
  readId: null,
  clubId: null,
  activeTab: "overview",
  readMeta: null,
  generation: 0,
  controllers: {},
  loaded: { overview: false, discussions: false, goals: false, quotes: false },
  topics: { page: 1, limit: 15, filter: "all", selectedTopicId: null, postsPage: 1 },
  quotes: { page: 1, limit: 20 },
};
```

### 3.2 Cancelamento de Requisições e Proteção contra Respostas Atrasadas (*Stale Requests*)
- Ao alternar abas rapidamente ou mudar de leitura coletiva, o módulo cancela chamadas em trânsito via `AbortController`:
  ```javascript
  function abortKey(key) {
    if (state.controllers[key]) {
      state.controllers[key].abort();
      delete state.controllers[key];
    }
  }
  ```
- Além do `signal`, uma chave incremental `generation` descarta o payload se a visão do usuário tiver sido reinicializada antes do término do fetch:
  ```javascript
  if (gen !== state.generation) {
    throw new DOMException("Stale request", "AbortError");
  }
  ```

### 3.3 Trava contra Duplo Clique (*Submit Lock*)
Para formulários colaborativos (novo tópico, respostas, metas, citações), a função `withSubmitLock` previne duplicações por duplo clique rápido:
```javascript
async function withSubmitLock(form, fn) {
  if (!form || form.dataset.submitting === "1") return;
  form.dataset.submitting = "1";
  const submitBtn = form.querySelector('button[type="submit"]');
  if (submitBtn) submitBtn.disabled = true;
  try {
    await fn();
  } finally {
    delete form.dataset.submitting;
    if (submitBtn) submitBtn.disabled = false;
  }
}
```

---

## 4. Barreira Real contra Spoilers (Click-to-Reveal)

O Lectoria implementa proteção contra spoilers que vai além de truques visuais com CSS:

1. **Fora do DOM até Revelação**:
   - Em tópicos, respostas ou citações marcadas com `is_spoiler: true`, o texto do spoiler **não é injetado no DOM**.
   - O conteúdo secreto permanece armazenado em uma closure JavaScript em memória.
   - O DOM recebe apenas um botão de barreira:
     ```html
     <div class="sr-spoiler-wrapper">
       <button type="button" class="sr-spoiler-barrier" aria-expanded="false">
         <span>⚠️ Conteúdo com spoiler. Clique ou pressione Enter para revelar.</span>
       </button>
     </div>
     ```
2. **Revelação Estritamente Voluntária**:
   - Ativação exclusiva via evento `click` ou teclado (`Enter` / `Espaço`).
   - Proibido o uso de `hover` (passar o mouse) para revelar spoilers, prevenindo revelações acidentais em mobile e navegação desktop.
3. **Acessibilidade para Leitores de Tela**:
   - Softwares de leitura de tela leem o aviso informativo de spoiler sem narrar o texto secreto prematuramente.

---

## 5. Responsividade e Contenção de Layout (320px a 1920px)

O Lectoria adota regras estritas de responsividade testadas em 8 larguras de viewport (`320px`, `360px`, `375px`, `390px`, `412px`, `768px`, `1280px` e `1920px`):

1. **Ausência de Overflow Horizontal**:
   - A invariante fundamental `document.documentElement.scrollWidth <= window.innerWidth` é validada em todas as visões.
2. **Rolagem Horizontal Intencional com Máscara de Gradiente**:
   - Na barra de abas principal (`.main-view-tabs`), a rolagem horizontal móvel utiliza máscara CSS com gradiente (`-webkit-mask-image`), sinalizando que existem itens adicionais à direita.
3. **Preservação de Capas em Grade e Lista (Correção v1.6.3)**:
   - A classe `.books-grid .book-card .book-cover` mantém proporção de aspect-ratio adequada (`aspect-ratio: 2 / 3`), preenchendo o container sem encolhimento residual.

---

## 6. Temas Visuais Editoriais

O Lectoria oferece 3 temas visuais ativados via atributo `data-theme` na tag `<html>`:

- **Tema Padrão / Escuro (Obsidiana Editorial)**: Fundo escuro quente (`#141311`), superfícies secundárias (`#1C1B18`), texto em pergaminho suave (`#EAE5DC`) e acentos terracota.
- **Tema Claro (Papel Marfim)**: `data-theme="light"` com fundo marfim (`#F6F3EC`), texto carvão (`#1C1B18`) e bordas neutras suaves.
- **Tema Sépia (Conforto Noturno)**: `data-theme="sepia"` com tons quentes de pergaminho envelhecido, ideal para leitura prolongada.

---

## 7. Segurança e Isolamento no Leitor Colaborativo (v1.8.0 — Fase 3)

- **Sanitização Estrita**: Todo conteúdo textual de notas, citações e `author_display_name` inserido nos popovers (`#highlight-popover`, `#page-note-popover`), pins de página e selos de autoria usa `textContent` ou `escapeHtml()` antes da inserção no DOM, prevenindo XSS.
- **Autoridade no Backend**: As flags `is_own`, `can_edit`, `can_delete` e `can_toggle_visibility` controlam apenas a apresentação de botões na interface; todas as operações mutáveis (`PUT`, `PATCH`, `DELETE`) continuam validadas pelo backend e protegidas por `X-CSRF-Token`.
- **Isolamento de Preferências por Usuário**: O modo de visualização de camadas (`all_visible`, `mine`, `hidden`) é salvo em `localStorage` sob chave prefixada pelo ID do usuário autenticado (`lectoria_layer_view_mode_u${currentUserId}`), evitando vazamento de preferências entre sessões distintas.
- **EPUB Seguro (`allowScriptedContent: false`)**: O iframe do EPUB.js mantém execução de scripts desativada e links `javascript:` / `data:` / `vbscript:` neutralizados; a seleção de texto não executa scripts embutidos e toda renderização de notas/citações públicas no DOM utiliza `textContent` ou `escapeHtml()`.
- **Caderno de Estudos Assíncrono (`drawerState`)**: Utiliza `AbortController` + `requestSeq` para descartar respostas obsoletas durante digitação ou troca rápida de escopo/ordenação, e `mergeUniqueById` na paginação incremental.
