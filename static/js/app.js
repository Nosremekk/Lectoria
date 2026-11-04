/**
 * Lectoria — Aplicação da Biblioteca (Iteração 2)
 * JavaScript puro (Vanilla ES6), rápido e sem dependências pesadas.
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

document.addEventListener("DOMContentLoaded", () => {
  // Elementos do DOM
  const booksContainer = document.getElementById("books-container");
  const booksCountEl = document.getElementById("books-count");
  const searchInput = document.getElementById("search-input");
  const sortSelect = document.getElementById("sort-select");
  const collectionSelect = document.getElementById("collection-select");
  const formatSelect = document.getElementById("format-select");
  const btnScan = document.getElementById("btn-scan");
  const scanText = document.getElementById("scan-text");
  const btnBackup = document.getElementById("btn-backup");
  const backupText = document.getElementById("backup-text");
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

  // Elementos de Autenticação e Perfis
  const adminControls = document.getElementById("admin-controls");
  const userBadgeContainer = document.getElementById("user-badge-container");
  const userDisplayName = document.getElementById("user-display-name");
  const userRoleBadge = document.getElementById("user-role-badge");
  const btnLogout = document.getElementById("btn-logout");
  const btnOpenLogin = document.getElementById("btn-open-login");

  const modalAuth = document.getElementById("modal-auth");
  const authModalTitle = document.getElementById("auth-modal-title");
  const authSetupBanner = document.getElementById("auth-setup-banner");
  const authGroupName = document.getElementById("auth-group-name");
  const authDisplayNameInput = document.getElementById("auth-display-name");
  const authUsernameInput = document.getElementById("auth-username");
  const authPasswordInput = document.getElementById("auth-password");
  const authErrorEl = document.getElementById("auth-error");
  const btnAuthSubmit = document.getElementById("btn-auth-submit");
  const btnCloseAuthModal = document.getElementById("btn-close-auth-modal");

  // Elementos do Gerenciador de Fontes
  const btnManageSources = document.getElementById("btn-manage-sources");
  const modalSources = document.getElementById("modal-sources");
  const btnCloseSourcesModal = document.getElementById("btn-close-sources-modal");
  const btnCloseSources = document.getElementById("btn-close-sources");
  const newSourceLabelInput = document.getElementById("new-source-label");
  const newSourcePathInput = document.getElementById("new-source-path");
  const btnCreateSource = document.getElementById("btn-create-source");
  const sourceErrorEl = document.getElementById("source-error");
  const sourcesListEl = document.getElementById("sources-list");

  // Elementos do Gerenciador de Usuários
  const btnManageUsers = document.getElementById("btn-manage-users");
  const modalUsers = document.getElementById("modal-users");
  const btnCloseUsersModal = document.getElementById("btn-close-users-modal");
  const btnCloseUsers = document.getElementById("btn-close-users");
  const newUserUsernameInput = document.getElementById("new-user-username");
  const newUserNameInput = document.getElementById("new-user-name");
  const newUserPasswordInput = document.getElementById("new-user-password");
  const newUserRoleSelect = document.getElementById("new-user-role");
  const btnCreateUser = document.getElementById("btn-create-user");
  const userErrorEl = document.getElementById("user-error");
  const usersListEl = document.getElementById("users-list");

  // Estado da aplicação
  let currentView = localStorage.getItem("lectoria_view") || "grid";
  let currentTheme = localStorage.getItem("lectoria_theme") || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  let currentStatus = "all";
  let currentCollectionId = "";
  let currentFormat = "";
  let searchTimeout = null;
  let allCollections = [];
  let selectedBookForCol = null;
  let currentUser = null;
  let isSetupRequired = false;

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
    if (currentFormat) params.set("format", currentFormat);

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
        const coverUrl = book.cover_url || `/api/books/${encodeURIComponent(book.id)}/cover`;
        const bookFormat = (book.format || (book.filename && book.filename.toLowerCase().endsWith(".epub") ? "epub" : "pdf")).toUpperCase();

        const collectionsChipsHtml = (book.collections || []).map(c => `
          <span class="collection-chip">${escapeHtml(c.name)}</span>
        `).join("");

        return `
          <article class="book-card" data-id="${book.id}">
            <div class="book-cover-wrapper">
              <a href="/reader/${book.id}" class="book-cover" title="Ler ${escapeHtml(book.title)}">
                <img
                  class="book-cover-img"
                  src="${escapeHtml(coverUrl)}"
                  alt="Capa de ${escapeHtml(book.title)}"
                  loading="lazy"
                >
                <div class="book-cover-badges">
                  <span class="tag-badge tag-format tag-format-${bookFormat.toLowerCase()}">${bookFormat}</span>
                  ${isCompleted ? '<span class="tag-badge tag-completed">Concluído</span>' : ''}
                  ${hasProgress ? '<span class="tag-badge tag-in-progress">Lendo</span>' : ''}
                </div>
              </a>
              <button
                type="button"
                class="btn-cover-customize btn-edit-book-cover"
                data-id="${book.id}"
                data-title="${escapeHtml(book.title)}"
                data-cover-url="${escapeHtml(coverUrl)}"
                data-cover-type="${escapeHtml(book.cover_type || "none")}"
                title="Alterar ou restaurar capa"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/></svg>
                <span>Capa</span>
              </button>
            </div>

            <div class="book-info">
              <a href="/reader/${book.id}" style="text-decoration: none; color: inherit;">
                <h3 class="book-title" title="${escapeHtml(book.title)}">${escapeHtml(book.title)}</h3>
              </a>
              <div class="book-author">${escapeHtml(book.author || "Autor não informado")}</div>

              <div class="book-meta-details">
                <span>${bookFormat === "EPUB" ? `${total} cap.` : `${total} págs.`}</span>
                <span>&bull;</span>
                <span>${formatBytes(book.filesize)}</span>
              </div>

              ${collectionsChipsHtml ? `<div class="book-collections-chips">${collectionsChipsHtml}</div>` : ""}

              <div class="progress-container">
                <div class="progress-bar-bg">
                  <div class="progress-bar-fill ${isCompleted ? "completed" : ""}" style="width: ${percentage}%"></div>
                </div>
                <div class="progress-label">
                  <span>${isCompleted ? "Concluído" : hasProgress ? (bookFormat === "EPUB" ? `${percentage}% concluído` : `Pág. ${page} de ${total}`) : "Não iniciado"}</span>
                  <span>${percentage > 0 ? `${percentage}%` : ""}</span>
                </div>
              </div>
            </div>

            <div class="card-actions-bar">
              <button type="button" class="btn-card-menu btn-edit-book-col" data-id="${book.id}" data-title="${escapeHtml(book.title)}" title="Gerenciar Coleções">
                Coleções
              </button>
              <a href="/api/books/${encodeURIComponent(book.id)}/download" class="btn-card-menu btn-download-book" title="Baixar ${bookFormat}">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg>
                <span>${bookFormat}</span>
              </a>
            </div>
          </article>
        `;
      }).join("");
    } else {
      booksContainer.innerHTML = books.map(book => {
        const percentage = book.progress_percentage || 0;
        const isCompleted = percentage >= 100 || (book.page_count > 0 && book.current_page >= book.page_count);
        const hasProgress = book.current_page && book.current_page > 1 && !isCompleted;
        const total = book.page_count || book.progress_total_pages || 1;
        const page = book.current_page || 1;
        const coverUrl = book.cover_url || `/api/books/${encodeURIComponent(book.id)}/cover`;
        const bookFormat = (book.format || (book.filename && book.filename.toLowerCase().endsWith(".epub") ? "epub" : "pdf")).toUpperCase();

        const collectionsChipsHtml = (book.collections || []).map(c => `
          <span class="collection-chip">${escapeHtml(c.name)}</span>
        `).join(" ");

        return `
          <div class="list-item" data-id="${book.id}">
            <a href="/reader/${book.id}" class="list-cover-link" title="Ler ${escapeHtml(book.title)}">
              <img class="list-cover-thumb" src="${escapeHtml(coverUrl)}" alt="Capa de ${escapeHtml(book.title)}" loading="lazy">
            </a>

            <a href="/reader/${book.id}" class="list-info" style="text-decoration: none; color: inherit;">
              <div class="list-title">
                ${escapeHtml(book.title)}
                <span class="tag-badge tag-format tag-format-${bookFormat.toLowerCase()}" style="margin-left: 0.4rem; vertical-align: middle;">${bookFormat}</span>
              </div>
              <div class="list-author">${escapeHtml(book.author || "Autor não informado")}</div>
              <div class="list-meta">
                ${bookFormat === "EPUB" ? `${total} cap.` : `${total} págs.`} &bull; ${formatBytes(book.filesize)} &bull; Adicionado em ${formatDate(book.created_at)}
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
              <button
                type="button"
                class="btn btn-icon btn-edit-book-cover"
                data-id="${book.id}"
                data-title="${escapeHtml(book.title)}"
                data-cover-url="${escapeHtml(coverUrl)}"
                data-cover-type="${escapeHtml(book.cover_type || "none")}"
                title="Alterar ou restaurar capa"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/></svg>
              </button>
              <a href="/api/books/${encodeURIComponent(book.id)}/download" class="btn btn-icon btn-download-book" title="Baixar ${bookFormat}">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg>
              </a>
              <button type="button" class="btn btn-icon btn-edit-book-col" data-id="${book.id}" data-title="${escapeHtml(book.title)}" title="Gerenciar Coleções">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>
              </button>
              <a href="/reader/${book.id}" class="btn btn-primary" style="padding: 0.35rem 0.7rem;">Ler</a>
            </div>
          </div>
        `;
      }).join("");
    }

    // Vincula botões de personalização de capa
    document.querySelectorAll(".btn-edit-book-cover").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        openBookCoverModal(
          btn.getAttribute("data-id"),
          btn.getAttribute("data-title"),
          btn.getAttribute("data-cover-url"),
          btn.getAttribute("data-cover-type")
        );
      });
    });

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

  if (formatSelect) {
    formatSelect.addEventListener("change", () => {
      currentFormat = formatSelect.value;
      loadBooks();
    });
  }

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

  if (btnBackup) {
    btnBackup.addEventListener("click", async () => {
      btnBackup.disabled = true;
      if (backupText) backupText.textContent = "Gerando...";
      try {
        const res = await fetch("/api/backup", { method: "POST" });
        if (res.ok) {
          const data = await res.json();
          if (backupText) backupText.textContent = "Salvo!";
          alert(`Backup consistente gerado com sucesso em backups/!\nArquivo: ${data.backup.filename}\nTamanho: ${Math.round(data.backup.size_bytes / 1024)} KB\nIntegridade: ${data.backup.integrity}`);
        } else {
          alert("Falha ao gerar backup.");
        }
      } catch (e) {
        alert("Erro de conexão ao gerar backup.");
      } finally {
        setTimeout(() => {
          if (backupText) backupText.textContent = "Backup";
          btnBackup.disabled = false;
        }, 1500);
      }
    });
  }

  // --- Autenticação e Perfis de Usuário ---
  async function initAuth() {
    try {
      const res = await fetch("/api/auth/status");
      if (!res.ok) throw new Error("Erro ao consultar status de autenticação");
      const data = await res.json();
      isSetupRequired = Boolean(data.setup_required);

      if (isSetupRequired) {
        modalAuth.classList.add("active");
        authModalTitle.textContent = "Configuração Inicial do Servidor";
        authSetupBanner.style.display = "block";
        authGroupName.style.display = "block";
        btnAuthSubmit.textContent = "Criar Administrador";
        btnCloseAuthModal.style.display = "none";
        userBadgeContainer.style.display = "none";
        btnOpenLogin.style.display = "none";
        if (adminControls) adminControls.style.display = "none";
      } else if (data.authenticated && data.user) {
        currentUser = data.user;
        userBadgeContainer.style.display = "flex";
        btnOpenLogin.style.display = "none";
        userDisplayName.textContent = currentUser.display_name || currentUser.username;
        userRoleBadge.textContent = currentUser.role === "admin" ? "Admin" : "Leitor";
        userRoleBadge.className = "user-role-tag " + (currentUser.role === "admin" ? "role-admin" : "role-reader");
        
        if (adminControls) {
          adminControls.style.display = currentUser.role === "admin" ? "flex" : "none";
        }
        modalAuth.classList.remove("active");
        await loadCollections();
        loadBooks();
      } else {
        currentUser = null;
        userBadgeContainer.style.display = "none";
        btnOpenLogin.style.display = "inline-flex";
        if (adminControls) adminControls.style.display = "none";
        
        authModalTitle.textContent = "Entrar no Lectoria";
        authSetupBanner.style.display = "none";
        authGroupName.style.display = "none";
        btnAuthSubmit.textContent = "Entrar";
        btnCloseAuthModal.style.display = "inline-flex";

        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get("login") === "1") {
          modalAuth.classList.add("active");
        }
        await loadCollections();
        loadBooks();
      }
    } catch (err) {
      console.warn("Falha ao inicializar autenticação:", err);
      await loadCollections();
      loadBooks();
    }
  }

  async function handleAuthSubmit() {
    const username = authUsernameInput.value.trim();
    const password = authPasswordInput.value;
    const displayName = authDisplayNameInput ? authDisplayNameInput.value.trim() : "";

    authErrorEl.style.display = "none";
    authErrorEl.textContent = "";

    if (!username || !password) {
      authErrorEl.textContent = "Preencha usuário e senha.";
      authErrorEl.style.display = "block";
      return;
    }

    try {
      btnAuthSubmit.disabled = true;
      if (isSetupRequired) {
        const res = await fetch("/api/auth/setup", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            username: username,
            password: password,
            display_name: displayName || username,
          }),
        });
        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || "Erro ao configurar administrador");
        }
      } else {
        const res = await fetch("/api/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            username: username,
            password: password,
          }),
        });
        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || "Usuário ou senha inválidos");
        }
      }

      authPasswordInput.value = "";
      modalAuth.classList.remove("active");
      await initAuth();
    } catch (e) {
      authErrorEl.textContent = e.message;
      authErrorEl.style.display = "block";
    } finally {
      btnAuthSubmit.disabled = false;
    }
  }

  btnAuthSubmit.addEventListener("click", handleAuthSubmit);
  authPasswordInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") handleAuthSubmit();
  });
  authUsernameInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") handleAuthSubmit();
  });

  if (btnOpenLogin) {
    btnOpenLogin.addEventListener("click", () => {
      authErrorEl.style.display = "none";
      modalAuth.classList.add("active");
    });
  }

  if (btnCloseAuthModal) {
    btnCloseAuthModal.addEventListener("click", () => {
      if (!isSetupRequired) modalAuth.classList.remove("active");
    });
  }

  if (btnLogout) {
    btnLogout.addEventListener("click", async () => {
      try {
        await fetch("/api/auth/logout", { method: "POST" });
        window.location.reload();
      } catch {
        alert("Erro ao encerrar sessão.");
      }
    });
  }

  // --- Gerenciador de Fontes de Livros (Admin) ---
  if (btnManageSources) {
    btnManageSources.addEventListener("click", async () => {
      modalSources.classList.add("active");
      await loadSources();
    });
  }
  if (btnCloseSourcesModal) btnCloseSourcesModal.addEventListener("click", () => modalSources.classList.remove("active"));
  if (btnCloseSources) btnCloseSources.addEventListener("click", () => modalSources.classList.remove("active"));

  async function loadSources() {
    try {
      const res = await fetch("/api/sources");
      if (!res.ok) throw new Error("Erro ao listar fontes");
      const data = await res.json();
      renderSourcesList(data.sources || []);
    } catch (e) {
      sourcesListEl.innerHTML = `<p style="color: #ef4444; font-size: 0.85rem;">Falha ao carregar fontes: ${escapeHtml(e.message)}</p>`;
    }
  }

  function renderSourcesList(sources) {
    if (!sources || sources.length === 0) {
      sourcesListEl.innerHTML = `<p style="font-size: 0.85rem; color: var(--text-faint); padding: 0.5rem 0;">Nenhuma fonte configurada.</p>`;
      return;
    }

    sourcesListEl.innerHTML = sources.map(s => {
      const isActive = Boolean(s.is_active);
      const badgeClass = isActive ? "source-badge-active" : "source-badge-inactive";
      const statusText = isActive ? "Ativa" : "Inativa";

      return `
        <div class="source-item" data-id="${s.id}">
          <div class="source-item-info">
            <div style="display: flex; align-items: center; gap: 0.4rem;">
              <strong style="font-size: 0.9rem;">${escapeHtml(s.name || s.label || s.path)}</strong>
              <span class="source-badge ${badgeClass}">${statusText}</span>
            </div>
            <code style="font-size: 0.78rem; color: var(--text-muted); font-family: var(--font-mono);">${escapeHtml(s.path)}</code>
            <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.15rem;">
              ${s.book_count || 0} livros no catálogo &bull; Última varredura: ${s.last_scanned_at ? formatDate(s.last_scanned_at) : "Nunca"}
              ${s.last_error ? `<span style="color: var(--danger); display: block;">Erro: ${escapeHtml(s.last_error)}</span>` : ""}
            </div>
          </div>
          <div style="display: flex; gap: 0.35rem; align-items: center;">
            <button class="btn btn-icon btn-toggle-source" data-id="${s.id}" data-active="${isActive ? "1" : "0"}" title="${isActive ? "Desativar Fonte" : "Ativar Fonte"}">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                ${isActive ? '<path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>' : '<path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8z"/>'}
              </svg>
            </button>
            <button class="btn btn-icon btn-scan-source" data-id="${s.id}" title="Escanear esta fonte agora">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/></svg>
            </button>
            <button class="btn btn-icon btn-danger btn-delete-source" data-id="${s.id}" data-label="${escapeHtml(s.name || s.label || s.path)}" title="Remover Fonte">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
            </button>
          </div>
        </div>
      `;
    }).join("");

    sourcesListEl.querySelectorAll(".btn-toggle-source").forEach(btn => {
      btn.addEventListener("click", async () => {
        const id = btn.getAttribute("data-id");
        const currentlyActive = btn.getAttribute("data-active") === "1";
        try {
          const res = await fetch(`/api/sources/${id}/active`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ is_active: !currentlyActive }),
          });
          if (res.ok) {
            await loadSources();
            loadBooks();
          }
        } catch {
          alert("Erro ao alterar estado da fonte.");
        }
      });
    });

    sourcesListEl.querySelectorAll(".btn-scan-source").forEach(btn => {
      btn.addEventListener("click", async () => {
        const id = btn.getAttribute("data-id");
        btn.disabled = true;
        try {
          const res = await fetch(`/api/sources/${id}/scan`, { method: "POST" });
          if (res.ok) {
            const data = await res.json();
            alert(`Varredura concluída!\nNovos livros: ${data.stats.added}\nAtualizados: ${data.stats.updated}\nTotal ativo: ${data.stats.total}`);
            await loadSources();
            loadBooks();
          } else {
            const err = await res.json();
            alert(err.detail || "Erro ao escanear fonte.");
          }
        } catch {
          alert("Erro de conexão ao escanear fonte.");
        } finally {
          btn.disabled = false;
        }
      });
    });

    sourcesListEl.querySelectorAll(".btn-delete-source").forEach(btn => {
      btn.addEventListener("click", async () => {
        const id = btn.getAttribute("data-id");
        const label = btn.getAttribute("data-label");
        if (confirm(`Remover a fonte "${label}"?\n\nOs arquivos PDF no seu disco NÃO serão apagados. Somente os registros de livros associados a ela serão desvinculados do catálogo do Lectoria.`)) {
          try {
            const res = await fetch(`/api/sources/${id}`, { method: "DELETE" });
            if (res.ok) {
              await loadSources();
              loadBooks();
            } else {
              const err = await res.json();
              alert(err.detail || "Erro ao excluir fonte.");
            }
          } catch {
            alert("Erro de conexão ao remover fonte.");
          }
        }
      });
    });
  }

  if (btnCreateSource) {
    btnCreateSource.addEventListener("click", async () => {
      const label = newSourceLabelInput.value.trim();
      const path = newSourcePathInput.value.trim();
      sourceErrorEl.style.display = "none";

      if (!path) {
        sourceErrorEl.textContent = "O caminho do diretório é obrigatório.";
        sourceErrorEl.style.display = "block";
        return;
      }

      try {
        btnCreateSource.disabled = true;
        const res = await fetch("/api/sources", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ label, path }),
        });
        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || "Falha ao cadastrar fonte");
        }
        newSourceLabelInput.value = "";
        newSourcePathInput.value = "";
        await loadSources();
        loadBooks();
      } catch (e) {
        sourceErrorEl.textContent = e.message;
        sourceErrorEl.style.display = "block";
      } finally {
        btnCreateSource.disabled = false;
      }
    });
  }

  // --- Gerenciador de Usuários (Admin) ---
  if (btnManageUsers) {
    btnManageUsers.addEventListener("click", async () => {
      modalUsers.classList.add("active");
      await loadUsers();
    });
  }
  if (btnCloseUsersModal) btnCloseUsersModal.addEventListener("click", () => modalUsers.classList.remove("active"));
  if (btnCloseUsers) btnCloseUsers.addEventListener("click", () => modalUsers.classList.remove("active"));

  async function loadUsers() {
    try {
      const res = await fetch("/api/users");
      if (!res.ok) throw new Error("Erro ao listar usuários");
      const data = await res.json();
      renderUsersList(data.users || []);
    } catch (e) {
      usersListEl.innerHTML = `<p style="color: #ef4444; font-size: 0.85rem;">Falha ao carregar usuários: ${escapeHtml(e.message)}</p>`;
    }
  }

  function renderUsersList(users) {
    if (!users || users.length === 0) {
      usersListEl.innerHTML = `<p style="font-size: 0.85rem; color: var(--text-faint); padding: 0.5rem 0;">Nenhum usuário cadastrado.</p>`;
      return;
    }

    usersListEl.innerHTML = users.map(u => {
      const isActive = Boolean(u.is_active);
      const isSelf = currentUser && currentUser.id === u.id;
      const roleText = u.role === "admin" ? "Admin" : "Leitor";
      const roleBadgeClass = u.role === "admin" ? "role-admin" : "role-reader";

      return `
        <div class="user-item" data-id="${u.id}">
          <div class="user-item-info">
            <div style="display: flex; align-items: center; gap: 0.4rem;">
              <strong style="font-size: 0.9rem;">${escapeHtml(u.display_name || u.username)}</strong>
              <span class="user-role-tag ${roleBadgeClass}">${roleText}</span>
              ${!isActive ? '<span class="source-badge source-badge-inactive">Inativo</span>' : ""}
              ${isSelf ? '<span style="font-size: 0.72rem; color: var(--accent); font-weight: 600;">(Você)</span>' : ""}
            </div>
            <code style="font-size: 0.78rem; color: var(--text-muted);">@${escapeHtml(u.username)}</code>
          </div>
          <div style="display: flex; gap: 0.35rem; align-items: center;">
            <button class="btn btn-icon btn-user-role" data-id="${u.id}" data-role="${u.role}" title="Alternar Papel (Admin/Leitor)" ${isSelf ? "disabled" : ""}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>
            </button>
            <button class="btn btn-icon btn-user-pass" data-id="${u.id}" data-name="${escapeHtml(u.username)}" title="Redefinir Senha">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M12.65 10C11.83 7.67 9.61 6 7 6c-3.31 0-6 2.69-6 6s2.69 6 6 6c2.61 0 4.83-1.67 5.65-4H17v4h4v-4h2v-4H12.65zM7 14c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2z"/></svg>
            </button>
            <button class="btn btn-icon btn-user-status" data-id="${u.id}" data-active="${isActive ? "1" : "0"}" title="${isActive ? "Desativar Conta" : "Ativar Conta"}" ${isSelf ? "disabled" : ""}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                ${isActive ? '<path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>' : '<path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8z"/>'}
              </svg>
            </button>
          </div>
        </div>
      `;
    }).join("");

    usersListEl.querySelectorAll(".btn-user-role").forEach(btn => {
      btn.addEventListener("click", async () => {
        const id = btn.getAttribute("data-id");
        const currentRole = btn.getAttribute("data-role");
        const newRole = currentRole === "admin" ? "reader" : "admin";
        try {
          const res = await fetch(`/api/users/${id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ role: newRole }),
          });
          if (res.ok) await loadUsers();
          else {
            const err = await res.json();
            alert(err.detail || "Erro ao alterar papel");
          }
        } catch {
          alert("Erro de conexão.");
        }
      });
    });

    usersListEl.querySelectorAll(".btn-user-pass").forEach(btn => {
      btn.addEventListener("click", async () => {
        const id = btn.getAttribute("data-id");
        const name = btn.getAttribute("data-name");
        const newPassword = prompt(`Digite a nova senha para o usuário @${name}:`);
        if (newPassword && newPassword.trim()) {
          try {
            const res = await fetch(`/api/users/${id}`, {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ new_password: newPassword.trim() }),
            });
            if (res.ok) alert("Senha redefinida com sucesso!");
            else {
              const err = await res.json();
              alert(err.detail || "Erro ao redefinir senha");
            }
          } catch {
            alert("Erro de conexão.");
          }
        }
      });
    });

    usersListEl.querySelectorAll(".btn-user-status").forEach(btn => {
      btn.addEventListener("click", async () => {
        const id = btn.getAttribute("data-id");
        const currentlyActive = btn.getAttribute("data-active") === "1";
        try {
          const res = await fetch(`/api/users/${id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ is_active: !currentlyActive }),
          });
          if (res.ok) await loadUsers();
          else {
            const err = await res.json();
            alert(err.detail || "Erro ao alterar status");
          }
        } catch {
          alert("Erro de conexão.");
        }
      });
    });
  }

  if (btnCreateUser) {
    btnCreateUser.addEventListener("click", async () => {
      const username = newUserUsernameInput.value.trim();
      const displayName = newUserNameInput.value.trim();
      const password = newUserPasswordInput.value;
      const role = newUserRoleSelect.value;
      userErrorEl.style.display = "none";

      if (!username || !password) {
        userErrorEl.textContent = "Nome de usuário e senha são obrigatórios.";
        userErrorEl.style.display = "block";
        return;
      }

      try {
        btnCreateUser.disabled = true;
        const res = await fetch("/api/users", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            username,
            display_name: displayName || username,
            password,
            role,
          }),
        });
        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || "Falha ao criar usuário");
        }
        newUserUsernameInput.value = "";
        newUserNameInput.value = "";
        newUserPasswordInput.value = "";
        await loadUsers();
      } catch (e) {
        userErrorEl.textContent = e.message;
        userErrorEl.style.display = "block";
      } finally {
        btnCreateUser.disabled = false;
      }
    });
  }

  // --- Seletor Visual de Diretórios (Admin) ---
  const btnOpenDirPicker = document.getElementById("btn-open-dir-picker");
  const modalDirPicker = document.getElementById("modal-dir-picker");
  const btnCloseDirPicker = document.getElementById("btn-close-dir-picker");
  const btnCancelDirPicker = document.getElementById("btn-cancel-dir-picker");
  const btnConfirmDirPicker = document.getElementById("btn-confirm-dir-picker");
  const btnDirUp = document.getElementById("btn-dir-up");
  const btnNativeDirPicker = document.getElementById("btn-native-dir-picker");
  const dirPickerRootsEl = document.getElementById("dir-picker-roots");
  const dirPickerCurrentPathEl = document.getElementById("dir-picker-current-path");
  const dirPickerPdfCountEl = document.getElementById("dir-picker-pdf-count");
  const dirPickerErrorEl = document.getElementById("dir-picker-error");
  const dirPickerListEl = document.getElementById("dir-picker-list");
  const dirPickerStatusHint = document.getElementById("dir-picker-status-hint");

  let currentBrowsedPath = "";
  let currentBrowsedParent = null;
  let currentBrowsedName = "";

  async function browseServerDirectory(targetPath = "") {
    if (!modalDirPicker) return;
    dirPickerErrorEl.style.display = "none";
    dirPickerErrorEl.textContent = "";

    const url = targetPath
      ? `/api/sources/browse?path=${encodeURIComponent(targetPath)}`
      : "/api/sources/browse";

    try {
      const res = await fetch(url);
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Não foi possível abrir o diretório.");
      }

      currentBrowsedPath = data.current_path || "";
      currentBrowsedParent = data.parent_path || null;
      currentBrowsedName = data.current_name || "";

      dirPickerCurrentPathEl.textContent = currentBrowsedPath;
      const pdfCount = data.current_pdf_count || 0;
      dirPickerPdfCountEl.textContent = `${pdfCount} ${pdfCount === 1 ? "PDF" : "PDFs"}`;
      btnDirUp.disabled = !currentBrowsedParent;
      btnConfirmDirPicker.disabled = !data.can_select_current;

      if (!data.can_select_current && data.current_error) {
        dirPickerStatusHint.textContent = data.current_error;
        dirPickerStatusHint.style.color = "var(--warning)";
      } else {
        dirPickerStatusHint.textContent = "Clique em uma subpasta para abrir ou selecione a pasta atual.";
        dirPickerStatusHint.style.color = "var(--text-muted)";
      }

      // Botão de janela nativa do sistema (Zenity/KDialog) quando disponível localmente
      if (data.native_picker && data.native_picker.available) {
        btnNativeDirPicker.style.display = "inline-flex";
      } else {
        btnNativeDirPicker.style.display = "none";
      }

      // Renderiza atalhos de raízes
      const roots = data.roots || [];
      dirPickerRootsEl.innerHTML = roots.map(r => `
        <button type="button" class="dir-root-chip" data-path="${escapeHtml(r.path)}">
          ${escapeHtml(r.label)}
        </button>
      `).join("");

      dirPickerRootsEl.querySelectorAll(".dir-root-chip").forEach(chip => {
        chip.addEventListener("click", () => {
          browseServerDirectory(chip.getAttribute("data-path"));
        });
      });

      // Renderiza subpastas
      const dirs = data.directories || [];
      if (dirs.length === 0) {
        dirPickerListEl.innerHTML = `
          <div style="padding: 2rem 1rem; text-align: center; color: var(--text-faint); font-size: 0.84rem;">
            Nenhuma subpasta disponível neste diretório.
          </div>
        `;
      } else {
        dirPickerListEl.innerHTML = dirs.map(d => {
          const isDisabled = !d.readable || d.is_symlink;
          const subPdfs = d.pdf_count || 0;
          return `
            <button
              type="button"
              class="dir-entry-row ${isDisabled ? "disabled" : ""}"
              data-path="${escapeHtml(d.path)}"
              ${isDisabled ? "disabled" : ""}
              title="${escapeHtml(d.reason || d.path)}"
            >
              <div class="dir-entry-main">
                <svg class="dir-entry-icon" width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/>
                </svg>
                <span class="dir-entry-name">${escapeHtml(d.name)}</span>
              </div>
              <div class="dir-entry-meta">
                ${subPdfs > 0 ? `<span class="dir-pdf-badge">${subPdfs} PDF${subPdfs > 1 ? "s" : ""}</span>` : ""}
                ${d.reason && isDisabled ? `<span style="color: var(--danger);">${escapeHtml(d.reason)}</span>` : ""}
              </div>
            </button>
          `;
        }).join("");

        dirPickerListEl.querySelectorAll(".dir-entry-row:not(.disabled)").forEach(row => {
          row.addEventListener("click", () => {
            browseServerDirectory(row.getAttribute("data-path"));
          });
        });
      }
    } catch (err) {
      dirPickerErrorEl.textContent = err.message;
      dirPickerErrorEl.style.display = "block";
    }
  }

  if (btnOpenDirPicker) {
    btnOpenDirPicker.addEventListener("click", async () => {
      modalDirPicker.classList.add("active");
      const initialPath = newSourcePathInput.value.trim();
      await browseServerDirectory(initialPath);
    });
  }

  if (btnDirUp) {
    btnDirUp.addEventListener("click", () => {
      if (currentBrowsedParent) {
        browseServerDirectory(currentBrowsedParent);
      }
    });
  }

  if (btnConfirmDirPicker) {
    btnConfirmDirPicker.addEventListener("click", () => {
      if (!currentBrowsedPath) return;
      newSourcePathInput.value = currentBrowsedPath;
      if (!newSourceLabelInput.value.trim() && currentBrowsedName) {
        newSourceLabelInput.value = currentBrowsedName;
      }
      sourceErrorEl.style.display = "none";
      modalDirPicker.classList.remove("active");
    });
  }

  if (btnNativeDirPicker) {
    btnNativeDirPicker.addEventListener("click", async () => {
      btnNativeDirPicker.disabled = true;
      dirPickerErrorEl.style.display = "none";
      try {
        const res = await fetch("/api/sources/pick-native", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ initial_dir: currentBrowsedPath || null }),
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.detail || "Não foi possível abrir o seletor do sistema.");
        }
        if (data.status === "selected" && data.path) {
          newSourcePathInput.value = data.path;
          if (!newSourceLabelInput.value.trim() && data.name_suggestion) {
            newSourceLabelInput.value = data.name_suggestion;
          }
          sourceErrorEl.style.display = "none";
          modalDirPicker.classList.remove("active");
        }
      } catch (err) {
        dirPickerErrorEl.textContent = err.message;
        dirPickerErrorEl.style.display = "block";
      } finally {
        btnNativeDirPicker.disabled = false;
      }
    });
  }

  if (btnCloseDirPicker) btnCloseDirPicker.addEventListener("click", () => modalDirPicker.classList.remove("active"));
  if (btnCancelDirPicker) btnCancelDirPicker.addEventListener("click", () => modalDirPicker.classList.remove("active"));

  // --- Modal de Personalização de Capa do Livro ---
  const modalBookCover = document.getElementById("modal-book-cover");
  const btnCloseCoverModal = document.getElementById("btn-close-cover-modal");
  const btnDoneCoverModal = document.getElementById("btn-done-cover-modal");
  const coverModalPreviewImg = document.getElementById("cover-modal-preview-img");
  const coverModalBookTitle = document.getElementById("cover-modal-book-title");
  const coverModalTypeBadge = document.getElementById("cover-modal-type-badge");
  const coverDropzone = document.getElementById("cover-dropzone");
  const coverFileInput = document.getElementById("cover-file-input");
  const btnRemoveCustomCover = document.getElementById("btn-remove-custom-cover");
  const coverModalError = document.getElementById("cover-modal-error");

  let selectedBookForCover = null;

  function updateCoverTypeBadge(coverType) {
    if (!coverModalTypeBadge) return;
    if (coverType === "custom") {
      coverModalTypeBadge.textContent = "Capa Personalizada";
      coverModalTypeBadge.className = "source-badge source-badge-active";
      if (btnRemoveCustomCover) btnRemoveCustomCover.style.display = "inline-flex";
    } else if (coverType === "extracted") {
      coverModalTypeBadge.textContent = "Extraída do PDF";
      coverModalTypeBadge.className = "source-badge source-badge-inactive";
      if (btnRemoveCustomCover) btnRemoveCustomCover.style.display = "none";
    } else {
      coverModalTypeBadge.textContent = "Capa Editorial Automática";
      coverModalTypeBadge.className = "source-badge source-badge-inactive";
      if (btnRemoveCustomCover) btnRemoveCustomCover.style.display = "none";
    }
  }

  function openBookCoverModal(bookId, bookTitle, coverUrl, coverType) {
    if (!modalBookCover) return;
    selectedBookForCover = bookId;
    coverModalBookTitle.textContent = bookTitle;
    coverModalPreviewImg.src = coverUrl;
    coverModalError.style.display = "none";
    coverModalError.textContent = "";
    updateCoverTypeBadge(coverType);
    modalBookCover.classList.add("active");
  }

  async function uploadCustomCoverFile(file) {
    if (!selectedBookForCover || !file) return;
    coverModalError.style.display = "none";
    coverModalError.textContent = "";

    if (file.size > 5 * 1024 * 1024) {
      coverModalError.textContent = "A imagem excede o tamanho máximo permitido de 5 MB.";
      coverModalError.style.display = "block";
      return;
    }

    try {
      const formData = new FormData();
      formData.append("file", file);

      const res = await fetch(`/api/books/${encodeURIComponent(selectedBookForCover)}/cover`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Falha ao enviar capa personalizada.");
      }

      const newUrl = data.cover_url || `/api/books/${encodeURIComponent(selectedBookForCover)}/cover?v=${Date.now()}`;
      coverModalPreviewImg.src = newUrl;
      updateCoverTypeBadge("custom");
      loadBooks();
    } catch (err) {
      coverModalError.textContent = err.message;
      coverModalError.style.display = "block";
    } finally {
      if (coverFileInput) coverFileInput.value = "";
    }
  }

  if (coverDropzone && coverFileInput) {
    coverDropzone.addEventListener("click", () => coverFileInput.click());
    coverDropzone.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        coverFileInput.click();
      }
    });
    coverFileInput.addEventListener("change", () => {
      if (coverFileInput.files && coverFileInput.files[0]) {
        uploadCustomCoverFile(coverFileInput.files[0]);
      }
    });
    coverDropzone.addEventListener("dragover", (e) => {
      e.preventDefault();
      coverDropzone.classList.add("dragover");
    });
    coverDropzone.addEventListener("dragleave", () => {
      coverDropzone.classList.remove("dragover");
    });
    coverDropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      coverDropzone.classList.remove("dragover");
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
        uploadCustomCoverFile(e.dataTransfer.files[0]);
      }
    });
  }

  if (btnRemoveCustomCover) {
    btnRemoveCustomCover.addEventListener("click", async () => {
      if (!selectedBookForCover) return;
      coverModalError.style.display = "none";
      try {
        const res = await fetch(`/api/books/${encodeURIComponent(selectedBookForCover)}/cover`, {
          method: "DELETE",
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.detail || "Erro ao remover capa personalizada.");
        }
        const newUrl = data.cover_url || `/api/books/${encodeURIComponent(selectedBookForCover)}/cover?v=${Date.now()}`;
        coverModalPreviewImg.src = newUrl;
        updateCoverTypeBadge(data.cover_type || "none");
        loadBooks();
      } catch (err) {
        coverModalError.textContent = err.message;
        coverModalError.style.display = "block";
      }
    });
  }

  if (btnCloseCoverModal) btnCloseCoverModal.addEventListener("click", () => modalBookCover.classList.remove("active"));
  if (btnDoneCoverModal) btnDoneCoverModal.addEventListener("click", () => modalBookCover.classList.remove("active"));

  // Fecha modais com tecla ESC
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      modalCollections.classList.remove("active");
      modalBookCol.classList.remove("active");
      if (!isSetupRequired) modalAuth.classList.remove("active");
      if (modalDirPicker && modalDirPicker.classList.contains("active")) {
        modalDirPicker.classList.remove("active");
        return;
      }
      modalSources.classList.remove("active");
      modalUsers.classList.remove("active");
      if (modalBookCover) modalBookCover.classList.remove("active");
    }
  });

  // Inicialização
  setView(currentView);
  initAuth();
});
