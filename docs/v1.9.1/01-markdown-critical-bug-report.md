# Lectoria v1.9.1 — Relatório Técnico do Bug Crítico de Markdown (`# `)

- **Identificador**: `P0-MD-001` (Fase 10.1)
- **Severidade**: **Crítica (P0)** — congelamento síncrono da aba e esgotamento de memória RAM do navegador (OOM crash)
- **Branch da Correção**: `fix/v1.9.1-markdown-heading-hang` (derivada de `main` @ `9ff89cd`)
- **Status**: **Corrigido e Validado Localmente** (aguardando autorização do proprietário para publicação)
- **Versão afetada em produção**: `1.9.0` (commit `9ff89cd`)

---

## 1. Descrição do Sintoma

Ao abrir o editor de reflexões autorais do **Atlas** (`/?tab=atlas&subtab=reflections` → **+ Nova reflexão** ou edição de reflexão existente) nos modos **Dividido** (padrão) ou **Visualizar** e digitar a sequência Markdown `# ` (cerquilha seguida de um espaço), a aba do navegador congela imediatamente na thread principal e consome memória RAM de forma ilimitada até o navegador encerrar o processo renderizador por falta de memória (*Out of Memory*).

O mesmo comportamento ocorria para `## `, `### ` e `#### ` (de 1 a 4 cerquilhas seguidas de exatamente um espaço sem texto subsequente na linha).

---

## 2. Causa Raiz Verificada

A causa raiz **não era um vazamento lento de memória (memory leak)** nem recursão de pilha, mas sim um **loop infinito síncrono com criação ilimitada de nós DOM** na função `renderSafeMarkdownToDom(container, markdownText)` em [`static/js/knowledge-api.js`](../../static/js/knowledge-api.js).

### Cadeia exata de execução

1. Em [`static/js/knowledge.js`](../../static/js/knowledge.js) (`#atlas-reflection-body`, listener `input`), cada tecla digitada dispara `updateEditorMetricsAndPreview()`, que invoca `renderSafeMarkdownToDom(dom.editorPreviewBox, bodyText)` quando o editor está em modo `"split"` (padrão) ou `"preview"`.
2. Em [`static/js/knowledge-api.js`](../../static/js/knowledge-api.js) (versão `1.9.0`, linhas 660–728), o parser linha a linha possuía uma **assimetria de regex** entre o detector de títulos e a condição de parada do bloco de parágrafo:
   - **Detector de título (linha 661)**:
     ```js
     const headingMatch = line.match(/^\s*(#{1,4})\s+(.+)$/);
     ```
     Para a entrada `"# "`, `\s+` consome o espaço após `#`, deixando **zero caracteres** para `(.+)`. Logo, `headingMatch` retornava **`null`** e o fluxo caía no fallback de parágrafo.
   - **Condição de parada do parágrafo (linha 717)**:
     ```js
     while (
       i < lines.length &&
       ...
       !/^\s*#{1,4}\s+/.test(lines[i]) &&
       ...
     ) {
       paraLines.push(lines[i].trim());
       i++;
     }
     ```
     Para a mesma entrada `"# "`, `/^\s*#{1,4}\s+/.test("# ")` retornava **`true`**, fazendo sua negação ser **`false`**.
3. Consequentemente:
   - O loop `while` interno do parágrafo executava **0 iterações**.
   - O índice `i` **nunca era incrementado**.
   - As linhas 725–727 criavam e anexavam um elemento vazio `<p class="atlas-md-p"></p>` em `container`.
   - O loop externo `while (i < lines.length)` repetia indefinidamente sobre o mesmo índice `i`, bloqueando 100% da thread principal de JavaScript e alocando centenas de milhares de elementos `<p>` por segundo até o processo renderizador do navegador esgotar a RAM e cair.

### Por que os testes anteriores da v1.9.0 não detectaram o defeito

O teste E2E [`tests/e2e/test_v190_atlas_reflections.py`](../../tests/e2e/test_v190_atlas_reflections.py) utilizava `page.fill("#atlas-reflection-body", "# Introdução\n\n...")`, que injeta o texto completo em um único evento `input` sem jamais passar pelo estado intermediário `"# "` que ocorre naturalmente na digitação humana caractere a caractere.

---

## 3. Evidências Objetivas: Antes vs. Depois da Correção

Reprodução segura conduzida em ambiente isolado (instância local efêmera do Uvicorn com banco SQLite temporário em `/tmp`, sem tocar dados de produção, Chromium headless do Playwright com `--js-flags=--max-old-space-size=128` e timeout estrito de 4 s).

> **Nota sobre navegadores**: o ambiente local possui o binário Playwright Chromium instalado (`chromium-1255`); o binário Playwright Firefox (`firefox-1572`) não está instalado neste host, portanto a validação automatizada foi executada em Chromium headless.

