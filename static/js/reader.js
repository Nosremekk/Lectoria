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

  // Controles de Camadas e Busca no PDF (v1.8.0)
  const layerViewModeSelect = document.getElementById("layer-view-mode-select");
  const mobileLayerViewSelect = document.getElementById("mobile-layer-view-select");
  const btnTogglePdfSearch = document.getElementById("btn-toggle-pdf-search");
  const readerSearchBar = document.getElementById("reader-search-bar");
  const pdfSearchInput = document.getElementById("pdf-search-input");
  const pdfSearchStatus = document.getElementById("pdf-search-status");
  const btnPdfSearchPrev = document.getElementById("btn-pdf-search-prev");
  const btnPdfSearchNext = document.getElementById("btn-pdf-search-next");
  const btnClosePdfSearch = document.getElementById("btn-close-pdf-search");

  // Barras Flutuantes e Modais
  const selectionToolbar = document.getElementById("selection-toolbar");
  const btnAddNoteFromSelection = document.getElementById("btn-add-note-from-selection");
  const drawingToolbar = document.getElementById("drawing-toolbar");
  const drawStrokeWidthSelect = document.getElementById("draw-stroke-width");
  const btnDrawEraser = document.getElementById("btn-draw-eraser");
  const btnDrawClear = document.getElementById("btn-draw-clear");
  const btnDrawVisibility = document.getElementById("btn-draw-visibility");
  const drawVisibilityIcon = document.getElementById("draw-visibility-icon");
  const drawVisibilityLabel = document.getElementById("draw-visibility-label");

  // Popovers Contextuais na Página (v1.8.0)
  const highlightPopover = document.getElementById("highlight-popover");
  const btnCloseHlPopover = document.getElementById("btn-close-hl-popover");
  const hlPopAvatar = document.getElementById("hl-pop-avatar");
  const hlPopAuthorName = document.getElementById("hl-pop-author-name");
  const hlPopVisBadge = document.getElementById("hl-pop-vis-badge");
  const hlPopQuote = document.getElementById("hl-pop-quote");
  const hlPopNoteBox = document.getElementById("hl-pop-note-box");
  const hlPopActions = document.getElementById("hl-pop-actions");

  const pageNotePopover = document.getElementById("page-note-popover");
  const btnCloseNotePopover = document.getElementById("btn-close-note-popover");
  const notePopAvatar = document.getElementById("note-pop-avatar");
  const notePopAuthorName = document.getElementById("note-pop-author-name");
  const notePopVisBadge = document.getElementById("note-pop-vis-badge");
  const notePopMeta = document.getElementById("note-pop-meta");
  const notePopContent = document.getElementById("note-pop-content");
  const notePopActions = document.getElementById("note-pop-actions");

  // Painel de Anotações (Estudos) e Marcador Rápido (v1.8.0)
  const btnToggleBookmark = document.getElementById("btn-toggle-bookmark");
  const btnMobileSheetBookmark = document.getElementById("btn-mobile-sheet-bookmark");
  const annotationsDrawer = document.getElementById("annotations-drawer");
  const btnCloseDrawer = document.getElementById("btn-close-drawer");
  const drawerTabs = document.querySelectorAll(".drawer-tab");
  const drawerScopeBtns = document.querySelectorAll(".drawer-scope-btn");
  const drawerSortSelect = document.getElementById("drawer-sort-select");
  const drawerItemsList = document.getElementById("drawer-items-list");
  const drawerLoadMoreContainer = document.getElementById("drawer-load-more-container");
  const btnDrawerLoadMore = document.getElementById("btn-drawer-load-more");
  const countAllEl = document.getElementById("count-all-annotations");
  const countHighlightsEl = document.getElementById("count-highlights");
  const countNotesEl = document.getElementById("count-notes");
  const countDrawingsEl = document.getElementById("count-drawings");
  const countBookmarksEl = document.getElementById("count-bookmarks");
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
  const noteVisibilitySelect = document.getElementById("note-visibility-select");
  const noteVisibilityNotice = document.getElementById("note-visibility-notice");
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
  const singlePublicDrawingLayer = document.getElementById("single-public-drawing-layer");
  const singleDrawingLayer = document.getElementById("single-drawing-layer");
  const singleNotePinsLayer = document.getElementById("single-note-pins-layer");

  // Elementos do Leitor EPUB (Fase 4)
  const epubWrapper = document.getElementById("epub-wrapper");
  const epubViewer = document.getElementById("epub-viewer");
  const epubNotePinsLayer = document.getElementById("epub-note-pins-layer");
  const epubHeaderTools = document.getElementById("epub-header-tools");
  const btnEpubToc = document.getElementById("btn-epub-toc");
  const btnEpubFontSmaller = document.getElementById("btn-epub-font-smaller");
  const btnEpubFontLarger = document.getElementById("btn-epub-font-larger");
  const epubFontSizeLabel = document.getElementById("epub-font-size-label");
  const btnEpubFontFamily = document.getElementById("btn-epub-font-family");
  const btnEpubLineHeight = document.getElementById("btn-epub-line-height");
  const btnEpubMargin = document.getElementById("btn-epub-margin");
  const btnEpubThemeSepia = document.getElementById("btn-epub-theme-sepia");
  const tocDrawer = document.getElementById("toc-drawer");
  const btnCloseToc = document.getElementById("btn-close-toc");
  const tocList = document.getElementById("toc-list");
  const mobileSheetEpubSection = document.getElementById("mobile-sheet-epub-section");
  const btnMobileSheetToc = document.getElementById("btn-mobile-sheet-toc");
  const btnMobileEpubSmaller = document.getElementById("btn-mobile-epub-smaller");
  const btnMobileEpubLarger = document.getElementById("btn-mobile-epub-larger");
  const btnMobileEpubFont = document.getElementById("btn-mobile-epub-font");
  const btnMobileEpubLineHeight = document.getElementById("btn-mobile-epub-line-height");
  const btnMobileEpubMargin = document.getElementById("btn-mobile-epub-margin");
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
  let epubLineHeight = "1.65";
  let epubMargin = "normal"; // 'compact' | 'normal' | 'wide'
  let epubTocFlat = [];
  let epubCurrentChapterLabel = "";
  const renderedEpubHighlightCfis = new Set();
  let epubEventsBound = false;
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

  // Estado das Anotações e Desenhos (v1.8.0 Colaborativo)
  let currentUserId = null;
  let layerViewMode = "all_visible"; // 'mine', 'all_visible', 'hidden'
  let bookAnnotations = { highlights: [], notes: [], drawing_pages: [], bookmarks: [] };
  let currentSelectionData = null; // { page, text, rects, context }
  let currentHighlightColor = "#fef08a";
  let editingNoteId = null;
  let editingNoteHighlightId = null;
  let editingNoteEpubCfi = null;
  let editingNoteChapterLabel = null;
  const pageDrawingsBundleCache = new Map(); // pageNum -> { own_drawing, public_drawings }
  const hiddenPublicDrawingAuthors = new Set();
  const pdfPageTextCache = new Map(); // pageNum -> lowercase text
  let pdfSearchMatches = []; // [{ page, count }]
  let pdfSearchMatchIdx = -1;
  let eligibleCollectiveReads = [];
  let currentBookTitle = "";
  let shareQuoteContext = null;
  let isSubmittingShareQuote = false;
  let readerToastTimer = null;

  const MAX_SHARED_QUOTE_LEN = 1500;
  const btnShareSelectionClub = document.getElementById("btn-share-selection-club");
  const modalShareQuote = document.getElementById("modal-share-collective-quote");
  const formShareQuote = document.getElementById("form-share-collective-quote");
  const shareQuoteBookLabel = document.getElementById("share-quote-book-label");
  const shareQuoteLocatorLabel = document.getElementById("share-quote-locator-label");
  const shareQuoteReadSelect = document.getElementById("share-quote-read-select");
  const shareQuoteText = document.getElementById("share-quote-text");
  const shareQuoteCharCounter = document.getElementById("share-quote-char-counter");
  const shareQuoteComment = document.getElementById("share-quote-comment");
  const shareQuoteSpoiler = document.getElementById("share-quote-spoiler");
  const shareQuoteError = document.getElementById("share-quote-error");
  const btnCloseShareQuote = document.getElementById("btn-close-share-quote");
  const btnCancelShareQuote = document.getElementById("btn-cancel-share-quote");
  const btnSubmitShareQuote = document.getElementById("btn-submit-share-quote");
  const readerToastEl = document.getElementById("reader-toast");

  function showReaderToast(message, type = "info") {
    if (!readerToastEl || !message) return;
    readerToastEl.textContent = message;
    readerToastEl.className = `reader-toast ${type}`;
    readerToastEl.style.display = "block";
    if (readerToastTimer) clearTimeout(readerToastTimer);
    readerToastTimer = setTimeout(() => {
      readerToastEl.style.display = "none";
    }, 4500);
  }

  function parseHashLocator() {
    const raw = (window.location.hash || "").replace(/^#/, "").trim();
    if (!raw) return { page: null, cfi: null, invalid: false };
    const params = new URLSearchParams(raw);
    if (params.has("page")) {
      const pVal = params.get("page");
      if (/^\d+$/.test(pVal) && parseInt(pVal, 10) >= 1) {
        return { page: parseInt(pVal, 10), cfi: null, invalid: false };
      }
      return { page: null, cfi: null, invalid: true };
    }
    if (params.has("cfi")) {
      const cVal = (params.get("cfi") || "").trim();
      if (cVal.startsWith("epubcfi(") && cVal.endsWith(")") && !cVal.includes("<") && !cVal.includes(">")) {
        return { page: null, cfi: cVal, invalid: false };
      }
      return { page: null, cfi: null, invalid: true };
    }
    return { page: null, cfi: null, invalid: false };
  }

  const hashLocator = parseHashLocator();

  async function loadEligibleCollectiveReads() {
    try {
      const res = await fetch(`/api/books/${encodeURIComponent(bookId)}/collective-reads`);
      if (!res.ok) return;
      const data = await res.json();
      eligibleCollectiveReads = Array.isArray(data.reads) ? data.reads : [];
      if (bookAnnotations && (bookAnnotations.notes || []).length > 0) {
        renderStudyDrawer();
      }
    } catch (_) {
      eligibleCollectiveReads = [];
    }
  }

  function updateShareQuoteCounter() {
    if (!shareQuoteText || !shareQuoteCharCounter) return;
    const len = shareQuoteText.value.length;
    shareQuoteCharCounter.textContent = `${len} / ${MAX_SHARED_QUOTE_LEN}`;
    shareQuoteCharCounter.style.color = len > MAX_SHARED_QUOTE_LEN ? "var(--danger)" : "var(--text-muted)";
  }

  function closeShareQuoteModal() {
    if (!modalShareQuote) return;
    modalShareQuote.classList.remove("active");
    shareQuoteContext = null;
    isSubmittingShareQuote = false;
    if (shareQuoteText) shareQuoteText.value = "";
    if (shareQuoteComment) shareQuoteComment.value = "";
    if (shareQuoteSpoiler) shareQuoteSpoiler.checked = false;
    if (shareQuoteError) {
      shareQuoteError.style.display = "none";
      shareQuoteError.textContent = "";
    }
    if (btnSubmitShareQuote) btnSubmitShareQuote.disabled = false;
  }

  function openShareQuoteModal({ quoteText = "", commentText = "", pageNumber = null, epubCfi = null, noteId = null, originalNoteQuote = null }) {
    if (!modalShareQuote || eligibleCollectiveReads.length === 0) return;
    shareQuoteContext = { pageNumber, epubCfi, noteId, originalNoteQuote };
    if (shareQuoteBookLabel) shareQuoteBookLabel.textContent = currentBookTitle || "Obra atual";
    if (shareQuoteLocatorLabel) {
      shareQuoteLocatorLabel.textContent = pageNumber ? `· Página ${pageNumber}` : epubCfi ? "· Posição EPUB" : "";
    }
    if (shareQuoteReadSelect) {
      shareQuoteReadSelect.replaceChildren();
      eligibleCollectiveReads.forEach((r) => {
        const opt = document.createElement("option");
        opt.value = r.read_id;
        opt.textContent = `${r.club_name} — ${r.title}`;
        shareQuoteReadSelect.appendChild(opt);
      });
    }
    if (shareQuoteText) shareQuoteText.value = quoteText || "";
    if (shareQuoteComment) shareQuoteComment.value = commentText || "";
    if (shareQuoteSpoiler) shareQuoteSpoiler.checked = false;
    if (shareQuoteError) {
      shareQuoteError.style.display = "none";
      shareQuoteError.textContent = "";
    }
    updateShareQuoteCounter();
    modalShareQuote.classList.add("active");
    setTimeout(() => shareQuoteText && shareQuoteText.focus(), 60);
  }

  if (shareQuoteText) shareQuoteText.addEventListener("input", updateShareQuoteCounter);
  if (btnCloseShareQuote) btnCloseShareQuote.addEventListener("click", closeShareQuoteModal);
  if (btnCancelShareQuote) btnCancelShareQuote.addEventListener("click", closeShareQuoteModal);
  if (btnShareSelectionClub) {
    btnShareSelectionClub.addEventListener("click", () => {
      if (!currentSelectionData) return;
      const sel = { ...currentSelectionData };
      hideSelectionToolbar();
      window.getSelection()?.removeAllRanges();
      openShareQuoteModal({
        quoteText: sel.text || "",
        pageNumber: sel.page || null,
        epubCfi: sel.epubCfi || null,
      });
    });
  }

  if (formShareQuote) {
    formShareQuote.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (isSubmittingShareQuote || !shareQuoteContext) return;
      if (shareQuoteError) shareQuoteError.style.display = "none";

      const readId = shareQuoteReadSelect ? shareQuoteReadSelect.value : "";
      const textVal = (shareQuoteText ? shareQuoteText.value : "").trim();
      const commentVal = (shareQuoteComment ? shareQuoteComment.value : "").trim();

      if (!readId) {
        shareQuoteError.textContent = "Selecione uma leitura coletiva de destino.";
        shareQuoteError.style.display = "block";
        return;
      }
      if (!textVal) {
        shareQuoteError.textContent = "O trecho não pode ser vazio.";
        shareQuoteError.style.display = "block";
        return;
      }
      if (textVal.length > MAX_SHARED_QUOTE_LEN) {
        shareQuoteError.textContent = `O trecho excede o limite de ${MAX_SHARED_QUOTE_LEN} caracteres (${textVal.length}). Reduza o texto antes de compartilhar.`;
        shareQuoteError.style.display = "block";
        return;
      }

      const payload = {
        comment_text: commentVal || null,
        is_spoiler: Boolean(shareQuoteSpoiler && shareQuoteSpoiler.checked),
      };
      if (shareQuoteContext.noteId && textVal === (shareQuoteContext.originalNoteQuote || "").trim()) {
        payload.note_id = shareQuoteContext.noteId;
      } else {
        payload.quote_text = textVal;
        if (shareQuoteContext.pageNumber) payload.page_number = shareQuoteContext.pageNumber;
        if (shareQuoteContext.epubCfi) payload.epub_cfi = shareQuoteContext.epubCfi;
      }

      isSubmittingShareQuote = true;
      if (btnSubmitShareQuote) btnSubmitShareQuote.disabled = true;

      try {
        const res = await fetch(`/api/collective-reads/${encodeURIComponent(readId)}/quotes`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => null);
        if (!res.ok) {
          throw new Error((data && data.detail) || `Erro HTTP ${res.status}`);
        }
        closeShareQuoteModal();
        showReaderToast("Citação compartilhada na leitura coletiva!", "success");
      } catch (err) {
        if (shareQuoteError) {
          shareQuoteError.textContent = err.message || "Erro ao compartilhar citação.";
          shareQuoteError.style.display = "block";
        }
        isSubmittingShareQuote = false;
        if (btnSubmitShareQuote) btnSubmitShareQuote.disabled = false;
      }
    });
  }

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
    if (readingProgressFill) {
      const pct = totalPages > 0 ? (currentPage / totalPages) * 100 : 0;
      readingProgressFill.style.width = `${pct}%`;
    }
    if (typeof updateBookmarkButtonState === "function") {
      updateBookmarkButtonState();
    }
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

  // --- Progresso (serializado contra concorrência e respostas fora de ordem) ---
  let progressSaveChain = Promise.resolve();
  let progressSaveSeq = 0;

  async function persistProgress(pageToSave, locator = null) {
    if (isRestoringProgress) return;
    clearTimeout(saveProgressTimeout);
    const mySeq = ++progressSaveSeq;
    const payload = {
      current_page: pageToSave,
      total_pages: totalPages,
    };
    const loc = locator || epubCfi;
    if (loc) {
      payload.progress_locator = loc;
    }

    const runSave = async () => {
      if (mySeq !== progressSaveSeq) return;
      const abortCtrl = typeof AbortController !== "undefined" ? new AbortController() : null;
      const timerId = abortCtrl ? setTimeout(() => abortCtrl.abort(), 8000) : null;
      try {
        const headers = { "Content-Type": "application/json" };
        const csrfToken = getCsrfToken();
        if (csrfToken) {
          headers["X-CSRF-Token"] = csrfToken;
        }
        await fetch(`/api/books/${bookId}/progress`, {
          method: "POST",
          headers,
          body: JSON.stringify(payload),
          signal: abortCtrl ? abortCtrl.signal : undefined,
        });
        if (mySeq === progressSaveSeq) {
          showSavedIndicator();
        }
      } catch (e) {
        console.warn("Erro ao salvar progresso:", e);
      } finally {
        if (timerId) clearTimeout(timerId);
      }
    };

    progressSaveChain = progressSaveChain.then(runSave, runSave);
    return progressSaveChain;
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

  function getLayerViewStorageKey() {
    return `lectoria_layer_view_mode_u${currentUserId || "anon"}`;
  }

  function applyLayerViewMode(mode, persist = true) {
    const valid = ["mine", "all_visible", "hidden"].includes(mode) ? mode : "all_visible";
    layerViewMode = valid;
    if (persist) {
      try {
        localStorage.setItem(getLayerViewStorageKey(), valid);
      } catch (_) {}
    }
    if (layerViewModeSelect) layerViewModeSelect.value = valid;
    if (mobileLayerViewSelect) mobileLayerViewSelect.value = valid;
    viewportEl.classList.toggle("layers-hidden", valid === "hidden");

    reloadBookAnnotations().then(() => {
      refreshAllDrawingLayersFromCache();
    });
  }

  function confirmFirstPublicShare(itemLabel = "anotação") {
    const key = `lectoria_publish_notice_seen_u${currentUserId || "anon"}`;
    try {
      if (sessionStorage.getItem(key) === "1") return true;
    } catch (_) {}
    const ok = window.confirm(
      `Ao tornar esta ${itemLabel} pública, outros usuários autenticados com acesso a este livro poderão visualizar o conteúdo e o seu nome de exibição.\n\nDeseja publicar agora?`
    );
    if (ok) {
      try { sessionStorage.setItem(key, "1"); } catch (_) {}
    }
    return ok;
  }

  if (layerViewModeSelect) {
    layerViewModeSelect.addEventListener("change", () => {
      applyLayerViewMode(layerViewModeSelect.value, true);
    });
  }
  if (mobileLayerViewSelect) {
    mobileLayerViewSelect.addEventListener("change", () => {
      applyLayerViewMode(mobileLayerViewSelect.value, true);
    });
  }

  // --- Carregamento de Anotações do Servidor (Camadas da Página) ---
  async function reloadBookAnnotations() {
    const fetchScope = layerViewMode === "mine" ? "mine" : "all_visible";
    try {
      const res = await fetch(`/api/books/${bookId}/annotations?scope=${encodeURIComponent(fetchScope)}`);
      if (res.ok) {
        bookAnnotations = await res.json();
        updateBookmarkButtonState();
        renderHighlightsOnActivePages();
        renderNotePinsOnActivePages();
        if (typeof loadDrawerAnnotations === "function") {
          await loadDrawerAnnotations({ append: false });
        }
      }
    } catch (e) {
      console.warn("Erro ao carregar anotações:", e);
    }
  }

  // --- Renderização no Modo Página Única (protegida por geração contra corrida assíncrona) ---
  let singleRenderGen = 0;

  async function renderSinglePage(num, options = {}) {
    if (!pdfDoc) return;
    const myRenderGen = ++singleRenderGen;
    const shouldResetScroll = options.resetScroll !== false;
    if (singleRenderTask) {
      try {
        singleRenderTask.cancel();
      } catch (_) {}
      singleRenderTask = null;
    }

    // Garante que o topo da nova página esteja perfeitamente alinhado (evita que o PDF desça sozinho)
    if (shouldResetScroll) {
      viewportEl.scrollTop = 0;
      viewportEl.scrollLeft = 0;
    }

    try {
      const page = await pdfDoc.getPage(num);
      if (myRenderGen !== singleRenderGen) return;

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

      const task = page.render({ canvasContext: ctx, viewport: viewport });
      singleRenderTask = task;
      await task.promise;
      if (singleRenderTask === task) {
        singleRenderTask = null;
      }
      if (myRenderGen !== singleRenderGen) return;

      // Mantém topo em 0 caso o redimensionamento do canvas tenha induzido deslocamento
      if (shouldResetScroll && viewportEl.scrollTop !== 0) {
        viewportEl.scrollTop = 0;
      }

      // Renderiza Camada de Texto para permitir seleção
      const textContent = await page.getTextContent();
      if (myRenderGen !== singleRenderGen) return;

      singleTextLayer.innerHTML = "";
      singleTextLayer.style.width = cssWidth;
      singleTextLayer.style.height = cssHeight;
      singleTextLayer.style.setProperty("--scale-factor", currentScale);
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

      // Renderiza Marcações, Pins de Notas e Desenhos (Camada Pública + Camada Pessoal)
      renderHighlightsForPage(num, singleHighlightsLayer);
      await setupDrawingLayerForPage(num, singleDrawingLayer, singlePublicDrawingLayer);
      if (myRenderGen !== singleRenderGen) return;
      renderNotePinsForPage(num, singleNotePinsLayer);

      if (document.activeElement !== pageNumInput) {
        pageNumInput.value = num;
      }
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

    const fragment = document.createDocumentFragment();
    for (let p = 1; p <= totalPages; p++) {
      const slot = document.createElement("div");
      slot.className = "page-slot page-container";
      slot.id = `page-slot-${p}`;
      slot.setAttribute("data-page-num", p);
      slot.style.width = `${slotWidth}px`;
      slot.style.height = `${slotHeight}px`;
      slot.innerHTML = `<div class="page-slot-placeholder">Página ${p}</div>`;
      fragment.appendChild(slot);
    }
    continuousWrapper.appendChild(fragment);

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
        const isTablet = window.innerWidth > 768 && window.innerWidth <= 1280;
        const dockMargin = isTablet ? 72 : 0;
        const hPadding = isMobile ? 8 : 48;
        scale = Math.max(viewportEl.clientWidth - (hPadding + dockMargin), 100) / unscaledViewport.width;
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

      const pubDLayer = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      pubDLayer.setAttribute("class", "public-drawing-layer");
      pubDLayer.setAttribute("aria-hidden", "true");
      slotElement.appendChild(pubDLayer);

      const dLayer = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      dLayer.setAttribute("class", "drawing-layer");
      slotElement.appendChild(dLayer);

      const pinsLayer = document.createElement("div");
      pinsLayer.className = "page-note-pins-layer";
      slotElement.appendChild(pinsLayer);

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
      await setupDrawingLayerForPage(pageNum, dLayer, pubDLayer);
      renderNotePinsForPage(pageNum, pinsLayer);
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

  function closeHighlightPopover() {
    if (highlightPopover) highlightPopover.style.display = "none";
  }

  function closePageNotePopover() {
    if (pageNotePopover) pageNotePopover.style.display = "none";
  }

  if (btnCloseHlPopover) btnCloseHlPopover.addEventListener("click", closeHighlightPopover);
  if (btnCloseNotePopover) btnCloseNotePopover.addEventListener("click", closePageNotePopover);

  function positionAnnotationPopover(popoverEl, anchorRect) {
    if (!popoverEl || !anchorRect) return;
    popoverEl.style.display = "block";
    const popW = Math.min(popoverEl.offsetWidth || 320, window.innerWidth - 24);
    const popH = popoverEl.offsetHeight || 220;
    const left = Math.min(Math.max(12, anchorRect.left), Math.max(12, window.innerWidth - popW - 12));
    let top = anchorRect.bottom + 8;
    if (top + popH > window.innerHeight - 12) {
      top = Math.max(56, anchorRect.top - popH - 8);
    }
    popoverEl.style.left = `${left}px`;
    popoverEl.style.top = `${top}px`;
  }

  function openHighlightPopover(h, anchorEl) {
    if (!highlightPopover) return;
    closePageNotePopover();

    const isOwn = h.is_own !== false;
    const isPublic = h.visibility === "public";

    if (hlPopAvatar) {
      hlPopAvatar.style.backgroundColor = h.author_avatar_color || "#d4a853";
    }
    if (hlPopAuthorName) {
      hlPopAuthorName.textContent = isOwn ? "Você" : (h.author_display_name || "Leitor");
    }
    if (hlPopVisBadge) {
      hlPopVisBadge.textContent = isPublic ? "🌐 Público" : "🔒 Privado";
      hlPopVisBadge.classList.toggle("is-public", isPublic);
    }
    if (hlPopQuote) {
      hlPopQuote.textContent = `"${h.selected_text || ""}"`;
      hlPopQuote.style.borderLeftColor = h.color || "#fef08a";
    }

    const linkedNote = (bookAnnotations.notes || []).find(
      n => n.highlight_id && String(n.highlight_id) === String(h.id)
    );
    if (hlPopNoteBox) {
      if (linkedNote && (linkedNote.is_own !== false || linkedNote.visibility === "public")) {
        const noteAuthor = linkedNote.is_own !== false ? "Sua nota" : `Nota de ${linkedNote.author_display_name || "Leitor"}`;
        hlPopNoteBox.textContent = `${noteAuthor}: ${linkedNote.content}`;
        hlPopNoteBox.style.display = "block";
      } else {
        hlPopNoteBox.textContent = "";
        hlPopNoteBox.style.display = "none";
      }
    }

    if (hlPopActions) {
      hlPopActions.innerHTML = "";
      if (isOwn) {
        const btnVis = document.createElement("button");
        btnVis.type = "button";
        btnVis.className = "btn btn-sm btn-outline";
        btnVis.textContent = isPublic ? "Tornar Privado" : "Tornar Público";
        btnVis.addEventListener("click", async () => {
          const nextVis = isPublic ? "private" : "public";
          if (nextVis === "public" && !confirmFirstPublicShare("marcação")) return;
          await toggleHighlightVisibility(h.id, nextVis);
          closeHighlightPopover();
        });
        hlPopActions.appendChild(btnVis);

        const btnNote = document.createElement("button");
        btnNote.type = "button";
        btnNote.className = "btn btn-sm btn-primary";
        btnNote.textContent = linkedNote && linkedNote.is_own !== false ? "Editar Nota" : "Adicionar Nota";
        btnNote.addEventListener("click", () => {
          closeHighlightPopover();
          if (linkedNote && linkedNote.is_own !== false) {
            openNoteEditorModal({
              title: "Editar Anotação",
              page: linkedNote.page,
              existingNoteId: linkedNote.id,
              initialContent: linkedNote.content,
              initialVisibility: linkedNote.visibility || "private",
            });
          } else {
            openNoteEditorModal({
              title: "Nova Nota no Destaque",
              page: h.page,
              highlightId: h.id,
              quote: h.selected_text,
            });
          }
        });
        hlPopActions.appendChild(btnNote);

        let confirmDeletePending = false;
        const btnDel = document.createElement("button");
        btnDel.type = "button";
        btnDel.className = "btn btn-sm btn-danger";
        btnDel.textContent = "Excluir";
        btnDel.addEventListener("click", async () => {
          if (!confirmDeletePending) {
            confirmDeletePending = true;
            btnDel.textContent = "Confirmar Exclusão";
            return;
          }
          await deleteHighlight(h.id);
          closeHighlightPopover();
        });
        hlPopActions.appendChild(btnDel);
      } else {
        const readonlySpan = document.createElement("span");
        readonlySpan.style.fontSize = "0.74rem";
        readonlySpan.style.color = "var(--text-muted)";
        readonlySpan.textContent = "Destaque público somente leitura";
        hlPopActions.appendChild(readonlySpan);
      }

      if (window.LectoriaReaderKnowledge && h && h.id) {
        const btnKnowledge = document.createElement("button");
        btnKnowledge.type = "button";
        btnKnowledge.className = "btn btn-sm btn-outline btn-open-knowledge";
        btnKnowledge.textContent = "Conhecimento";
        btnKnowledge.title = "Gerenciar conceitos, conexões e backlinks no Atlas";
        btnKnowledge.addEventListener("click", () => {
          closeHighlightPopover();
          window.LectoriaReaderKnowledge.openForAnnotation({
            entityType: "highlight",
            entityId: h.id,
            excerpt: h.selected_text || "",
            isOwn: isOwn,
            authorName: isOwn ? "" : (h.author_display_name || "Leitor"),
            bookId: bookId,
            page: h.page || null,
            epubCfi: h.epub_cfi || null,
            chapterLabel: h.chapter_label || "",
          }, anchorEl);
        });
        hlPopActions.appendChild(btnKnowledge);
      }
    }

    positionAnnotationPopover(highlightPopover, anchorEl.getBoundingClientRect());
  }

  function openPageNotePopover(n, anchorEl) {
    if (!pageNotePopover) return;
    closeHighlightPopover();

    const isOwn = n.is_own !== false;
    const isPublic = n.visibility === "public";

    if (notePopAvatar) {
      notePopAvatar.style.backgroundColor = n.author_avatar_color || "#d4a853";
    }
    if (notePopAuthorName) {
      notePopAuthorName.textContent = isOwn ? "Você" : (n.author_display_name || "Leitor");
    }
    if (notePopVisBadge) {
      notePopVisBadge.textContent = isPublic ? "🌐 Pública" : "🔒 Privada";
      notePopVisBadge.classList.toggle("is-public", isPublic);
    }
    if (notePopMeta) {
      const dateStr = formatTimestamp(n.updated_at || n.created_at);
      if (n.epub_cfi) {
        const chapPart = n.chapter_label ? `${n.chapter_label}` : "Posição EPUB";
        notePopMeta.textContent = `${chapPart}${dateStr ? ` · ${dateStr}` : ""}`;
      } else {
        notePopMeta.textContent = `Página ${n.page}${dateStr ? ` · ${dateStr}` : ""}`;
      }
    }
    if (notePopContent) {
      notePopContent.textContent = n.content || "";
    }

    if (notePopActions) {
      notePopActions.innerHTML = "";
      if (isOwn) {
        const btnVis = document.createElement("button");
        btnVis.type = "button";
        btnVis.className = "btn btn-sm btn-outline";
        btnVis.textContent = isPublic ? "Tornar Privada" : "Tornar Pública";
        btnVis.addEventListener("click", async () => {
          const nextVis = isPublic ? "private" : "public";
          if (nextVis === "public" && !confirmFirstPublicShare("anotação")) return;
          await toggleNoteVisibility(n.id, nextVis);
          closePageNotePopover();
        });
        notePopActions.appendChild(btnVis);

        const btnEdit = document.createElement("button");
        btnEdit.type = "button";
        btnEdit.className = "btn btn-sm btn-primary";
        btnEdit.textContent = "Editar";
        btnEdit.addEventListener("click", () => {
          closePageNotePopover();
          openNoteEditorModal({
            title: "Editar Anotação",
            page: n.page,
            existingNoteId: n.id,
            initialContent: n.content,
            initialVisibility: n.visibility || "private",
          });
        });
        notePopActions.appendChild(btnEdit);

        let confirmDeletePending = false;
        const btnDel = document.createElement("button");
        btnDel.type = "button";
        btnDel.className = "btn btn-sm btn-danger";
        btnDel.textContent = "Excluir";
        btnDel.addEventListener("click", async () => {
          if (!confirmDeletePending) {
            confirmDeletePending = true;
            btnDel.textContent = "Confirmar Exclusão";
            return;
          }
          await deleteNoteById(n.id);
          closePageNotePopover();
        });
        notePopActions.appendChild(btnDel);
      } else {
        const readonlySpan = document.createElement("span");
        readonlySpan.style.fontSize = "0.74rem";
        readonlySpan.style.color = "var(--text-muted)";
        readonlySpan.textContent = "Anotação pública somente leitura";
        notePopActions.appendChild(readonlySpan);
      }

      if (window.LectoriaReaderKnowledge && n && n.id) {
        const btnKnowledge = document.createElement("button");
        btnKnowledge.type = "button";
        btnKnowledge.className = "btn btn-sm btn-outline btn-open-knowledge";
        btnKnowledge.textContent = "Conhecimento";
        btnKnowledge.title = "Gerenciar conceitos, conexões e backlinks no Atlas";
        btnKnowledge.addEventListener("click", () => {
          closePageNotePopover();
          window.LectoriaReaderKnowledge.openForAnnotation({
            entityType: "note",
            entityId: n.id,
            excerpt: n.content || "",
            isOwn: isOwn,
            authorName: isOwn ? "" : (n.author_display_name || "Leitor"),
            bookId: bookId,
            page: n.page || null,
            epubCfi: n.epub_cfi || null,
            chapterLabel: n.chapter_label || "",
          }, anchorEl);
        });
        notePopActions.appendChild(btnKnowledge);
      }
    }

    positionAnnotationPopover(pageNotePopover, anchorEl.getBoundingClientRect());
  }

  // --- Renderização de Destaques (Highlights) ---
  function renderHighlightsForPage(pageNum, container) {
    if (!container) return;
    container.innerHTML = "";
    if (layerViewMode === "hidden") return;

    const pageHighlights = (bookAnnotations.highlights || []).filter(h => {
      if (h.page !== pageNum) return false;
      if (layerViewMode === "mine") return h.is_own !== false;
      return true;
    });

    pageHighlights.forEach(h => {
      const isOwn = h.is_own !== false;
      (h.rects || []).forEach(r => {
        const box = document.createElement("div");
        box.className = isOwn ? "highlight-box" : "highlight-box highlight-public-other";
        box.setAttribute("data-highlight-id", h.id);
        box.setAttribute("data-is-own", isOwn ? "true" : "false");
        if (!isOwn && h.author_display_name) {
          box.setAttribute("data-author-name", h.author_display_name);
        }
        box.style.left = `${r.x * 100}%`;
        box.style.top = `${r.y * 100}%`;
        box.style.width = `${r.w * 100}%`;
        box.style.height = `${r.h * 100}%`;
        box.style.backgroundColor = h.color || "#fef08a";
        const authorLabel = isOwn ? "Você" : (h.author_display_name || "Leitor");
        box.title = `${authorLabel}: "${(h.selected_text || "").slice(0, 80)}"`;

        box.addEventListener("click", (e) => {
          e.stopPropagation();
          openHighlightPopover(h, box);
        });

        container.appendChild(box);
      });
    });
  }

  function renderHighlightsOnActivePages() {
    if (bookFormat === "epub") {
      renderEpubHighlights();
      return;
    }
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

  // --- Renderização de Pins de Notas e Selo de Desenhos Públicos na Página ---
  function renderNotePinsForPage(pageNum, container) {
    if (!container) return;
    container.innerHTML = "";
    if (layerViewMode === "hidden") return;

    const stack = document.createElement("div");
    stack.className = "page-note-pins-stack";

    // 1. Selo discreto de desenhos públicos de outros autores (quando visível)
    if (layerViewMode === "all_visible") {
      const bundle = pageDrawingsBundleCache.get(pageNum);
      const pubDrawings = (bundle && Array.isArray(bundle.public_drawings)) ? bundle.public_drawings : [];
      const activePubDrawings = pubDrawings.filter(d => !d.is_own && Array.isArray(d.strokes) && d.strokes.length > 0);
      if (activePubDrawings.length > 0) {
        const badge = document.createElement("button");
        badge.type = "button";
        badge.className = "page-public-drawings-badge";
        const names = activePubDrawings.map(d => d.author_display_name || "Leitor");
        const anyHidden = activePubDrawings.some(d => hiddenPublicDrawingAuthors.has(String(d.author_user_id || d.user_id || "")));
        const dot = document.createElement("span");
        dot.className = "author-avatar-dot";
        dot.style.backgroundColor = activePubDrawings[0].author_avatar_color || "#38bdf8";
        const label = document.createElement("span");
        label.textContent = anyHidden
          ? `Desenhos ocultos (${names.join(", ")})`
          : `Desenhos: ${names.join(", ")}`;
        badge.title = "Clique para ocultar ou mostrar desenhos públicos desta página nesta sessão";
        badge.appendChild(dot);
        badge.appendChild(label);
        badge.addEventListener("click", (e) => {
          e.stopPropagation();
          activePubDrawings.forEach(d => {
            const uidKey = String(d.author_user_id || d.user_id || "");
            if (!uidKey) return;
            if (anyHidden) hiddenPublicDrawingAuthors.delete(uidKey);
            else hiddenPublicDrawingAuthors.add(uidKey);
          });
          refreshAllDrawingLayersFromCache();
          renderNotePinsOnActivePages();
        });
        stack.appendChild(badge);
      }
    }

    // 2. Pins de notas da página (próprias e públicas conforme layerViewMode)
    const pageNotes = (bookAnnotations.notes || []).filter(n => {
      if (n.page !== pageNum) return false;
      if (layerViewMode === "mine") return n.is_own !== false;
      return true;
    });

    pageNotes.forEach(n => {
      const isOwn = n.is_own !== false;
      const pin = document.createElement("button");
      pin.type = "button";
      pin.className = isOwn ? "page-note-pin" : "page-note-pin is-public-other";
      pin.setAttribute("data-note-id", n.id);
      pin.setAttribute("data-is-own", isOwn ? "true" : "false");

      const dot = document.createElement("span");
      dot.className = "author-avatar-dot";
      dot.style.backgroundColor = isOwn ? "#d4a853" : (n.author_avatar_color || "#38bdf8");

      const textSpan = document.createElement("span");
      textSpan.className = "page-note-pin-text";
      const prefix = isOwn ? (n.visibility === "public" ? "🌐 " : "📝 ") : `${n.author_display_name || "Leitor"}: `;
      textSpan.textContent = `${prefix}${(n.content || "").slice(0, 28)}`;

      pin.title = `${isOwn ? "Sua nota" : (n.author_display_name || "Leitor")}: ${n.content || ""}`;
      pin.appendChild(dot);
      pin.appendChild(textSpan);

      pin.addEventListener("click", (e) => {
        e.stopPropagation();
        openPageNotePopover(n, pin);
      });

      stack.appendChild(pin);
    });

    if (stack.childElementCount > 0) {
      container.appendChild(stack);
    }
  }

  function renderNotePinsOnActivePages() {
    if (bookFormat === "epub") {
      renderEpubNotePins();
      return;
    }
    if (viewMode === "single") {
      renderNotePinsForPage(currentPage, singleNotePinsLayer);
    } else {
      document.querySelectorAll(".page-slot").forEach(slot => {
        const p = parseInt(slot.getAttribute("data-page-num"), 10);
        const pinsLayer = slot.querySelector(".page-note-pins-layer");
        if (pinsLayer) renderNotePinsForPage(p, pinsLayer);
      });
    }
  }

  async function toggleHighlightVisibility(highlightId, visibility) {
    try {
      const res = await fetch(`/api/books/${bookId}/highlights/${highlightId}/visibility`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ visibility }),
      });
      if (res.ok) {
        showReaderToast(visibility === "public" ? "Destaque publicado no livro." : "Destaque tornado privado.", "info");
        await reloadBookAnnotations();
      } else {
        showReaderToast("Não foi possível alterar a visibilidade do destaque.", "error");
      }
    } catch {
      showReaderToast("Erro ao alterar visibilidade do destaque.", "error");
    }
  }

  async function toggleNoteVisibility(noteId, visibility) {
    try {
      const res = await fetch(`/api/books/${bookId}/notes/${noteId}/visibility`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ visibility }),
      });
      if (res.ok) {
        showReaderToast(visibility === "public" ? "Anotação publicada no livro." : "Anotação tornada privada.", "info");
        await reloadBookAnnotations();
      } else {
        showReaderToast("Não foi possível alterar a visibilidade da anotação.", "error");
      }
    } catch {
      showReaderToast("Erro ao alterar visibilidade da anotação.", "error");
    }
  }

  async function deleteNoteById(noteId) {
    try {
      const res = await fetch(`/api/books/${bookId}/notes/${noteId}`, { method: "DELETE" });
      if (res.ok) {
        showReaderToast("Anotação removida.", "info");
        await reloadBookAnnotations();
      }
    } catch {
      showReaderToast("Erro ao excluir anotação.", "error");
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
    if (btnShareSelectionClub) {
      btnShareSelectionClub.style.display = eligibleCollectiveReads.length > 0 ? "inline-flex" : "none";
    }
    const firstRect = clientRects[0];
    const tbWidth = Math.min(selectionToolbar.offsetWidth || 260, window.innerWidth - 20);
    const maxLeft = Math.max(10, window.innerWidth - tbWidth - 10);
    const desiredLeft = firstRect.left + firstRect.width / 2 - tbWidth / 2;
    selectionToolbar.style.left = `${Math.min(maxLeft, Math.max(10, desiredLeft))}px`;
    selectionToolbar.style.top = `${Math.max(60, firstRect.top - 44)}px`;
    selectionToolbar.classList.add("active");
  }

  function hideSelectionToolbar() {
    selectionToolbar.classList.remove("active");
    if (btnShareSelectionClub) btnShareSelectionClub.style.display = "none";
    currentSelectionData = null;
  }

  function clearAllSelections() {
    window.getSelection()?.removeAllRanges();
    if (bookFormat === "epub" && epubRendition) {
      try {
        (epubRendition.getContents() || []).forEach(c => {
          c.window?.getSelection()?.removeAllRanges();
        });
      } catch (_) {}
    }
  }

  async function saveHighlightFromSelection(selData, color = currentHighlightColor) {
    if (!selData) return null;
    try {
      const payload = {
        page: Math.max(1, parseInt(selData.page, 10) || currentPage || 1),
        color: color,
        rects: Array.isArray(selData.rects) ? selData.rects : [],
        selected_text: selData.text,
        text_context: selData.context || null,
      };
      if (selData.epubCfi) {
        payload.epub_cfi = selData.epubCfi;
      }
      if (selData.chapterLabel) {
        payload.chapter_label = selData.chapterLabel;
      }
      const res = await fetch(`/api/books/${bookId}/highlights`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        const data = await res.json();
        clearAllSelections();
        hideSelectionToolbar();
        await reloadBookAnnotations();
        return data.highlight;
      }
    } catch (e) {
      showReaderToast("Erro ao salvar marcação.", "error");
    }
    return null;
  }

  // Modo Marca-Texto Ativo: ao soltar o ponteiro com texto selecionado, destaca automaticamente
  async function handleHighlightToolRelease() {
    if (currentTool !== "highlight" || bookFormat !== "pdf") return;
    handleTextSelection();
    if (!currentSelectionData) return;
    const selCopy = { ...currentSelectionData };
    const created = await saveHighlightFromSelection(selCopy, currentHighlightColor);
    if (created) {
      showReaderToast("Destaque aplicado (clique nele para anotar ou publicar).", "info");
    }
  }

  viewportEl.addEventListener("mouseup", () => {
    if (currentTool === "highlight") {
      setTimeout(handleHighlightToolRelease, 15);
    }
  });
  viewportEl.addEventListener("touchend", () => {
    if (currentTool === "highlight") {
      setTimeout(handleHighlightToolRelease, 60);
    }
  });

  // Ações de Cores de Destaque
  document.querySelectorAll(".highlight-color-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      if (!currentSelectionData) return;
      const color = btn.getAttribute("data-color");
      currentHighlightColor = color;
      await saveHighlightFromSelection(currentSelectionData, color);
    });
  });

  // Botão "Nota a partir da seleção"
  btnAddNoteFromSelection.addEventListener("click", async () => {
    if (!currentSelectionData) return;
    const selData = { ...currentSelectionData };
    clearAllSelections();
    hideSelectionToolbar();

    // Cria o destaque primeiro e abre o modal de anotação vinculado
    try {
      const hlPayload = {
        page: Math.max(1, parseInt(selData.page, 10) || currentPage || 1),
        color: "#fef08a",
        rects: Array.isArray(selData.rects) ? selData.rects : [],
        selected_text: selData.text,
        text_context: selData.context || null,
      };
      if (selData.epubCfi) hlPayload.epub_cfi = selData.epubCfi;
      if (selData.chapterLabel) hlPayload.chapter_label = selData.chapterLabel;

      const res = await fetch(`/api/books/${bookId}/highlights`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(hlPayload),
      });

      if (res.ok) {
        const createdH = (await res.json()).highlight;
        await reloadBookAnnotations();
        openNoteEditorModal({
          title: "Adicionar Anotação ao Trecho",
          page: hlPayload.page,
          highlightId: createdH.id,
          quote: selData.text,
          epubCfi: selData.epubCfi || null,
          chapterLabel: selData.chapterLabel || null,
        });
      }
    } catch (e) {
      alert("Erro ao criar destaque para anotação.");
    }
  });

  // --- Modal de Editor de Anotação ---
  function updateNoteVisibilityNotice() {
    if (!noteVisibilitySelect || !noteVisibilityNotice) return;
    noteVisibilityNotice.style.display = noteVisibilitySelect.value === "public" ? "block" : "none";
  }

  if (noteVisibilitySelect) {
    noteVisibilitySelect.addEventListener("change", updateNoteVisibilityNotice);
  }

  function openNoteEditorModal({ title, page, highlightId = null, quote = null, existingNoteId = null, initialContent = "", initialVisibility = "private", epubCfi: noteCfi = null, chapterLabel = null }) {
    editingNoteId = existingNoteId;
    editingNoteHighlightId = highlightId;
    editingNoteEpubCfi = noteCfi;
    editingNoteChapterLabel = chapterLabel;
    modalNoteTitle.textContent = title || "Anotação";
    noteContentInput.value = initialContent;
    if (noteVisibilitySelect) {
      noteVisibilitySelect.value = initialVisibility === "public" ? "public" : "private";
      updateNoteVisibilityNotice();
    }

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
    editingNoteEpubCfi = null;
    editingNoteChapterLabel = null;
    noteContentInput.value = "";
    if (noteVisibilitySelect) {
      noteVisibilitySelect.value = "private";
      updateNoteVisibilityNotice();
    }
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
    const selectedVisibility = noteVisibilitySelect ? noteVisibilitySelect.value : "private";

    try {
      if (editingNoteId) {
        // Atualiza nota existente
        const res = await fetch(`/api/books/${bookId}/notes/${editingNoteId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content, visibility: selectedVisibility }),
        });
        if (!res.ok) throw new Error();
      } else {
        // Cria nova nota (privada por padrão)
        const notePayload = {
          page: Math.max(1, targetPage),
          content,
          highlight_id: editingNoteHighlightId,
          visibility: selectedVisibility,
        };
        if (bookFormat === "epub") {
          const finalCfi = editingNoteEpubCfi || epubCfi || null;
          if (finalCfi) notePayload.epub_cfi = finalCfi;
          const finalChap = editingNoteChapterLabel || epubCurrentChapterLabel || null;
          if (finalChap) notePayload.chapter_label = finalChap;
        }
        const res = await fetch(`/api/books/${bookId}/notes`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(notePayload),
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
    if (bookFormat === "epub") {
      openNoteEditorModal({
        title: `Nova Anotação${epubCurrentChapterLabel ? ` (${epubCurrentChapterLabel})` : ""}`,
        page: Math.max(1, currentPage || 1),
        highlightId: null,
        quote: null,
        epubCfi: epubCfi || null,
        chapterLabel: epubCurrentChapterLabel || null,
      });
      return;
    }
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

  // --- Painel Lateral de Anotações Colaborativo (Drawer) e Marcadores (Fase 5) ---
  const drawerState = {
    scope: "all_visible",
    filter: "all",
    knowledgeFilter: "all",
    sort: "position",
    search: "",
    limit: 50,
    offset: 0,
    hasMore: false,
    loading: false,
    error: null,
    requestSeq: 0,
    abortController: null,
    summaryAbortCtrl: null,
    batchSummaryAbortCtrl: null,
    batchSummarySeq: 0,
    bookKnowledgeSummary: null,
    knowledgeSummaries: new Map(),
    data: { highlights: [], notes: [], drawing_pages: [], bookmarks: [] },
    totalCounts: { highlights: 0, notes: 0, drawings: 0, bookmarks: 0 },
  };
  let drawerSearchDebounceTimer = null;
  const drawerKnowledgeFilterBtns = document.querySelectorAll(".drawer-kf-btn");
  const drawerKnowledgeSummaryEl = document.getElementById("drawer-knowledge-summary");

  function renderDrawerKnowledgeSummaryBar() {
    if (!drawerKnowledgeSummaryEl) return;
    const s = drawerState.bookKnowledgeSummary;
    if (!s) {
      drawerKnowledgeSummaryEl.textContent = "";
      return;
    }
    drawerKnowledgeSummaryEl.textContent =
      `Atlas: ${s.distinct_concepts_count} conceito(s) · ${s.personal_links_count} conexão(ões) · ${s.connected_annotations_count} anotação(ões) conectada(s)`;
  }

  function hydrateDrawerKnowledgeStrips() {
    if (!drawerItemsList || !window.LectoriaKnowledgeApi) return;
    const kApi = window.LectoriaKnowledgeApi;
    drawerItemsList.querySelectorAll(".drawer-knowledge-strip").forEach((strip) => {
      kApi.clearNode(strip);
      const etype = strip.getAttribute("data-entity-type");
      const eid = strip.getAttribute("data-entity-id");
      if (!etype || !eid) return;
      const summary = drawerState.knowledgeSummaries.get(`${etype}:${eid}`);
      if (!summary || (!summary.is_connected && (!summary.concepts || summary.concepts.length === 0))) {
        strip.classList.add("hidden");
        return;
      }
      strip.classList.remove("hidden");

      const concepts = Array.isArray(summary.concepts) ? summary.concepts : [];
      concepts.slice(0, 4).forEach((c) => {
        const chip = kApi.createEl("span", "drawer-concept-chip");
        const dot = kApi.createEl("span", "rk-color-dot");
        dot.style.backgroundColor = kApi.sanitizeHexColor(c.color);
        chip.appendChild(dot);
        chip.appendChild(kApi.createEl("span", "drawer-concept-chip-text", c.name));
        strip.appendChild(chip);
      });
      if (concepts.length > 4) {
        strip.appendChild(kApi.createEl("span", "drawer-concept-chip drawer-concept-more", `+${concepts.length - 4}`));
      }

      const parts = [];
      if (summary.outgoing_count > 0) parts.push(`${summary.outgoing_count} saída(s)`);
      if (summary.incoming_count > 0) parts.push(`${summary.incoming_count} backlink(s)`);
      if (parts.length > 0) {
        strip.appendChild(kApi.createEl("span", "drawer-links-badge", parts.join(" · ")));
      }
    });
  }

  async function loadDrawerBookKnowledgeSummary() {
    if (!window.LectoriaKnowledgeApi || !bookId) return;
    if (drawerState.summaryAbortCtrl) {
      try { drawerState.summaryAbortCtrl.abort(); } catch (_) {}
    }
    const ctrl = new AbortController();
    drawerState.summaryAbortCtrl = ctrl;
    try {
      const data = await window.LectoriaKnowledgeApi.getBookSummary(bookId, ctrl.signal);
      if (drawerState.summaryAbortCtrl === ctrl) {
        drawerState.bookKnowledgeSummary = data;
        renderDrawerKnowledgeSummaryBar();
      }
    } catch (_) {
      // Degradação graciosa: nunca bloquear o Caderno de Estudos se o resumo do Atlas falhar
    }
  }

  async function loadDrawerBatchKnowledgeSummaries(highlightsBatch, notesBatch, { reset = false } = {}) {
    if (!window.LectoriaKnowledgeApi) return;
    if (reset) {
      drawerState.knowledgeSummaries.clear();
    }
    const entities = [];
    (highlightsBatch || []).forEach((h) => {
      if (h && h.id) entities.push({ entity_type: "highlight", entity_id: String(h.id) });
    });
    (notesBatch || []).forEach((n) => {
      if (n && n.id) entities.push({ entity_type: "note", entity_id: String(n.id) });
    });
    if (entities.length === 0) {
      hydrateDrawerKnowledgeStrips();
      return;
    }

    if (drawerState.batchSummaryAbortCtrl) {
      try { drawerState.batchSummaryAbortCtrl.abort(); } catch (_) {}
    }
    const ctrl = new AbortController();
    drawerState.batchSummaryAbortCtrl = ctrl;
    const seq = ++drawerState.batchSummarySeq;

    try {
      // Lote único de até 200 entidades (zero N+1)
      const chunk = entities.slice(0, 200);
      const res = await window.LectoriaKnowledgeApi.getEntitiesSummary(chunk, ctrl.signal);
      if (seq !== drawerState.batchSummarySeq) return;
      const map = (res && res.summaries) || {};
      Object.keys(map).forEach((k) => {
        drawerState.knowledgeSummaries.set(k, map[k]);
      });
      hydrateDrawerKnowledgeStrips();
    } catch (_) {
      // Degradação graciosa: anotações convencionais permanecem intactas
    }
  }

  function refreshDrawerKnowledgeAfterMutation() {
    if (!annotationsDrawer?.classList.contains("active")) return;
    if (drawerState.knowledgeFilter !== "all") {
      loadDrawerAnnotations({ append: false });
    } else {
      loadDrawerBookKnowledgeSummary();
      loadDrawerBatchKnowledgeSummaries(drawerState.data.highlights, drawerState.data.notes, { reset: true });
    }
  }

  function mergeUniqueById(existingList, newList) {
    const map = new Map();
    (existingList || []).forEach(item => map.set(String(item.id), item));
    (newList || []).forEach(item => map.set(String(item.id), item));
    return Array.from(map.values());
  }

  function compareEpubCfi(cfiA, cfiB) {
    if (!cfiA && !cfiB) return 0;
    if (!cfiA) return 1;
    if (!cfiB) return -1;
    try {
      if (window.ePub && ePub.CFI && typeof ePub.CFI.prototype.compare === "function") {
        return new ePub.CFI().compare(cfiA, cfiB);
      }
    } catch (_) {}
    const cleanA = String(cfiA).replace(/\[[^\]]*\]/g, "");
    const cleanB = String(cfiB).replace(/\[[^\]]*\]/g, "");
    const numsA = cleanA.match(/\d+/g)?.map(Number) || [];
    const numsB = cleanB.match(/\d+/g)?.map(Number) || [];
    const len = Math.max(numsA.length, numsB.length);
    for (let i = 0; i < len; i++) {
      const a = numsA[i] ?? -1;
      const b = numsB[i] ?? -1;
      if (a !== b) return a - b;
    }
    return String(cfiA).localeCompare(String(cfiB));
  }

  async function loadDrawerAnnotations({ append = false } = {}) {
    if (drawerState.abortController) {
      try { drawerState.abortController.abort(); } catch (_) {}
    }
    const controller = new AbortController();
    drawerState.abortController = controller;
    const seq = ++drawerState.requestSeq;
    const nextOffset = append ? (drawerState.offset + drawerState.limit) : 0;

    drawerState.loading = true;
    drawerState.error = null;
    if (btnDrawerLoadMore) {
      btnDrawerLoadMore.disabled = true;
      btnDrawerLoadMore.textContent = "Carregando...";
    }
    if (!append && drawerItemsList && annotationsDrawer?.classList.contains("active") && (
      drawerState.data.highlights.length === 0 &&
      drawerState.data.notes.length === 0 &&
      drawerState.data.drawing_pages.length === 0 &&
      drawerState.data.bookmarks.length === 0
    )) {
      drawerItemsList.innerHTML = `<div style="text-align:center;color:var(--text-muted);padding:2rem 1rem;font-size:0.84rem;">Carregando anotações...</div>`;
    }

    try {
      const params = new URLSearchParams({
        scope: drawerState.scope,
        sort: drawerState.sort,
        limit: String(drawerState.limit),
        offset: String(nextOffset),
        knowledge_filter: drawerState.knowledgeFilter || "all",
      });
      if (drawerState.search) {
        params.set("search", drawerState.search);
      }
      const res = await fetch(`/api/books/${bookId}/annotations?${params.toString()}`, {
        signal: controller.signal,
      });
      if (seq !== drawerState.requestSeq) return;
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const payload = await res.json();
      if (seq !== drawerState.requestSeq) return;

      drawerState.offset = nextOffset;
      drawerState.hasMore = Boolean(payload.has_more);
      drawerState.totalCounts = payload.total_counts || payload.counts || {
        highlights: (payload.highlights || []).length,
        notes: (payload.notes || []).length,
        drawings: (payload.drawing_pages || []).length,
        bookmarks: (payload.bookmarks || []).length,
      };

      const pageHighlights = payload.highlights || [];
      const pageNotes = payload.notes || [];

      if (append) {
        drawerState.data = {
          highlights: mergeUniqueById(drawerState.data.highlights, pageHighlights),
          notes: mergeUniqueById(drawerState.data.notes, pageNotes),
          drawing_pages: mergeUniqueById(drawerState.data.drawing_pages, payload.drawing_pages),
          bookmarks: mergeUniqueById(drawerState.data.bookmarks, payload.bookmarks),
        };
      } else {
        drawerState.data = {
          highlights: pageHighlights,
          notes: pageNotes,
          drawing_pages: payload.drawing_pages || [],
          bookmarks: payload.bookmarks || [],
        };
      }

      if (annotationsDrawer?.classList.contains("active")) {
        if (!append) {
          loadDrawerBookKnowledgeSummary();
        }
        loadDrawerBatchKnowledgeSummaries(pageHighlights, pageNotes, { reset: !append });
      }
    } catch (err) {
      if (err && err.name === "AbortError") return;
      if (seq !== drawerState.requestSeq) return;
      drawerState.error = "Não foi possível carregar as anotações.";
    } finally {
      if (seq === drawerState.requestSeq) {
        drawerState.loading = false;
        if (btnDrawerLoadMore) {
          btnDrawerLoadMore.disabled = false;
          btnDrawerLoadMore.textContent = "Carregar mais";
        }
        updateDrawerCounts();
        renderDrawerList();
      }
    }
  }

  function findBookmarkForCurrentPosition() {
    const bookmarks = (bookAnnotations && bookAnnotations.bookmarks) || drawerState.data.bookmarks || [];
    if (bookFormat === "epub") {
      if (epubCfi) {
        return bookmarks.find(b => b.epub_cfi && String(b.epub_cfi) === String(epubCfi)) || null;
      }
      return bookmarks.find(b => b.page === currentPage && !b.epub_cfi) || null;
    }
    return bookmarks.find(b => b.page === currentPage && !b.epub_cfi) || null;
  }

  function updateBookmarkButtonState() {
    const match = findBookmarkForCurrentPosition();
    const active = Boolean(match);
    if (btnToggleBookmark) {
      btnToggleBookmark.classList.toggle("is-bookmarked", active);
      btnToggleBookmark.setAttribute("aria-pressed", active ? "true" : "false");
      btnToggleBookmark.title = active
        ? "Remover marcador desta posição (k)"
        : "Adicionar marcador nesta posição (k)";
    }
  }

  async function toggleCurrentPositionBookmark() {
    const existing = findBookmarkForCurrentPosition();
    try {
      if (existing) {
        const res = await fetch(`/api/books/${bookId}/bookmarks/${encodeURIComponent(existing.id)}`, {
          method: "DELETE",
        });
        if (!res.ok) throw new Error();
        showReaderToast("Marcador removido.", "info");
      } else {
        const payload = {
          page: Math.max(1, parseInt(currentPage, 10) || 1),
          label: bookFormat === "epub"
            ? (epubCurrentChapterLabel || `Posição ${currentPage}%`)
            : `Página ${currentPage}`,
        };
        if (bookFormat === "epub") {
          if (epubCfi) payload.epub_cfi = epubCfi;
          if (epubCurrentChapterLabel) payload.chapter_label = epubCurrentChapterLabel;
        }
        const res = await fetch(`/api/books/${bookId}/bookmarks`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error();
        showReaderToast("Marcador pessoal salvo.", "info");
      }
      await reloadBookAnnotations();
    } catch {
      showReaderToast("Erro ao atualizar marcador.", "error");
    }
  }

  if (btnToggleBookmark) {
    btnToggleBookmark.addEventListener("click", toggleCurrentPositionBookmark);
  }
  if (btnMobileSheetBookmark) {
    btnMobileSheetBookmark.addEventListener("click", () => {
      closeMobileSheet();
      toggleCurrentPositionBookmark();
    });
  }

  btnToggleDrawer.addEventListener("click", () => {
    annotationsDrawer.classList.toggle("active");
    if (annotationsDrawer.classList.contains("active")) {
      closeThumbnailsDrawer();
      loadDrawerAnnotations({ append: false });
    }
  });
  btnCloseDrawer.addEventListener("click", () => annotationsDrawer.classList.remove("active"));

  drawerScopeBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      const nextScope = btn.getAttribute("data-drawer-scope");
      if (!nextScope || nextScope === drawerState.scope) return;
      drawerScopeBtns.forEach(b => b.classList.toggle("active", b === btn));
      drawerState.scope = nextScope;
      loadDrawerAnnotations({ append: false });
    });
  });

  drawerKnowledgeFilterBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      const nextKf = btn.getAttribute("data-knowledge-filter") || "all";
      if (nextKf === drawerState.knowledgeFilter) return;
      drawerKnowledgeFilterBtns.forEach(b => b.classList.toggle("active", b === btn));
      drawerState.knowledgeFilter = nextKf;
      loadDrawerAnnotations({ append: false });
    });
  });

  if (drawerSortSelect) {
    drawerSortSelect.addEventListener("change", () => {
      drawerState.sort = drawerSortSelect.value === "recent" ? "recent" : "position";
      loadDrawerAnnotations({ append: false });
    });
  }

  if (drawerSearchInput) {
    drawerSearchInput.addEventListener("input", (e) => {
      const val = e.target.value.trim();
      drawerState.search = val;
      if (btnClearDrawerSearch) {
        btnClearDrawerSearch.style.display = val ? "inline-block" : "none";
      }
      clearTimeout(drawerSearchDebounceTimer);
      drawerSearchDebounceTimer = setTimeout(() => {
        loadDrawerAnnotations({ append: false });
      }, 280);
    });
  }

  if (btnClearDrawerSearch) {
    btnClearDrawerSearch.addEventListener("click", () => {
      drawerSearchInput.value = "";
      drawerState.search = "";
      btnClearDrawerSearch.style.display = "none";
      clearTimeout(drawerSearchDebounceTimer);
      loadDrawerAnnotations({ append: false });
    });
  }

  drawerTabs.forEach(tab => {
    tab.addEventListener("click", () => {
      drawerTabs.forEach(t => t.classList.remove("active"));
      tab.classList.add("active");
      drawerState.filter = tab.getAttribute("data-drawer-filter");
      renderDrawerList();
    });
  });

  if (btnDrawerLoadMore) {
    btnDrawerLoadMore.addEventListener("click", () => {
      if (!drawerState.loading && drawerState.hasMore) {
        loadDrawerAnnotations({ append: true });
      }
    });
  }

  function updateDrawerCounts() {
    const tc = drawerState.totalCounts || {};
    const hCount = tc.highlights ?? (drawerState.data.highlights || []).length;
    const nCount = tc.notes ?? (drawerState.data.notes || []).length;
    const dCount = tc.drawings ?? (drawerState.data.drawing_pages || []).length;
    const bCount = tc.bookmarks ?? (drawerState.data.bookmarks || []).length;
    countHighlightsEl.textContent = hCount;
    countNotesEl.textContent = nCount;
    if (countDrawingsEl) countDrawingsEl.textContent = dCount;
    if (countBookmarksEl) countBookmarksEl.textContent = bCount;
    countAllEl.textContent = hCount + nCount + dCount;
  }

  function renderDrawerItemCard(item) {
    if (item.type === "highlight") {
      const h = item.data;
      const isOwn = h.is_own !== false;
      const isPublic = h.visibility === "public";
      const authorText = isOwn ? "Você" : escapeHtml(h.author_display_name || "Leitor");
      const visBadge = `<span class="visibility-pill ${isPublic ? "is-public" : ""}">${isPublic ? "🌐 Público" : "🔒 Privado"}</span>`;
      const dateFormatted = formatTimestamp(h.created_at);
      const cfiAttr = h.epub_cfi ? `data-epub-cfi="${escapeHtml(h.epub_cfi)}"` : "";
      const chapAttr = h.chapter_label ? `data-chapter-label="${escapeHtml(h.chapter_label)}"` : "";
      return `
        <div class="annotation-item" data-goto-page="${h.page}" data-item-type="highlight" data-item-id="${escapeHtml(h.id)}" ${cfiAttr} ${chapAttr} data-is-own="${isOwn ? "true" : "false"}">
          <div class="annotation-item-header">
            <span class="annotation-badge badge-highlight">Destaque</span>
            <div style="display: flex; align-items: center; gap: 0.35rem; flex-wrap: wrap;">
              <span style="font-size: 0.72rem; color: var(--text-muted);">${authorText}</span>
              ${visBadge}
              <span class="annotation-date">${dateFormatted}</span>
              <span style="display: inline-block; width: 12px; height: 12px; border-radius: 50%; background-color: ${escapeHtml(h.color || "#fef08a")}; border: 1px solid rgba(0,0,0,0.2);"></span>
            </div>
          </div>
          <div class="annotation-quote" style="border-left-color: ${escapeHtml(h.color || "#fef08a")};">
            "${escapeHtml(h.selected_text)}"
          </div>
          <div class="drawer-knowledge-strip hidden" data-entity-type="highlight" data-entity-id="${escapeHtml(h.id)}"></div>
          <div class="annotation-item-actions">
            <button type="button" class="btn-item-action btn-drawer-knowledge" data-entity-type="highlight" data-entity-id="${escapeHtml(h.id)}">Conhecimento</button>
            ${isOwn ? `
              <button class="btn-item-action btn-toggle-vis-h" data-h-id="${escapeHtml(h.id)}" data-next-vis="${isPublic ? "private" : "public"}">${isPublic ? "Tornar Privado" : "Tornar Público"}</button>
              <button class="btn-item-action btn-add-note-to-h" data-h-id="${escapeHtml(h.id)}" data-page="${h.page}" data-quote="${escapeHtml(h.selected_text)}" ${cfiAttr} ${chapAttr}>Adicionar Nota</button>
              <button class="btn-item-action danger btn-del-highlight" data-h-id="${escapeHtml(h.id)}">Excluir</button>
            ` : `<button class="btn-item-action btn-goto-annotation" data-goto-page="${h.page}" data-item-type="highlight" data-item-id="${escapeHtml(h.id)}" ${cfiAttr} ${chapAttr}>${bookFormat === "epub" ? "Ir para a Posição" : "Ir para a Página"}</button>`}
          </div>
        </div>
      `;
    }
    if (item.type === "note") {
      const n = item.data;
      const isOwn = n.is_own !== false;
      const isPublic = n.visibility === "public";
      const authorText = isOwn ? "Você" : escapeHtml(n.author_display_name || "Leitor");
      const visBadge = `<span class="visibility-pill ${isPublic ? "is-public" : ""}">${isPublic ? "🌐 Pública" : "🔒 Privada"}</span>`;
      const dateFormatted = formatTimestamp(n.updated_at || n.created_at);
      const cfiAttr = n.epub_cfi ? `data-epub-cfi="${escapeHtml(n.epub_cfi)}"` : "";
      const chapAttr = n.chapter_label ? `data-chapter-label="${escapeHtml(n.chapter_label)}"` : "";
      return `
        <div class="annotation-item" data-goto-page="${n.page}" data-item-type="note" data-item-id="${escapeHtml(n.id)}" ${cfiAttr} ${chapAttr} data-is-own="${isOwn ? "true" : "false"}">
          <div class="annotation-item-header">
            <span class="annotation-badge badge-note">Nota</span>
            <div style="display: flex; align-items: center; gap: 0.35rem; flex-wrap: wrap;">
              <span style="font-size: 0.72rem; color: var(--text-muted);">${authorText}</span>
              ${visBadge}
              <span class="annotation-date">${dateFormatted}</span>
            </div>
          </div>
          ${n.highlight_text ? `<div class="annotation-quote">"${escapeHtml(n.highlight_text)}"</div>` : ""}
          <div class="annotation-note-text">${escapeHtml(n.content)}</div>
          <div class="drawer-knowledge-strip hidden" data-entity-type="note" data-entity-id="${escapeHtml(n.id)}"></div>
          <div class="annotation-item-actions">
            <button type="button" class="btn-item-action btn-drawer-knowledge" data-entity-type="note" data-entity-id="${escapeHtml(n.id)}">Conhecimento</button>
            ${isOwn ? `
              <button class="btn-item-action btn-toggle-vis-n" data-note-id="${escapeHtml(n.id)}" data-next-vis="${isPublic ? "private" : "public"}">${isPublic ? "Tornar Privada" : "Tornar Pública"}</button>
              ${eligibleCollectiveReads.length > 0 ? `<button class="btn-item-action btn-share-note-club" data-note-id="${escapeHtml(n.id)}">Compartilhar</button>` : ""}
              <button class="btn-item-action btn-edit-note" data-note-id="${escapeHtml(n.id)}" data-page="${n.page}" data-content="${escapeHtml(n.content)}" data-visibility="${isPublic ? "public" : "private"}">Editar</button>
              <button class="btn-item-action danger btn-del-note" data-note-id="${escapeHtml(n.id)}">Excluir</button>
            ` : `<button class="btn-item-action btn-goto-annotation" data-goto-page="${n.page}" data-item-type="note" data-item-id="${escapeHtml(n.id)}" ${cfiAttr} ${chapAttr}>${bookFormat === "epub" ? "Ir para a Posição" : "Ir para a Página"}</button>`}
          </div>
        </div>
      `;
    }
    if (item.type === "drawing") {
      const d = item.data;
      const isOwn = d.is_own !== false;
      const isPublic = d.visibility === "public";
      const authorText = isOwn ? "Você" : escapeHtml(d.author_display_name || "Leitor");
      const visBadge = `<span class="visibility-pill ${isPublic ? "is-public" : ""}">${isPublic ? "🌐 Público" : "🔒 Privado"}</span>`;
      const dateFormatted = formatTimestamp(d.updated_at);
      return `
        <div class="annotation-item" data-goto-page="${d.page}" data-item-type="drawing" data-is-own="${isOwn ? "true" : "false"}">
          <div class="annotation-item-header">
            <span class="annotation-badge badge-drawing">Desenho Livre</span>
            <div style="display: flex; align-items: center; gap: 0.35rem; flex-wrap: wrap;">
              <span style="font-size: 0.72rem; color: var(--text-muted);">${authorText}</span>
              ${visBadge}
              <span class="annotation-date">${dateFormatted}</span>
            </div>
          </div>
          <div style="font-size: 0.8rem; color: var(--text-muted); font-style: italic;">
            ${isOwn ? "Seus traços vetoriais nesta página." : `Traços vetoriais públicos de ${authorText}.`}
          </div>
          <div class="annotation-item-actions">
            <button class="btn-item-action btn-goto-annotation" data-goto-page="${d.page}">Ir para a Página</button>
            ${isOwn ? `
              <button class="btn-item-action btn-toggle-vis-d" data-page="${d.page}" data-next-vis="${isPublic ? "private" : "public"}">${isPublic ? "Tornar Privado" : "Tornar Público"}</button>
              <button class="btn-item-action danger btn-del-drawing" data-page="${d.page}">Excluir</button>
            ` : ""}
          </div>
        </div>
      `;
    }
    if (item.type === "bookmark") {
      const b = item.data;
      const dateFormatted = formatTimestamp(b.created_at || b.updated_at);
      const cfiAttr = b.epub_cfi ? `data-epub-cfi="${escapeHtml(b.epub_cfi)}"` : "";
      const chapAttr = b.chapter_label ? `data-chapter-label="${escapeHtml(b.chapter_label)}"` : "";
      const locLabel = b.epub_cfi
        ? (b.chapter_label ? escapeHtml(b.chapter_label) : `Posição ${b.page}%`)
        : `Página ${b.page}`;
      return `
        <div class="annotation-item" data-goto-page="${b.page}" data-item-type="bookmark" ${cfiAttr} ${chapAttr} data-is-own="true">
          <div class="annotation-item-header">
            <span class="annotation-badge badge-bookmark">Marcador</span>
            <div style="display: flex; align-items: center; gap: 0.35rem; flex-wrap: wrap;">
              <span class="visibility-pill">🔒 Privado</span>
              <span class="annotation-date">${dateFormatted}</span>
            </div>
          </div>
          <div class="annotation-note-text" style="font-weight: 500;">${escapeHtml(b.label || locLabel)}</div>
          <div style="font-size: 0.74rem; color: var(--text-muted); margin-top: 0.15rem;">${locLabel}</div>
          <div class="annotation-item-actions">
            <button class="btn-item-action btn-goto-annotation" data-goto-page="${b.page}" ${cfiAttr} ${chapAttr}>${bookFormat === "epub" ? "Ir para a Posição" : "Ir para a Página"}</button>
            <button class="btn-item-action danger btn-del-bookmark" data-bm-id="${escapeHtml(b.id)}">Excluir</button>
          </div>
        </div>
      `;
    }
    return "";
  }

  function renderDrawerList() {
    if (drawerLoadMoreContainer) {
      drawerLoadMoreContainer.style.display = (drawerState.hasMore && drawerState.filter !== "bookmarks") ? "block" : "none";
    }

    if (drawerState.error) {
      drawerItemsList.innerHTML = `
        <div style="text-align: center; color: var(--text-faint); padding: 2.5rem 1rem;">
          <p>${escapeHtml(drawerState.error)}</p>
          <button id="btn-retry-drawer-load" type="button" class="btn btn-sm btn-outline" style="margin-top: 0.6rem;">Tentar novamente</button>
        </div>
      `;
      const retryBtn = document.getElementById("btn-retry-drawer-load");
      if (retryBtn) retryBtn.addEventListener("click", () => loadDrawerAnnotations({ append: false }));
      return;
    }

    const highlights = drawerState.data.highlights || [];
    const notes = drawerState.data.notes || [];
    const drawingPages = drawerState.data.drawing_pages || [];
    const bookmarks = drawerState.data.bookmarks || [];

    let items = [];

    if (drawerState.filter === "bookmarks") {
      bookmarks.forEach(b => {
        items.push({ type: "bookmark", data: b, page: b.page, cfi: b.epub_cfi || "", date: b.created_at || b.updated_at });
      });
    } else {
      if (drawerState.filter === "all" || drawerState.filter === "highlights") {
        highlights.forEach(h => {
          items.push({ type: "highlight", data: h, page: h.page, cfi: h.epub_cfi || "", date: h.created_at });
        });
      }
      if (drawerState.filter === "all" || drawerState.filter === "notes") {
        notes.forEach(n => {
          items.push({ type: "note", data: n, page: n.page, cfi: n.epub_cfi || "", date: n.updated_at || n.created_at });
        });
      }
      if (drawerState.filter === "all" || drawerState.filter === "drawings") {
        drawingPages.forEach(d => {
          items.push({ type: "drawing", data: d, page: d.page, cfi: "", date: d.updated_at });
        });
      }
    }

    if (items.length === 0) {
      drawerItemsList.innerHTML = `
        <div style="text-align: center; color: var(--text-faint); padding: 3rem 1rem;">
          <p>${drawerState.search ? "Nenhum resultado encontrado para a busca." : "Nenhum item encontrado neste filtro."}</p>
          <p style="font-size: 0.8rem; margin-top: 0.4rem;">
            ${drawerState.search ? "Tente buscar por outras palavras-chave ou autor." : "Selecione trechos no livro para destacar, adicionar notas, desenhar ou criar marcadores."}
          </p>
        </div>
      `;
      return;
    }

    let html = "";
    if (drawerState.sort === "recent") {
      items.sort((a, b) => new Date(b.date || 0) - new Date(a.date || 0));
      items.forEach(item => {
        html += renderDrawerItemCard(item);
      });
    } else if (bookFormat === "epub") {
      items.sort((a, b) => {
        const cmp = compareEpubCfi(a.cfi, b.cfi);
        if (cmp !== 0) return cmp;
        if (a.page !== b.page) return a.page - b.page;
        return new Date(a.date || 0) - new Date(b.date || 0);
      });
      items.forEach(item => {
        html += renderDrawerItemCard(item);
      });
    } else {
      // Agrupa por página no PDF
      const pageMap = new Map();
      items.forEach(item => {
        if (!pageMap.has(item.page)) pageMap.set(item.page, []);
        pageMap.get(item.page).push(item);
      });
      const sortedPages = Array.from(pageMap.keys()).sort((a, b) => a - b);
      sortedPages.forEach(pageNum => {
        const pageItems = pageMap.get(pageNum);
        pageItems.sort((a, b) => new Date(a.date || 0) - new Date(b.date || 0));
        html += `
          <div class="drawer-page-group">
            <div class="drawer-page-header">
              <span>Página ${pageNum}</span>
              <span style="font-size: 0.72rem; color: var(--text-faint);">${pageItems.length} item(s)</span>
            </div>
        `;
        pageItems.forEach(item => {
          html += renderDrawerItemCard(item);
        });
        html += `</div>`;
      });
    }

    drawerItemsList.innerHTML = html;
    hydrateDrawerKnowledgeStrips();

    drawerItemsList.querySelectorAll(".btn-drawer-knowledge").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        if (!window.LectoriaReaderKnowledge) return;
        const etype = btn.getAttribute("data-entity-type");
        const eid = btn.getAttribute("data-entity-id");
        if (!etype || !eid) return;
        const list = etype === "note" ? (drawerState.data.notes || []) : (drawerState.data.highlights || []);
        const rec = list.find(x => String(x.id) === String(eid));
        if (!rec) return;
        const isOwn = rec.is_own !== false;
        window.LectoriaReaderKnowledge.openForAnnotation(
          {
            entityType: etype,
            entityId: rec.id,
            excerpt: etype === "note" ? (rec.content || "") : (rec.selected_text || ""),
            isOwn,
            authorName: isOwn ? "" : (rec.author_display_name || "Leitor"),
            bookId,
            page: rec.page || null,
            epubCfi: rec.epub_cfi || null,
            chapterLabel: rec.chapter_label || "",
          },
          btn,
          { onMutated: refreshDrawerKnowledgeAfterMutation }
        );
      });
    });

    // Navegação ao clicar no item do painel ou no botão "Ir para a Posição/Página"
    function handleDrawerItemNavigation(el) {
      if (bookFormat === "epub") {
        const cfi = el.getAttribute("data-epub-cfi");
        const chap = el.getAttribute("data-chapter-label");
        navigateToEpubCfi(cfi, chap);
        return;
      }
      const targetPage = parseInt(el.getAttribute("data-goto-page"), 10);
      const itemType = el.getAttribute("data-item-type");
      const itemId = el.getAttribute("data-item-id");
      if (targetPage) {
        goToPage(targetPage);
        if (itemId && (itemType === "highlight" || itemType === "note")) {
          setTimeout(() => {
            const selector = itemType === "highlight"
              ? `.highlight-box[data-highlight-id="${CSS.escape(itemId)}"]`
              : `.page-note-pin[data-note-id="${CSS.escape(itemId)}"]`;
            const targetEl = document.querySelector(selector);
            if (targetEl) {
              targetEl.style.outline = "2px solid #38bdf8";
              targetEl.style.outlineOffset = "2px";
              setTimeout(() => {
                targetEl.style.outline = "";
                targetEl.style.outlineOffset = "";
              }, 1800);
            }
          }, 350);
        }
      }
    }

    drawerItemsList.querySelectorAll(".annotation-item").forEach(el => {
      el.addEventListener("click", (e) => {
        if (e.target.closest("button")) return;
        handleDrawerItemNavigation(el);
      });
    });

    drawerItemsList.querySelectorAll(".btn-goto-annotation").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        handleDrawerItemNavigation(btn);
      });
    });

    drawerItemsList.querySelectorAll(".btn-toggle-vis-h").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const nextVis = btn.getAttribute("data-next-vis");
        if (nextVis === "public" && !confirmFirstPublicShare("marcação")) return;
        await toggleHighlightVisibility(btn.getAttribute("data-h-id"), nextVis);
      });
    });

    drawerItemsList.querySelectorAll(".btn-toggle-vis-n").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const nextVis = btn.getAttribute("data-next-vis");
        if (nextVis === "public" && !confirmFirstPublicShare("anotação")) return;
        await toggleNoteVisibility(btn.getAttribute("data-note-id"), nextVis);
      });
    });

    drawerItemsList.querySelectorAll(".btn-toggle-vis-d").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const p = parseInt(btn.getAttribute("data-page"), 10);
        const nextVis = btn.getAttribute("data-next-vis");
        if (nextVis === "public" && !confirmFirstPublicShare("desenho")) return;
        try {
          const res = await fetch(`/api/books/${bookId}/pages/${p}/drawing/visibility`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ visibility: nextVis }),
          });
          if (!res.ok) throw new Error();
          pageDrawingsBundleCache.delete(p);
          if (p === currentPage) updateDrawVisibilityButton(p);
          await reloadBookAnnotations();
          refreshAllDrawingLayersFromCache();
        } catch {
          showReaderToast("Erro ao alterar visibilidade do desenho.", "error");
        }
      });
    });

    drawerItemsList.querySelectorAll(".btn-del-drawing").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const p = parseInt(btn.getAttribute("data-page"), 10);
        if (confirm(`Deseja excluir seu desenho da Página ${p}?`)) {
          try {
            const res = await fetch(`/api/books/${bookId}/pages/${p}/drawing`, { method: "DELETE" });
            if (!res.ok) throw new Error();
            pageStrokesMap.set(p, []);
            pageDrawingsBundleCache.delete(p);
            await reloadBookAnnotations();
            refreshAllDrawingLayersFromCache();
          } catch {
            showReaderToast("Erro ao excluir desenho.", "error");
          }
        }
      });
    });

    drawerItemsList.querySelectorAll(".btn-del-bookmark").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const bmId = btn.getAttribute("data-bm-id");
        try {
          const res = await fetch(`/api/books/${bookId}/bookmarks/${encodeURIComponent(bmId)}`, { method: "DELETE" });
          if (!res.ok) throw new Error();
          showReaderToast("Marcador removido.", "info");
          await reloadBookAnnotations();
        } catch {
          showReaderToast("Erro ao remover marcador.", "error");
        }
      });
    });

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
          epubCfi: btn.getAttribute("data-epub-cfi") || null,
          chapterLabel: btn.getAttribute("data-chapter-label") || null,
        });
      });
    });

    drawerItemsList.querySelectorAll(".btn-share-note-club").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const nid = btn.getAttribute("data-note-id");
        const noteObj = (drawerState.data.notes || bookAnnotations.notes || []).find(item => String(item.id) === String(nid));
        if (!noteObj) return;
        const baseQuote = noteObj.highlight_text || noteObj.content || "";
        const baseComment = noteObj.highlight_text ? (noteObj.content || "") : "";
        openShareQuoteModal({
          quoteText: baseQuote,
          commentText: baseComment,
          pageNumber: noteObj.page || null,
          noteId: noteObj.id,
          originalNoteQuote: baseQuote,
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
          initialVisibility: btn.getAttribute("data-visibility") || "private",
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

  function getSvgViewBoxDimensions(svgElement) {
    const rect = svgElement ? svgElement.getBoundingClientRect() : null;
    const aspect = (rect && rect.width > 0 && rect.height > 0)
      ? (rect.height / rect.width)
      : (basePageHeight > 0 && basePageWidth > 0 ? basePageHeight / basePageWidth : 1.3333);
    const vbWidth = 1000;
    const vbHeight = Math.max(100, Math.round(vbWidth * aspect));
    return { vbWidth, vbHeight };
  }

  function applySvgViewBox(svgElement) {
    if (!svgElement) return { vbWidth: 1000, vbHeight: 1333 };
    const dims = getSvgViewBoxDimensions(svgElement);
    svgElement.setAttribute("viewBox", `0 0 ${dims.vbWidth} ${dims.vbHeight}`);
    svgElement.setAttribute("preserveAspectRatio", "xMidYMid meet");
    return dims;
  }

  async function loadDrawingBundleForPage(pageNum, forceRefresh = false) {
    if (!forceRefresh && pageDrawingsBundleCache.has(pageNum)) {
      const cached = pageDrawingsBundleCache.get(pageNum);
      pageStrokesMap.set(pageNum, (cached.own_drawing && cached.own_drawing.strokes) || []);
      return cached;
    }
    try {
      const res = await fetch(`/api/books/${bookId}/pages/${pageNum}/drawing`);
      if (res.ok) {
        const data = await res.json();
        const own = data.own_drawing || {
          page: pageNum,
          strokes: data.strokes || [],
          visibility: data.visibility || "private",
          is_own: true,
        };
        const pub = Array.isArray(data.public_drawings) ? data.public_drawings : [];
        const bundle = { own_drawing: own, public_drawings: pub };
        pageDrawingsBundleCache.set(pageNum, bundle);
        pageStrokesMap.set(pageNum, own.strokes || []);
        return bundle;
      }
    } catch {
      // Retorna vazio
    }
    const emptyBundle = {
      own_drawing: { page: pageNum, strokes: [], visibility: "private", is_own: true },
      public_drawings: [],
    };
    return emptyBundle;
  }

  function updateDrawVisibilityButton(pageNum = currentPage) {
    if (!btnDrawVisibility) return;
    const bundle = pageDrawingsBundleCache.get(pageNum);
    const vis = (bundle && bundle.own_drawing && bundle.own_drawing.visibility) || "private";
    const isPublic = vis === "public";
    btnDrawVisibility.setAttribute("data-visibility", vis);
    btnDrawVisibility.classList.toggle("is-public", isPublic);
    if (drawVisibilityIcon) drawVisibilityIcon.textContent = isPublic ? "🌐" : "🔒";
    if (drawVisibilityLabel) drawVisibilityLabel.textContent = isPublic ? "Público" : "Privado";
  }

  function renderPublicDrawingsLayer(pageNum, publicSvgElement) {
    if (!publicSvgElement) return;
    publicSvgElement.innerHTML = "";
    if (layerViewMode !== "all_visible") return;

    const bundle = pageDrawingsBundleCache.get(pageNum);
    const pubDrawings = (bundle && Array.isArray(bundle.public_drawings)) ? bundle.public_drawings : [];
    const visibleDrawings = pubDrawings.filter(d => {
      if (d.is_own) return false;
      const uidKey = String(d.author_user_id || d.user_id || "");
      return !hiddenPublicDrawingAuthors.has(uidKey);
    });
    if (visibleDrawings.length === 0) return;

    const { vbWidth, vbHeight } = applySvgViewBox(publicSvgElement);
    visibleDrawings.forEach(d => {
      (d.strokes || []).forEach((s, idx) => {
        if (!s.points || s.points.length < 2) return;
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("class", "drawing-stroke public-drawing-stroke");
        path.setAttribute("data-author", d.author_display_name || "Leitor");
        path.setAttribute("data-stroke-index", idx);
        path.setAttribute("stroke", s.color || "#3d7cf4");
        path.setAttribute("stroke-width", String((s.width || 4) * 1.4));
        path.setAttribute("stroke-opacity", "0.78");
        let dAttr = `M ${s.points[0].x * vbWidth} ${s.points[0].y * vbHeight}`;
        for (let i = 1; i < s.points.length; i++) {
          dAttr += ` L ${s.points[i].x * vbWidth} ${s.points[i].y * vbHeight}`;
        }
        path.setAttribute("d", dAttr);
        publicSvgElement.appendChild(path);
      });
    });
  }

  function refreshAllDrawingLayersFromCache() {
    if (viewMode === "single") {
      const bundle = pageDrawingsBundleCache.get(currentPage);
      const ownStrokes = (bundle && bundle.own_drawing && bundle.own_drawing.strokes) || pageStrokesMap.get(currentPage) || [];
      renderSvgStrokes(singleDrawingLayer, ownStrokes);
      renderPublicDrawingsLayer(currentPage, singlePublicDrawingLayer);
      renderNotePinsForPage(currentPage, singleNotePinsLayer);
      updateDrawVisibilityButton(currentPage);
    } else {
      document.querySelectorAll(".page-slot").forEach(slot => {
        const p = parseInt(slot.getAttribute("data-page-num"), 10);
        const ownSvg = slot.querySelector(".drawing-layer");
        const pubSvg = slot.querySelector(".public-drawing-layer");
        const pinsLayer = slot.querySelector(".page-note-pins-layer");
        if (ownSvg) {
          const ownStrokes = pageStrokesMap.get(p) || [];
          renderSvgStrokes(ownSvg, ownStrokes);
        }
        if (pubSvg) renderPublicDrawingsLayer(p, pubSvg);
        if (pinsLayer) renderNotePinsForPage(p, pinsLayer);
      });
      updateDrawVisibilityButton(currentPage);
    }
  }

  async function setupDrawingLayerForPage(pageNum, svgElement, publicSvgElement = null) {
    svgElement.innerHTML = "";
    svgElement.setAttribute("data-page-num", pageNum);
    updateDrawingLayerInteractivity(svgElement);
    if (publicSvgElement) {
      publicSvgElement.innerHTML = "";
      publicSvgElement.setAttribute("data-page-num", pageNum);
    }

    const bundle = await loadDrawingBundleForPage(pageNum);
    const ownStrokes = (bundle.own_drawing && bundle.own_drawing.strokes) || [];
    renderSvgStrokes(svgElement, ownStrokes);
    if (publicSvgElement) {
      renderPublicDrawingsLayer(pageNum, publicSvgElement);
    }
    if (pageNum === currentPage) {
      updateDrawVisibilityButton(pageNum);
    }

    // Eventos de desenho e borracha atuam apenas sobre a camada pessoal
    svgElement.onpointerdown = (e) => handleDrawPointerDown(e, pageNum, svgElement);
    svgElement.onpointermove = (e) => handleDrawPointerMove(e, pageNum, svgElement);
    svgElement.onpointerup = (e) => handleDrawPointerUp(e, pageNum, svgElement);
    svgElement.onpointercancel = (e) => handleDrawPointerUp(e, pageNum, svgElement);
  }

  function renderSvgStrokes(svgElement, strokes) {
    if (!svgElement) return;
    svgElement.innerHTML = "";
    if (layerViewMode === "hidden") return;
    const { vbWidth, vbHeight } = applySvgViewBox(svgElement);

    strokes.forEach((s, idx) => {
      if (!s.points || s.points.length < 2) return;
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("class", "drawing-stroke");
      path.setAttribute("data-stroke-index", idx);
      path.setAttribute("stroke", s.color || "#3d7cf4");
      path.setAttribute("stroke-width", String((s.width || 4) * 1.4));

      let d = `M ${s.points[0].x * vbWidth} ${s.points[0].y * vbHeight}`;
      for (let i = 1; i < s.points.length; i++) {
        d += ` L ${s.points[i].x * vbWidth} ${s.points[i].y * vbHeight}`;
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
    if (rect.width <= 0 || rect.height <= 0) return;
    const { vbWidth, vbHeight } = applySvgViewBox(svgElement);
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
    path.setAttribute("stroke-width", String(currentDrawWidth * 1.4));
    path.setAttribute("d", `M ${nx * vbWidth} ${ny * vbHeight}`);
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
    if (rect.width <= 0 || rect.height <= 0) return;
    const { vbWidth, vbHeight } = getSvgViewBoxDimensions(svgElement);
    const nx = (e.clientX - rect.left) / rect.width;
    const ny = (e.clientY - rect.top) / rect.height;
    currentStroke.points.push({ x: nx, y: ny });

    const currentPath = svgElement.querySelector(".current-drawing-path");
    if (currentPath) {
      const pts = currentStroke.points;
      let d = `M ${pts[0].x * vbWidth} ${pts[0].y * vbHeight}`;
      for (let i = 1; i < pts.length; i++) {
        d += ` L ${pts[i].x * vbWidth} ${pts[i].y * vbHeight}`;
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

  async function saveDrawingNow(pageNum) {
    const strokes = pageStrokesMap.get(pageNum) || [];
    const res = await fetch(`/api/books/${bookId}/pages/${pageNum}/drawing`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ strokes }),
    });
    if (res.ok) {
      const saved = await res.json();
      const existing = pageDrawingsBundleCache.get(pageNum) || { own_drawing: {}, public_drawings: [] };
      existing.own_drawing = {
        ...(existing.own_drawing || {}),
        page: pageNum,
        strokes: [...strokes],
        visibility: saved.visibility || (existing.own_drawing && existing.own_drawing.visibility) || "private",
        is_own: true,
      };
      pageDrawingsBundleCache.set(pageNum, existing);
      if (pageNum === currentPage) updateDrawVisibilityButton(pageNum);
      showSavedIndicator();
    }
  }

  function queueSaveDrawing(pageNum) {
    clearTimeout(saveDrawingTimeout);
    saveDrawingTimeout = setTimeout(async () => {
      try {
        await saveDrawingNow(pageNum);
      } catch (e) {
        console.warn("Erro ao salvar desenho:", e);
      }
    }, 600);
  }

  if (btnDrawVisibility) {
    btnDrawVisibility.addEventListener("click", async () => {
      const targetPage = currentPage;
      const bundle = await loadDrawingBundleForPage(targetPage);
      const currentVis = (bundle.own_drawing && bundle.own_drawing.visibility) || "private";
      const nextVis = currentVis === "public" ? "private" : "public";
      const strokes = pageStrokesMap.get(targetPage) || [];

      if (strokes.length === 0) {
        showReaderToast("Desenhe algum traço na página antes de publicá-lo.", "info");
        return;
      }
      if (nextVis === "public" && !confirmFirstPublicShare("desenho")) {
        return;
      }

      try {
        clearTimeout(saveDrawingTimeout);
        await saveDrawingNow(targetPage);
        const res = await fetch(`/api/books/${bookId}/pages/${targetPage}/drawing/visibility`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ visibility: nextVis }),
        });
        if (!res.ok) throw new Error();
        const data = await res.json();
        const cached = pageDrawingsBundleCache.get(targetPage) || { own_drawing: {}, public_drawings: [] };
        cached.own_drawing = data.drawing || { ...(cached.own_drawing || {}), visibility: nextVis };
        pageDrawingsBundleCache.set(targetPage, cached);
        updateDrawVisibilityButton(targetPage);
        showReaderToast(
          nextVis === "public" ? "Desenho da página publicado." : "Desenho da página tornado privado.",
          "info"
        );
        await reloadBookAnnotations();
      } catch {
        showReaderToast("Erro ao alterar visibilidade do desenho.", "error");
      }
    });
  }

  btnDrawClear.addEventListener("click", async () => {
    if (confirm(`Deseja limpar apenas os seus desenhos da Página ${currentPage}?`)) {
      try {
        clearTimeout(saveDrawingTimeout);
        await fetch(`/api/books/${bookId}/pages/${currentPage}/drawing`, { method: "DELETE" });
        pageStrokesMap.set(currentPage, []);
        const cached = pageDrawingsBundleCache.get(currentPage);
        if (cached && cached.own_drawing) {
          cached.own_drawing.strokes = [];
          cached.own_drawing.visibility = "private";
        }
        updateDrawVisibilityButton(currentPage);
        if (viewMode === "single") {
          singleDrawingLayer.innerHTML = "";
        } else {
          const slot = document.getElementById(`page-slot-${currentPage}`);
          if (slot) {
            const svg = slot.querySelector(".drawing-layer");
            if (svg) svg.innerHTML = "";
          }
        }
        await reloadBookAnnotations();
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
      return renderSinglePage(currentPage, { resetScroll: true });
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

  // --- Busca Textual Interna no PDF (v1.8.0) ---
  function openPdfSearchBar() {
    if (!readerSearchBar || bookFormat === "epub") return;
    readerSearchBar.style.display = "flex";
    if (pdfSearchInput) {
      pdfSearchInput.focus();
      pdfSearchInput.select();
    }
  }

  function closePdfSearchBar() {
    if (readerSearchBar) readerSearchBar.style.display = "none";
  }

  async function runPdfSearch(queryText) {
    if (!pdfDoc) return;
    const term = String(queryText || "").trim().toLowerCase();
    pdfSearchMatches = [];
    pdfSearchMatchIdx = -1;

    if (!term) {
      if (pdfSearchStatus) pdfSearchStatus.textContent = "";
      return;
    }

    if (pdfSearchStatus) pdfSearchStatus.textContent = "Buscando...";
    for (let p = 1; p <= totalPages; p++) {
      let pageText = pdfPageTextCache.get(p);
      if (pageText === undefined) {
        try {
          const page = await pdfDoc.getPage(p);
          const tc = await page.getTextContent();
          pageText = (tc.items || []).map(it => it.str || "").join(" ").toLowerCase();
          pdfPageTextCache.set(p, pageText);
        } catch {
          pageText = "";
        }
      }
      if (pageText.includes(term)) {
        pdfSearchMatches.push({ page: p });
      }
    }

    if (pdfSearchMatches.length === 0) {
      if (pdfSearchStatus) pdfSearchStatus.textContent = "0/0";
      return;
    }

    const exactIdx = pdfSearchMatches.findIndex(m => m.page >= currentPage);
    pdfSearchMatchIdx = exactIdx >= 0 ? exactIdx : 0;
    if (pdfSearchStatus) pdfSearchStatus.textContent = `${pdfSearchMatchIdx + 1}/${pdfSearchMatches.length}`;
    goToPage(pdfSearchMatches[pdfSearchMatchIdx].page);
  }

  function stepPdfSearchMatch(delta) {
    if (pdfSearchMatches.length === 0) return;
    pdfSearchMatchIdx = (pdfSearchMatchIdx + delta + pdfSearchMatches.length) % pdfSearchMatches.length;
    if (pdfSearchStatus) pdfSearchStatus.textContent = `${pdfSearchMatchIdx + 1}/${pdfSearchMatches.length}`;
    goToPage(pdfSearchMatches[pdfSearchMatchIdx].page);
  }

  if (btnTogglePdfSearch) btnTogglePdfSearch.addEventListener("click", openPdfSearchBar);
  if (btnClosePdfSearch) btnClosePdfSearch.addEventListener("click", closePdfSearchBar);
  if (btnPdfSearchNext) btnPdfSearchNext.addEventListener("click", () => stepPdfSearchMatch(1));
  if (btnPdfSearchPrev) btnPdfSearchPrev.addEventListener("click", () => stepPdfSearchMatch(-1));
  if (pdfSearchInput) {
    pdfSearchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        if (pdfSearchMatches.length > 0 && pdfSearchInput.dataset.lastQuery === pdfSearchInput.value.trim()) {
          stepPdfSearchMatch(e.shiftKey ? -1 : 1);
        } else {
          pdfSearchInput.dataset.lastQuery = pdfSearchInput.value.trim();
          runPdfSearch(pdfSearchInput.value);
        }
      } else if (e.key === "Escape") {
        closePdfSearchBar();
      }
    });
  }

  // Fecha popovers contextuais ao clicar fora deles
  document.addEventListener("click", (e) => {
    if (highlightPopover && highlightPopover.style.display !== "none") {
      if (!highlightPopover.contains(e.target) && !e.target.closest(".highlight-box")) {
        closeHighlightPopover();
      }
    }
    if (pageNotePopover && pageNotePopover.style.display !== "none") {
      if (!pageNotePopover.contains(e.target) && !e.target.closest(".page-note-pin")) {
        closePageNotePopover();
      }
    }
  });

  // --- Atalhos de Teclado Globais ---
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && window.LectoriaReaderKnowledge && window.LectoriaReaderKnowledge.isModalOpen()) {
      e.preventDefault();
      window.LectoriaReaderKnowledge.close();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f" && bookFormat === "pdf") {
      e.preventDefault();
      openPdfSearchBar();
      return;
    }
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;
    if (window.LectoriaReaderKnowledge && window.LectoriaReaderKnowledge.isModalOpen()) return;

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
    } else if (e.key.toLowerCase() === "v") {
      const order = ["all_visible", "mine", "hidden"];
      const next = order[(order.indexOf(layerViewMode) + 1) % order.length];
      applyLayerViewMode(next, true);
    } else if (e.key.toLowerCase() === "k") {
      toggleCurrentPositionBookmark();
    } else if (e.key === "/" && bookFormat === "pdf") {
      e.preventDefault();
      openPdfSearchBar();
    } else if (e.key.toLowerCase() === "t") {
      cycleTheme();
    } else if (e.key === "?") {
      if (modalShortcutsHelp) modalShortcutsHelp.classList.add("active");
    } else if (e.key === "Escape") {
      hideSelectionToolbar();
      closeHighlightPopover();
      closePageNotePopover();
      closePdfSearchBar();
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
    if (window.LectoriaReaderKnowledge) {
      window.LectoriaReaderKnowledge.cleanup();
    }
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
        if (epubRendition) {
          epubRendition.resize();
          setTimeout(() => renderEpubHighlights(), 180);
        }
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
        if (epubRendition) {
          epubRendition.resize();
          setTimeout(() => renderEpubHighlights(), 180);
        }
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
  // SUPORTE NATIVO A EPUB COLABORATIVO (Fase 4)
  // =========================================
  const EPUB_MARGIN_MAP = {
    compact: "0 0.5rem",
    normal: "0 1.25rem",
    wide: "0 2.75rem",
  };
  const EPUB_MARGIN_LABELS = {
    compact: "M:C",
    normal: "M:N",
    wide: "M:A",
  };

  function applyEpubTypography() {
    if (!epubRendition) return;
    const marginVal = EPUB_MARGIN_MAP[epubMargin] || EPUB_MARGIN_MAP.normal;
    epubRendition.themes.default({
      "body": {
        "padding": `${marginVal} !important`,
        "font-family": `${epubFontFamily}, Georgia, serif !important`,
        "font-size": `${epubFontSize}% !important`,
        "line-height": `${epubLineHeight} !important`,
      },
    });
    epubRendition.themes.fontSize(`${epubFontSize}%`);
    epubRendition.themes.font(epubFontFamily);
    if (btnEpubLineHeight) {
      const sp = btnEpubLineHeight.querySelector("span");
      if (sp) sp.textContent = epubLineHeight;
    }
    if (btnMobileEpubLineHeight) {
      const sp = btnMobileEpubLineHeight.querySelector("span");
      if (sp) sp.textContent = epubLineHeight;
    }
    if (btnEpubMargin) {
      const sp = btnEpubMargin.querySelector("span");
      if (sp) sp.textContent = EPUB_MARGIN_LABELS[epubMargin] || "M:N";
    }
    if (btnMobileEpubMargin) {
      const sp = btnMobileEpubMargin.querySelector("span");
      if (sp) sp.textContent = EPUB_MARGIN_LABELS[epubMargin] || "M:N";
    }
    setTimeout(() => renderEpubHighlights(), 120);
  }

  function changeEpubFontSize(delta) {
    epubFontSize = Math.min(Math.max(epubFontSize + delta, 60), 220);
    if (epubFontSizeLabel) epubFontSizeLabel.textContent = `${epubFontSize}%`;
    localStorage.setItem("lectoria_epub_font_size", epubFontSize);
    applyEpubTypography();
  }

  function toggleEpubFontFamily() {
    epubFontFamily = epubFontFamily === "serif" ? "sans-serif" : "serif";
    if (btnEpubFontFamily) {
      const sp = btnEpubFontFamily.querySelector("span");
      if (sp) sp.style.fontFamily = epubFontFamily;
    }
    if (btnMobileEpubFont) {
      const sp = btnMobileEpubFont.querySelector("span");
      if (sp) sp.style.fontFamily = epubFontFamily;
    }
    localStorage.setItem("lectoria_epub_font_family", epubFontFamily);
    applyEpubTypography();
  }

  function cycleEpubLineHeight() {
    const options = ["1.4", "1.65", "1.9"];
    epubLineHeight = options[(options.indexOf(String(epubLineHeight)) + 1) % options.length];
    localStorage.setItem("lectoria_epub_line_height", epubLineHeight);
    applyEpubTypography();
  }

  function cycleEpubMargin() {
    const options = ["compact", "normal", "wide"];
    epubMargin = options[(options.indexOf(epubMargin) + 1) % options.length];
    localStorage.setItem("lectoria_epub_margin", epubMargin);
    applyEpubTypography();
  }

  function flattenEpubToc(toc, list = []) {
    if (!Array.isArray(toc)) return list;
    toc.forEach(item => {
      list.push({
        label: item.label ? item.label.trim() : "",
        href: item.href || "",
      });
      if (Array.isArray(item.subitems) && item.subitems.length > 0) {
        flattenEpubToc(item.subitems, list);
      }
    });
    return list;
  }

  function resolveCurrentEpubChapterLabel(location) {
    try {
      const href = location && location.start && location.start.href ? location.start.href : "";
      if (!href || epubTocFlat.length === 0) return "";
      const baseHref = href.split("#")[0];
      const match = epubTocFlat.find(it => it.href && (it.href.split("#")[0] === baseHref || baseHref.endsWith(it.href.split("#")[0])));
      return match && match.label ? match.label : "";
    } catch {
      return "";
    }
  }

  function getEpubAnchorRectFromEvent(e) {
    const epubBox = document.getElementById("epub-viewer");
    const boxRect = epubBox ? epubBox.getBoundingClientRect() : { left: 40, top: 100, width: 300, height: 300 };
    try {
      const target = e && e.target;
      const iframe = epubViewer ? epubViewer.querySelector("iframe") : null;
      if (target && typeof target.getBoundingClientRect === "function" && iframe) {
        const tRect = target.getBoundingClientRect();
        const fRect = iframe.getBoundingClientRect();
        return {
          left: fRect.left + tRect.left,
          top: fRect.top + tRect.top,
          right: fRect.left + tRect.right,
          bottom: fRect.top + tRect.bottom,
          width: tRect.width,
          height: tRect.height,
        };
      }
    } catch (_) {}
    return {
      left: boxRect.left + 32,
      top: boxRect.top + 48,
      right: boxRect.left + 200,
      bottom: boxRect.top + 80,
      width: 168,
      height: 32,
    };
  }

  function renderEpubHighlights() {
    if (!epubRendition || !epubRendition.annotations) return;
    // Remove registros anteriores sem quebrar se o capítulo mudou
    renderedEpubHighlightCfis.forEach(cfi => {
      try {
        epubRendition.annotations.remove(cfi, "highlight");
      } catch (_) {}
    });
    renderedEpubHighlightCfis.clear();

    if (layerViewMode === "hidden") return;

    const highlights = (bookAnnotations.highlights || []).filter(h => {
      if (!h.epub_cfi || !/^epubcfi\(.+\)$/i.test(String(h.epub_cfi).trim())) return false;
      if (layerViewMode === "mine") return h.is_own !== false;
      return true;
    });

    highlights.forEach(h => {
      const isOwn = h.is_own !== false;
      const cfi = String(h.epub_cfi).trim();
      const cls = isOwn ? "epub-hl-own" : "epub-hl-public-other";
      const styles = isOwn
        ? { fill: h.color || "#fef08a", "fill-opacity": "0.38", "mix-blend-mode": "multiply" }
        : {
            fill: h.color || "#fef08a",
            "fill-opacity": "0.28",
            stroke: "#334155",
            "stroke-dasharray": "3,2",
            "stroke-width": "1",
          };
      try {
        epubRendition.annotations.highlight(
          cfi,
          { id: h.id, isOwn },
          (e) => {
            if (e && typeof e.stopPropagation === "function") e.stopPropagation();
            const virtualAnchor = { getBoundingClientRect: () => getEpubAnchorRectFromEvent(e) };
            openHighlightPopover(h, virtualAnchor);
          },
          cls,
          styles
        );
        renderedEpubHighlightCfis.add(cfi);
      } catch (err) {
        // Tratamento defensivo para CFI inválido ou fora do capítulo atual
      }
    });
  }

  function renderEpubNotePins() {
    if (!epubNotePinsLayer) return;
    epubNotePinsLayer.innerHTML = "";
    if (layerViewMode === "hidden") return;

    const notes = (bookAnnotations.notes || []).filter(n => {
      if (layerViewMode === "mine") return n.is_own !== false;
      return true;
    });
    if (notes.length === 0) return;

    const stack = document.createElement("div");
    stack.className = "page-note-pins-stack";

    notes.slice(0, 6).forEach(n => {
      const isOwn = n.is_own !== false;
      const pin = document.createElement("button");
      pin.type = "button";
      pin.className = isOwn ? "page-note-pin" : "page-note-pin is-public-other";
      pin.setAttribute("data-note-id", n.id);
      pin.setAttribute("data-is-own", isOwn ? "true" : "false");

      const dot = document.createElement("span");
      dot.className = "author-avatar-dot";
      dot.style.backgroundColor = isOwn ? "#d4a853" : (n.author_avatar_color || "#38bdf8");

      const textSpan = document.createElement("span");
      textSpan.className = "page-note-pin-text";
      const prefix = isOwn ? (n.visibility === "public" ? "🌐 " : "📝 ") : `${n.author_display_name || "Leitor"}: `;
      textSpan.textContent = `${prefix}${(n.content || "").slice(0, 28)}`;

      pin.title = `${isOwn ? "Sua nota" : (n.author_display_name || "Leitor")}: ${n.content || ""}`;
      pin.appendChild(dot);
      pin.appendChild(textSpan);

      pin.addEventListener("click", (e) => {
        e.stopPropagation();
        openPageNotePopover(n, pin);
      });

      stack.appendChild(pin);
    });

    epubNotePinsLayer.appendChild(stack);
  }

  async function navigateToEpubCfi(cfi, fallbackChapterLabel = null) {
    if (!epubRendition) return;
    const cleanCfi = String(cfi || "").trim();
    const isValidCfi = /^epubcfi\(.+\)$/i.test(cleanCfi);

    if (isValidCfi) {
      try {
        await epubRendition.display(cleanCfi);
        renderEpubHighlights();
        // Destaque temporário suave na localização navegada
        try {
          epubRendition.annotations.highlight(
            cleanCfi,
            { tempFlash: true },
            null,
            "epub-hl-flash",
            { fill: "#38bdf8", "fill-opacity": "0.45" }
          );
          setTimeout(() => {
            try { epubRendition.annotations.remove(cleanCfi, "highlight"); } catch (_) {}
            renderEpubHighlights();
          }, 1800);
        } catch (_) {}
        return;
      } catch (err) {
        // Segue para fallback controlado abaixo
      }
    }

    // Fallback seguro para o capítulo associado sem sobrescrever o progresso atual
    if (fallbackChapterLabel && epubTocFlat.length > 0) {
      const chap = epubTocFlat.find(
        it => it.label && it.label.toLowerCase() === String(fallbackChapterLabel).trim().toLowerCase() && it.href
      );
      if (chap) {
        try {
          await epubRendition.display(chap.href);
          showReaderToast(`CFI indisponível; exibindo capítulo "${chap.label}".`, "info");
          return;
        } catch (_) {}
      }
    }

    showReaderToast("Posição EPUB (CFI) inválida ou incompatível; mantendo posição atual.", "error");
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

    // Configurações salvas de tamanho de fonte, tipografia, espaçamento e margens
    epubFontSize = parseInt(localStorage.getItem("lectoria_epub_font_size"), 10) || 100;
    epubFontFamily = localStorage.getItem("lectoria_epub_font_family") || "serif";
    epubLineHeight = localStorage.getItem("lectoria_epub_line_height") || "1.65";
    epubMargin = localStorage.getItem("lectoria_epub_margin") || "normal";
    if (epubFontSizeLabel) epubFontSizeLabel.textContent = `${epubFontSize}%`;
    if (btnEpubFontFamily) {
      const sp = btnEpubFontFamily.querySelector("span");
      if (sp) sp.style.fontFamily = epubFontFamily;
    }

    if (!epubEventsBound) {
      epubEventsBound = true;
      if (btnEpubFontSmaller) btnEpubFontSmaller.addEventListener("click", () => changeEpubFontSize(-10));
      if (btnEpubFontLarger) btnEpubFontLarger.addEventListener("click", () => changeEpubFontSize(10));
      if (btnEpubFontFamily) btnEpubFontFamily.addEventListener("click", toggleEpubFontFamily);
      if (btnEpubLineHeight) btnEpubLineHeight.addEventListener("click", cycleEpubLineHeight);
      if (btnEpubMargin) btnEpubMargin.addEventListener("click", cycleEpubMargin);
      if (btnEpubThemeSepia) btnEpubThemeSepia.addEventListener("click", () => applyTheme("sepia"));

      if (btnMobileEpubSmaller) btnMobileEpubSmaller.addEventListener("click", () => changeEpubFontSize(-10));
      if (btnMobileEpubLarger) btnMobileEpubLarger.addEventListener("click", () => changeEpubFontSize(10));
      if (btnMobileEpubFont) btnMobileEpubFont.addEventListener("click", toggleEpubFontFamily);
      if (btnMobileEpubLineHeight) btnMobileEpubLineHeight.addEventListener("click", cycleEpubLineHeight);
      if (btnMobileEpubMargin) btnMobileEpubMargin.addEventListener("click", cycleEpubMargin);
      if (btnMobileEpubSepia) btnMobileEpubSepia.addEventListener("click", () => applyTheme("sepia"));

      if (btnTabletFontSmaller) btnTabletFontSmaller.addEventListener("click", () => changeEpubFontSize(-10));
      if (btnTabletFontLarger) btnTabletFontLarger.addEventListener("click", () => changeEpubFontSize(10));
      if (btnTabletFontFamily) btnTabletFontFamily.addEventListener("click", toggleEpubFontFamily);
    }

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

    applyEpubTypography();
    applyEpubTheme(currentTheme);

    // Carrega sumário (TOC) e anotações colaborativas do livro
    epubBook.loaded.navigation.then(nav => {
      epubTocFlat = flattenEpubToc(nav.toc);
      renderEpubToc(nav.toc);
    });
    const epubAnnotationsPromise = reloadBookAnnotations();

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

    // Atualização de localização, renderização de destaques CFI e salvamento de progresso
    epubRendition.on("rendered", () => {
      renderEpubHighlights();
      renderEpubNotePins();
    });

    epubRendition.on("relocated", (location) => {
      epubCfi = location.start.cfi;
      epubCurrentChapterLabel = resolveCurrentEpubChapterLabel(location) || epubCurrentChapterLabel;
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
      renderEpubHighlights();
      renderEpubNotePins();

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

    epubRendition.on("selected", (cfiRange, contents) => {
      try {
        const sel = contents && contents.window ? contents.window.getSelection() : null;
        const text = sel ? sel.toString().trim() : "";
        if (!text) return;
        currentSelectionData = {
          page: Math.max(1, currentPage || 1),
          epubCfi: cfiRange,
          chapterLabel: epubCurrentChapterLabel || null,
          text,
          rects: [],
          context: text.slice(0, 150),
        };
        if (selectionToolbar) {
          selectionToolbar.querySelectorAll(".highlight-color-btn, #btn-add-note-from-selection").forEach(b => {
            b.style.display = "inline-flex";
          });
          if (btnShareSelectionClub) {
            btnShareSelectionClub.style.display = eligibleCollectiveReads.length > 0 ? "inline-flex" : "none";
          }
          const epubBox = document.getElementById("epub-viewer");
          const boxRect = epubBox ? epubBox.getBoundingClientRect() : { left: 20, top: 80, width: window.innerWidth };
          const tbWidth = Math.min(selectionToolbar.offsetWidth || 280, window.innerWidth - 20);
          const maxLeft = Math.max(10, window.innerWidth - tbWidth - 10);
          selectionToolbar.style.left = `${Math.min(maxLeft, Math.max(10, boxRect.left + boxRect.width / 2 - tbWidth / 2))}px`;
          selectionToolbar.style.top = `${Math.max(64, boxRect.top + 12)}px`;
          selectionToolbar.classList.add("active");
        }
      } catch (_) {}
    });

    // Restaura posição via hash #cfi=... (citação compartilhada) ou progresso salvo (CFI)
    const savedLocator = progData && progData.progress_locator;
    if (hashLocator.invalid) {
      showReaderToast("Localizador inválido na URL; exibindo posição padrão.", "error");
    }
    if (hashLocator.cfi) {
      try {
        await epubRendition.display(hashLocator.cfi);
      } catch (_) {
        showReaderToast("Localizador EPUB inválido ou obsoleto; exibindo posição padrão.", "error");
        if (savedLocator) await epubRendition.display(savedLocator).catch(() => epubRendition.display());
        else await epubRendition.display();
      }
    } else if (savedLocator) {
      await epubRendition.display(savedLocator);
    } else {
      await epubRendition.display();
    }

    await epubAnnotationsPromise.catch(() => {});
    renderEpubHighlights();
    renderEpubNotePins();
    updateTabletControls();
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  // --- Carregamento Inicial do Documento ---
  try {
    // Paraleliza a busca de metadados da obra, progresso salvo e sessão atual (isolamento de preferência por usuário)
    const [bookRes, progRes, meRes] = await Promise.all([
      fetch(`/api/books/${bookId}`),
      fetch(`/api/books/${bookId}/progress`).catch(() => null),
      fetch(`/api/auth/me`).catch(() => null),
    ]);
    if (bookRes.status === 401) {
      window.location.href = `/?login=1`;
      return;
    }
    if (!bookRes.ok) throw new Error("Livro não encontrado.");
    if (meRes && meRes.ok) {
      try {
        const meData = await meRes.json();
        if (meData && meData.user && meData.user.id) {
          currentUserId = meData.user.id;
        }
      } catch (_) {}
    }
    try {
      const savedViewMode = localStorage.getItem(getLayerViewStorageKey());
      if (["mine", "all_visible", "hidden"].includes(savedViewMode)) {
        layerViewMode = savedViewMode;
      }
    } catch (_) {}
    if (layerViewModeSelect) layerViewModeSelect.value = layerViewMode;
    if (mobileLayerViewSelect) mobileLayerViewSelect.value = layerViewMode;
    viewportEl.classList.toggle("layers-hidden", layerViewMode === "hidden");

    const bookData = await bookRes.json();
    currentBookTitle = bookData.title || "";
    bookTitleEl.textContent = bookData.title;
    document.title = `${bookData.title} — Lectoria`;

    if (window.LectoriaReaderKnowledge) {
      window.LectoriaReaderKnowledge.init({
        getBookId: () => bookId,
        getBookFormat: () => bookFormat,
        getCurrentPage: () => currentPage,
        getEpubCfi: () => epubCfi,
        persistProgress: () => persistProgress(currentPage, epubCfi),
        goToPage: (pageNum) => goToPage(pageNum),
        navigateToEpubCfi: (cfi, chapLabel) => navigateToEpubCfi(cfi, chapLabel),
        showToast: (msg, isError) => showReaderToast(msg, isError ? "error" : "info"),
        getCsrfToken: () => getCsrfToken(),
      });
    }

    try {
      const urlParams = new URLSearchParams(window.location.search);
      if (urlParams.get("drawer") === "1" && annotationsDrawer) {
        annotationsDrawer.classList.add("active");
        loadDrawerAnnotations({ append: false });
      }
    } catch (_) {}
    loadEligibleCollectiveReads();

    bookFormat = (bookData.format || (bookData.filename && bookData.filename.toLowerCase().endsWith(".epub") ? "epub" : "pdf")).toLowerCase();

    const bookCoverMini = document.getElementById("reader-book-cover");
    if (bookCoverMini) {
      bookCoverMini.src = bookData.cover_url || `/api/books/${encodeURIComponent(bookId)}/cover`;
      bookCoverMini.style.display = "block";
    }

    let progData = null;
    let initialPage = 1;
    if (progRes && progRes.ok) {
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

    // Configuração de alto desempenho para PDF.js (v1.6.1):
    // 1. disableStream: true evita o streaming concorrente não intencional de 100% do arquivo
    // 2. rangeChunkSize: 524288 (512 KB) reduz drasticamente as viagens de ida e volta (RTT)
    // 3. length antecipa a dimensão exata do documento sem esperar handshake extra
    const loadingTask = pdfjsLib.getDocument({
      url: `/api/books/${bookId}/file`,
      rangeChunkSize: 524288,
      disableAutoFetch: true,
      disableStream: true,
      length: (bookData && bookData.filesize) || undefined,
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

    // Carregamento progressivo de anotações: não bloqueia a pintura da 1ª página
    const annotationsPromise = reloadBookAnnotations();

    if (hashLocator.invalid) {
      showReaderToast("Localizador inválido na URL; exibindo posição padrão.", "error");
    } else if (hashLocator.page !== null) {
      if (hashLocator.page >= 1 && hashLocator.page <= totalPages) {
        initialPage = hashLocator.page;
      } else {
        showReaderToast(`Página ${hashLocator.page} fora dos limites (1–${totalPages}); exibindo posição padrão.`, "error");
      }
    }

    currentPage = Math.min(initialPage, totalPages);
    updateReadingProgressBar();
    updateMobilePageControls();
    updateTabletControls();
    setViewMode(viewMode);
    setTimeout(() => {
      isRestoringProgress = false;
    }, 600);
    annotationsPromise.catch((e) => console.warn("Erro ao carregar anotações em segundo plano:", e));
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
