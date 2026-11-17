# Motores de Leitura: PDF.js e EPUB.js

Este documento descreve a integração técnica dos motores de leitura **PDF.js** e **EPUB.js** no Lectoria, as otimizações de streaming HTTP Range, a navegação precisa por localizadores (`#page=` e `#cfi=`), o compartilhamento de trechos e as diretrizes de desempenho para hardware modesto.

---

## 1. Visão Geral dos Motores

O leitor adaptativo do Lectoria reside em [static/reader.html](file:///home/vini/Jellyfin%20livros/static/reader.html) e [static/js/reader.js](file:///home/vini/Jellyfin%20livros/static/js/reader.js). A renderização é 100% executada no navegador do cliente sem conversão pesada no servidor.

| Motor | Formato Suportado | Biblioteca Base | Localizador URL |
| :--- | :--- | :--- | :--- |
| **PDF.js** | Documentos PDF (`.pdf`) | Mozilla PDF.js (local) | Hash `#page=N` (1-based) |
| **EPUB.js** | Livros digitais EPUB (`.epub`) | Futurepress EPUB.js + JSZip (local) | Hash `#cfi=epubcfi(...)` |

---

## 2. Otimização de Desempenho e Streaming HTTP Range (PDF)

Para viabilizar a execução fluida em mini-PCs e servidores domésticos (ex: Intel Celeron 847 com 2 GB ou 4 GB de RAM), o leitor PDF aplica regras defensivas estritas:

### 2.1 Configuração do PDF.js
Na inicialização do documento via `pdfjsLib.getDocument(...)`:
- **`disableStream: true`**: Elimina o download progressivo concorrente em segundo plano. Sem esta diretiva, o navegador tenta baixar 100% de um arquivo de 200 MB enquanto renderiza a página 1, saturando a banda e o disco do servidor.
- **`disableAutoFetch: true`**: Evita requisições especulativas automáticas de dados além dos estritamente necessários para a página visível.
- **`rangeChunkSize: 524288` (512 KB)**: Bloco equilibrado que reduz o número de viagens de ida e volta (RTT) necessárias para leitura da tabela de referências cruzadas (`xref`) do PDF sem sobrecarregar o buffer de memória.

### 2.2 Resolução no Backend (`main.py` / `library.py`)
- O endpoint `/api/books/{book_id}/file` emprega `resolve_book_file_for_user`, substituindo consultas com múltiplos JOINs por uma consulta única e direta de segurança e caminho no disco.
- Cabeçalhos padronizados de cache:
  - `Accept-Ranges: bytes` (habilita fatiamento nativo pelo cliente).
  - `Cache-Control: private, no-cache`.
  - `ETag` determinístico e suporte a `304 Not Modified` / `HEAD`.
  - Códigos de status emitidos: `206 Partial Content` para blocos parciais e `200 OK` quando o arquivo inteiro é requisitado.

---

## 3. Navegação Precisa por Localizadores na URL

Ao abrir o leitor via link gerado na Leitura Compartilhada ou biblioteca, a função `parseHashLocator()` em `reader.js` analisa o fragmento da URL:

### 3.1 Localizador de Página (PDF)
- **Formato**: `/reader/{book_id}#page=N`
- **Validação e Limites**:
  - `N` deve ser um número inteiro estritamente positivo (`^\d+$`).
  - O valor é verificado contra `totalPages` retornado pelo documento (`1 <= page <= totalPages`).
  - Caso `page` esteja dentro dos limites, o leitor inicia diretamente na página física `N`, atualizando o `#page-num-input`.
- **Fallback**: Se `N` for zero, negativo, não numérico ou superior ao total de páginas, o leitor abre na posição padrão/salva e exibe uma notificação suave:
  ```
  Página N fora dos limites (1–X); exibindo posição padrão.
  ```

### 3.2 Localizador Canônico EPUB (CFI)
- **Formato**: `/reader/{book_id}#cfi=epubcfi(...)`
- **Validação e Renderização**:
  - A string é codificada na URL (`encodeURIComponent`) e deve iniciar com `epubcfi(` e terminar com `)`.
  - O leitor aguarda a inicialização completa de `rendition` do EPUB.js antes de chamar `epubRendition.display(hashLocator.cfi)`.
- **Fallback**: Caso o CFI seja inválido, corrompido ou pertença a uma edição com estrutura interna divergente, a exceção é capturada, a posição padrão salva é restaurada e o leitor exibe toast informativo sem interromper a execução:
  ```
  Localizador EPUB inválido ou obsoleto; exibindo posição padrão.
  ```

---

## 4. Seleção de Texto e Compartilhamento de Citações

1. **Ativação da Barra Flutuante (`#selection-toolbar`)**:
   - No PDF.js: O evento `mouseup` na camada de texto (`.textLayer`) captura o texto selecionado, número da página e coordenadas do retângulo.
   - No EPUB.js: O listener `epubRendition.on("selected", (cfiRange, contents) => ...)` captura o fragmento CFI da seleção.
2. **Botão "Compartilhar na Leitura Coletiva" (`#btn-share-selection-club`)**:
   - Exibido apenas quando:
     - Há seleção de texto não vazia.
     - O usuário pertence a pelo menos um clube com leitura coletiva elegível (`planned` ou `in_progress`, em clube não arquivado) para aquele livro (via `GET /api/books/{id}/collective-reads`).
3. **Clamping Horizontal em Telas Pequenas**:
   - Em smartphones (320px a 412px), a posição horizontal da barra de ferramentas é delimitada defensivamente:
     ```javascript
     const tbWidth = Math.min(selectionToolbar.offsetWidth || 260, window.innerWidth - 20);
     const maxLeft = Math.max(10, window.innerWidth - tbWidth - 10);
     selectionToolbar.style.left = `${Math.min(maxLeft, Math.max(10, desiredLeft))}px`;
     ```
   - Isso elimina qualquer extravasamento horizontal (`overflow-x`) fora da janela do dispositivo.
4. **Modal de Confirmação e Limite de 1.500 Caracteres**:
   - O modal `#modal-share-collective-quote` permite revisar o texto, selecionar a leitura coletiva de destino, adicionar comentário opcional e marcar flag de spoiler.
   - O campo `#share-quote-text` não possui truncamento silencioso: se o usuário colar mais de 1.500 caracteres, um erro explícito é exibido no formulário orientando a sintetizar a citação.

---

## 5. Notificações Não Bloqueantes (`#reader-toast`)

- As mensagens de alerta e fallback utilizam um elemento flutuante independente `#reader-toast` com classes `.success` e `.error`.
- Anunciado para leitores de tela com `role="status"` e `aria-live="polite"`.
- Desaparece automaticamente após 4 segundos, sem congelar a leitura ou bloquear cliques nos controles do leitor.

---

## 6. Leitor PDF Colaborativo e Experiência de Leitura (v1.8.0 — Fase 3)

### 6.1 Arquitetura em Camadas no Container da Página PDF (`.page-container`)
Cada página PDF (no modo Página Única `#single-page-wrapper` e nos slots virtualizados `.page-slot` da Rolagem Contínua) empilha as seguintes camadas em ordem estrita de `z-index`:
1. `<canvas>` (`PDF.js`): renderização do documento.
2. `<div class="textLayer">`: camada de seleção de texto nativa.
3. `<div class="highlights-layer">`: destaques próprios (`.highlight-box`) e destaques públicos de terceiros (`.highlight-box.highlight-public-other` com contorno tracejado).
4. `<svg class="public-drawing-layer">`: desenhos públicos de outros leitores (`pointer-events: none`, imune à borracha e ao botão *Limpar Página*).
5. `<svg class="drawing-layer">`: camada pessoal de desenho vetorial do usuário autenticado (editável no modo `Desenhar`).
6. `<div class="page-note-pins-layer">`: indicadores discretos de notas na margem da página (`.page-note-pin`) e selo de autoria de desenhos públicos (`.page-public-drawings-badge`).

### 6.2 Geometria Isotrópica e Escalabilidade dos Desenhos (`viewBox`)
- Todas as coordenadas permanecem armazenadas no SQLite no intervalo normalizado `[0.0..1.0]` para preservar 100% de compatibilidade retroativa.
- Na renderização SVG, `applySvgViewBox()` calcula a razão de aspecto real da página (`aspect = height / width`) e define `viewBox="0 0 1000 ${vbHeight}"` com `preserveAspectRatio="xMidYMid meet"`.
- Como as unidades X e Y são isotrópicas, a espessura dos traços (`stroke-width`) é uniforme em todas as direções e escala proporcionalmente em hardware durante zoom (`0.4x–4.0x`), ajuste à largura (`w`), ajuste à página (`p`) e redimensionamento de viewport.

### 6.3 Popovers Contextuais, Publicação Explícita e Controle de Camadas
- **Popover de Destaque (`#highlight-popover`) e Nota (`#page-note-popover`)**: substituem o `window.confirm()` imediato ao clicar em um destaque, exibindo texto/conteúdo sanitizado (`textContent`), avatar e nome do autor, nota vinculada autorizada, estado de visibilidade e ações permitidas (apenas leitura para itens de terceiros).
- **Publicação Explícita**: toda anotação e desenho nasce `private`. Ao publicar pela primeira vez na sessão, `confirmFirstPublicShare()` exibe um aviso claro informando que outros leitores autenticados com acesso ao livro verão o conteúdo e o nome de exibição do autor.
- **Controle de Visualização (`#layer-view-mode-select`)**: alterna entre `all_visible` (*Minhas + Públicas*), `mine` (*Somente minhas*) e `hidden` (*Ocultar anotações*), persistindo a preferência localmente isolada por usuário (`lectoria_layer_view_mode_u{userId}`) sem alterar `visibility` no banco.
- **Modo Marca-Texto Ativo (`#btn-tool-highlight`)**: destaca automaticamente o texto selecionado no PDF ao soltar o ponteiro (`mouseup`/`touchend`).
- **Correção de `dockMargin` e Busca Textual**: `renderSlotPage()` aplica o mesmo `dockMargin = 72px` em tablets (`768px–1280px`) que `calculateScaleForPage()`, e `#reader-search-bar` (`Ctrl+F` / `/`) realiza busca incremental via `page.getTextContent()` com cache leve por página.

---

## 7. Leitor EPUB Colaborativo (v1.8.0 — Fase 4)

### 7.1 Seleção Universal e Persistência por EPUB CFI
- O listener `epubRendition.on("selected", (cfiRange, contents) => ...)` funciona em **qualquer livro EPUB**, independentemente de vínculo com clubes ou leituras coletivas.
- A barra `#selection-toolbar` exibe as 5 cores de destaque, o botão **Nota** e (condicionalmente, apenas quando `eligibleCollectiveReads.length > 0`) o botão **Compartilhar na Leitura Coletiva**.
- Destaques e notas são persistidos com `epub_cfi` (`epubcfi(...)`), `chapter_label` resolvido via TOC achatado (`flattenEpubToc`) e `rects: []` (sem conversão para coordenadas fixas PDF).

### 7.2 Renderização via `epubRendition.annotations` e Tolerância a Reflow
- `renderEpubHighlights()` limpa registros anteriores (`epubRendition.annotations.remove`) e registra os destaques visíveis conforme `layerViewMode` (`all_visible`, `mine`, `hidden`) usando `epubRendition.annotations.highlight(...)`.
- Destaques públicos de terceiros recebem contorno tracejado (`.epub-hl-public-other`) e abrem `#highlight-popover` com coordenadas convertidas do iframe para a janela principal (`getEpubAnchorRectFromEvent()`).
- Alterações de tamanho de fonte (`60%–220%`), família (`serif`/`sans-serif`), espaçamento entre linhas (`1.4`, `1.65`, `1.9`), margens (`compact`, `normal`, `wide`), tema (`dark`, `light`, `sepia`) e redimensionamento de viewport acionam `renderEpubHighlights()` para manter o alinhamento exato após o reflow.
- `epubRendition.on("rendered")` e `epubRendition.on("relocated")` re-sincronizam destaques e pins de notas (`#epub-note-pins-layer`) sem duplicar listeners (`epubEventsBound`).

### 7.3 Navegação Direta por CFI e Fallback Seguro
- `navigateToEpubCfi(cfi, fallbackChapterLabel)` valida o formato `epubcfi(...)`, navega com `epubRendition.display(cfi)` e aplica destaque temporário de 1,8s na passagem localizada.
- Em caso de CFI inválido ou incompatível, tenta fallback pelo capítulo correspondente no TOC sem sobrescrever o progresso atual, exibindo toast informativo.

---

## 8. Caderno de Estudos Colaborativo e Marcadores Pessoais (v1.8.0 — Fase 5)

### 8.1 Escopos Independentes, Abas e Ordenação
- O painel `#annotations-drawer` mantém `drawerState` independente do controle de camadas da página (`layerViewMode`):
  - **Minhas** (`scope=mine`), **Públicas** (`scope=public`) e **Todas visíveis** (`scope=all_visible`).
- **Abas**: `Todas`, `Destaques`, `Notas`, `Desenhos` e `Marcadores` (`bookmarks`). A aba `Marcadores` exibe exclusivamente marcadores pessoais e privados do usuário autenticado, sem opção de publicação.
- **Ordenação**:
  - `recent`: ordem cronológica decrescente (`created_at DESC` / `updated_at DESC`).
  - `position`: agrupamento por página no PDF e ordenação numérica de segmentos CFI (`compareEpubCfi()`) no EPUB (evitando erro lexicográfico `"10" < "4"`).

### 8.2 Pesquisa, Paginação Incremental e Marcador Rápido
- Pesquisa com debounce de 280ms, `AbortController` e guarda `requestSeq` contra respostas antigas; busca segura em conteúdo, texto destacado, capítulo e nome público do autor.
- Paginação incremental (`limit = 50`, botão **Carregar mais**) usando `has_more` e `total_counts` retornados pelo backend, com merge sem duplicatas por `id`.
- Botão `#btn-toggle-bookmark` (atalho `k`) no cabeçalho do leitor e `#btn-mobile-sheet-bookmark` no mobile criam/removem marcadores na página PDF ou `epub_cfi` atual.