| Cenário / Métrica | Antes da Correção (`v1.9.0`) | Depois da Correção (`fix/v1.9.1-markdown-heading-hang`) |
| :--- | :--- | :--- |
| `renderSafeMarkdownToDom(box, "# ")` (instrumentado) | **Loop infinito** — trava de segurança atingiu **5.001** appends `<p class="atlas-md-p">` em **6,1 ms** (~820k nós/s) | **1 append (`H1`)** em **0,2 ms** |
| `renderSafeMarkdownToDom(box, "## ")` | **Loop infinito** — 5.001 appends `<p>` em **4,8 ms** | **1 append (`H2`)** em **< 0,1 ms** |
| `renderSafeMarkdownToDom(box, "### ")` | **Loop infinito** — 5.001 appends `<p>` em **52,3 ms** | **1 append (`H3`)** em **< 0,1 ms** |
| `renderSafeMarkdownToDom(box, "#### ")` | **Loop infinito** — 5.001 appends `<p>` em **4,9 ms** | **1 append (`H4`)** em **< 0,1 ms** |
| `renderSafeMarkdownToDom(box, "##### ")` / `"###### "` | Caía como parágrafo literal (H5/H6 não suportados) | **1 append (`H5` / `H6`)** em **< 0,1 ms** |
| Digitação real `# ` na UI do Atlas (`#atlas-reflection-body`, modo Dividido) | **Congelamento total** — `TimeoutError` após **4.173 ms** (thread principal travada) | **118,34 ms** (inclui clique + delay de 10 ms/tecla) |
| Nós filhos em `#atlas-reflection-preview` após digitar `# ` | Explosão ilimitada de `<p>` até OOM | **1 nó (`<h1 class="atlas-md-h1">`)** |
| Heap JS (`usedJSHeapSize`) antes → depois de digitar `# ` | Crescimento contínuo até o limite do processo (OOM) | **9,54 MB → 9,54 MB** (`+0,00 MB`) |
| Escala controlada (250 títulos H1–H6 + 250 parágrafos) | N/A (travava ao digitar qualquer título) | **Exatos 500 nós DOM**, renderização linear (< 2,5 s no E2E) |

---

## 4. Correção Aplicada e Revisão de Consumidores

### Auditoria de consumidores
Busca em todo o diretório `static/` confirmou que `renderSafeMarkdownToDom` em `static/js/knowledge-api.js` é o **único** renderizador Markdown do frontend e possui um único ponto de chamada ativo: `updateEditorMetricsAndPreview()` em `static/js/knowledge.js`.

### Mudanças mínimas implementadas

1. **[`static/js/knowledge-api.js`](../../static/js/knowledge-api.js)**:
   - Unificação das expressões regulares de bloco (`HEADING_RE`, `HEADING_PREFIX_RE`, `HR_RE`, `QUOTE_RE`, `UL_RE`, `OL_RE`) como fonte única de verdade dentro de `renderSafeMarkdownToDom`.
   - Suporte completo a títulos **H1–H6** (`HEADING_RE = /^\s*(#{1,6})\s+(.*)$/` e `HEADING_PREFIX_RE = /^\s*#{1,6}\s+/`), aceitando conteúdo vazio durante a digitação (`# ` → `<h1 class="atlas-md-h1">`) e preservando `#` sem espaço (`#`, `#tag`, `####### texto`) como parágrafo normal.
   - **Garantia matemática de terminação**: adicionada guarda defensiva `if (i === startIndex) { paraLines.push(trimmed); i++; }` no laço principal e guarda de progresso contra match de comprimento zero (`if (match[0].length === 0) { INLINE_TOKEN_RE.lastIndex++; continue; }`) em `appendInlineMarkdownNodes`.
   - Mantida 100% a construção segura via `createElement` / `createTextNode` (zero `innerHTML`, bloqueio de `javascript:` e imagens externas).
2. **[`static/css/style.css`](../../static/css/style.css)**:
   - Adicionados `.atlas-md-h5` e `.atlas-md-h6` ao bloco tipográfico de `.atlas-md-h1..h4`.
3. **[`static/js/knowledge.js`](../../static/js/knowledge.js)**:
   - Memoização leve (`dom.editorPreviewBox.dataset.lastRenderedMd !== bodyText`) em `updateEditorMetricsAndPreview()` para evitar reconstruções redundantes do DOM quando o conteúdo não mudou (ex.: ao alternar modos de visualização ou eventos não-mutantes).

---

## 5. Cobertura de Testes de Regressão Adicionada

Criada a suíte automatizada portátil [`tests/e2e/test_v191_markdown_heading_regression.py`](../../tests/e2e/test_v191_markdown_heading_regression.py) validando:
- Digitação real caractere por caractere (`locator.type`) de `# ` até `###### ` sem congelamento e com exatamente 1 elemento `H1`–`H6`.
- `# ` seguido de `Enter` + parágrafo e `#` sem espaço (`#semespaco`, `####### sete cerquilhas`).
- Renderização de todos os 6 níveis (`H1`–`H6`), formatação inline (`**negrito**`, `*itálico*`) e Unicode/acentuação (`Época`, `ação`, `coração`, `Ω`).
- Proteção XSS intacta (`<script>`, `<img onerror>`, `javascript:` em links e bloqueio de imagem externa).
- Alternância entre modos **Editar**, **Visualizar** e **Dividido**, digitação rápida e deleção (`Backspace`).
- Conteúdo longo (250 títulos + 250 parágrafos = exatos 500 nós DOM, tempo controlado).
- Salvamento persistido e reabertura da reflexão com H1–H6.
- Zero `console.error` e zero `pageerror`.
