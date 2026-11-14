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
