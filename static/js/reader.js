/**
 * Lectoria — Leitor de PDF com PDF.js (Iteração 2)
 * Renderização leve e adaptativa com suporte a Página Única, Rolagem Contínua sob demanda,
 * atalhos de teclado e persistência automática de progresso.
 */

// Configura o worker do PDF.js a partir do caminho estático local
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

  // Elementos da interface
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

  const viewportEl = document.getElementById("reader-viewport");
  const singlePageWrapper = document.getElementById("single-page-wrapper");
  const continuousWrapper = document.getElementById("continuous-wrapper");
  const canvas = document.getElementById("pdf-canvas");
  const ctx = canvas.getContext("2d");

  // Estado do leitor
  let pdfDoc = null;
  let currentPage = 1;
  let totalPages = 1;
  let currentScale = 1.0;
  let zoomMode = "width"; // 'width', 'page', 'custom'
  let viewMode = localStorage.getItem("lectoria_reader_mode") || "single"; // 'single' ou 'continuous'
  let currentTheme = localStorage.getItem("lectoria_theme") || "dark";
  let singleRenderTask = null;
  let saveTimeout = null;

  // Estado da rolagem contínua
  let continuousObserver = null;
  const renderedSlots = new Map(); // pageNum -> { canvas, renderTask }
  let basePageWidth = 600;
  let basePageHeight = 800;

  // Aplica tema
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
    setTimeout(() => {
      saveIndicator.classList.remove("visible");
    }, 2000);
  }

  // --- Persistência do Progresso ---
  async function persistProgress(pageToSave) {
    try {
      const payload = {
        current_page: pageToSave,
        total_pages: totalPages,
      };

      const res = await fetch(`/api/books/${bookId}/progress`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        showSavedIndicator();
      }
    } catch (err) {
      console.warn("Falha ao salvar progresso automaticamente:", err);
    }
  }

  function queueSaveProgress() {
    clearTimeout(saveTimeout);
    saveTimeout = setTimeout(() => {
      persistProgress(currentPage);
    }, 1500);
  }

  function emergencySave() {
    const payload = JSON.stringify({
      current_page: currentPage,
      total_pages: totalPages,
    });

    if (navigator.sendBeacon) {
      const blob = new Blob([payload], { type: "application/json" });
      navigator.sendBeacon(`/api/books/${bookId}/progress`, blob);
    } else {
      fetch(`/api/books/${bookId}/progress`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: payload,
        keepalive: true,
      });
    }
  }

  window.addEventListener("beforeunload", emergencySave);
  window.addEventListener("pagehide", emergencySave);

  // --- Cálculo de Escala (Zoom) ---
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

      canvas.width = viewport.width;
      canvas.height = viewport.height;
      canvas.style.width = `${viewport.width / pixelRatio}px`;
      canvas.style.height = `${viewport.height / pixelRatio}px`;

      singleRenderTask = page.render({
        canvasContext: ctx,
        viewport: viewport,
      });

      await singleRenderTask.promise;
      singleRenderTask = null;

      pageNumInput.value = num;
      btnPrevPage.disabled = num <= 1;
      btnNextPage.disabled = num >= totalPages;

      queueSaveProgress();
    } catch (err) {
      if (err.name !== "RenderingCancelledException") {
        console.error("Erro na renderização da página:", err);
      }
    }
  }

  // --- Renderização no Modo Rolagem Contínua (Virtualizado / Lazy) ---
  function setupContinuousView() {
    continuousWrapper.innerHTML = "";
    renderedSlots.clear();

    if (continuousObserver) {
      continuousObserver.disconnect();
    }

    const availableWidth = viewportEl.clientWidth - 48;
    const scale = zoomMode === "width" ? (availableWidth / basePageWidth) : currentScale;
    const slotWidth = Math.round(basePageWidth * scale);
    const slotHeight = Math.round(basePageHeight * scale);

    // Cria slots de placeholder para cada página
    for (let p = 1; p <= totalPages; p++) {
      const slot = document.createElement("div");
      slot.className = "page-slot";
      slot.id = `page-slot-${p}`;
      slot.setAttribute("data-page-num", p);
      slot.style.width = `${slotWidth}px`;
      slot.style.height = `${slotHeight}px`;

      slot.innerHTML = `<div class="page-slot-placeholder">Página ${p}</div>`;
      continuousWrapper.appendChild(slot);
    }

    // Observer para lazy-render e descarregamento de páginas distantes (economia de memória)
    continuousObserver = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        const pageNum = parseInt(entry.target.getAttribute("data-page-num"), 10);
        if (entry.isIntersecting) {
          renderSlotCanvas(entry.target, pageNum);
        } else {
          // Descarrega páginas que saíram de vista para preservar RAM
          unloadSlotCanvas(entry.target, pageNum);
        }
      });
    }, {
      root: viewportEl,
      rootMargin: "300px 0px 300px 0px",
    });

    document.querySelectorAll(".page-slot").forEach(slot => {
      continuousObserver.observe(slot);
    });

    // Rola até a página inicial
    const initialSlot = document.getElementById(`page-slot-${currentPage}`);
    if (initialSlot) {
      initialSlot.scrollIntoView();
    }
  }

  async function renderSlotCanvas(slotElement, pageNum) {
    if (renderedSlots.has(pageNum)) return;

    try {
      const page = await pdfDoc.getPage(pageNum);
      const unscaledViewport = page.getViewport({ scale: 1.0 });

      let scale = currentScale;
      if (zoomMode === "width") {
        const availableWidth = viewportEl.clientWidth - 48;
        scale = availableWidth / unscaledViewport.width;
      }

      const pixelRatio = window.devicePixelRatio || 1;
      const viewport = page.getViewport({ scale: scale * pixelRatio });

      const c = document.createElement("canvas");
      c.width = viewport.width;
      c.height = viewport.height;
      c.style.width = `${viewport.width / pixelRatio}px`;
      c.style.height = `${viewport.height / pixelRatio}px`;

      slotElement.style.width = `${viewport.width / pixelRatio}px`;
      slotElement.style.height = `${viewport.height / pixelRatio}px`;
      slotElement.innerHTML = "";
      slotElement.appendChild(c);

      const renderTask = page.render({
        canvasContext: c.getContext("2d"),
        viewport: viewport,
      });

      renderedSlots.set(pageNum, { canvas: c, renderTask });
      await renderTask.promise;
    } catch (e) {
      if (e.name !== "RenderingCancelledException") {
        console.warn(`Erro no render do slot ${pageNum}:`, e);
      }
    }
  }

  function unloadSlotCanvas(slotElement, pageNum) {
    if (renderedSlots.has(pageNum)) {
      const item = renderedSlots.get(pageNum);
      if (item.renderTask) {
        item.renderTask.cancel();
      }
      renderedSlots.delete(pageNum);
      slotElement.innerHTML = `<div class="page-slot-placeholder">Página ${pageNum}</div>`;
    }
  }

  // Detecta página visível ao rolar no modo contínuo
  let scrollTimeout = null;
  viewportEl.addEventListener("scroll", () => {
    if (viewMode !== "continuous") return;

    clearTimeout(scrollTimeout);
    scrollTimeout = setTimeout(() => {
      const viewportRect = viewportEl.getBoundingClientRect();
      const centerY = viewportRect.top + viewportRect.height / 2;

      let closestPage = currentPage;
      let minDistance = Infinity;

      document.querySelectorAll(".page-slot").forEach(slot => {
        const r = slot.getBoundingClientRect();
        const slotCenter = r.top + r.height / 2;
        const dist = Math.abs(slotCenter - centerY);
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

  // --- Alternância entre Modos de Visualização ---
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
      if (slot) {
        slot.scrollIntoView({ behavior: "smooth" });
      }
      pageNumInput.value = currentPage;
      queueSaveProgress();
    }
  }

  btnPrevPage.addEventListener("click", () => {
    if (currentPage > 1) goToPage(currentPage - 1);
  });

  btnNextPage.addEventListener("click", () => {
    if (currentPage < totalPages) goToPage(currentPage + 1);
  });

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

  // --- Atalhos de Teclado Abrangentes ---
  window.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;

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
    }
  });

  // Redimensionamento de Janela
  let resizeTimeout = null;
  window.addEventListener("resize", () => {
    if (zoomMode === "width" || zoomMode === "page") {
      clearTimeout(resizeTimeout);
      resizeTimeout = setTimeout(() => {
        if (viewMode === "single") renderSinglePage(currentPage);
        else setupContinuousView();
      }, 150);
    }
  });

  // --- Carregamento Inicial do Documento ---
  try {
    const bookRes = await fetch(`/api/books/${bookId}`);
    if (!bookRes.ok) throw new Error("Livro não encontrado.");
    const bookData = await bookRes.json();
    bookTitleEl.textContent = bookData.title;
    document.title = `${bookData.title} — Lectoria`;

    // Recupera última página salva
    const progRes = await fetch(`/api/books/${bookId}/progress`);
    let initialPage = 1;
    if (progRes.ok) {
      const progData = await progRes.json();
      if (progData.current_page && progData.current_page >= 1) {
        initialPage = progData.current_page;
      }
    }

    // Carrega PDF sob demanda com streaming Range Requests
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

    // Obtém dimensões da primeira página para proporções base
    const firstPage = await pdfDoc.getPage(1);
    const firstVp = firstPage.getViewport({ scale: 1.0 });
    basePageWidth = firstVp.width;
    basePageHeight = firstVp.height;

    currentPage = Math.min(initialPage, totalPages);
    setViewMode(viewMode);
  } catch (err) {
    console.error("Falha ao abrir o documento:", err);
    viewportEl.innerHTML = `
      <div class="empty-state" style="padding-top: 5rem;">
        <h3>Erro ao abrir o documento</h3>
        <p>Não foi possível carregar o arquivo PDF. Verifique se o arquivo ainda existe na biblioteca.</p>
        <p style="margin-top: 1rem;"><a href="/" class="btn btn-primary">Voltar à Biblioteca</a></p>
      </div>
    `;
  }
});
