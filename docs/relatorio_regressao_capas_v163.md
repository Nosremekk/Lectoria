# Relatório de Diagnóstico e Correção de Regressão Visual — Lectoria v1.6.3

## 1. Contexto e Sintoma Observado

Após as otimizações de navegação e filtros da versão v1.6.2 (renderização progressiva em lotes de 36 livros e unificação de alternância Grade/Lista via CSS), foi identificada uma regressão visual:

- **Na página inicial ("Descobertas & Destaques"):** As capas dos livros nas estantes horizontais (`.shelf-card`) eram exibidas normalmente, preenchendo a área designada com proporção e alinhamento corretos.
- **Na seção "Biblioteca & Explorar" (Modo Grade):** As capas dos livros apareciam com tamanho diminuto (miniatura ~48×68 px), fixadas no canto superior esquerdo de um container cinza desproporcionalmente grande (~221×314 px).

---

## 2. Diagnóstico e Causa Raiz Comprovada

A inspeção do DOM renderizado e da cascata de estilos CSS revelou com precisão matemática a causa raiz:

1. **Separação de componentes da Página Inicial vs Biblioteca:**
   - A página inicial utiliza os componentes `.shelf-card`, `.shelf-cover-wrapper` e `.shelf-cover-img`, que nunca dependeram das classes de listagem da biblioteca. Por esse motivo, a página inicial não foi afetada.
   - A biblioteca utiliza `.book-card` dentro do container `#books-container`.

2. **Vazamento de classes da Lista para a Grade no JavaScript:**
   - Na v1.6.2, para unificar a alternância entre Grade e Lista sem refazer requisições nem reconstruir o DOM via JavaScript, a função `generateBookCardHtml(book)` em `static/js/app.js` agrupou classes da Grade e da Lista no mesmo template:
     ```html
     <article class="book-card list-item" data-id="${book.id}">
       <div class="book-cover-wrapper">
         <a href="/reader/${book.id}" class="book-cover list-cover-link">
           <img class="book-cover-img list-cover-thumb" ...>
     ```
   - Além de `list-cover-link`, foram incluídas indevidamente `list-item`, `list-info`, `list-title`, `list-author`, `list-meta`, `list-progress` e `list-actions`.

3. **Conflito de Cascata e Ordem de Declaração no CSS:**
   - Em `static/css/style.css`, a regra genérica `.book-cover` declara `width: 100%; height: 100%;` na linha 432.
   - Mais abaixo (linha 683), a regra legada `.list-cover-link` declarava:
     ```css
     .list-cover-link {
       flex-shrink: 0;
       width: 48px;
       height: 68px;
       ...
     }
     ```
   - Como ambas as regras possuem especificidade de classe única (`0-1-0`), a regra que aparece por último na ordem do arquivo vence. Consequentemente, `.list-cover-link` sobrepôs `.book-cover`, forçando `width: 48px` e `height: 68px` no link da capa mesmo quando `#books-container` estava no modo Grade (`.books-grid`).
   - A imagem interna (`.book-cover-img`), configurada para `100%` do seu pai, encolheu para 46×66 px, deixando o restante do `.book-cover-wrapper` (221×314 px) vazio.

---

## 3. Correção Implementada (Candidata v1.6.3)

A correção atuou nas duas frentes para garantir robustez e manutenibilidade:

### 3.1 Higienização do Template HTML (`static/js/app.js`)
Removidas todas as classes legadas `list-*` de `generateBookCardHtml()`. O template passou a gerar estritamente a estrutura semântica limpa:
- `<article class="book-card" data-id="${book.id}">`
- `<div class="book-cover-wrapper">`
- `<a href="..." class="book-cover">`
- `<img class="book-cover-img" src="..." loading="lazy">`
- `<div class="book-info">`, `<h3 class="book-title">`, `<div class="book-author">`, `<div class="book-meta-details">`, `<div class="progress-container">`, `<div class="card-actions-bar">`

### 3.2 Isolamento Estrito no CSS (`static/css/style.css`)
1. As regras legadas `.list-cover-link` e `.list-cover-thumb` foram escopadas como fallback seguro sob `.books-list`.
2. Em `.books-list .book-card .book-cover-wrapper`:
   - Adicionado `aspect-ratio: auto;` (evita conflitos com a proporção de 1 / 1.42 do modo grade).
3. Em `.books-list .book-card .book-cover::after`:
   - Adicionado `display: none;` (elimina o vinco de lombada que escurecia a miniatura na listagem).
4. Em `.books-list .book-card:hover`:
   - Adicionado `transform: none;` (evita elevação vertical inadequada em linhas de listagem).

---

## 4. Evidências Métricas e Comparativo de Bounding Box

Medições automatizadas executadas via Playwright em instância de teste com resolução Desktop (1280×800) e Mobile (375×812 e 412×915):

| Componente / Modo | Antes da Correção (v1.6.2) | Após a Correção (v1.6.3 Candidata) | Proporção / Ocupação Real |
| :--- | :--- | :--- | :--- |
| **Grid: `.book-cover-wrapper`** | 186.5 × 264.8 px | 221.7 × 314.8 px | Proporção 1 / 1.42 exata |
| **Grid: `a.book-cover`** | **48.0 × 68.0 px** (miniatura) | **221.7 × 313.8 px** (100%) | 100% da área do container |
| **Grid: `img.book-cover-img`** | **46.0 × 66.0 px** (encolhida) | **221.7 × 313.8 px** (100%) | `object-fit: cover` ativo e preenchido |
| **Lista: `.book-cover-wrapper`** | 48.0 × 68.0 px | 48.0 × 68.0 px | Miniatura padronizada preservada |
| **Lista: `img.book-cover-img`** | 46.0 × 66.0 px | 46.0 × 66.0 px | Encaixe perfeito no wrapper da lista |
| **Página Inicial: `.shelf-cover-img`**| 168.0 × 252.0 px | 168.0 × 252.0 px | Preservada sem alteração |
| **Mobile 375px: `img.book-cover-img`**| 46.0 × 66.0 px | 169.9 × 240.3 px | Ocupa 100% da coluna do grid |
| **Mobile 412px: `img.book-cover-img`**| 46.0 × 66.0 px | 186.8 × 264.3 px | Ocupa 100% da coluna do grid |

### Comportamento de Rede e Recursos
- **Alternância Grade $\leftrightarrow$ Lista:** **0 requisições HTTP** (chaveamento instantâneo via CSS puro).
- **Chunking progressivo:** Lote inicial de 36 cards carregado com renderização de mais cards sob demanda com scroll ou clique.
- **Overflow horizontal involuntário:** `scrollWidth == clientWidth` em 320px, 375px, 390px, 412px e 768px (`overflowX == false`).

---

## 5. Bateria de Testes Automatizados

A integridade do sistema foi validada com a execução de toda a suíte de testes:
- **Total de testes executados:** 209 testes (178 unitários/integração + 31 Playwright end-to-end).
- **Resultado:** **209 aprovados (100%), 0 falhas**.
- **Novo teste de regressão adicionado:** `tests/test_v163_cover_rendering.py` garantindo que nenhuma classe `list-*` seja reinserida no template do card e que o schema SQLite permaneça estritamente na versão 10.
