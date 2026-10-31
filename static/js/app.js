/**
 * Lectoria — Aplicação da Biblioteca (Iteração 2)
 * JavaScript puro (Vanilla ES6), rápido e sem dependências pesadas.
 */

document.addEventListener("DOMContentLoaded", () => {
  // Elementos do DOM
  const booksContainer = document.getElementById("books-container");
  const booksCountEl = document.getElementById("books-count");
  const searchInput = document.getElementById("search-input");
  const sortSelect = document.getElementById("sort-select");
  const collectionSelect = document.getElementById("collection-select");
  const btnScan = document.getElementById("btn-scan");
  const scanText = document.getElementById("scan-text");
  const btnViewGrid = document.getElementById("btn-view-grid");
  const btnViewList = document.getElementById("btn-view-list");
  const btnThemeToggle = document.getElementById("btn-theme-toggle");
  const statusTabs = document.querySelectorAll(".status-tabs .tab-btn");

  // Modais
  const modalCollections = document.getElementById("modal-collections");
  const btnManageCollections = document.getElementById("btn-manage-collections");
  const btnCloseModalCol = document.getElementById("btn-close-modal-col");
  const btnModalColDone = document.getElementById("btn-modal-col-done");
  const btnCreateCol = document.getElementById("btn-create-col");
  const newColNameInput = document.getElementById("new-col-name");
  const collectionsListEl = document.getElementById("collections-list");

  const modalBookCol = document.getElementById("modal-book-collections");
  const btnCloseBookColModal = document.getElementById("btn-close-book-col-modal");
  const btnCancelBookCol = document.getElementById("btn-cancel-book-col");
  const btnSaveBookCol = document.getElementById("btn-save-book-col");
  const bookColModalSubtitle = document.getElementById("book-col-modal-subtitle");
  const bookColCheckboxes = document.getElementById("book-col-checkboxes");

  // Estado da aplicação
  let currentView = localStorage.getItem("lectoria_view") || "grid";
  let currentTheme = localStorage.getItem("lectoria_theme") || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  let currentStatus = "all";
  let currentCollectionId = "";
  let searchTimeout = null;
  let allCollections = [];
  let selectedBookForCol = null;

  // --- Gerenciamento de Tema (Claro / Escuro) ---
  function applyTheme(theme) {
    currentTheme = theme;
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("lectoria_theme", theme);
  }

  btnThemeToggle.addEventListener("click", () => {
    applyTheme(currentTheme === "dark" ? "light" : "dark");
  });

  applyTheme(currentTheme);

  // --- Funções Auxiliares ---
  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function formatBytes(bytes) {
    if (!bytes || bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
  }

  function formatDate(isoStr) {
    if (!isoStr) return "";
    try {
      const d = new Date(isoStr);
      return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "short", year: "numeric" });
    } catch {
      return isoStr;
    }
  }

  function getCoverGradientClass(title) {
    let hash = 0;
    for (let i = 0; i < title.length; i++) {
      hash = (hash << 5) - hash + title.charCodeAt(i);
      hash |= 0;
    }
    const index = (Math.abs(hash) % 7) + 1;
    return `book-cover-bg-${index}`;
  }

  function setView(view) {
    currentView = view;
    localStorage.setItem("lectoria_view", view);

    if (view === "grid") {
      btnViewGrid.classList.add("btn-active");
      btnViewList.classList.remove("btn-active");
      booksContainer.className = "books-grid";
    } else {
      btnViewList.classList.add("btn-active");
      btnViewGrid.classList.remove("btn-active");
      booksContainer.className = "books-list";
    }
  }

  // --- Carregamento de Coleções ---
  async function loadCollections() {
    try {
      const res = await fetch("/api/collections");
      if (!res.ok) throw new Error("Erro ao carregar coleções");
      const data = await res.json();
      allCollections = data.collections || [];

      // Atualiza dropdown de coleções
      const selectedValue = collectionSelect.value;
      collectionSelect.innerHTML = `<option value="">Todas as coleções</option>` +
        allCollections.map(c => `
          <option value="${c.id}" ${selectedValue === String(c.id) ? "selected" : ""}>
            ${escapeHtml(c.name)} (${c.book_count})
          </option>
        `).join("");

      renderCollectionsManagerList();
    } catch (err) {
      console.warn("Falha ao sincronizar coleções:", err);
    }
  }

  function renderCollectionsManagerList() {
    if (allCollections.length === 0) {
      collectionsListEl.innerHTML = `<p style="font-size: 0.85rem; color: var(--text-faint); padding: 0.5rem 0;">Nenhuma coleção criada ainda.</p>`;
      return;
    }

    collectionsListEl.innerHTML = allCollections.map(c => `
      <div class="collection-manager-item" data-id="${c.id}">
        <div>
          <strong style="font-size: 0.9rem;">${escapeHtml(c.name)}</strong>
          <span style="font-size: 0.78rem; color: var(--text-muted); margin-left: 0.4rem;">(${c.book_count} livros)</span>
        </div>
        <div style="display: flex; gap: 0.3rem;">
          <button class="btn btn-icon btn-rename-col" data-id="${c.id}" data-name="${escapeHtml(c.name)}" title="Renomear">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>
          </button>
          <button class="btn btn-icon btn-danger btn-delete-col" data-id="${c.id}" data-name="${escapeHtml(c.name)}" title="Excluir Coleção">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
          </button>
        </div>
      </div>
    `).join("");

    // Listeners de renomear e excluir
    collectionsListEl.querySelectorAll(".btn-rename-col").forEach(btn => {
      btn.addEventListener("click", async () => {
        const id = btn.getAttribute("data-id");
        const currentName = btn.getAttribute("data-name");
        const newName = prompt("Novo nome da coleção:", currentName);
        if (newName && newName.trim() && newName.trim() !== currentName) {
          try {
            const res = await fetch(`/api/collections/${id}`, {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ name: newName.trim() }),
            });
            if (!res.ok) {
              const err = await res.json();
              alert(err.detail || "Erro ao renomear coleção");
              return;
            }
            await loadCollections();
            loadBooks();
          } catch (e) {
            alert("Erro de conexão ao renomear.");
          }
        }
      });
    });

    collectionsListEl.querySelectorAll(".btn-delete-col").forEach(btn => {
      btn.addEventListener("click", async () => {
        const id = btn.getAttribute("data-id");
        const name = btn.getAttribute("data-name");
        if (confirm(`Deseja excluir a coleção "${name}"? Os arquivos PDF NÃO serão apagados.`)) {
          try {
            const res = await fetch(`/api/collections/${id}`, { method: "DELETE" });
            if (res.ok) {
              await loadCollections();
              loadBooks();
            }
          } catch (e) {
            alert("Erro ao excluir coleção.");
          }
        }
      });
    });
  }

  // --- Carregamento e Renderização de Livros ---
  async function loadBooks() {
    const query = searchInput.value.trim();
    const [sortBy, order] = sortSelect.value.split("_");

    const params = new URLSearchParams();
    if (query) params.set("search", query);
    if (sortBy) params.set("sort_by", sortBy);
    if (order) params.set("order", order);
    if (currentStatus && currentStatus !== "all") params.set("status", currentStatus);
    if (currentCollectionId) params.set("collection_id", currentCollectionId);

    try {
      booksCountEl.textContent = "Carregando acervo...";
      const res = await fetch(`/api/books?${params.toString()}`);
      if (!res.ok) throw new Error("Erro ao buscar livros");

      const data = await res.json();
      renderBooks(data.books);
    } catch (err) {
      console.error(err);
      booksContainer.innerHTML = `
        <div class="empty-state">
          <h3>Erro ao carregar a biblioteca</h3>
          <p>Verifique se o servidor está ativo e tente novamente.</p>
        </div>
      `;
      booksCountEl.textContent = "Erro na conexão";
    }
  }

  function renderBooks(books) {
    if (!books || books.length === 0) {
      booksCountEl.textContent = "Nenhum livro encontrado";
      booksContainer.innerHTML = `
        <div class="empty-state">
          <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="margin-bottom: 0.75rem; opacity: 0.6;">
            <path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1-2.5-2.5Z"/>
            <path d="M6 6h10"/>
            <path d="M6 10h10"/>
          </svg>
          <h3>Nenhum livro correspondente</h3>
          <p>Tente alterar os filtros de status ou o termo de pesquisa.</p>
        </div>
      `;
      return;
    }

    booksCountEl.textContent = `${books.length} ${books.length === 1 ? "documento disponível" : "documentos disponíveis"}`;

    if (currentView === "grid") {
      booksContainer.innerHTML = books.map(book => {
        const percentage = book.progress_percentage || 0;
        const isCompleted = percentage >= 100 || (book.page_count > 0 && book.current_page >= book.page_count);
        const hasProgress = book.current_page && book.current_page > 1 && !isCompleted;
        const total = book.page_count || book.progress_total_pages || 1;
        const page = book.current_page || 1;
        const coverBgClass = getCoverGradientClass(book.title);

        const collectionsChipsHtml = (book.collections || []).map(c => `
          <span class="collection-chip">${escapeHtml(c.name)}</span>
        `).join("");

        return `
          <div class="book-card" data-id="${book.id}">
            <a href="/reader/${book.id}" class="book-cover ${coverBgClass}" title="Ler ${escapeHtml(book.title)}">
              <div class="book-cover-decor"></div>
              <div class="book-cover-top">
                <svg class="book-cover-icon" viewBox="0 0 24 24"><path d="M18 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zM6 4h5v8l-2.5-1.5L6 12V4z"/></svg>
                <div class="book-cover-badges">
                  ${isCompleted ? '<span class="tag-badge tag-completed">Concluído</span>' : ''}
                  ${hasProgress ? '<span class="tag-badge tag-in-progress">Lendo</span>' : ''}
                </div>
              </div>

              <div class="book-cover-center">
                <svg class="book-cover-motif" viewBox="0 0 24 24"><path d="M21 5c-1.11-.35-2.33-.5-3.5-.5-1.95 0-4.05.4-5.5 1.5-1.45-1.1-3.55-1.5-5.5-1.5S2.45 4.65 1 5.75v14.4c0 .35.26.65.6.65.1 0 .2-.03.3-.08 1.4-1.07 3.5-1.72 5.1-1.72 1.65 0 3.8.65 5.2 1.72.75-.45 1.7-.72 2.8-.72 1.6 0 3.7.65 5.1 1.72.1.05.2.08.3.08.34 0 .6-.3.6-.65V5.75c-.45-.4-1.05-.6-1.7-.75zM19 18c-1.22 0-2.67-.32-3.5-.82V7.12c.83.5 2.28.88 3.5.88V18z"/></svg>
                <div class="book-cover-title-preview">${escapeHtml(book.title)}</div>
              </div>
            </a>

            <div class="book-info">
              <a href="/reader/${book.id}" style="text-decoration: none; color: inherit;">
                <h3 class="book-title" title="${escapeHtml(book.title)}">${escapeHtml(book.title)}</h3>
              </a>
              <div class="book-author">${escapeHtml(book.author || "Autor não informado")}</div>

              <div class="book-meta-details">
                <span>${total} págs.</span>
                <span>&bull;</span>
                <span>${formatBytes(book.filesize)}</span>
              </div>

              ${collectionsChipsHtml ? `<div class="book-collections-chips">${collectionsChipsHtml}</div>` : ""}

              <div class="progress-container">
                <div class="progress-bar-bg">
                  <div class="progress-bar-fill ${isCompleted ? "completed" : ""}" style="width: ${percentage}%"></div>
                </div>
                <div class="progress-label">
                  <span>${isCompleted ? "Concluído" : hasProgress ? `Pág. ${page} de ${total}` : "Não iniciado"}</span>
                  <span>${percentage > 0 ? `${percentage}%` : ""}</span>
                </div>
              </div>
            </div>

            <div class="card-actions-bar">
              <button class="btn-card-menu btn-edit-book-col" data-id="${book.id}" data-title="${escapeHtml(book.title)}" title="Gerenciar Coleções">
                Coleções &bull;&bull;&bull;
              </button>
            </div>
          </div>
        `;
      }).join("");
    } else {
      booksContainer.innerHTML = books.map(book => {
        const percentage = book.progress_percentage || 0;
        const isCompleted = percentage >= 100 || (book.page_count > 0 && book.current_page >= book.page_count);
        const hasProgress = book.current_page && book.current_page > 1 && !isCompleted;
        const total = book.page_count || book.progress_total_pages || 1;
        const page = book.current_page || 1;

        const collectionsChipsHtml = (book.collections || []).map(c => `
          <span class="collection-chip">${escapeHtml(c.name)}</span>
        `).join(" ");

        return `
          <div class="list-item">
            <a href="/reader/${book.id}" class="list-info" style="text-decoration: none; color: inherit;">
              <div class="list-title">${escapeHtml(book.title)}</div>
              <div class="list-author">${escapeHtml(book.author || "Autor não informado")}</div>
              <div class="list-meta">
                ${total} págs. &bull; ${formatBytes(book.filesize)} &bull; Adicionado em ${formatDate(book.created_at)}
                ${book.progress_updated_at ? `&bull; Lido em ${formatDate(book.progress_updated_at)}` : ""}
              </div>
              ${collectionsChipsHtml ? `<div class="book-collections-chips">${collectionsChipsHtml}</div>` : ""}
            </a>

            <div class="list-progress">
              <div class="progress-bar-bg">
                <div class="progress-bar-fill ${isCompleted ? "completed" : ""}" style="width: ${percentage}%"></div>
              </div>
              <div class="progress-label">
                <span>${isCompleted ? "Concluído" : hasProgress ? `Pág. ${page} de ${total}` : "Não iniciado"}</span>
                <span>${percentage > 0 ? `${percentage}%` : ""}</span>
              </div>
            </div>

            <div class="list-actions">
              <button class="btn btn-icon btn-edit-book-col" data-id="${book.id}" data-title="${escapeHtml(book.title)}" title="Gerenciar Coleções">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>
              </button>
              <a href="/reader/${book.id}" class="btn btn-primary" style="padding: 0.35rem 0.65rem;">Ler</a>
            </div>
          </div>
        `;
      }).join("");
    }

    // Vincula botões de atribuição de coleções
    document.querySelectorAll(".btn-edit-book-col").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        openBookCollectionsModal(btn.getAttribute("data-id"), btn.getAttribute("data-title"));
      });
    });
  }

  // --- Modal: Atribuição de Coleções ao Livro ---
  async function openBookCollectionsModal(bookId, bookTitle) {
    selectedBookForCol = bookId;
    bookColModalSubtitle.textContent = `Documento: "${bookTitle}"`;

    if (allCollections.length === 0) {
      bookColCheckboxes.innerHTML = `<p style="font-size: 0.85rem; color: var(--text-faint);">Crie uma coleção primeiro para poder associar este livro.</p>`;
      modalBookCol.classList.add("active");
      return;
    }

    // Busca detalhes atuais do livro para saber quais coleções estão marcadas
    try {
      const res = await fetch(`/api/books/${bookId}`);
      const bookData = await res.json();
      const activeColIds = new Set((bookData.collections || []).map(c => c.id));

      bookColCheckboxes.innerHTML = allCollections.map(c => `
        <label class="checkbox-item">
          <input type="checkbox" value="${c.id}" ${activeColIds.has(c.id) ? "checked" : ""}>
          <span style="font-size: 0.88rem;">${escapeHtml(c.name)}</span>
        </label>
      `).join("");

      modalBookCol.classList.add("active");
    } catch (e) {
      alert("Erro ao carregar coleções do documento.");
    }
  }

  btnSaveBookCol.addEventListener("click", async () => {
    if (!selectedBookForCol) return;
    const checkedInputs = bookColCheckboxes.querySelectorAll("input[type='checkbox']:checked");
    const colIds = Array.from(checkedInputs).map(i => parseInt(i.value, 10));

    try {
      const res = await fetch(`/api/books/${selectedBookForCol}/collections`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ collection_ids: colIds }),
      });

      if (res.ok) {
        modalBookCol.classList.remove("active");
        await loadCollections();
        loadBooks();
      } else {
        alert("Erro ao atualizar coleções do livro.");
      }
    } catch (e) {
      alert("Erro de conexão ao salvar.");
    }
  });

  btnCloseBookColModal.addEventListener("click", () => modalBookCol.classList.remove("active"));
  btnCancelBookCol.addEventListener("click", () => modalBookCol.classList.remove("active"));

  // --- Modal: Gerenciamento Geral de Coleções ---
  btnManageCollections.addEventListener("click", () => {
    modalCollections.classList.add("active");
    renderCollectionsManagerList();
  });

  btnCloseModalCol.addEventListener("click", () => modalCollections.classList.remove("active"));
  btnModalColDone.addEventListener("click", () => modalCollections.classList.remove("active"));

  btnCreateCol.addEventListener("click", async () => {
    const name = newColNameInput.value.trim();
    if (!name) return;

    try {
      const res = await fetch("/api/collections", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });

      if (res.status === 201) {
        newColNameInput.value = "";
        await loadCollections();
        loadBooks();
      } else {
        const err = await res.json();
        alert(err.detail || "Erro ao criar coleção.");
      }
    } catch (e) {
      alert("Erro de comunicação com o servidor.");
    }
  });

  // --- Filtros e Controles ---
  statusTabs.forEach(tab => {
    tab.addEventListener("click", () => {
      statusTabs.forEach(t => t.classList.remove("active"));
      tab.classList.add("active");
      currentStatus = tab.getAttribute("data-status");
      loadBooks();
    });
  });

  collectionSelect.addEventListener("change", () => {
    currentCollectionId = collectionSelect.value;
    loadBooks();
  });

  searchInput.addEventListener("input", () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(loadBooks, 250);
  });

  sortSelect.addEventListener("change", loadBooks);

  btnViewGrid.addEventListener("click", () => {
    setView("grid");
    loadBooks();
  });

  btnViewList.addEventListener("click", () => {
    setView("list");
    loadBooks();
  });

  btnScan.addEventListener("click", async () => {
    btnScan.disabled = true;
    scanText.textContent = "Escaneando...";

    try {
      const res = await fetch("/api/scan", { method: "POST" });
      const data = await res.json();
      const stats = data.stats;
      scanText.textContent = `+${stats.added} novos`;
      setTimeout(() => {
        scanText.textContent = "Escanear";
        btnScan.disabled = false;
      }, 2000);
      await loadCollections();
      loadBooks();
    } catch (err) {
      console.error(err);
      scanText.textContent = "Erro ao escanear";
      setTimeout(() => {
        scanText.textContent = "Escanear";
        btnScan.disabled = false;
      }, 2000);
    }
  });

  // Fecha modais com tecla ESC
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      modalCollections.classList.remove("active");
      modalBookCol.classList.remove("active");
    }
  });

  // Inicialização
  setView(currentView);
  loadCollections();
  loadBooks();
});
