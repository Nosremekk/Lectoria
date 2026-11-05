/**
 * Lectoria — Leitor de PDF com PDF.js (Iteração 3)
 * Marca-texto com coordenadas normalizadas, Anotações textuais vinculadas,
 * Desenho livre vetorial (SVG) e Painel lateral de estudos.
 */

// Utilitário de segurança CSRF com Double Submit Cookie
function getCsrfToken() {
  const match = document.cookie.match(/(^|;\s*)lectoria_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[2]) : "";
}

// Interceptor global do fetch para injetar automaticamente o token CSRF em requisições mutantes
const _nativeFetch = window.fetch;
window.fetch = function(url, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const token = getCsrfToken();
    if (token) {
      if (!options.headers) {
        options.headers = {};
      }
      if (options.headers instanceof Headers) {
        if (!options.headers.has("X-CSRF-Token")) {
          options.headers.set("X-CSRF-Token", token);
        }
      } else if (Array.isArray(options.headers)) {
        options.headers.push(["X-CSRF-Token", token]);
      } else {
        options.headers["X-CSRF-Token"] = token;
      }
    }
  }
  return _nativeFetch.call(this, url, options);
};

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
  const btnBackLastPage = document.getElementById("btn-back-last-page");
  const btnZoomIn = document.getElementById("btn-zoom-in");
  const btnZoomOut = document.getElementById("btn-zoom-out");
  const btnFitWidth = document.getElementById("btn-fit-width");
  const btnFitPage = document.getElementById("btn-fit-page");
  const btnModeSingle = document.getElementById("btn-mode-single");
  const btnModeContinuous = document.getElementById("btn-mode-continuous");
  const btnReaderTheme = document.getElementById("btn-reader-theme");
  const btnDownloadPdf = document.getElementById("btn-download-pdf");
  const btnShortcutsHelp = document.getElementById("btn-shortcuts-help");
  const saveIndicator = document.getElementById("save-indicator");
  const readingProgressFill = document.getElementById("reading-progress-fill");

  // Ferramentas de Modo
  const btnToolRead = document.getElementById("btn-tool-read");
  const btnToolHighlight = document.getElementById("btn-tool-highlight");
  const btnToolDraw = document.getElementById("btn-tool-draw");
  const btnToggleDrawer = document.getElementById("btn-toggle-drawer");
  const btnToggleThumbnails = document.getElementById("btn-toggle-thumbnails");

  // Barras Flutuantes e Modais
  const selectionToolbar = document.getElementById("selection-toolbar");
  const btnAddNoteFromSelection = document.getElementById("btn-add-note-from-selection");
  const drawingToolbar = document.getElementById("drawing-toolbar");
  const drawStrokeWidthSelect = document.getElementById("draw-stroke-width");
  const btnDrawEraser = document.getElementById("btn-draw-eraser");
  const btnDrawClear = document.getElementById("btn-draw-clear");

  // Painel de Anotações (Estudos)
  const annotationsDrawer = document.getElementById("annotations-drawer");
  const btnCloseDrawer = document.getElementById("btn-close-drawer");
  const drawerTabs = document.querySelectorAll(".drawer-tab");
  const drawerItemsList = document.getElementById("drawer-items-list");
  const countAllEl = document.getElementById("count-all-annotations");
  const countHighlightsEl = document.getElementById("count-highlights");
  const countNotesEl = document.getElementById("count-notes");
  const countDrawingsEl = document.getElementById("count-drawings");
  const btnNewPageNote = document.getElementById("btn-new-page-note");

  const drawerSearchInput = document.getElementById("drawer-search-input");
  const btnClearDrawerSearch = document.getElementById("btn-clear-drawer-search");
  const btnToggleExportMenu = document.getElementById("btn-toggle-export-menu");
  const exportDropdownMenu = document.getElementById("export-dropdown-menu");
  const btnExportMarkdown = document.getElementById("btn-export-markdown");
  const btnExportJson = document.getElementById("btn-export-json");
  const btnTriggerImport = document.getElementById("btn-trigger-import");
  const importFileInput = document.getElementById("import-file-input");

  // Painel de Miniaturas
  const thumbnailsDrawer = document.getElementById("thumbnails-drawer");
  const btnCloseThumbnails = document.getElementById("btn-close-thumbnails");
  const thumbnailsList = document.getElementById("thumbnails-list");

  // Modais
  const modalNoteEditor = document.getElementById("modal-note-editor");
  const modalNoteTitle = document.getElementById("modal-note-title");
  const modalNoteQuoteBox = document.getElementById("modal-note-quote-box");
  const modalNoteQuoteText = document.getElementById("modal-note-quote-text");
  const noteContentInput = document.getElementById("note-content-input");
  const btnCloseModalNote = document.getElementById("btn-close-modal-note");
  const btnCancelNote = document.getElementById("btn-cancel-note");
  const btnSaveNote = document.getElementById("btn-save-note");

  const modalImportPreview = document.getElementById("modal-import-preview");
  const btnCloseModalImport = document.getElementById("btn-close-modal-import");
  const btnCancelImport = document.getElementById("btn-cancel-import");
  const btnConfirmImport = document.getElementById("btn-confirm-import");
  const importPreviewSummary = document.getElementById("import-preview-summary");
  const importPreviewList = document.getElementById("import-preview-list");

  const modalShortcutsHelp = document.getElementById("modal-shortcuts-help");
  const btnCloseModalShortcuts = document.getElementById("btn-close-modal-shortcuts");
  const btnCloseShortcutsBtn = document.getElementById("btn-close-shortcuts-btn");

  // Containers de Visualização
  const viewportEl = document.getElementById("reader-viewport");
  const singlePageWrapper = document.getElementById("single-page-wrapper");
  const continuousWrapper = document.getElementById("continuous-wrapper");
  const canvas = document.getElementById("pdf-canvas");
  const ctx = canvas.getContext("2d");
  const singleTextLayer = document.getElementById("single-text-layer");
  const singleHighlightsLayer = document.getElementById("single-highlights-layer");
  const singleDrawingLayer = document.getElementById("single-drawing-layer");

  // Elementos do Leitor EPUB (Fase 4)
  const epubWrapper = document.getElementById("epub-wrapper");
  const epubViewer = document.getElementById("epub-viewer");
  const epubHeaderTools = document.getElementById("epub-header-tools");
  const btnEpubToc = document.getElementById("btn-epub-toc");
  const btnEpubFontSmaller = document.getElementById("btn-epub-font-smaller");
  const btnEpubFontLarger = document.getElementById("btn-epub-font-larger");
  const epubFontSizeLabel = document.getElementById("epub-font-size-label");
  const btnEpubFontFamily = document.getElementById("btn-epub-font-family");
  const btnEpubThemeSepia = document.getElementById("btn-epub-theme-sepia");
  const tocDrawer = document.getElementById("toc-drawer");
  const btnCloseToc = document.getElementById("btn-close-toc");
  const tocList = document.getElementById("toc-list");
  const mobileSheetEpubSection = document.getElementById("mobile-sheet-epub-section");
  const btnMobileSheetToc = document.getElementById("btn-mobile-sheet-toc");
  const btnMobileEpubSmaller = document.getElementById("btn-mobile-epub-smaller");
  const btnMobileEpubLarger = document.getElementById("btn-mobile-epub-larger");
  const btnMobileEpubFont = document.getElementById("btn-mobile-epub-font");
  const btnMobileEpubSepia = document.getElementById("btn-mobile-epub-sepia");

  // Barra Acoplável para Tablets (Fase 2)
  const tabletDockToolbar = document.getElementById("tablet-dock-toolbar");
  const btnTabletDockSide = document.getElementById("btn-tablet-dock-side");
  const tabletModeBadge = document.getElementById("tablet-mode-badge");
  const btnTabletPrev = document.getElementById("btn-tablet-prev");
  const btnTabletNext = document.getElementById("btn-tablet-next");
  const tabletPagePill = document.getElementById("tablet-page-pill");
  const tabletPdfTools = document.getElementById("tablet-pdf-tools");
  const tabletEpubTools = document.getElementById("tablet-epub-tools");
  const btnTabletToolRead = document.getElementById("btn-tablet-tool-read");
  const btnTabletToolHighlight = document.getElementById("btn-tablet-tool-highlight");
  const btnTabletToolDraw = document.getElementById("btn-tablet-tool-draw");
  const btnTabletToolEraser = document.getElementById("btn-tablet-tool-eraser");
  const btnTabletFontSmaller = document.getElementById("btn-tablet-font-smaller");
  const btnTabletFontLarger = document.getElementById("btn-tablet-font-larger");
  const btnTabletFontFamily = document.getElementById("btn-tablet-font-family");
  const btnTabletToc = document.getElementById("btn-tablet-toc");
  const btnTabletDrawer = document.getElementById("btn-tablet-drawer");
  const btnTabletTheme = document.getElementById("btn-tablet-theme");

  // --- Estado do Leitor ---
  let bookFormat = "pdf"; // 'pdf' ou 'epub'
  let pdfDoc = null;
  let epubBook = null;
  let epubRendition = null;
  let epubCfi = null;
  let epubFontSize = 100;
  let epubFontFamily = "serif";
  let currentPage = 1;
  let totalPages = 1;
  let currentScale = 1.0;
  let zoomMode = "width";
  let viewMode = localStorage.getItem("lectoria_reader_mode") || "single";
  let currentTheme = localStorage.getItem("lectoria_theme") || "dark";
  let currentTool = "read"; // 'read', 'highlight', 'draw', 'eraser'
  let isProgrammaticScroll = false;
  let isRestoringProgress = true;

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

  // Estado da Iteração 4 (Navegação, Estudos, Miniaturas)
  let lastVisitedPage = null;
  let drawerSearchTerm = "";
  let pendingImportData = null;
  let thumbnailsInitialized = false;
  const renderedThumbnails = new Set();
  let thumbnailObserver = null;

  function updateReadingProgressBar() {
    if (!readingProgressFill) return;
    const pct = totalPages > 0 ? (currentPage / totalPages) * 100 : 0;
    readingProgressFill.style.width = `${pct}%`;
  }

  function updateLastVisitedPageButton() {
    if (!btnBackLastPage) return;
    if (lastVisitedPage && lastVisitedPage !== currentPage) {
      btnBackLastPage.style.display = "inline-flex";
      btnBackLastPage.title = `Voltar à página ${lastVisitedPage} (b)`;
    } else {
      btnBackLastPage.style.display = "none";
    }
  }

  if (btnBackLastPage) {
    btnBackLastPage.addEventListener("click", () => {
      if (lastVisitedPage) goToPage(lastVisitedPage);
    });
  }

  // --- Tema (Claro, Escuro e Sépia) ---
  function applyEpubTheme(theme) {
    if (!epubRendition) return;
    try {
      if (theme === "dark") {
        epubRendition.themes.override("color", "#ece8de");
        epubRendition.themes.override("background", "#161513");
      } else if (theme === "sepia") {
        epubRendition.themes.override("color", "#3d2f1f");
        epubRendition.themes.override("background", "#f4ecd8");
      } else {
        epubRendition.themes.override("color", "#1c1b18");
        epubRendition.themes.override("background", "#fcfbf7");
      }
    } catch (_) {}
  }

  function applyTheme(theme) {
    currentTheme = theme;
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("lectoria_theme", theme);
    applyEpubTheme(theme);
  }

  function cycleTheme() {
    const nextTheme = currentTheme === "dark" ? "light" : currentTheme === "light" ? "sepia" : "dark";
    applyTheme(nextTheme);
  }

  btnReaderTheme.addEventListener("click", cycleTheme);
  applyTheme(currentTheme);

  if (btnDownloadPdf) {
    btnDownloadPdf.addEventListener("click", () => {
      window.location.href = `/api/books/${encodeURIComponent(bookId)}/download`;
    });
  }

  function showSavedIndicator() {
    saveIndicator.classList.add("visible");
    const mobileSaveIndicator = document.getElementById("mobile-save-indicator");
    if (mobileSaveIndicator) mobileSaveIndicator.classList.add("visible");
    setTimeout(() => {
      saveIndicator.classList.remove("visible");
      if (mobileSaveIndicator) mobileSaveIndicator.classList.remove("visible");
    }, 2000);
  }

  // --- Progresso ---
  async function persistProgress(pageToSave, locator = null) {
    if (isRestoringProgress) return;
    try {
      const payload = {
        current_page: pageToSave,
        total_pages: totalPages,
      };
      const loc = locator || epubCfi;
      if (loc) {
        payload.progress_locator = loc;
      }
      await fetch(`/api/books/${bookId}/progress`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      showSavedIndicator();
    } catch (e) {
      console.warn("Erro ao salvar progresso:", e);
    }
  }

  function queueSaveProgress(locator = null, delay = 600) {
    if (isRestoringProgress) return;
    clearTimeout(saveProgressTimeout);
    saveProgressTimeout = setTimeout(() => persistProgress(currentPage, locator), delay);
  }

  function emergencySave() {
    if (isRestoringProgress) return;
    const payload = JSON.stringify({
      current_page: currentPage,
      total_pages: totalPages,
      progress_locator: epubCfi,
    });
    const csrfToken = getCsrfToken();
    const progressUrl = csrfToken
      ? `/api/books/${bookId}/progress?csrf_token=${encodeURIComponent(csrfToken)}`
      : `/api/books/${bookId}/progress`;
    if (navigator.sendBeacon) {
      navigator.sendBeacon(progressUrl, new Blob([payload], { type: "application/json" }));
    } else {
      fetch(progressUrl, { method: "POST", headers: { "Content-Type": "application/json" }, body: payload, keepalive: true });
    }
  }
  window.addEventListener("beforeunload", emergencySave);
  window.addEventListener("pagehide", emergencySave);

  // --- Escala (Zoom) Otimizada para Telas Móveis e Desktop ---
  function calculateScaleForPage(unscaledViewport) {
    const isMobile = window.innerWidth < 768;
    const isTablet = window.innerWidth >= 768 && window.innerWidth <= 1280;
    // Em tablets, reserva 72px para a barra flutuante lateral evitando qualquer sobreposição com o documento
    const dockMargin = isTablet ? 72 : 0;
    const hPadding = isMobile ? 8 : (48 + dockMargin);
    const vPadding = isMobile ? 12 : 48;
    const availableWidth = Math.max(viewportEl.clientWidth - hPadding, 100);
    const availableHeight = Math.max(viewportEl.clientHeight - vPadding, 100);
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
  async function renderSinglePage(num, options = {}) {
    if (!pdfDoc) return;
    const shouldResetScroll = options.resetScroll !== false;
    if (singleRenderTask) {
      singleRenderTask.cancel();
      singleRenderTask = null;
    }

    // Garante que o topo da nova página esteja perfeitamente alinhado (evita que o PDF desça sozinho)
    if (shouldResetScroll) {
      viewportEl.scrollTop = 0;
      viewportEl.scrollLeft = 0;
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

      // Mantém topo em 0 caso o redimensionamento do canvas tenha induzido deslocamento
      if (shouldResetScroll && viewportEl.scrollTop !== 0) {
        viewportEl.scrollTop = 0;
      }

      // Renderiza Camada de Texto para permitir seleção
      singleTextLayer.innerHTML = "";
      singleTextLayer.style.width = cssWidth;
      singleTextLayer.style.height = cssHeight;
      singleTextLayer.style.setProperty("--scale-factor", currentScale);
      const textContent = await page.getTextContent();
      pdfjsLib.renderTextLayer({
        textContentSource: textContent,
        container: singleTextLayer,
        viewport: page.getViewport({ scale: currentScale }),
        textDivs: [],
      });

      // Reforça alinhamento superior exato
      if (shouldResetScroll && viewportEl.scrollTop !== 0) {
        viewportEl.scrollTop = 0;
      }

      // Renderiza Marcações e Desenhos
      renderHighlightsForPage(num, singleHighlightsLayer);
      await setupDrawingLayerForPage(num, singleDrawingLayer);

      pageNumInput.value = num;
      btnPrevPage.disabled = num <= 1;
      btnNextPage.disabled = num >= totalPages;
      updateReadingProgressBar();
      updateMobilePageControls();
      if (thumbnailsInitialized) highlightActiveThumbnail();
      queueSaveProgress();
    } catch (err) {
      if (err.name !== "RenderingCancelledException") {
        console.error("Erro ao renderizar página:", err);
      }
    }
  }

  // --- Renderização no Modo Rolagem Contínua (Lazy & Virtualizado) ---
  function setupContinuousView(targetPage = null, preservedOffsetRatio = null) {
    if (continuousObserver) {
      continuousObserver.disconnect();
      continuousObserver = null;
    }
    renderedSlots.forEach(item => {
      if (item && item.renderTask) {
        try { item.renderTask.cancel(); } catch (_) {}
      }
    });
    renderedSlots.clear();
    continuousWrapper.innerHTML = "";

    const isMobile = window.innerWidth < 768;
    const isTablet = window.innerWidth >= 768 && window.innerWidth <= 1280;
    const dockMargin = isTablet ? 72 : 0;
    const hPadding = isMobile ? 8 : (48 + dockMargin);
    const availableWidth = Math.max(viewportEl.clientWidth - hPadding, 100);
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

    const pageToScroll = targetPage || currentPage;
    currentPage = pageToScroll;
    pageNumInput.value = pageToScroll;
    btnPrevPage.disabled = pageToScroll <= 1;
    btnNextPage.disabled = pageToScroll >= totalPages;
    updateReadingProgressBar();
    updateMobilePageControls();
    updateTabletControls();

    const initialSlot = document.getElementById(`page-slot-${pageToScroll}`);
    if (initialSlot) {
      isProgrammaticScroll = true;
      const extraOffset = (preservedOffsetRatio && preservedOffsetRatio > 0)
        ? Math.round(initialSlot.clientHeight * preservedOffsetRatio)
        : 0;
      viewportEl.scrollTop = initialSlot.offsetTop + extraOffset;
      setTimeout(() => {
        isProgrammaticScroll = false;
        isRestoringProgress = false;
      }, 350);
    } else {
      setTimeout(() => {
        isProgrammaticScroll = false;
        isRestoringProgress = false;
      }, 350);
    }
  }

  async function renderSlotPage(slotElement, pageNum) {
    if (renderedSlots.has(pageNum)) return;

    try {
      const page = await pdfDoc.getPage(pageNum);
      const unscaledViewport = page.getViewport({ scale: 1.0 });

      let scale = currentScale;
      if (zoomMode === "width") {
        const isMobile = window.innerWidth <= 768;
        const hPadding = isMobile ? 8 : 48;
        scale = Math.max(viewportEl.clientWidth - hPadding, 100) / unscaledViewport.width;
      }

      const pixelRatio = window.devicePixelRatio || 1;
      const viewport = page.getViewport({ scale: scale * pixelRatio });
      const cssWidth = `${viewport.width / pixelRatio}px`;
      const cssHeight = `${viewport.height / pixelRatio}px`;

      const prevHeight = parseFloat(slotElement.style.height) || 0;
      slotElement.style.width = cssWidth;
      slotElement.style.height = cssHeight;
      const newHeight = parseFloat(cssHeight);
      if (prevHeight > 0 && Math.abs(newHeight - prevHeight) > 4) {
        if (slotElement.offsetTop < viewportEl.scrollTop) {
          isProgrammaticScroll = true;
          viewportEl.scrollTop += (newHeight - prevHeight);
          setTimeout(() => { isProgrammaticScroll = false; }, 80);
        }
      }
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
      tLayer.style.setProperty("--scale-factor", scale);
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
    if (viewMode !== "continuous" || bookFormat === "epub") return;
    if (isRestoringProgress || isProgrammaticScroll) return;

    clearTimeout(scrollTimeout);
    scrollTimeout = setTimeout(() => {
      if (isRestoringProgress || isProgrammaticScroll) return;

      const targetY = viewportEl.scrollTop + viewportEl.clientHeight / 2;
      let closestPage = currentPage;
      let minDistance = Infinity;

      const slots = continuousWrapper.children;
      for (let i = 0; i < slots.length; i++) {
        const slot = slots[i];
        const slotCenter = slot.offsetTop + slot.clientHeight / 2;
        const dist = Math.abs(slotCenter - targetY);
        if (dist < minDistance) {
          minDistance = dist;
          const p = parseInt(slot.getAttribute("data-page-num"), 10);
          if (p) closestPage = p;
        } else if (dist > minDistance && slotCenter > targetY) {
          // Já ultrapassou o centro de visão e a distância começou a crescer
          break;
        }
      }

      if (closestPage && closestPage !== currentPage) {
        currentPage = closestPage;
        pageNumInput.value = currentPage;
        btnPrevPage.disabled = currentPage <= 1;
        btnNextPage.disabled = currentPage >= totalPages;
        updateReadingProgressBar();
        updateMobilePageControls();
        updateTabletControls();
        queueSaveProgress();
      }
    }, 120);
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

  // --- Formatação de Datas ---
  function formatTimestamp(isoStr) {
    if (!isoStr) return "";
    try {
      const d = new Date(isoStr);
      if (isNaN(d.getTime())) return isoStr;
      const day = String(d.getDate()).padStart(2, "0");
      const month = String(d.getMonth() + 1).padStart(2, "0");
      const year = d.getFullYear();
      const hours = String(d.getHours()).padStart(2, "0");
      const mins = String(d.getMinutes()).padStart(2, "0");
      return `${day}/${month}/${year} ${hours}:${mins}`;
    } catch {
      return isoStr;
    }
  }

  // --- Painel Lateral de Anotações (Drawer) ---
  let activeDrawerFilter = "all";

  btnToggleDrawer.addEventListener("click", () => {
    annotationsDrawer.classList.toggle("active");
    if (annotationsDrawer.classList.contains("active")) {
      closeThumbnailsDrawer();
      reloadBookAnnotations();
    }
  });
  btnCloseDrawer.addEventListener("click", () => annotationsDrawer.classList.remove("active"));

  if (drawerSearchInput) {
    drawerSearchInput.addEventListener("input", (e) => {
      drawerSearchTerm = e.target.value.toLowerCase().trim();
      if (btnClearDrawerSearch) {
        btnClearDrawerSearch.style.display = drawerSearchTerm ? "inline-block" : "none";
      }
      renderDrawerList();
    });
  }

  if (btnClearDrawerSearch) {
    btnClearDrawerSearch.addEventListener("click", () => {
      drawerSearchInput.value = "";
      drawerSearchTerm = "";
      btnClearDrawerSearch.style.display = "none";
      renderDrawerList();
    });
  }

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
    const dCount = (bookAnnotations.drawing_pages || []).length;
    countHighlightsEl.textContent = hCount;
    countNotesEl.textContent = nCount;
    if (countDrawingsEl) countDrawingsEl.textContent = dCount;
    countAllEl.textContent = hCount + nCount + dCount;
  }

  function renderDrawerList() {
    const highlights = bookAnnotations.highlights || [];
    const notes = bookAnnotations.notes || [];
    const drawingPages = bookAnnotations.drawing_pages || [];

    let items = [];

    if (activeDrawerFilter === "all" || activeDrawerFilter === "highlights") {
      highlights.forEach(h => {
        if (!drawerSearchTerm || h.selected_text.toLowerCase().includes(drawerSearchTerm)) {
          items.push({ type: "highlight", data: h, page: h.page, date: h.created_at });
        }
      });
    }

    if (activeDrawerFilter === "all" || activeDrawerFilter === "notes") {
      notes.forEach(n => {
        const textMatch = n.content.toLowerCase().includes(drawerSearchTerm) ||
          (n.highlight_text && n.highlight_text.toLowerCase().includes(drawerSearchTerm));
        if (!drawerSearchTerm || textMatch) {
          items.push({ type: "note", data: n, page: n.page, date: n.updated_at || n.created_at });
        }
      });
    }

    if (activeDrawerFilter === "all" || activeDrawerFilter === "drawings") {
      drawingPages.forEach(d => {
        const searchMatch = !drawerSearchTerm ||
          `desenho pág página ${d.page}`.toLowerCase().includes(drawerSearchTerm);
        if (searchMatch) {
          items.push({ type: "drawing", data: d, page: d.page, date: d.updated_at });
        }
      });
    }

    if (items.length === 0) {
      drawerItemsList.innerHTML = `
        <div style="text-align: center; color: var(--text-faint); padding: 3rem 1rem;">
          <p>${drawerSearchTerm ? "Nenhum resultado encontrado para a busca." : "Nenhuma anotação nesta categoria."}</p>
          <p style="font-size: 0.8rem; margin-top: 0.4rem;">
            ${drawerSearchTerm ? "Tente buscar por outras palavras-chave." : "Selecione trechos no livro para destacar, adicionar notas ou desenhe livremente."}
          </p>
        </div>
      `;
      return;
    }

    // Agrupa por página
    const pageMap = new Map();
    items.forEach(item => {
      if (!pageMap.has(item.page)) {
        pageMap.set(item.page, []);
      }
      pageMap.get(item.page).push(item);
    });

    const sortedPages = Array.from(pageMap.keys()).sort((a, b) => a - b);

    let html = "";
    sortedPages.forEach(pageNum => {
      const pageItems = pageMap.get(pageNum);
      pageItems.sort((a, b) => new Date(a.date) - new Date(b.date));

      html += `
        <div class="drawer-page-group">
          <div class="drawer-page-header">
            <span>Página ${pageNum}</span>
            <span style="font-size: 0.72rem; color: var(--text-faint);">${pageItems.length} item(s)</span>
          </div>
      `;

      pageItems.forEach(item => {
        if (item.type === "highlight") {
          const h = item.data;
          const dateFormatted = formatTimestamp(h.created_at);
          html += `
            <div class="annotation-item" data-goto-page="${h.page}">
              <div class="annotation-item-header">
                <span class="annotation-badge badge-highlight">Destaque</span>
                <div style="display: flex; align-items: center; gap: 0.4rem;">
                  <span class="annotation-date">${dateFormatted}</span>
                  <span style="display: inline-block; width: 12px; height: 12px; border-radius: 50%; background-color: ${h.color}; border: 1px solid rgba(0,0,0,0.2);"></span>
                </div>
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
        } else if (item.type === "note") {
          const n = item.data;
          const dateFormatted = formatTimestamp(n.updated_at || n.created_at);
          html += `
            <div class="annotation-item" data-goto-page="${n.page}">
              <div class="annotation-item-header">
                <span class="annotation-badge badge-note">Nota</span>
                <span class="annotation-date">${dateFormatted}</span>
              </div>
              ${n.highlight_text ? `<div class="annotation-quote">"${escapeHtml(n.highlight_text)}"</div>` : ""}
              <div class="annotation-note-text">${escapeHtml(n.content)}</div>
              <div class="annotation-item-actions">
                <button class="btn-item-action btn-edit-note" data-note-id="${n.id}" data-page="${n.page}" data-content="${escapeHtml(n.content)}">Editar</button>
                <button class="btn-item-action danger btn-del-note" data-note-id="${n.id}">Excluir</button>
              </div>
            </div>
          `;
        } else if (item.type === "drawing") {
          const d = item.data;
          const dateFormatted = formatTimestamp(d.updated_at);
          html += `
            <div class="annotation-item" data-goto-page="${d.page}">
              <div class="annotation-item-header">
                <span class="annotation-badge badge-drawing">Desenho Livre</span>
                <span class="annotation-date">${dateFormatted}</span>
              </div>
              <div style="font-size: 0.8rem; color: var(--text-muted); font-style: italic;">
                Contém traços vetoriais e ilustrações desenhadas à mão.
              </div>
              <div class="annotation-item-actions">
                <button class="btn-item-action" data-goto-page="${d.page}">Ir para a Página</button>
              </div>
            </div>
          `;
        }
      });

      html += `</div>`;
    });

    drawerItemsList.innerHTML = html;

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

  // --- Exportação e Importação de Estudos ---
  if (btnToggleExportMenu) {
    btnToggleExportMenu.addEventListener("click", (e) => {
      e.stopPropagation();
      const isVisible = exportDropdownMenu.style.display !== "none";
      exportDropdownMenu.style.display = isVisible ? "none" : "flex";
    });
    document.addEventListener("click", () => {
      if (exportDropdownMenu) exportDropdownMenu.style.display = "none";
    });
  }

  if (btnExportMarkdown) {
    btnExportMarkdown.addEventListener("click", (e) => {
      e.preventDefault();
      window.location.href = `/api/books/${bookId}/export/markdown`;
    });
  }

  if (btnExportJson) {
    btnExportJson.addEventListener("click", (e) => {
      e.preventDefault();
      window.location.href = `/api/books/${bookId}/export/json`;
    });
  }

  if (btnTriggerImport && importFileInput) {
    btnTriggerImport.addEventListener("click", () => {
      importFileInput.value = "";
      importFileInput.click();
    });

    importFileInput.addEventListener("change", async () => {
      const file = importFileInput.files[0];
      if (!file) return;

      try {
        const text = await file.text();
        const data = JSON.parse(text);

        const res = await fetch(`/api/books/${bookId}/import/preview`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        });

        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || "Arquivo JSON de estudo incompatível.");
        }

        const jsonRes = await res.json();
        pendingImportData = data;
        openImportPreviewModal(jsonRes.preview);
      } catch (err) {
        alert("Erro ao processar importação: " + err.message);
      }
    });
  }

  function openImportPreviewModal(preview) {
    if (!modalImportPreview) return;
    const counts = preview.counts || {};
    const srcBook = preview.source_book || {};
    importPreviewSummary.innerHTML = `
      <div class="import-stat-row"><strong>Documento de origem:</strong> <span>${escapeHtml(srcBook.title || "Não especificado")}</span></div>
      <div class="import-stat-row"><strong>Destaques novos a importar:</strong> <span style="color: var(--accent); font-weight:600;">${counts.highlights_new || 0} (${counts.highlights_duplicate || 0} duplicatas ignoradas)</span></div>
      <div class="import-stat-row"><strong>Notas novas a importar:</strong> <span style="color: var(--accent); font-weight:600;">${counts.notes_new || 0} (${counts.notes_duplicate || 0} duplicatas ignoradas)</span></div>
      <div class="import-stat-row"><strong>Desenhos a importar/mesclar:</strong> <span style="color: var(--accent); font-weight:600;">${counts.drawings_new || 0} (${counts.drawings_duplicate || 0} duplicatas ignoradas)</span></div>
      <div class="import-stat-row" style="margin-top:0.4rem; padding-top:0.4rem; border-top:1px dashed var(--border-color);">
        <strong>Total de novos itens:</strong> <span style="font-size:0.95rem; font-weight:700; color:var(--success);">${counts.total_new_items || 0}</span>
      </div>
    `;

    let samplesHtml = "";
    if (preview.preview_highlights && preview.preview_highlights.length > 0) {
      samplesHtml += `<div><strong>Amostra de destaques a importar:</strong></div>`;
      preview.preview_highlights.forEach(h => {
        samplesHtml += `<div>• Pág. ${h.page}: "${escapeHtml(h.text)}..."</div>`;
      });
    }
    if (preview.preview_notes && preview.preview_notes.length > 0) {
      samplesHtml += `<div style="margin-top:0.4rem;"><strong>Amostra de notas a importar:</strong></div>`;
      preview.preview_notes.forEach(n => {
        samplesHtml += `<div>• Pág. ${n.page}: ${escapeHtml(n.content)}</div>`;
      });
    }
    if (!samplesHtml) {
      samplesHtml = `<em>Nenhum item novo para importar (todos os itens já existem neste documento).</em>`;
    }
    importPreviewList.innerHTML = samplesHtml;
    modalImportPreview.classList.add("active");
  }

  function closeImportPreviewModal() {
    if (modalImportPreview) modalImportPreview.classList.remove("active");
    pendingImportData = null;
  }
  if (btnCloseModalImport) btnCloseModalImport.addEventListener("click", closeImportPreviewModal);
  if (btnCancelImport) btnCancelImport.addEventListener("click", closeImportPreviewModal);

  if (btnConfirmImport) {
    btnConfirmImport.addEventListener("click", async () => {
      if (!pendingImportData) return;
      try {
        btnConfirmImport.disabled = true;
        btnConfirmImport.textContent = "Importando...";

        const res = await fetch(`/api/books/${bookId}/import/confirm`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(pendingImportData),
        });

        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || "Falha ao gravar importação.");
        }

        const data = await res.json();
        closeImportPreviewModal();
        alert(`Importação concluída com sucesso! (${data.result.imported_counts.total} itens processados)`);
        await reloadBookAnnotations();
        if (thumbnailsInitialized) {
          thumbnailsInitialized = false;
          initThumbnails();
        }
      } catch (e) {
        alert("Erro na importação: " + e.message);
      } finally {
        btnConfirmImport.disabled = false;
        btnConfirmImport.textContent = "Confirmar Importação";
      }
    });
  }

  // --- Painel Lateral de Miniaturas com Lazy Rendering e Gestão Eficiente de Recursos ---
  function openThumbnailsDrawer() {
    if (!thumbnailsDrawer) return;
    thumbnailsDrawer.classList.add("active");
    if (annotationsDrawer) annotationsDrawer.classList.remove("active");
    if (!thumbnailsInitialized) {
      initThumbnails();
    } else {
      attachThumbnailObserver();
      highlightActiveThumbnail();
    }
  }

  function closeThumbnailsDrawer() {
    if (!thumbnailsDrawer) return;
    thumbnailsDrawer.classList.remove("active");
    // Desconecta o observer para poupar ciclos de CPU/RAM enquanto a gaveta estiver oculta
    if (thumbnailObserver) {
      thumbnailObserver.disconnect();
    }
  }

  if (btnToggleThumbnails) {
    btnToggleThumbnails.addEventListener("click", () => {
      if (thumbnailsDrawer.classList.contains("active")) {
        closeThumbnailsDrawer();
      } else {
        openThumbnailsDrawer();
      }
    });
  }
  if (btnCloseThumbnails) {
    btnCloseThumbnails.addEventListener("click", closeThumbnailsDrawer);
  }

  function attachThumbnailObserver() {
    if (!thumbnailsList) return;
    if (!thumbnailObserver) {
      thumbnailObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
          if (entry.isIntersecting) {
            const card = entry.target;
            const pNum = parseInt(card.getAttribute("data-page-num"), 10);
            renderThumbnail(pNum, card);
            if (thumbnailObserver) thumbnailObserver.unobserve(card);
          }
        });
      }, { root: thumbnailsList, rootMargin: "200px" });
    }
    // Observa somente os cartões que ainda não foram renderizados
    thumbnailsList.querySelectorAll(".thumbnail-card").forEach(card => {
      const pNum = parseInt(card.getAttribute("data-page-num"), 10);
      if (!renderedThumbnails.has(pNum)) {
        thumbnailObserver.observe(card);
      }
    });
  }

  function initThumbnails() {
    if (!pdfDoc) return;
    if (thumbnailsInitialized) {
      attachThumbnailObserver();
      highlightActiveThumbnail();
      return;
    }
    thumbnailsInitialized = true;
    thumbnailsList.innerHTML = "";

    const annotatedPages = new Map();
    (bookAnnotations.highlights || []).forEach(h => {
      if (!annotatedPages.has(h.page)) annotatedPages.set(h.page, {});
      annotatedPages.get(h.page).h = true;
    });
    (bookAnnotations.notes || []).forEach(n => {
      if (!annotatedPages.has(n.page)) annotatedPages.set(n.page, {});
      annotatedPages.get(n.page).n = true;
    });
    (bookAnnotations.drawing_pages || []).forEach(d => {
      if (!annotatedPages.has(d.page)) annotatedPages.set(d.page, {});
      annotatedPages.get(d.page).d = true;
    });

    for (let i = 1; i <= totalPages; i++) {
      const card = document.createElement("div");
      card.className = `thumbnail-card ${i === currentPage ? "active" : ""}`;
      card.id = `thumbnail-card-${i}`;
      card.setAttribute("data-page-num", i);

      const wrapper = document.createElement("div");
      wrapper.className = "thumbnail-canvas-wrapper";
      const canvasEl = document.createElement("canvas");
      canvasEl.className = "thumbnail-canvas";
      wrapper.appendChild(canvasEl);
      card.appendChild(wrapper);

      const footer = document.createElement("div");
      footer.className = "thumbnail-footer";

      const numSpan = document.createElement("span");
      numSpan.className = "thumbnail-page-number";
      numSpan.textContent = `Pág. ${i}`;
      footer.appendChild(numSpan);

      const badges = document.createElement("div");
      badges.className = "thumbnail-badges";
      const pData = annotatedPages.get(i);
      if (pData) {
        if (pData.h) {
          const dot = document.createElement("span");
          dot.className = "thumbnail-badge-dot";
          dot.style.backgroundColor = "#eab308";
          dot.title = "Destaques";
          badges.appendChild(dot);
        }
        if (pData.n) {
          const dot = document.createElement("span");
          dot.className = "thumbnail-badge-dot";
          dot.style.backgroundColor = "#3b82f6";
          dot.title = "Notas";
          badges.appendChild(dot);
        }
        if (pData.d) {
          const dot = document.createElement("span");
          dot.className = "thumbnail-badge-dot";
          dot.style.backgroundColor = "#10b981";
          dot.title = "Desenhos";
          badges.appendChild(dot);
        }
      }
      footer.appendChild(badges);
      card.appendChild(footer);

      card.addEventListener("click", () => {
        goToPage(i);
        highlightActiveThumbnail();
      });

      thumbnailsList.appendChild(card);
    }
    attachThumbnailObserver();
    highlightActiveThumbnail();
  }

  async function renderThumbnail(pageNum, cardEl) {
    if (renderedThumbnails.has(pageNum)) return;
    renderedThumbnails.add(pageNum);

    try {
      const page = await pdfDoc.getPage(pageNum);
      const viewport = page.getViewport({ scale: 0.22 });
      const canvasEl = cardEl.querySelector(".thumbnail-canvas");
      if (!canvasEl) return;
      canvasEl.width = viewport.width;
      canvasEl.height = viewport.height;
      const tCtx = canvasEl.getContext("2d");
      await page.render({ canvasContext: tCtx, viewport }).promise;
    } catch (e) {
      console.warn(`Erro na miniatura da pág. ${pageNum}:`, e);
    }
  }

  function highlightActiveThumbnail() {
    document.querySelectorAll(".thumbnail-card").forEach(c => {
      const p = parseInt(c.getAttribute("data-page-num"), 10);
      if (p === currentPage) {
        c.classList.add("active");
        c.scrollIntoView({ block: "nearest", behavior: "smooth" });
      } else {
        c.classList.remove("active");
      }
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
    updateTabletControls();
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
    viewportEl.classList.toggle("tool-active-draw", mode === "draw");
    btnToolRead.classList.toggle("active", mode === "read");
    btnToolHighlight.classList.toggle("active", mode === "highlight");
    btnToolDraw.classList.toggle("active", mode === "draw");

    const btnMobileToolRead = document.getElementById("btn-mobile-tool-read");
    const btnMobileToolHighlight = document.getElementById("btn-mobile-tool-highlight");
    const btnMobileToolDraw = document.getElementById("btn-mobile-tool-draw");
    if (btnMobileToolRead) btnMobileToolRead.classList.toggle("active", mode === "read");
    if (btnMobileToolHighlight) btnMobileToolHighlight.classList.toggle("active", mode === "highlight");
    if (btnMobileToolDraw) btnMobileToolDraw.classList.toggle("active", mode === "draw");

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
    updateTabletControls();
  }

  btnToolRead.addEventListener("click", () => setToolMode("read"));
  btnToolHighlight.addEventListener("click", () => setToolMode("highlight"));
  btnToolDraw.addEventListener("click", () => setToolMode("draw"));

  // --- Alternância entre Modos de Visualização (Página / Rolagem) ---
  function setViewMode(mode) {
    viewMode = mode;
    localStorage.setItem("lectoria_reader_mode", mode);

    const iconMobileModeSingle = document.getElementById("icon-mobile-mode-single");
    const iconMobileModeContinuous = document.getElementById("icon-mobile-mode-continuous");
    if (iconMobileModeSingle && iconMobileModeContinuous) {
      iconMobileModeSingle.style.display = mode === "single" ? "block" : "none";
      iconMobileModeContinuous.style.display = mode === "continuous" ? "block" : "none";
    }

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
      renderSinglePage(currentPage, { resetScroll: true });
    }
  }

  btnModeSingle.addEventListener("click", () => setViewMode("single"));
  btnModeContinuous.addEventListener("click", () => setViewMode("continuous"));

  // --- Navegação entre Páginas ---
  function goToPage(num) {
    if (bookFormat === "epub") {
      if (epubRendition) {
        if (num > currentPage) epubRendition.next();
        else if (num < currentPage) epubRendition.prev();
      }
      return;
    }
    let target = parseInt(num, 10);
    if (isNaN(target)) target = 1;
    if (target < 1) target = 1;
    if (target > totalPages) target = totalPages;

    if (target !== currentPage) {
      lastVisitedPage = currentPage;
      updateLastVisitedPageButton();
    }

    currentPage = target;
    pageNumInput.value = currentPage;
    updateReadingProgressBar();
    updateMobilePageControls();
    updateTabletControls();
    if (thumbnailsInitialized) highlightActiveThumbnail();

    clearTimeout(scrollTimeout);
    queueSaveProgress();

    if (viewMode === "single") {
      renderSinglePage(currentPage, { resetScroll: true });
    } else {
      const slot = document.getElementById(`page-slot-${currentPage}`);
      if (slot) {
        isProgrammaticScroll = true;
        viewportEl.scrollTop = slot.offsetTop;
        setTimeout(() => {
          isProgrammaticScroll = false;
        }, 300);
      }
    }
  }

  btnPrevPage.addEventListener("click", () => {
    if (bookFormat === "epub") {
      if (epubRendition) epubRendition.prev();
    } else if (currentPage > 1) {
      goToPage(currentPage - 1);
    }
  });

  btnNextPage.addEventListener("click", () => {
    if (bookFormat === "epub") {
      if (epubRendition) epubRendition.next();
    } else if (currentPage < totalPages) {
      goToPage(currentPage + 1);
    }
  });

  pageNumInput.addEventListener("change", () => goToPage(pageNumInput.value));
  pageNumInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      goToPage(pageNumInput.value);
      pageNumInput.blur();
    }
  });

  // --- Controles de Zoom ---
  function getContinuousRelativeOffset() {
    const activeSlot = document.getElementById(`page-slot-${currentPage}`);
    if (!activeSlot || activeSlot.clientHeight <= 0) return 0;
    return Math.max(0, (viewportEl.scrollTop - activeSlot.offsetTop) / activeSlot.clientHeight);
  }

  btnZoomIn.addEventListener("click", () => {
    if (bookFormat === "epub") {
      changeEpubFontSize(10);
      return;
    }
    zoomMode = "custom";
    currentScale = Math.min(currentScale * 1.25, 4.0);
    isProgrammaticScroll = true;
    if (viewMode === "single") {
      renderSinglePage(currentPage, { resetScroll: false });
    } else {
      setupContinuousView(currentPage, getContinuousRelativeOffset());
    }
  });

  btnZoomOut.addEventListener("click", () => {
    if (bookFormat === "epub") {
      changeEpubFontSize(-10);
      return;
    }
    zoomMode = "custom";
    currentScale = Math.max(currentScale / 1.25, 0.4);
    isProgrammaticScroll = true;
    if (viewMode === "single") {
      renderSinglePage(currentPage, { resetScroll: false });
    } else {
      setupContinuousView(currentPage, getContinuousRelativeOffset());
    }
  });

  btnFitWidth.addEventListener("click", () => {
    if (bookFormat === "epub") return;
    zoomMode = "width";
    isProgrammaticScroll = true;
    if (viewMode === "single") {
      renderSinglePage(currentPage, { resetScroll: false });
    } else {
      setupContinuousView(currentPage, getContinuousRelativeOffset());
    }
  });

  btnFitPage.addEventListener("click", () => {
    if (bookFormat === "epub") return;
    zoomMode = "page";
    isProgrammaticScroll = true;
    if (viewMode === "single") {
      renderSinglePage(currentPage, { resetScroll: false });
    } else {
      setupContinuousView(currentPage);
    }
  });

  // --- Modal de Atalhos de Teclado ---
  if (btnShortcutsHelp && modalShortcutsHelp) {
    btnShortcutsHelp.addEventListener("click", () => {
      modalShortcutsHelp.classList.add("active");
    });
  }
  if (btnCloseModalShortcuts && modalShortcutsHelp) {
    btnCloseModalShortcuts.addEventListener("click", () => {
      modalShortcutsHelp.classList.remove("active");
    });
  }
  if (btnCloseShortcutsBtn && modalShortcutsHelp) {
    btnCloseShortcutsBtn.addEventListener("click", () => {
      modalShortcutsHelp.classList.remove("active");
    });
  }

  // --- Atalhos de Teclado Globais ---
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
    } else if (e.key.toLowerCase() === "r") {
      setToolMode("read");
    } else if (e.key.toLowerCase() === "b") {
      if (lastVisitedPage) goToPage(lastVisitedPage);
    } else if (e.key.toLowerCase() === "a") {
      btnToggleDrawer.click();
    } else if (e.key.toLowerCase() === "m") {
      if (btnToggleThumbnails) btnToggleThumbnails.click();
    } else if (e.key.toLowerCase() === "s") {
      setViewMode(viewMode === "single" ? "continuous" : "single");
    } else if (e.key.toLowerCase() === "t") {
      cycleTheme();
    } else if (e.key === "?") {
      if (modalShortcutsHelp) modalShortcutsHelp.classList.add("active");
    } else if (e.key === "Escape") {
      hideSelectionToolbar();
      closeNoteEditorModal();
      if (modalImportPreview) modalImportPreview.classList.remove("active");
      if (modalShortcutsHelp) modalShortcutsHelp.classList.remove("active");
      if (annotationsDrawer) annotationsDrawer.classList.remove("active");
      closeThumbnailsDrawer();
      if (typeof closeTocDrawer === "function") closeTocDrawer();
      if (typeof closeMobileSheet === "function") closeMobileSheet();
      if (typeof closeMobileJumpModal === "function") closeMobileJumpModal();
    }
  });

  // Limpeza de recursos e observers para evitar vazamento de memória e ciclos de CPU
  function cleanupObservers() {
    if (thumbnailObserver) {
      thumbnailObserver.disconnect();
      thumbnailObserver = null;
    }
    if (continuousObserver) {
      continuousObserver.disconnect();
      continuousObserver = null;
    }
  }

  window.addEventListener("beforeunload", cleanupObservers);
  window.addEventListener("pagehide", cleanupObservers);

  // Redimensionamento de Janela
  let resizeTimeout = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(() => {
      if (bookFormat === "epub") {
        if (epubRendition) epubRendition.resize();
        return;
      }
      isProgrammaticScroll = true;
      if (viewMode === "single") renderSinglePage(currentPage, { resetScroll: false });
      else setupContinuousView(currentPage);
    }, 180);
  });

  // =========================================
  // GESTOS TOUCH E CONTROLES MÓVEIS (Iteração 11)
  // =========================================

  function updateMobilePageControls() {
    const mobilePageCurrent = document.getElementById("mobile-page-current");
    const mobilePageTotal = document.getElementById("mobile-page-total");
    const mobileJumpMax = document.getElementById("mobile-jump-max");
    const btnPrevPageMobile = document.getElementById("btn-prev-page-mobile");
    const btnNextPageMobile = document.getElementById("btn-next-page-mobile");
    const mobilePageInputDirect = document.getElementById("mobile-page-input-direct");

    if (mobilePageCurrent) mobilePageCurrent.textContent = currentPage;
    if (mobilePageTotal) mobilePageTotal.textContent = totalPages || "--";
    if (mobileJumpMax) mobileJumpMax.textContent = totalPages || "--";
    if (btnPrevPageMobile) btnPrevPageMobile.disabled = currentPage <= 1;
    if (btnNextPageMobile) btnNextPageMobile.disabled = currentPage >= totalPages;
    if (mobilePageInputDirect) {
      mobilePageInputDirect.value = currentPage;
      if (totalPages) mobilePageInputDirect.max = totalPages;
    }
  }

  // --- Gestos de Toque: Deslize (Swipe), Duplo Toque e Pinça (Pinch-to-Zoom) ---
  let touchStartX = 0;
  let touchStartY = 0;
  let touchStartTime = 0;
  let isPinching = false;
  let initialPinchDistance = null;
  let initialPinchScale = 1.0;
  let lastPinchRatio = 1.0;
  let lastTapTime = 0;

  function getTouchDistance(t1, t2) {
    const dx = t1.clientX - t2.clientX;
    const dy = t1.clientY - t2.clientY;
    return Math.hypot(dx, dy);
  }

  viewportEl.addEventListener("touchstart", (e) => {
    if (currentTool === "draw") return; // Desenho vetorial gerencia seus próprios eventos de toque

    if (e.touches.length === 2) {
      isPinching = true;
      initialPinchDistance = getTouchDistance(e.touches[0], e.touches[1]);
      initialPinchScale = currentScale;
      lastPinchRatio = 1.0;
    } else if (e.touches.length === 1) {
      isPinching = false;
      touchStartX = e.touches[0].clientX;
      touchStartY = e.touches[0].clientY;
      touchStartTime = Date.now();
    }
  }, { passive: true });

  viewportEl.addEventListener("touchmove", (e) => {
    if (currentTool === "draw") return;

    if (isPinching && e.touches.length === 2 && initialPinchDistance) {
      const currentDist = getTouchDistance(e.touches[0], e.touches[1]);
      lastPinchRatio = currentDist / initialPinchDistance;
    }
  }, { passive: true });

  viewportEl.addEventListener("touchend", (e) => {
    if (currentTool === "draw") return;

    if (isPinching) {
      if (e.touches.length < 2) {
        isPinching = false;
        if (initialPinchDistance && Math.abs(lastPinchRatio - 1.0) > 0.12) {
          zoomMode = "custom";
          currentScale = Math.min(Math.max(initialPinchScale * lastPinchRatio, 0.45), 3.5);
          if (viewMode === "single") renderSinglePage(currentPage, { resetScroll: false });
          else setupContinuousView();
        }
        initialPinchDistance = null;
        lastPinchRatio = 1.0;
      }
      return;
    }

    if (e.changedTouches.length === 1) {
      const touchEndX = e.changedTouches[0].clientX;
      const touchEndY = e.changedTouches[0].clientY;
      const deltaX = touchEndX - touchStartX;
      const deltaY = touchEndY - touchStartY;
      const elapsedTime = Date.now() - touchStartTime;

      // Duplo Toque para Zoom Rápido (Alterna entre largura da tela e zoom ampliado)
      const now = Date.now();
      const isQuickTap = elapsedTime < 250 && Math.abs(deltaX) < 15 && Math.abs(deltaY) < 15;
      if (isQuickTap && (now - lastTapTime < 320)) {
        lastTapTime = 0;
        if (zoomMode === "width" && currentScale < 1.35) {
          zoomMode = "custom";
          currentScale = 1.75;
        } else {
          zoomMode = "width";
        }
        if (viewMode === "single") renderSinglePage(currentPage, { resetScroll: false });
        else setupContinuousView();
        return;
      }
      if (isQuickTap) {
        lastTapTime = now;
      }

      // Deslize Horizontal (Swipe) para Avançar / Voltar Página no modo Página Única
      // Apenas aciona se não estiver em zoom muito ampliado (para permitir pan horizontal suave)
      if (viewMode === "single" && (zoomMode === "width" || zoomMode === "page" || currentScale <= 1.2)) {
        if (elapsedTime < 500 && Math.abs(deltaX) > 48 && Math.abs(deltaX) > 1.5 * Math.abs(deltaY)) {
          if (deltaX < -48 && currentPage < totalPages) {
            goToPage(currentPage + 1);
          } else if (deltaX > 48 && currentPage > 1) {
            goToPage(currentPage - 1);
          }
        }
      }
    }
  }, { passive: true });

  // --- Rotação de Tela (Landscape / Portrait) ---
  window.addEventListener("orientationchange", () => {
    clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(() => {
      if (bookFormat === "epub") {
        if (epubRendition) epubRendition.resize();
        return;
      }
      isProgrammaticScroll = true;
      if (viewMode === "single") renderSinglePage(currentPage, { resetScroll: false });
      else setupContinuousView(currentPage);
    }, 200);
  });

  // --- Barra de Navegação Inferior Móvel ---
  const btnPrevPageMobile = document.getElementById("btn-prev-page-mobile");
  const btnNextPageMobile = document.getElementById("btn-next-page-mobile");
  const btnMobilePageIndicator = document.getElementById("btn-mobile-page-indicator");
  const btnMobileToggleMode = document.getElementById("btn-mobile-toggle-mode");
  const btnMobileDrawer = document.getElementById("btn-mobile-drawer");
  const btnMobileMenu = document.getElementById("btn-mobile-menu");
  const btnReaderThemeMobile = document.getElementById("btn-reader-theme-mobile");

  if (btnPrevPageMobile) {
    btnPrevPageMobile.addEventListener("click", () => {
      if (currentPage > 1) goToPage(currentPage - 1);
    });
  }

  if (btnNextPageMobile) {
    btnNextPageMobile.addEventListener("click", () => {
      if (currentPage < totalPages) goToPage(currentPage + 1);
    });
  }

  if (btnMobileToggleMode) {
    btnMobileToggleMode.addEventListener("click", () => {
      setViewMode(viewMode === "single" ? "continuous" : "single");
    });
  }

  if (btnMobileDrawer) {
    btnMobileDrawer.addEventListener("click", () => {
      if (annotationsDrawer) {
        const isActive = annotationsDrawer.classList.contains("active");
        if (isActive) {
          annotationsDrawer.classList.remove("active");
        } else {
          annotationsDrawer.classList.add("active");
          if (thumbnailsDrawer) closeThumbnailsDrawer();
          closeMobileSheet();
        }
      }
    });
  }

  if (btnReaderThemeMobile) {
    btnReaderThemeMobile.addEventListener("click", () => {
      cycleTheme();
    });
  }

  // --- Bottom Sheet de Mais Opções ---
  const sheetMobileMenu = document.getElementById("sheet-mobile-menu");
  const btnCloseMobileSheet = document.getElementById("btn-close-mobile-sheet");

  function openMobileSheet() {
    if (sheetMobileMenu) sheetMobileMenu.classList.add("active");
  }

  function closeMobileSheet() {
    if (sheetMobileMenu) sheetMobileMenu.classList.remove("active");
  }

  if (btnMobileMenu) {
    btnMobileMenu.addEventListener("click", openMobileSheet);
  }

  if (btnCloseMobileSheet) {
    btnCloseMobileSheet.addEventListener("click", closeMobileSheet);
  }

  if (sheetMobileMenu) {
    sheetMobileMenu.addEventListener("click", (e) => {
      if (e.target === sheetMobileMenu) closeMobileSheet();
    });
  }

  // Ações do Bottom Sheet
  const btnMobileToolRead = document.getElementById("btn-mobile-tool-read");
  const btnMobileToolHighlight = document.getElementById("btn-mobile-tool-highlight");
  const btnMobileToolDraw = document.getElementById("btn-mobile-tool-draw");
  const btnMobileFitWidth = document.getElementById("btn-mobile-fit-width");
  const btnMobileFitPage = document.getElementById("btn-mobile-fit-page");
  const btnMobileZoomIn = document.getElementById("btn-mobile-zoom-in");
  const btnMobileZoomOut = document.getElementById("btn-mobile-zoom-out");
  const btnMobileSheetThumbnails = document.getElementById("btn-mobile-sheet-thumbnails");
  const btnMobileSheetDownload = document.getElementById("btn-mobile-sheet-download");
  const btnMobileSheetShortcuts = document.getElementById("btn-mobile-sheet-shortcuts");

  if (btnMobileToolRead) {
    btnMobileToolRead.addEventListener("click", () => {
      setToolMode("read");
      closeMobileSheet();
    });
  }

  if (btnMobileToolHighlight) {
    btnMobileToolHighlight.addEventListener("click", () => {
      setToolMode("highlight");
      closeMobileSheet();
    });
  }

  if (btnMobileToolDraw) {
    btnMobileToolDraw.addEventListener("click", () => {
      setToolMode("draw");
      closeMobileSheet();
    });
  }

  if (btnMobileFitWidth) {
    btnMobileFitWidth.addEventListener("click", () => {
      btnFitWidth.click();
      closeMobileSheet();
    });
  }

  if (btnMobileFitPage) {
    btnMobileFitPage.addEventListener("click", () => {
      btnFitPage.click();
      closeMobileSheet();
    });
  }

  if (btnMobileZoomIn) {
    btnMobileZoomIn.addEventListener("click", () => {
      btnZoomIn.click();
      closeMobileSheet();
    });
  }

  if (btnMobileZoomOut) {
    btnMobileZoomOut.addEventListener("click", () => {
      btnZoomOut.click();
      closeMobileSheet();
    });
  }

  if (btnMobileSheetThumbnails) {
    btnMobileSheetThumbnails.addEventListener("click", () => {
      closeMobileSheet();
      openThumbnailsDrawer();
    });
  }

  if (btnMobileSheetDownload) {
    btnMobileSheetDownload.addEventListener("click", () => {
      closeMobileSheet();
      if (btnDownloadPdf) btnDownloadPdf.click();
    });
  }

  if (btnMobileSheetShortcuts) {
    btnMobileSheetShortcuts.addEventListener("click", () => {
      closeMobileSheet();
      if (modalShortcutsHelp) modalShortcutsHelp.classList.add("active");
    });
  }

  // --- Modal de Pulo Direto de Página para Mobile ---
  const modalMobilePageJump = document.getElementById("modal-mobile-page-jump");
  const btnCloseModalJump = document.getElementById("btn-close-modal-jump");
  const btnCancelModalJump = document.getElementById("btn-cancel-modal-jump");
  const btnConfirmModalJump = document.getElementById("btn-confirm-modal-jump");
  const mobilePageInputDirect = document.getElementById("mobile-page-input-direct");
  const btnMobileJumpFirst = document.getElementById("btn-mobile-jump-first");
  const btnMobileJumpLast = document.getElementById("btn-mobile-jump-last");

  function openMobileJumpModal() {
    if (!modalMobilePageJump) return;
    if (mobilePageInputDirect) {
      mobilePageInputDirect.value = currentPage;
      mobilePageInputDirect.max = totalPages;
    }
    modalMobilePageJump.classList.add("active");
    setTimeout(() => {
      if (mobilePageInputDirect) {
        mobilePageInputDirect.focus();
        mobilePageInputDirect.select();
      }
    }, 100);
  }

  function closeMobileJumpModal() {
    if (modalMobilePageJump) modalMobilePageJump.classList.remove("active");
  }

  if (btnMobilePageIndicator) {
    btnMobilePageIndicator.addEventListener("click", openMobileJumpModal);
  }

  if (btnCloseModalJump) btnCloseModalJump.addEventListener("click", closeMobileJumpModal);
  if (btnCancelModalJump) btnCancelModalJump.addEventListener("click", closeMobileJumpModal);

  if (btnConfirmModalJump) {
    btnConfirmModalJump.addEventListener("click", () => {
      const val = parseInt(mobilePageInputDirect.value, 10);
      if (!isNaN(val)) {
        goToPage(val);
      }
      closeMobileJumpModal();
    });
  }

  if (mobilePageInputDirect) {
    mobilePageInputDirect.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        const val = parseInt(mobilePageInputDirect.value, 10);
        if (!isNaN(val)) goToPage(val);
        closeMobileJumpModal();
      }
    });
  }

  if (btnMobileJumpFirst) {
    btnMobileJumpFirst.addEventListener("click", () => {
      goToPage(1);
      closeMobileJumpModal();
    });
  }

  if (btnMobileJumpLast) {
    btnMobileJumpLast.addEventListener("click", () => {
      goToPage(totalPages);
      closeMobileJumpModal();
    });
  }

  // =========================================
  // BARRA ACOPLÁVEL PARA TABLETS (Fase 2)
  // =========================================
  function updateTabletControls() {
    if (!tabletDockToolbar) return;
    if (tabletPagePill) {
      if (bookFormat === "epub") {
        tabletPagePill.textContent = `${Math.round(currentPage)}%`;
      } else {
        tabletPagePill.textContent = `${currentPage} / ${totalPages || "--"}`;
      }
    }
    if (tabletModeBadge) {
      if (bookFormat === "epub") {
        tabletModeBadge.textContent = "EPUB";
      } else {
        const modeNames = { read: "Ler", highlight: "Destacar", draw: "Desenho", eraser: "Borracha" };
        tabletModeBadge.textContent = isEraserActive ? "Borracha" : (modeNames[currentTool] || "Ler");
      }
    }
    if (tabletPdfTools && tabletEpubTools) {
      tabletPdfTools.style.display = bookFormat === "epub" ? "none" : "flex";
      tabletEpubTools.style.display = bookFormat === "epub" ? "flex" : "none";
    }
    if (btnTabletToolRead) btnTabletToolRead.classList.toggle("active", currentTool === "read" && !isEraserActive);
    if (btnTabletToolHighlight) btnTabletToolHighlight.classList.toggle("active", currentTool === "highlight");
    if (btnTabletToolDraw) btnTabletToolDraw.classList.toggle("active", currentTool === "draw" && !isEraserActive);
    if (btnTabletToolEraser) {
      btnTabletToolEraser.classList.toggle("active", isEraserActive);
      btnTabletToolEraser.style.display = (currentTool === "draw" || isEraserActive) ? "flex" : "none";
    }
  }

  if (btnTabletDockSide) {
    const savedDockSide = localStorage.getItem("lectoria_tablet_dock") || "right";
    tabletDockToolbar.classList.remove("dock-left", "dock-right");
    tabletDockToolbar.classList.add(savedDockSide === "left" ? "dock-left" : "dock-right");
    document.body.classList.remove("dock-side-left", "dock-side-right");
    document.body.classList.add(savedDockSide === "left" ? "dock-side-left" : "dock-side-right");

    btnTabletDockSide.addEventListener("click", () => {
      const isRight = tabletDockToolbar.classList.contains("dock-right");
      tabletDockToolbar.classList.remove("dock-left", "dock-right");
      const targetSide = isRight ? "left" : "right";
      tabletDockToolbar.classList.add(targetSide === "left" ? "dock-left" : "dock-right");
      document.body.classList.remove("dock-side-left", "dock-side-right");
      document.body.classList.add(targetSide === "left" ? "dock-side-left" : "dock-side-right");
      localStorage.setItem("lectoria_tablet_dock", targetSide);
      if (viewMode === "single") renderSinglePage(currentPage, { resetScroll: false });
      else setupContinuousView(currentPage);
    });
  }

  if (btnTabletPrev) {
    btnTabletPrev.addEventListener("click", () => {
      if (bookFormat === "epub") {
        if (epubRendition) epubRendition.prev();
      } else if (currentPage > 1) {
        goToPage(currentPage - 1);
      }
    });
  }

  if (btnTabletNext) {
    btnTabletNext.addEventListener("click", () => {
      if (bookFormat === "epub") {
        if (epubRendition) epubRendition.next();
      } else if (currentPage < totalPages) {
        goToPage(currentPage + 1);
      }
    });
  }

  if (btnTabletToolRead) btnTabletToolRead.addEventListener("click", () => setToolMode("read"));
  if (btnTabletToolHighlight) btnTabletToolHighlight.addEventListener("click", () => setToolMode("highlight"));
  if (btnTabletToolDraw) btnTabletToolDraw.addEventListener("click", () => setToolMode("draw"));
  if (btnTabletToolEraser) {
    btnTabletToolEraser.addEventListener("click", () => {
      setToolMode("draw");
      if (!isEraserActive) btnDrawEraser.click();
      updateTabletControls();
    });
  }
  if (btnTabletDrawer) btnTabletDrawer.addEventListener("click", () => btnToggleDrawer.click());
  if (btnTabletTheme) btnTabletTheme.addEventListener("click", cycleTheme);
  if (btnTabletToc) btnTabletToc.addEventListener("click", () => openTocDrawer());

  // =========================================
  // SUPORTE NATIVO A EPUB (Fase 4)
  // =========================================
  function changeEpubFontSize(delta) {
    epubFontSize = Math.min(Math.max(epubFontSize + delta, 60), 220);
    if (epubFontSizeLabel) epubFontSizeLabel.textContent = `${epubFontSize}%`;
    if (epubRendition) {
      epubRendition.themes.fontSize(`${epubFontSize}%`);
    }
    localStorage.setItem("lectoria_epub_font_size", epubFontSize);
  }

  function toggleEpubFontFamily() {
    epubFontFamily = epubFontFamily === "serif" ? "sans-serif" : "serif";
    if (epubRendition) {
      epubRendition.themes.font(epubFontFamily);
    }
    if (btnEpubFontFamily) {
      const sp = btnEpubFontFamily.querySelector("span");
      if (sp) sp.style.fontFamily = epubFontFamily;
    }
    if (btnMobileEpubFont) {
      const sp = btnMobileEpubFont.querySelector("span");
      if (sp) sp.style.fontFamily = epubFontFamily;
    }
    localStorage.setItem("lectoria_epub_font_family", epubFontFamily);
  }

  function openTocDrawer() {
    if (!tocDrawer) return;
    tocDrawer.classList.add("active");
    if (annotationsDrawer) annotationsDrawer.classList.remove("active");
    if (thumbnailsDrawer) closeThumbnailsDrawer();
  }

  function closeTocDrawer() {
    if (!tocDrawer) return;
    tocDrawer.classList.remove("active");
  }

  if (btnCloseToc) btnCloseToc.addEventListener("click", closeTocDrawer);
  if (btnEpubToc) btnEpubToc.addEventListener("click", openTocDrawer);
  if (btnMobileSheetToc) {
    btnMobileSheetToc.addEventListener("click", () => {
      closeMobileSheet();
      openTocDrawer();
    });
  }

  function renderEpubToc(toc) {
    if (!tocList || !toc || toc.length === 0) return;
    tocList.innerHTML = "";
    toc.forEach((item, idx) => {
      const a = document.createElement("a");
      a.className = "toc-item-link";
      a.textContent = item.label ? item.label.trim() : `Capítulo ${idx + 1}`;
      a.setAttribute("data-href", item.href);
      a.addEventListener("click", (e) => {
        e.preventDefault();
        if (epubRendition) {
          epubRendition.display(item.href);
        }
        closeTocDrawer();
      });
      tocList.appendChild(a);
    });
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      if (document.querySelector(`script[src="${src}"]`)) {
        resolve();
        return;
      }
      const s = document.createElement("script");
      s.src = src;
      s.onload = () => resolve();
      s.onerror = (e) => reject(new Error(`Falha ao carregar script ${src}`));
      document.body.appendChild(s);
    });
  }

  async function ensureEpubScriptsLoaded() {
    if (typeof ePub !== "undefined" && typeof JSZip !== "undefined") {
      return;
    }
    await loadScript("/static/vendor/epubjs/jszip.min.js");
    await loadScript("/static/vendor/epubjs/epub.min.js");
  }

  async function initEpubReader(bookData, progData) {
    bookFormat = "epub";

    // Verificação de segurança: bloqueio de arquivos criptografados com DRM
    if (bookData.is_drm) {
      viewportEl.innerHTML = `
        <div class="empty-state" style="padding-top: 5rem;">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="var(--color-primary)" style="margin-bottom: 1rem;">
            <path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z"/>
          </svg>
          <h3 style="margin-bottom: 0.5rem;">Livro protegido por DRM</h3>
          <p style="max-width: 500px; margin: 0 auto; color: var(--text-secondary); line-height: 1.6;">
            Este arquivo possui proteção por DRM proprietário (${escapeHtml(bookData.drm_type || "criptografia de direitos autorais")}) e seu conteúdo está criptografado.<br><br>
            O Lectoria é uma biblioteca aberta e suporta publicações livres de DRM.
          </p>
          <p style="margin-top: 1.5rem;"><a href="/" class="btn btn-primary">Voltar à Biblioteca</a></p>
        </div>
      `;
      return;
    }

    singlePageWrapper.style.display = "none";
    continuousWrapper.style.display = "none";
    epubWrapper.style.display = "flex";

    // Exibe ferramentas específicas do EPUB
    if (epubHeaderTools) epubHeaderTools.style.display = "flex";
    if (mobileSheetEpubSection) mobileSheetEpubSection.style.display = "block";
    if (btnMobileSheetToc) btnMobileSheetToc.style.display = "flex";

    // Oculta ferramentas específicas de PDF
    const toolModeGroup = document.querySelector(".tool-mode-group");
    if (toolModeGroup) toolModeGroup.style.display = "none";
    const viewModeToggles = document.querySelector(".view-mode-toggles");
    if (viewModeToggles) viewModeToggles.style.display = "none";
    const zoomControls = document.querySelector(".zoom-controls");
    if (zoomControls) zoomControls.style.display = "none";
    if (btnToggleThumbnails) btnToggleThumbnails.style.display = "none";

    const labelDl = document.getElementById("label-download-format");
    if (labelDl) labelDl.textContent = "Baixar Arquivo EPUB Original";

    // Configurações salvas de tamanho de fonte e tipografia
    epubFontSize = parseInt(localStorage.getItem("lectoria_epub_font_size"), 10) || 100;
    epubFontFamily = localStorage.getItem("lectoria_epub_font_family") || "serif";
    if (epubFontSizeLabel) epubFontSizeLabel.textContent = `${epubFontSize}%`;
    if (btnEpubFontFamily) {
      const sp = btnEpubFontFamily.querySelector("span");
      if (sp) sp.style.fontFamily = epubFontFamily;
    }

    if (btnEpubFontSmaller) btnEpubFontSmaller.addEventListener("click", () => changeEpubFontSize(-10));
    if (btnEpubFontLarger) btnEpubFontLarger.addEventListener("click", () => changeEpubFontSize(10));
    if (btnEpubFontFamily) btnEpubFontFamily.addEventListener("click", toggleEpubFontFamily);
    if (btnEpubThemeSepia) btnEpubThemeSepia.addEventListener("click", () => applyTheme("sepia"));

    if (btnMobileEpubSmaller) btnMobileEpubSmaller.addEventListener("click", () => changeEpubFontSize(-10));
    if (btnMobileEpubLarger) btnMobileEpubLarger.addEventListener("click", () => changeEpubFontSize(10));
    if (btnMobileEpubFont) btnMobileEpubFont.addEventListener("click", toggleEpubFontFamily);
    if (btnMobileEpubSepia) btnMobileEpubSepia.addEventListener("click", () => applyTheme("sepia"));

    if (btnTabletFontSmaller) btnTabletFontSmaller.addEventListener("click", () => changeEpubFontSize(-10));
    if (btnTabletFontLarger) btnTabletFontLarger.addEventListener("click", () => changeEpubFontSize(10));
    if (btnTabletFontFamily) btnTabletFontFamily.addEventListener("click", toggleEpubFontFamily);

    // Carrega sob demanda as bibliotecas do EPUB (sem sobrecarregar leitura de PDFs)
    await ensureEpubScriptsLoaded();

    if (typeof ePub === "undefined") {
      throw new Error("Biblioteca de renderização EPUB não pôde ser carregada.");
    }

    epubBook = ePub(`/api/books/${bookId}/file`, { openAs: "epub" });
    epubRendition = epubBook.renderTo("epub-viewer", {
      width: "100%",
      height: "100%",
      spread: "none",
      allowScriptedContent: false,
    });

    // Interceptação e isolamento seguro de links clicados no EPUB
    epubRendition.on("linkClicked", (href) => {
      if (!href) return;
      const lower = href.trim().toLowerCase();
      if (lower.startsWith("javascript:") || lower.startsWith("data:") || lower.startsWith("vbscript:")) {
        console.warn("Link potencialmente inseguro bloqueado no EPUB:", href);
        return;
      }
      if (lower.startsWith("http://") || lower.startsWith("https://")) {
        const confirmMsg = `O livro contém um link para o endereço externo:\n\n${href}\n\nDeseja abrir esta página em uma nova aba com proteção de isolamento?`;
        if (confirm(confirmMsg)) {
          window.open(href, "_blank", "noopener,noreferrer");
        }
        return;
      }
      epubRendition.display(href).catch(e => console.warn("Erro ao navegar para link interno:", e));
    });

    // Hook no iframe do EPUB: higieniza tags <a> e remove scripts
    epubRendition.hooks.content.register((contents) => {
      const doc = contents.document;
      if (!doc) return;
      doc.querySelectorAll("a").forEach(a => {
        const rawHref = a.getAttribute("href") || "";
        const lower = rawHref.trim().toLowerCase();
        if (lower.startsWith("javascript:") || lower.startsWith("data:") || lower.startsWith("vbscript:")) {
          a.removeAttribute("href");
          a.style.pointerEvents = "none";
          a.style.cursor = "default";
        } else if (lower.startsWith("http://") || lower.startsWith("https://")) {
          a.setAttribute("target", "_blank");
          a.setAttribute("rel", "noopener noreferrer");
        }
      });
    });

    epubRendition.themes.default({
      "body": {
        "padding": "0 1.25rem !important",
        "font-family": `${epubFontFamily}, Georgia, serif !important`,
        "font-size": `${epubFontSize}% !important`,
        "line-height": "1.65 !important",
      }
    });

    applyEpubTheme(currentTheme);

    // Carrega sumário (TOC)
    epubBook.loaded.navigation.then(nav => {
      renderEpubToc(nav.toc);
    });

    // Gera localizações em segundo plano
    epubBook.ready.then(() => {
      return epubBook.locations.generate(1024);
    }).then(() => {
      if (epubCfi && epubBook.locations) {
        const pct = Math.round(epubBook.locations.percentageFromCfi(epubCfi) * 100);
        if (pct >= 0 && pct <= 100) {
          currentPage = pct;
          updateReadingProgressBar();
          updateMobilePageControls();
          updateTabletControls();
        }
      }
    });

    // Atualização de localização e salvamento de progresso
    epubRendition.on("relocated", (location) => {
      epubCfi = location.start.cfi;
      let pct = 0;
      if (epubBook.locations && epubBook.locations.length() > 0) {
        pct = Math.round(epubBook.locations.percentageFromCfi(epubCfi) * 100);
      } else if (location.start.percentage) {
        pct = Math.round(location.start.percentage * 100);
      }
      currentPage = Math.min(Math.max(pct, 1), 100);
      totalPages = 100;
      pageCountDisplay.textContent = "%";
      pageNumInput.value = currentPage;

      updateReadingProgressBar();
      updateMobilePageControls();
      updateTabletControls();

      queueSaveProgress(epubCfi);
      isRestoringProgress = false;
    });

    // Conecta gestos de toque no documento do EPUB
    epubRendition.on("touchstart", (e) => {
      if (e.touches && e.touches.length === 1) {
        touchStartX = e.touches[0].clientX;
        touchStartY = e.touches[0].clientY;
        touchStartTime = Date.now();
      }
    });

    epubRendition.on("touchend", (e) => {
      if (e.changedTouches && e.changedTouches.length === 1) {
        const dx = e.changedTouches[0].clientX - touchStartX;
        const dy = e.changedTouches[0].clientY - touchStartY;
        const dt = Date.now() - touchStartTime;
        if (dt < 450 && Math.abs(dx) > 44 && Math.abs(dx) > 1.4 * Math.abs(dy)) {
          if (dx < -44) epubRendition.next();
          else if (dx > 44) epubRendition.prev();
        }
      }
    });

    // Restaura progresso salvo (CFI) ou inicia do começo
    const savedLocator = progData && progData.progress_locator;
    if (savedLocator) {
      await epubRendition.display(savedLocator);
    } else {
      await epubRendition.display();
    }

    updateTabletControls();
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  // --- Carregamento Inicial do Documento ---
  try {
    const bookRes = await fetch(`/api/books/${bookId}`);
    if (bookRes.status === 401) {
      window.location.href = `/?login=1`;
      return;
    }
    if (!bookRes.ok) throw new Error("Livro não encontrado.");
    const bookData = await bookRes.json();
    bookTitleEl.textContent = bookData.title;
    document.title = `${bookData.title} — Lectoria`;

    bookFormat = (bookData.format || (bookData.filename && bookData.filename.toLowerCase().endsWith(".epub") ? "epub" : "pdf")).toLowerCase();

    const bookCoverMini = document.getElementById("reader-book-cover");
    if (bookCoverMini) {
      bookCoverMini.src = bookData.cover_url || `/api/books/${encodeURIComponent(bookId)}/cover`;
      bookCoverMini.style.display = "block";
    }

    const progRes = await fetch(`/api/books/${bookId}/progress`);
    let progData = null;
    let initialPage = 1;
    if (progRes.ok) {
      progData = await progRes.json();
      if (progData.current_page && progData.current_page >= 1) {
        initialPage = progData.current_page;
      }
    }

    // Se o formato for EPUB, inicializa o motor de renderização dedicado
    if (bookFormat === "epub") {
      await initEpubReader(bookData, progData);
      return;
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
    updateMobilePageControls();

    const firstPage = await pdfDoc.getPage(1);
    const firstVp = firstPage.getViewport({ scale: 1.0 });
    basePageWidth = firstVp.width;
    basePageHeight = firstVp.height;

    // Se o livro ainda não possui capa extraída ou personalizada, gera miniatura leve da 1ª página em segundo plano
    if (!bookData.cover_type || bookData.cover_type === "none" || bookData.cover_type === "fallback") {
      setTimeout(async () => {
        try {
          const thumbScale = Math.min(340 / firstVp.width, 1.0);
          const thumbVp = firstPage.getViewport({ scale: thumbScale });
          const offCanvas = document.createElement("canvas");
          offCanvas.width = Math.max(1, Math.floor(thumbVp.width));
          offCanvas.height = Math.max(1, Math.floor(thumbVp.height));
          const offCtx = offCanvas.getContext("2d");
          offCtx.fillStyle = "#ffffff";
          offCtx.fillRect(0, 0, offCanvas.width, offCanvas.height);
          await firstPage.render({ canvasContext: offCtx, viewport: thumbVp }).promise;

          // Verifica se a página renderizada não está completamente em branco
          const imgData = offCtx.getImageData(0, 0, offCanvas.width, offCanvas.height).data;
          let nonWhitePixels = 0;
          for (let i = 0; i < imgData.length; i += 16) {
            if (imgData[i] < 245 || imgData[i + 1] < 245 || imgData[i + 2] < 245) {
              nonWhitePixels++;
              if (nonWhitePixels > 25) break;
            }
          }

          if (nonWhitePixels > 25) {
            offCanvas.toBlob(async (blob) => {
              if (!blob) return;
              const formData = new FormData();
              formData.append("file", blob, "cover.jpg");
              const cacheRes = await fetch(`/api/books/${encodeURIComponent(bookId)}/cover/cache`, {
                method: "POST",
                body: formData,
              });
              if (cacheRes.ok && bookCoverMini) {
                bookCoverMini.src = `/api/books/${encodeURIComponent(bookId)}/cover?v=${Date.now()}`;
              }
            }, "image/jpeg", 0.85);
          }
        } catch (e) {
          // Falha silenciosa: mantém a capa editorial substituta
        }
      }, 600);
    }

    await reloadBookAnnotations();

    currentPage = Math.min(initialPage, totalPages);
    updateReadingProgressBar();
    updateMobilePageControls();
    updateTabletControls();
    setViewMode(viewMode);
    setTimeout(() => {
      isRestoringProgress = false;
    }, 600);
  } catch (err) {
    console.error("Falha ao abrir documento:", err);
    const isEpub = bookFormat === "epub";
    const msg = isEpub
      ? "Não foi possível carregar o arquivo EPUB. Verifique se o livro não está corrompido ou protegido por DRM."
      : "Não foi possível carregar o arquivo PDF.";
    viewportEl.innerHTML = `
      <div class="empty-state" style="padding-top: 5rem;">
        <h3>Erro ao abrir o documento</h3>
        <p>${msg}</p>
        <p style="margin-top: 1rem;"><a href="/" class="btn btn-primary">Voltar à Biblioteca</a></p>
      </div>
    `;
  }
});
