/**
 * Lectoria — Leitor de PDF com PDF.js (Iteração 3)
 * Marca-texto com coordenadas normalizadas, Anotações textuais vinculadas,
 * Desenho livre vetorial (SVG) e Painel lateral de estudos.
 */

if (window.pdfjsLib) {
  pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/vendor/pdfjs/pdf.worker.min.js";
}

document.addEventListener("DOMContentLoaded", async () => {
  const pathParts = window.location.pathname.split("/");
  const bookId = pathParts[pathParts.length - 1];

  if (!bookId) {
    alert("Identificador do livro inválido.");
    window.location.href = "/";
    return;
  }

  // --- Elementos do DOM ---
  const bookTitleEl = document.getElementById("reader-book-title");
  const pageNumInput = document.getElementById("page-num-input");
  const pageCountDisplay = document.getElementById("page-count-display");
  const btnPrevPage = document.getElementById("btn-prev-page");
  const btnNextPage = document.getElementById("btn-next-page");
  const btnZoomIn = document.getElementById("btn-zoom-in");
  const btnZoomOut = document.getElementById("btn-zoom-out");
  const btnFitWidth = document.getElementById("btn-fit-width");
  const btnFitPage = document.getElementById("btn-fit-page");
  const btnModeSingle = document.getElementById("btn-mode-single");
  const btnModeContinuous = document.getElementById("btn-mode-continuous");
  const btnReaderTheme = document.getElementById("btn-reader-theme");
  const saveIndicator = document.getElementById("save-indicator");

  // Ferramentas de Modo
  const btnToolRead = document.getElementById("btn-tool-read");
  const btnToolHighlight = document.getElementById("btn-tool-highlight");
  const btnToolDraw = document.getElementById("btn-tool-draw");
  const btnToggleDrawer = document.getElementById("btn-toggle-drawer");

  // Barras Flutuantes e Modais
  const selectionToolbar = document.getElementById("selection-toolbar");
  const btnAddNoteFromSelection = document.getElementById("btn-add-note-from-selection");
  const drawingToolbar = document.getElementById("drawing-toolbar");
  const drawStrokeWidthSelect = document.getElementById("draw-stroke-width");
  const btnDrawEraser = document.getElementById("btn-draw-eraser");
  const btnDrawClear = document.getElementById("btn-draw-clear");

  // Painel de Anotações
  const annotationsDrawer = document.getElementById("annotations-drawer");
  const btnCloseDrawer = document.getElementById("btn-close-drawer");
  const drawerTabs = document.querySelectorAll(".drawer-tab");
  const drawerItemsList = document.getElementById("drawer-items-list");
  const countAllEl = document.getElementById("count-all-annotations");
  const countHighlightsEl = document.getElementById("count-highlights");
  const countNotesEl = document.getElementById("count-notes");
  const btnNewPageNote = document.getElementById("btn-new-page-note");

  // Modal Editor de Anotação
  const modalNoteEditor = document.getElementById("modal-note-editor");
  const modalNoteTitle = document.getElementById("modal-note-title");
  const modalNoteQuoteBox = document.getElementById("modal-note-quote-box");
  const modalNoteQuoteText = document.getElementById("modal-note-quote-text");
  const noteContentInput = document.getElementById("note-content-input");
  const btnCloseModalNote = document.getElementById("btn-close-modal-note");
  const btnCancelNote = document.getElementById("btn-cancel-note");
  const btnSaveNote = document.getElementById("btn-save-note");

  // Containers de Visualização
  const viewportEl = document.getElementById("reader-viewport");
  const singlePageWrapper = document.getElementById("single-page-wrapper");
  const continuousWrapper = document.getElementById("continuous-wrapper");
  const canvas = document.getElementById("pdf-canvas");
  const ctx = canvas.getContext("2d");
  const singleTextLayer = document.getElementById("single-text-layer");
  const singleHighlightsLayer = document.getElementById("single-highlights-layer");
  const singleDrawingLayer = document.getElementById("single-drawing-layer");

  // --- Estado do Leitor ---
  let pdfDoc = null;
  let currentPage = 1;
  let totalPages = 1;
  let currentScale = 1.0;
  let zoomMode = "width";
  let viewMode = localStorage.getItem("lectoria_reader_mode") || "single";
  let currentTheme = localStorage.getItem("lectoria_theme") || "dark";
  let currentTool = "read"; // 'read', 'highlight', 'draw'

  let singleRenderTask = null;
  let saveProgressTimeout = null;
  let continuousObserver = null;
  const renderedSlots = new Map();
  let basePageWidth = 600;
  let basePageHeight = 800;

  // Estado das Anotações e Desenhos
  let bookAnnotations = { highlights: [], notes: [], drawing_pages: [] };
  let currentSelectionData = null; // { page, text, rects, context }
  let editingNoteId = null;
  let editingNoteHighlightId = null;

  // Estado do Desenho Livre
  let isDrawing = false;
  let currentDrawColor = "#3d7cf4";
  let currentDrawWidth = 4;
  let isEraserActive = false;
  let currentStroke = null;
  let pageStrokesMap = new Map(); // pageNum -> array of strokes
  let saveDrawingTimeout = null;

  // --- Tema ---
  function applyTheme(theme) {
    currentTheme = theme;
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("lectoria_theme", theme);
  }
  btnReaderTheme.addEventListener("click", () => {
    applyTheme(currentTheme === "dark" ? "light" : "dark");
  });
  applyTheme(currentTheme);

  function showSavedIndicator() {
    saveIndicator.classList.add("visible");
    setTimeout(() => saveIndicator.classList.remove("visible"), 2000);
  }

  // --- Progresso ---
  async function persistProgress(pageToSave) {
    try {
      await fetch(`/api/books/${bookId}/progress`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_page: pageToSave, total_pages: totalPages }),
      });
      showSavedIndicator();
    } catch (e) {
      console.warn("Erro ao salvar progresso:", e);
    }
  }

  function queueSaveProgress() {
    clearTimeout(saveProgressTimeout);
    saveProgressTimeout = setTimeout(() => persistProgress(currentPage), 1500);
  }

  function emergencySave() {
    const payload = JSON.stringify({ current_page: currentPage, total_pages: totalPages });
    if (navigator.sendBeacon) {
      navigator.sendBeacon(`/api/books/${bookId}/progress`, new Blob([payload], { type: "application/json" }));
    } else {
      fetch(`/api/books/${bookId}/progress`, { method: "POST", headers: { "Content-Type": "application/json" }, body: payload, keepalive: true });
    }
  }
  window.addEventListener("beforeunload", emergencySave);
  window.addEventListener("pagehide", emergencySave);

  // --- Escala (Zoom) ---
  function calculateScaleForPage(unscaledViewport) {
    const availableWidth = viewportEl.clientWidth - 48;
    const availableHeight = viewportEl.clientHeight - 48;
    if (zoomMode === "width") {
      let s = availableWidth / unscaledViewport.width;
      return s > 0 ? s : 1.0;
    } else if (zoomMode === "page") {
      let sw = availableWidth / unscaledViewport.width;
      let sh = availableHeight / unscaledViewport.height;
      return Math.min(sw, sh) > 0 ? Math.min(sw, sh) : 1.0;
    }
    return currentScale;
  }

  // --- Carregamento de Anotações do Servidor ---
  async function reloadBookAnnotations() {
    try {
      const res = await fetch(`/api/books/${bookId}/annotations`);
      if (res.ok) {
        bookAnnotations = await res.json();
        updateDrawerCounts();
        renderDrawerList();
        renderHighlightsOnActivePages();
      }
    } catch (e) {
      console.warn("Erro ao carregar anotações:", e);
    }
  }

  // --- Renderização no Modo Página Única ---
  async function renderSinglePage(num) {
    if (!pdfDoc) return;
    if (singleRenderTask) {
      singleRenderTask.cancel();
      singleRenderTask = null;
    }

    try {
      const page = await pdfDoc.getPage(num);
      const unscaledViewport = page.getViewport({ scale: 1.0 });
      currentScale = calculateScaleForPage(unscaledViewport);

      const pixelRatio = window.devicePixelRatio || 1;
      const viewport = page.getViewport({ scale: currentScale * pixelRatio });
      const cssWidth = `${viewport.width / pixelRatio}px`;
      const cssHeight = `${viewport.height / pixelRatio}px`;

      canvas.width = viewport.width;
      canvas.height = viewport.height;
      canvas.style.width = cssWidth;
      canvas.style.height = cssHeight;

      singlePageWrapper.style.width = cssWidth;
      singlePageWrapper.style.height = cssHeight;
      singlePageWrapper.setAttribute("data-page-num", num);

      singleRenderTask = page.render({ canvasContext: ctx, viewport: viewport });
      await singleRenderTask.promise;
      singleRenderTask = null;

      // Renderiza Camada de Texto para permitir seleção
      singleTextLayer.innerHTML = "";
      singleTextLayer.style.width = cssWidth;
      singleTextLayer.style.height = cssHeight;
      const textContent = await page.getTextContent();
      pdfjsLib.renderTextLayer({
        textContentSource: textContent,
        container: singleTextLayer,
        viewport: page.getViewport({ scale: currentScale }),
        textDivs: [],
      });

      // Renderiza Marcações e Desenhos
      renderHighlightsForPage(num, singleHighlightsLayer);
      await setupDrawingLayerForPage(num, singleDrawingLayer);

      pageNumInput.value = num;
      btnPrevPage.disabled = num <= 1;
      btnNextPage.disabled = num >= totalPages;
      queueSaveProgress();
    } catch (err) {
      if (err.name !== "RenderingCancelledException") {
        console.error("Erro ao renderizar página:", err);
      }
    }
  }

  // --- Renderização no Modo Rolagem Contínua (Lazy & Virtualizado) ---
  function setupContinuousView() {
    continuousWrapper.innerHTML = "";
    renderedSlots.clear();
    if (continuousObserver) continuousObserver.disconnect();

    const availableWidth = viewportEl.clientWidth - 48;
    const scale = zoomMode === "width" ? (availableWidth / basePageWidth) : currentScale;
    const slotWidth = Math.round(basePageWidth * scale);
    const slotHeight = Math.round(basePageHeight * scale);

    for (let p = 1; p <= totalPages; p++) {
      const slot = document.createElement("div");
      slot.className = "page-slot page-container";
      slot.id = `page-slot-${p}`;
      slot.setAttribute("data-page-num", p);
      slot.style.width = `${slotWidth}px`;
      slot.style.height = `${slotHeight}px`;
      slot.innerHTML = `<div class="page-slot-placeholder">Página ${p}</div>`;
      continuousWrapper.appendChild(slot);
    }

    continuousObserver = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        const pageNum = parseInt(entry.target.getAttribute("data-page-num"), 10);
        if (entry.isIntersecting) {
          renderSlotPage(entry.target, pageNum);
        } else {
          unloadSlotPage(entry.target, pageNum);
        }
      });
    }, { root: viewportEl, rootMargin: "300px 0px 300px 0px" });

    document.querySelectorAll(".page-slot").forEach(slot => continuousObserver.observe(slot));

    const initialSlot = document.getElementById(`page-slot-${currentPage}`);
    if (initialSlot) initialSlot.scrollIntoView();
  }

  async function renderSlotPage(slotElement, pageNum) {
    if (renderedSlots.has(pageNum)) return;

    try {
      const page = await pdfDoc.getPage(pageNum);
      const unscaledViewport = page.getViewport({ scale: 1.0 });

      let scale = currentScale;
      if (zoomMode === "width") {
        scale = (viewportEl.clientWidth - 48) / unscaledViewport.width;
      }

      const pixelRatio = window.devicePixelRatio || 1;
      const viewport = page.getViewport({ scale: scale * pixelRatio });
      const cssWidth = `${viewport.width / pixelRatio}px`;
      const cssHeight = `${viewport.height / pixelRatio}px`;

      slotElement.style.width = cssWidth;
      slotElement.style.height = cssHeight;
      slotElement.innerHTML = "";

      const c = document.createElement("canvas");
      c.width = viewport.width;
      c.height = viewport.height;
      c.style.width = cssWidth;
      c.style.height = cssHeight;
      slotElement.appendChild(c);

      const tLayer = document.createElement("div");
      tLayer.className = "textLayer";
      tLayer.style.width = cssWidth;
      tLayer.style.height = cssHeight;
      slotElement.appendChild(tLayer);

      const hLayer = document.createElement("div");
      hLayer.className = "highlights-layer";
      slotElement.appendChild(hLayer);

      const dLayer = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      dLayer.setAttribute("class", "drawing-layer");
      slotElement.appendChild(dLayer);

      const renderTask = page.render({ canvasContext: c.getContext("2d"), viewport: viewport });
      renderedSlots.set(pageNum, { canvas: c, renderTask });
      await renderTask.promise;

      const textContent = await page.getTextContent();
      pdfjsLib.renderTextLayer({
        textContentSource: textContent,
        container: tLayer,
        viewport: page.getViewport({ scale: scale }),
        textDivs: [],
      });

      renderHighlightsForPage(pageNum, hLayer);
      await setupDrawingLayerForPage(pageNum, dLayer);
    } catch (e) {
      if (e.name !== "RenderingCancelledException") {
        console.warn(`Erro no slot ${pageNum}:`, e);
      }
    }
  }

  function unloadSlotPage(slotElement, pageNum) {
    if (renderedSlots.has(pageNum)) {
      const item = renderedSlots.get(pageNum);
      if (item.renderTask) item.renderTask.cancel();
      renderedSlots.delete(pageNum);
      slotElement.innerHTML = `<div class="page-slot-placeholder">Página ${pageNum}</div>`;
    }
  }

  // --- Scroll no Modo Contínuo ---
  let scrollTimeout = null;
  viewportEl.addEventListener("scroll", () => {
    if (viewMode !== "continuous") return;
    clearTimeout(scrollTimeout);
    scrollTimeout = setTimeout(() => {
      const centerY = viewportEl.getBoundingClientRect().top + viewportEl.clientHeight / 2;
      let closestPage = currentPage;
      let minDistance = Infinity;

      document.querySelectorAll(".page-slot").forEach(slot => {
        const dist = Math.abs(slot.getBoundingClientRect().top + slot.clientHeight / 2 - centerY);
        if (dist < minDistance) {
          minDistance = dist;
          closestPage = parseInt(slot.getAttribute("data-page-num"), 10);
        }
      });

      if (closestPage !== currentPage) {
        currentPage = closestPage;
        pageNumInput.value = currentPage;
        btnPrevPage.disabled = currentPage <= 1;
        btnNextPage.disabled = currentPage >= totalPages;
        queueSaveProgress();
      }
    }, 100);
  });

  // --- Renderização de Destaques (Highlights) ---
  function renderHighlightsForPage(pageNum, container) {
    container.innerHTML = "";
    const pageHighlights = (bookAnnotations.highlights || []).filter(h => h.page === pageNum);

    pageHighlights.forEach(h => {
      (h.rects || []).forEach(r => {
        const box = document.createElement("div");
        box.className = "highlight-box";
        box.style.left = `${r.x * 100}%`;
        box.style.top = `${r.y * 100}%`;
        box.style.width = `${r.w * 100}%`;
        box.style.height = `${r.h * 100}%`;
        box.style.backgroundColor = h.color || "#fef08a";
        box.title = `Destaque: "${h.selected_text}" (Clique para excluir ou anotar)`;

        box.addEventListener("click", (e) => {
          e.stopPropagation();
          if (confirm(`Deseja remover este destaque?\n\n"${h.selected_text.substring(0, 80)}..."`)) {
            deleteHighlight(h.id);
          }
        });

        container.appendChild(box);
      });
    });
  }

  function renderHighlightsOnActivePages() {
    if (viewMode === "single") {
      renderHighlightsForPage(currentPage, singleHighlightsLayer);
    } else {
      document.querySelectorAll(".page-slot").forEach(slot => {
        const p = parseInt(slot.getAttribute("data-page-num"), 10);
        const hLayer = slot.querySelector(".highlights-layer");
        if (hLayer) renderHighlightsForPage(p, hLayer);
      });
    }
  }

  async function deleteHighlight(highlightId) {
    try {
      const res = await fetch(`/api/books/${bookId}/highlights/${highlightId}`, { method: "DELETE" });
      if (res.ok) {
        await reloadBookAnnotations();
      }
    } catch (e) {
      alert("Erro ao excluir destaque.");
    }
  }

  // --- Seleção de Texto e Popover Flutuante ---
  document.addEventListener("selectionchange", () => {
    if (currentTool === "draw") return;
    handleTextSelection();
  });

  function handleTextSelection() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
      hideSelectionToolbar();
      return;
    }

    const text = sel.toString().trim();
    if (!text || text.length === 0) {
      hideSelectionToolbar();
      return;
    }

    const range = sel.getRangeAt(0);
    let pageContainer = range.commonAncestorContainer;
    while (pageContainer && !pageContainer.classList?.contains("page-container")) {
      pageContainer = pageContainer.parentElement;
    }

    if (!pageContainer) {
      hideSelectionToolbar();
      return;
    }

    const pageNum = parseInt(pageContainer.getAttribute("data-page-num"), 10);
    const pageRect = pageContainer.getBoundingClientRect();
    const clientRects = Array.from(range.getClientRects());
    if (clientRects.length === 0) return;

    // Normalização das coordenadas relativas à página (0.0 a 1.0)
    const normalizedRects = clientRects.map(r => ({
      x: Math.max(0, (r.left - pageRect.left) / pageRect.width),
      y: Math.max(0, (r.top - pageRect.top) / pageRect.height),
      w: Math.min(1, r.width / pageRect.width),
      h: Math.min(1, r.height / pageRect.height),
    }));

    currentSelectionData = {
      page: pageNum,
      text: text,
      rects: normalizedRects,
      context: range.startContainer.textContent?.substring(0, 150) || "",
    };

    // Posiciona toolbar flutuante logo acima da seleção
    const firstRect = clientRects[0];
    selectionToolbar.style.left = `${Math.max(10, firstRect.left + firstRect.width / 2 - 100)}px`;
    selectionToolbar.style.top = `${Math.max(60, firstRect.top - 44)}px`;
    selectionToolbar.classList.add("active");
  }

  function hideSelectionToolbar() {
    selectionToolbar.classList.remove("active");
    currentSelectionData = null;
  }

  // Ações de Cores de Destaque
  document.querySelectorAll(".highlight-color-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      if (!currentSelectionData) return;
      const color = btn.getAttribute("data-color");
      try {
        const res = await fetch(`/api/books/${bookId}/highlights`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            page: currentSelectionData.page,
            color: color,
            rects: currentSelectionData.rects,
            selected_text: currentSelectionData.text,
            text_context: currentSelectionData.context,
          }),
        });

        if (res.ok) {
          window.getSelection()?.removeAllRanges();
          hideSelectionToolbar();
          await reloadBookAnnotations();
        }
      } catch (e) {
        alert("Erro ao salvar marcação.");
      }
    });
  });

  // Botão "Nota a partir da seleção"
  btnAddNoteFromSelection.addEventListener("click", async () => {
    if (!currentSelectionData) return;
    const selData = { ...currentSelectionData };
    window.getSelection()?.removeAllRanges();
    hideSelectionToolbar();

    // Cria o destaque primeiro e abre o modal de anotação vinculado
    try {
      const res = await fetch(`/api/books/${bookId}/highlights`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          page: selData.page,
          color: "#fef08a",
          rects: selData.rects,
          selected_text: selData.text,
          text_context: selData.context,
        }),
      });

      if (res.ok) {
        const createdH = (await res.json()).highlight;
        await reloadBookAnnotations();
        openNoteEditorModal({
          title: "Adicionar Anotação ao Trecho",
          page: selData.page,
          highlightId: createdH.id,
          quote: selData.text,
        });
      }
    } catch (e) {
      alert("Erro ao criar destaque para anotação.");
    }
  });

  // --- Modal de Editor de Anotação ---
  function openNoteEditorModal({ title, page, highlightId = null, quote = null, existingNoteId = null, initialContent = "" }) {
    editingNoteId = existingNoteId;
    editingNoteHighlightId = highlightId;
    modalNoteTitle.textContent = title || "Anotação";
    noteContentInput.value = initialContent;

    if (quote) {
      modalNoteQuoteText.textContent = `"${quote}"`;
      modalNoteQuoteBox.style.display = "block";
    } else {
      modalNoteQuoteBox.style.display = "none";
    }

    modalNoteEditor.setAttribute("data-target-page", page || currentPage);
    modalNoteEditor.classList.add("active");
    setTimeout(() => noteContentInput.focus(), 100);
  }

  function closeNoteEditorModal() {
    modalNoteEditor.classList.remove("active");
    editingNoteId = null;
    editingNoteHighlightId = null;
    noteContentInput.value = "";
  }

  btnCloseModalNote.addEventListener("click", closeNoteEditorModal);
  btnCancelNote.addEventListener("click", closeNoteEditorModal);

  btnSaveNote.addEventListener("click", async () => {
    const content = noteContentInput.value.trim();
    if (!content) {
      alert("Por favor, digite o conteúdo da anotação.");
      return;
    }

    const targetPage = parseInt(modalNoteEditor.getAttribute("data-target-page"), 10) || currentPage;

    try {
      if (editingNoteId) {
        // Atualiza nota existente
        const res = await fetch(`/api/books/${bookId}/notes/${editingNoteId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content }),
        });
        if (!res.ok) throw new Error();
      } else {
        // Cria nova nota
        const res = await fetch(`/api/books/${bookId}/notes`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            page: targetPage,
            content,
            highlight_id: editingNoteHighlightId,
          }),
        });
        if (!res.ok) throw new Error();
      }

      closeNoteEditorModal();
      await reloadBookAnnotations();
    } catch (e) {
      alert("Erro ao salvar anotação.");
    }
  });

  btnNewPageNote.addEventListener("click", () => {
    openNoteEditorModal({
      title: `Nova Anotação (Página ${currentPage})`,
      page: currentPage,
      highlightId: null,
      quote: null,
    });
  });

  // --- Painel Lateral de Anotações (Drawer) ---
  let activeDrawerFilter = "all";

  btnToggleDrawer.addEventListener("click", () => {
    annotationsDrawer.classList.toggle("active");
    if (annotationsDrawer.classList.contains("active")) {
      reloadBookAnnotations();
    }
  });
  btnCloseDrawer.addEventListener("click", () => annotationsDrawer.classList.remove("active"));

  drawerTabs.forEach(tab => {
    tab.addEventListener("click", () => {
      drawerTabs.forEach(t => t.classList.remove("active"));
      tab.classList.add("active");
      activeDrawerFilter = tab.getAttribute("data-drawer-filter");
      renderDrawerList();
    });
  });

  function updateDrawerCounts() {
    const hCount = (bookAnnotations.highlights || []).length;
    const nCount = (bookAnnotations.notes || []).length;
    countHighlightsEl.textContent = hCount;
    countNotesEl.textContent = nCount;
    countAllEl.textContent = hCount + nCount;
  }

  function renderDrawerList() {
    const highlights = bookAnnotations.highlights || [];
    const notes = bookAnnotations.notes || [];

    let items = [];
    if (activeDrawerFilter === "all" || activeDrawerFilter === "highlights") {
      highlights.forEach(h => items.push({ type: "highlight", data: h, page: h.page, date: h.created_at }));
    }
    if (activeDrawerFilter === "all" || activeDrawerFilter === "notes") {
      notes.forEach(n => items.push({ type: "note", data: n, page: n.page, date: n.created_at }));
    }

    items.sort((a, b) => a.page - b.page || new Date(a.date) - new Date(b.date));

    if (items.length === 0) {
      drawerItemsList.innerHTML = `
        <div style="text-align: center; color: var(--text-faint); padding: 3rem 1rem;">
          <p>Nenhuma marcação ou anotação nesta categoria.</p>
          <p style="font-size: 0.8rem; margin-top: 0.4rem;">Selecione textos no livro para destacar ou use o botão acima para anotar.</p>
        </div>
      `;
      return;
    }

    drawerItemsList.innerHTML = items.map(item => {
      if (item.type === "highlight") {
        const h = item.data;
        return `
          <div class="annotation-item" data-goto-page="${h.page}">
            <div class="annotation-item-header">
              <span class="annotation-page-badge">Pág. ${h.page}</span>
              <span style="display: inline-block; width: 12px; height: 12px; border-radius: 50%; background-color: ${h.color}; border: 1px solid rgba(0,0,0,0.2);"></span>
            </div>
            <div class="annotation-quote" style="border-left-color: ${h.color};">
              "${escapeHtml(h.selected_text)}"
            </div>
            <div class="annotation-item-actions">
              <button class="btn-item-action btn-add-note-to-h" data-h-id="${h.id}" data-page="${h.page}" data-quote="${escapeHtml(h.selected_text)}">Adicionar Nota</button>
              <button class="btn-item-action danger btn-del-highlight" data-h-id="${h.id}">Excluir</button>
            </div>
          </div>
        `;
      } else {
        const n = item.data;
        return `
          <div class="annotation-item" data-goto-page="${n.page}">
            <div class="annotation-item-header">
              <span class="annotation-page-badge">Pág. ${n.page}</span>
              <span style="font-size: 0.72rem; color: var(--text-faint);">Nota</span>
            </div>
            ${n.highlight_text ? `<div class="annotation-quote">"${escapeHtml(n.highlight_text)}"</div>` : ""}
            <div class="annotation-note-text">${escapeHtml(n.content)}</div>
            <div class="annotation-item-actions">
              <button class="btn-item-action btn-edit-note" data-note-id="${n.id}" data-page="${n.page}" data-content="${escapeHtml(n.content)}">Editar</button>
              <button class="btn-item-action danger btn-del-note" data-note-id="${n.id}">Excluir</button>
            </div>
          </div>
        `;
      }
    }).join("");

    // Navegação ao clicar no item do painel
    drawerItemsList.querySelectorAll(".annotation-item").forEach(el => {
      el.addEventListener("click", (e) => {
        if (e.target.closest("button")) return;
        const targetPage = parseInt(el.getAttribute("data-goto-page"), 10);
        goToPage(targetPage);
      });
    });

    // Ações dos itens
    drawerItemsList.querySelectorAll(".btn-del-highlight").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteHighlight(btn.getAttribute("data-h-id"));
      });
    });

    drawerItemsList.querySelectorAll(".btn-add-note-to-h").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        openNoteEditorModal({
          title: "Nova Nota no Destaque",
          page: parseInt(btn.getAttribute("data-page"), 10),
          highlightId: btn.getAttribute("data-h-id"),
          quote: btn.getAttribute("data-quote"),
        });
      });
    });

    drawerItemsList.querySelectorAll(".btn-edit-note").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        openNoteEditorModal({
          title: "Editar Anotação",
          page: parseInt(btn.getAttribute("data-page"), 10),
          existingNoteId: btn.getAttribute("data-note-id"),
          initialContent: btn.getAttribute("data-content"),
        });
      });
    });

    drawerItemsList.querySelectorAll(".btn-del-note").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const noteId = btn.getAttribute("data-note-id");
        if (confirm("Deseja excluir esta anotação?")) {
          try {
            await fetch(`/api/books/${bookId}/notes/${noteId}`, { method: "DELETE" });
            await reloadBookAnnotations();
          } catch {
            alert("Erro ao excluir anotação.");
          }
        }
      });
    });
  }

  // --- Camada de Desenho Livre Vetorial (SVG) ---
  let isErasing = false;

  function distanceToSegment(px, py, x1, y1, x2, y2) {
    const dx = x2 - x1;
    const dy = y2 - y1;
    const lenSq = dx * dx + dy * dy;
    if (lenSq === 0) {
      const ex = px - x1;
      const ey = py - y1;
      return Math.sqrt(ex * ex + ey * ey);
    }
    let t = ((px - x1) * dx + (py - y1) * dy) / lenSq;
    t = Math.max(0, Math.min(1, t));
    const projX = x1 + t * dx;
    const projY = y1 + t * dy;
    const rx = px - projX;
    const ry = py - projY;
    return Math.sqrt(rx * rx + ry * ry);
  }

  function checkStrokeCollision(stroke, clientX, clientY, svgRect, eraserRadiusPx = 18) {
    if (!stroke.points || stroke.points.length === 0) return false;
    const px = clientX - svgRect.left;
    const py = clientY - svgRect.top;
    const w = svgRect.width;
    const h = svgRect.height;
    const threshold = eraserRadiusPx + ((stroke.width || 4) / 2);

    if (stroke.points.length === 1) {
      const sx = stroke.points[0].x * w;
      const sy = stroke.points[0].y * h;
      return Math.hypot(px - sx, py - sy) <= threshold;
    }

    for (let i = 0; i < stroke.points.length - 1; i++) {
      const x1 = stroke.points[i].x * w;
      const y1 = stroke.points[i].y * h;
      const x2 = stroke.points[i + 1].x * w;
      const y2 = stroke.points[i + 1].y * h;
      if (distanceToSegment(px, py, x1, y1, x2, y2) <= threshold) {
        return true;
      }
    }
    return false;
  }

  function eraseStrokesAtPoint(clientX, clientY, pageNum, svgElement) {
    const strokes = pageStrokesMap.get(pageNum);
    if (!strokes || strokes.length === 0) return false;

    const svgRect = svgElement.getBoundingClientRect();
    if (svgRect.width <= 0 || svgRect.height <= 0) return false;

    let modified = false;
    for (let i = strokes.length - 1; i >= 0; i--) {
      if (checkStrokeCollision(strokes[i], clientX, clientY, svgRect, 18)) {
        strokes.splice(i, 1);
        modified = true;
      }
    }

    if (modified) {
      pageStrokesMap.set(pageNum, strokes);
      renderSvgStrokes(svgElement, strokes);
      queueSaveDrawing(pageNum);
      return true;
    }
    return false;
  }

  async function loadStrokesForPage(pageNum) {
    if (pageStrokesMap.has(pageNum)) return pageStrokesMap.get(pageNum);
    try {
      const res = await fetch(`/api/books/${bookId}/pages/${pageNum}/drawing`);
      if (res.ok) {
        const data = await res.json();
        const strokes = data.strokes || [];
        pageStrokesMap.set(pageNum, strokes);
        return strokes;
      }
    } catch {
      // Retorna vazio
    }
    return [];
  }

  async function setupDrawingLayerForPage(pageNum, svgElement) {
    svgElement.innerHTML = "";
    svgElement.setAttribute("data-page-num", pageNum);
    updateDrawingLayerInteractivity(svgElement);

    const strokes = await loadStrokesForPage(pageNum);
    renderSvgStrokes(svgElement, strokes);

    // Eventos de desenho e borracha
    svgElement.onpointerdown = (e) => handleDrawPointerDown(e, pageNum, svgElement);
    svgElement.onpointermove = (e) => handleDrawPointerMove(e, pageNum, svgElement);
    svgElement.onpointerup = (e) => handleDrawPointerUp(e, pageNum, svgElement);
    svgElement.onpointercancel = (e) => handleDrawPointerUp(e, pageNum, svgElement);
  }

  function renderSvgStrokes(svgElement, strokes) {
    svgElement.innerHTML = "";
    const rect = svgElement.getBoundingClientRect();
    const w = rect.width;
    const h = rect.height;
    if (w <= 0 || h <= 0) return;

    strokes.forEach((s, idx) => {
      if (!s.points || s.points.length < 2) return;
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("class", "drawing-stroke");
      path.setAttribute("data-stroke-index", idx);
      path.setAttribute("stroke", s.color || "#3d7cf4");
      path.setAttribute("stroke-width", s.width || 4);

      let d = `M ${s.points[0].x * w} ${s.points[0].y * h}`;
      for (let i = 1; i < s.points.length; i++) {
        d += ` L ${s.points[i].x * w} ${s.points[i].y * h}`;
      }
      path.setAttribute("d", d);
      svgElement.appendChild(path);
    });
  }

  function handleDrawPointerDown(e, pageNum, svgElement) {
    if (currentTool !== "draw") return;
    e.preventDefault();

    if (isEraserActive) {
      isErasing = true;
      try {
        svgElement.setPointerCapture(e.pointerId);
      } catch (_) {}
      eraseStrokesAtPoint(e.clientX, e.clientY, pageNum, svgElement);
      return;
    }

    try {
      svgElement.setPointerCapture(e.pointerId);
    } catch (_) {}

    const rect = svgElement.getBoundingClientRect();
    const nx = (e.clientX - rect.left) / rect.width;
    const ny = (e.clientY - rect.top) / rect.height;

    isDrawing = true;
    currentStroke = {
      color: currentDrawColor,
      width: currentDrawWidth,
      points: [{ x: nx, y: ny }],
    };

    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("class", "drawing-stroke current-drawing-path");
    path.setAttribute("stroke", currentDrawColor);
    path.setAttribute("stroke-width", currentDrawWidth);
    path.setAttribute("d", `M ${nx * rect.width} ${ny * rect.height}`);
    svgElement.appendChild(path);
  }

  function handleDrawPointerMove(e, pageNum, svgElement) {
    if (currentTool !== "draw") return;
    e.preventDefault();

    if (isEraserActive) {
      if (isErasing || e.buttons > 0) {
        eraseStrokesAtPoint(e.clientX, e.clientY, pageNum, svgElement);
      }
      return;
    }

    if (!isDrawing || !currentStroke) return;

    const rect = svgElement.getBoundingClientRect();
    const nx = (e.clientX - rect.left) / rect.width;
    const ny = (e.clientY - rect.top) / rect.height;
    currentStroke.points.push({ x: nx, y: ny });

    const currentPath = svgElement.querySelector(".current-drawing-path");
    if (currentPath) {
      const pts = currentStroke.points;
      let d = `M ${pts[0].x * rect.width} ${pts[0].y * rect.height}`;
      for (let i = 1; i < pts.length; i++) {
        d += ` L ${pts[i].x * rect.width} ${pts[i].y * rect.height}`;
      }
      currentPath.setAttribute("d", d);
    }
  }

  function handleDrawPointerUp(e, pageNum, svgElement) {
    if (currentTool !== "draw") return;

    if (isEraserActive) {
      isErasing = false;
      try {
        if (svgElement.hasPointerCapture(e.pointerId)) {
          svgElement.releasePointerCapture(e.pointerId);
        }
      } catch (_) {}
      return;
    }

    if (!isDrawing) return;
    isDrawing = false;

    if (currentStroke && currentStroke.points.length >= 2) {
      let strokes = pageStrokesMap.get(pageNum) || [];
      strokes.push(currentStroke);
      pageStrokesMap.set(pageNum, strokes);
      renderSvgStrokes(svgElement, strokes);
      queueSaveDrawing(pageNum);
    }
    currentStroke = null;
  }

  function queueSaveDrawing(pageNum) {
    clearTimeout(saveDrawingTimeout);
    saveDrawingTimeout = setTimeout(async () => {
      const strokes = pageStrokesMap.get(pageNum) || [];
      try {
        await fetch(`/api/books/${bookId}/pages/${pageNum}/drawing`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ strokes }),
        });
        showSavedIndicator();
      } catch (e) {
        console.warn("Erro ao salvar desenho:", e);
      }
    }, 1000);
  }

  btnDrawClear.addEventListener("click", async () => {
    if (confirm(`Deseja limpar todos os desenhos da Página ${currentPage}?`)) {
      try {
        await fetch(`/api/books/${bookId}/pages/${currentPage}/drawing`, { method: "DELETE" });
        pageStrokesMap.set(currentPage, []);
        if (viewMode === "single") {
          singleDrawingLayer.innerHTML = "";
        } else {
          const slot = document.getElementById(`page-slot-${currentPage}`);
          if (slot) {
            const svg = slot.querySelector(".drawing-layer");
            if (svg) svg.innerHTML = "";
          }
        }
      } catch {
        alert("Erro ao limpar desenhos.");
      }
    }
  });

  // Ferramentas de Desenho (Cores, Largura, Borracha)
  document.querySelectorAll(".drawing-toolbar .color-dot").forEach(dot => {
    dot.addEventListener("click", () => {
      document.querySelectorAll(".drawing-toolbar .color-dot").forEach(d => d.classList.remove("active"));
      dot.classList.add("active");
      currentDrawColor = dot.getAttribute("data-color");
      isEraserActive = false;
      btnDrawEraser.classList.remove("btn-active");
      updateAllDrawingLayersInteractivity();
    });
  });

  drawStrokeWidthSelect.addEventListener("change", () => {
    currentDrawWidth = parseInt(drawStrokeWidthSelect.value, 10);
  });

  btnDrawEraser.addEventListener("click", () => {
    isEraserActive = !isEraserActive;
    btnDrawEraser.classList.toggle("btn-active", isEraserActive);
    if (isEraserActive) {
      document.querySelectorAll(".drawing-toolbar .color-dot").forEach(d => d.classList.remove("active"));
    } else {
      const activeDot = document.querySelector(`.drawing-toolbar .color-dot[data-color="${currentDrawColor}"]`);
      if (activeDot) activeDot.classList.add("active");
    }
    updateAllDrawingLayersInteractivity();
  });

  function updateDrawingLayerInteractivity(svgElement) {
    if (currentTool === "draw") {
      svgElement.classList.add("active");
      svgElement.classList.toggle("eraser-active", isEraserActive);
    } else {
      svgElement.classList.remove("active", "eraser-active");
    }
  }

  function updateAllDrawingLayersInteractivity() {
    if (viewMode === "single") {
      updateDrawingLayerInteractivity(singleDrawingLayer);
    } else {
      document.querySelectorAll(".drawing-layer").forEach(svg => updateDrawingLayerInteractivity(svg));
    }
  }

  // --- Alternância de Modos (Leitura, Marca-texto, Desenho) ---
  function setToolMode(mode) {
    currentTool = mode;
    btnToolRead.classList.toggle("active", mode === "read");
    btnToolHighlight.classList.toggle("active", mode === "highlight");
    btnToolDraw.classList.toggle("active", mode === "draw");

    drawingToolbar.classList.toggle("active", mode === "draw");
    hideSelectionToolbar();

    if (mode !== "draw") {
      isEraserActive = false;
      isErasing = false;
      isDrawing = false;
      btnDrawEraser.classList.remove("btn-active");
      const activeDot = document.querySelector(`.drawing-toolbar .color-dot[data-color="${currentDrawColor}"]`);
      if (activeDot) activeDot.classList.add("active");
    }

    updateAllDrawingLayersInteractivity();
  }

  btnToolRead.addEventListener("click", () => setToolMode("read"));
  btnToolHighlight.addEventListener("click", () => setToolMode("highlight"));
  btnToolDraw.addEventListener("click", () => setToolMode("draw"));

  // --- Alternância entre Modos de Visualização (Página / Rolagem) ---
  function setViewMode(mode) {
    viewMode = mode;
    localStorage.setItem("lectoria_reader_mode", mode);

    if (mode === "continuous") {
      btnModeContinuous.classList.add("active");
      btnModeSingle.classList.remove("active");
      singlePageWrapper.style.display = "none";
      continuousWrapper.style.display = "flex";
      setupContinuousView();
    } else {
      btnModeSingle.classList.add("active");
      btnModeContinuous.classList.remove("active");
      if (continuousObserver) continuousObserver.disconnect();
      continuousWrapper.style.display = "none";
      singlePageWrapper.style.display = "inline-block";
      renderSinglePage(currentPage);
    }
  }

  btnModeSingle.addEventListener("click", () => setViewMode("single"));
  btnModeContinuous.addEventListener("click", () => setViewMode("continuous"));

  // --- Navegação entre Páginas ---
  function goToPage(num) {
    let target = parseInt(num, 10);
    if (isNaN(target)) target = 1;
    if (target < 1) target = 1;
    if (target > totalPages) target = totalPages;

    currentPage = target;

    if (viewMode === "single") {
      renderSinglePage(currentPage);
    } else {
      const slot = document.getElementById(`page-slot-${currentPage}`);
      if (slot) slot.scrollIntoView({ behavior: "smooth" });
      pageNumInput.value = currentPage;
      queueSaveProgress();
    }
  }

  btnPrevPage.addEventListener("click", () => { if (currentPage > 1) goToPage(currentPage - 1); });
  btnNextPage.addEventListener("click", () => { if (currentPage < totalPages) goToPage(currentPage + 1); });
  pageNumInput.addEventListener("change", () => goToPage(pageNumInput.value));
  pageNumInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      goToPage(pageNumInput.value);
      pageNumInput.blur();
    }
  });

  // --- Controles de Zoom ---
  btnZoomIn.addEventListener("click", () => {
    zoomMode = "custom";
    currentScale = Math.min(currentScale * 1.25, 4.0);
    if (viewMode === "single") renderSinglePage(currentPage);
    else setupContinuousView();
  });

  btnZoomOut.addEventListener("click", () => {
    zoomMode = "custom";
    currentScale = Math.max(currentScale / 1.25, 0.4);
    if (viewMode === "single") renderSinglePage(currentPage);
    else setupContinuousView();
  });

  btnFitWidth.addEventListener("click", () => {
    zoomMode = "width";
    if (viewMode === "single") renderSinglePage(currentPage);
    else setupContinuousView();
  });

  btnFitPage.addEventListener("click", () => {
    zoomMode = "page";
    if (viewMode === "single") renderSinglePage(currentPage);
    else setupContinuousView();
  });

  // --- Atalhos de Teclado ---
  window.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;

    if (e.key === "ArrowLeft" || e.key === "PageUp") {
      if (currentPage > 1) goToPage(currentPage - 1);
    } else if (e.key === "ArrowRight" || e.key === "PageDown" || e.key === " ") {
      if (currentPage < totalPages) goToPage(currentPage + 1);
    } else if (e.key === "+" || e.key === "=") {
      btnZoomIn.click();
    } else if (e.key === "-") {
      btnZoomOut.click();
    } else if (e.key.toLowerCase() === "w") {
      btnFitWidth.click();
    } else if (e.key.toLowerCase() === "p") {
      btnFitPage.click();
    } else if (e.key.toLowerCase() === "d") {
      setToolMode(currentTool === "draw" ? "read" : "draw");
    } else if (e.key.toLowerCase() === "h") {
      setToolMode(currentTool === "highlight" ? "read" : "highlight");
    } else if (e.key === "Escape") {
      hideSelectionToolbar();
      closeNoteEditorModal();
      annotationsDrawer.classList.remove("active");
    }
  });

  // Redimensionamento de Janela
  let resizeTimeout = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(() => {
      if (viewMode === "single") renderSinglePage(currentPage);
      else setupContinuousView();
    }, 150);
  });

  function escapeHtml(str) {
    if (!str) return "";
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  // --- Carregamento Inicial do Documento ---
  try {
    const bookRes = await fetch(`/api/books/${bookId}`);
    if (!bookRes.ok) throw new Error("Livro não encontrado.");
    const bookData = await bookRes.json();
    bookTitleEl.textContent = bookData.title;
    document.title = `${bookData.title} — Lectoria`;

    const progRes = await fetch(`/api/books/${bookId}/progress`);
    let initialPage = 1;
    if (progRes.ok) {
      const progData = await progRes.json();
      if (progData.current_page && progData.current_page >= 1) {
        initialPage = progData.current_page;
      }
    }

    const loadingTask = pdfjsLib.getDocument({
      url: `/api/books/${bookId}/file`,
      rangeChunkSize: 65536,
      disableAutoFetch: true,
      disableStream: false,
    });

    pdfDoc = await loadingTask.promise;
    totalPages = pdfDoc.numPages;
    pageCountDisplay.textContent = totalPages;
    pageNumInput.max = totalPages;

    const firstPage = await pdfDoc.getPage(1);
    const firstVp = firstPage.getViewport({ scale: 1.0 });
    basePageWidth = firstVp.width;
    basePageHeight = firstVp.height;

    await reloadBookAnnotations();

    currentPage = Math.min(initialPage, totalPages);
    setViewMode(viewMode);
  } catch (err) {
    console.error("Falha ao abrir documento:", err);
    viewportEl.innerHTML = `
      <div class="empty-state" style="padding-top: 5rem;">
        <h3>Erro ao abrir o documento</h3>
        <p>Não foi possível carregar o arquivo PDF.</p>
        <p style="margin-top: 1rem;"><a href="/" class="btn btn-primary">Voltar à Biblioteca</a></p>
      </div>
    `;
  }
});
