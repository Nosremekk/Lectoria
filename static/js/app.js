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

  // Elementos da Atualização v1.4.0 & v1.5.0 — Descoberta, Social e Painel Admin
  const tabNavDiscovery = document.getElementById("tab-nav-discovery");
  const tabNavLibrary = document.getElementById("tab-nav-library");
  const tabNavSocial = document.getElementById("tab-nav-social");
  const tabNavAdmin = document.getElementById("tab-nav-admin");
  const discoverySection = document.getElementById("discovery-section");
  const librarySection = document.getElementById("library-section");
  const socialSection = document.getElementById("social-section");
  const adminSection = document.getElementById("admin-section");
  const shelvesContainer = document.getElementById("shelves-container");
  const discoveryLoading = document.getElementById("discovery-loading");
  const btnOpenProfile = document.getElementById("btn-open-profile");

  const categorySelect = document.getElementById("category-select");
  const subcategorySelect = document.getElementById("subcategory-select");
  const genreSelect = document.getElementById("genre-select");
  const originSelect = document.getElementById("origin-select");
  const idStatusSelect = document.getElementById("id-status-select");
  const btnClearFilters = document.getElementById("btn-clear-filters");

  // Modal: Detalhes da Obra
  const modalBookDetails = document.getElementById("modal-book-details");
  const btnCloseDetailsModal = document.getElementById("btn-close-details-modal");
  const detailsCoverImg = document.getElementById("details-cover-img");
  const detailsFormatBadge = document.getElementById("details-format-badge");
  const btnDetailsRead = document.getElementById("btn-details-read");
  const btnDetailsEditCover = document.getElementById("btn-details-edit-cover");
  const btnDetailsCollections = document.getElementById("btn-details-collections");
  const btnDetailsDownload = document.getElementById("btn-details-download");
  const detailsIdStatusBadge = document.getElementById("details-id-status-badge");
  const detailsTitle = document.getElementById("details-title");
  const detailsAuthor = document.getElementById("details-author");
  const detailsTaxonomyTags = document.getElementById("details-taxonomy-tags");
  const detailsDescription = document.getElementById("details-description");
  const detailsMetaPublisher = document.getElementById("details-meta-publisher");
  const detailsMetaYear = document.getElementById("details-meta-year");
  const detailsMetaLanguage = document.getElementById("details-meta-language");
  const detailsMetaIsbn13 = document.getElementById("details-meta-isbn13");
  const detailsMetaIsbn10 = document.getElementById("details-meta-isbn10");
  const detailsMetaEdition = document.getElementById("details-meta-edition");
  const detailsMetaFile = document.getElementById("details-meta-file");
  const detailsReconcileQuery = document.getElementById("details-reconcile-query");
  const btnDetailsSearchCandidates = document.getElementById("btn-details-search-candidates");
  const detailsCandidatesLoading = document.getElementById("details-candidates-loading");
  const detailsCandidatesList = document.getElementById("details-candidates-list");

  // Formulário de Edição Manual
  const editFieldTitle = document.getElementById("edit-field-title");
  const editFieldAuthor = document.getElementById("edit-field-author");
  const editFieldPublisher = document.getElementById("edit-field-publisher");
  const editFieldYear = document.getElementById("edit-field-year");
  const editFieldLanguage = document.getElementById("edit-field-language");
  const editFieldIsbn13 = document.getElementById("edit-field-isbn13");
  const editFieldIsbn10 = document.getElementById("edit-field-isbn10");
  const editFieldDesc = document.getElementById("edit-field-desc");
  const lockTitle = document.getElementById("lock-title");
  const lockAuthor = document.getElementById("lock-author");
  const lockPublisher = document.getElementById("lock-publisher");
  const lockIsbn = document.getElementById("lock-isbn");
  const lockDesc = document.getElementById("lock-desc");
  const btnSaveManualMetadata = document.getElementById("btn-save-manual-metadata");

  // Modal: Central de Metadados
  const btnMetadataCenter = document.getElementById("btn-metadata-center");
  const modalMetadataCenter = document.getElementById("modal-metadata-center");
  const btnCloseMetaCenterModal = document.getElementById("btn-close-meta-center-modal");
  const btnDoneMetaCenterModal = document.getElementById("btn-done-meta-center-modal");
  const queueStatusBadge = document.getElementById("queue-status-badge");
  const queueProgressBar = document.getElementById("queue-progress-bar");
  const queueMetricTotal = document.getElementById("queue-metric-total");
  const queueMetricProcessed = document.getElementById("queue-metric-processed");
  const queueMetricIdentified = document.getElementById("queue-metric-identified");
  const queueMetricReview = document.getElementById("queue-metric-review");
  const queueMetricNotfound = document.getElementById("queue-metric-notfound");
  const queueMetricFailed = document.getElementById("queue-metric-failed");
  const btnQueueEnqueuePending = document.getElementById("btn-queue-enqueue-pending");
  const btnQueueEnqueueAll = document.getElementById("btn-queue-enqueue-all");
  const btnQueuePause = document.getElementById("btn-queue-pause");
  const btnQueueResume = document.getElementById("btn-queue-resume");
  const btnQueueCancel = document.getElementById("btn-queue-cancel");

  // Estado da aplicação
  let currentMainTab = localStorage.getItem("lectoria_main_tab") || "discovery";
  let activeBookForDetails = null;
  let queuePollInterval = null;
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
    if (currentStatus === "favorites") {
      params.set("favorite_only", "true");
    } else if (currentStatus === "want_to_read") {
      params.set("want_to_read_only", "true");
    } else if (currentStatus && currentStatus !== "all") {
      params.set("status", currentStatus);
    }
    if (currentCollectionId) params.set("collection_id", currentCollectionId);
    if (currentFormat) params.set("format", currentFormat);

    if (categorySelect && categorySelect.value) params.set("category", categorySelect.value);
    if (subcategorySelect && subcategorySelect.value) params.set("subcategory", subcategorySelect.value);
    if (genreSelect && genreSelect.value) params.set("genre", genreSelect.value);
    if (originSelect && originSelect.value) params.set("origin", originSelect.value);
    if (idStatusSelect && idStatusSelect.value) params.set("identification_status", idStatusSelect.value);

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
              <button type="button" class="btn-card-menu btn-book-details" data-id="${book.id}" title="Ver Detalhes e Metadados">
                Detalhes
              </button>
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
                class="btn btn-subtle btn-book-details"
                data-id="${book.id}"
                title="Ver Detalhes e Metadados"
              >
                Detalhes
              </button>
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

    // Vincula botões de detalhes da obra
    document.querySelectorAll(".btn-book-details").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        openBookDetails(btn.getAttribute("data-id"));
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
        if (tabNavAdmin) {
          tabNavAdmin.style.display = currentUser.role === "admin" ? "inline-flex" : "none";
        }
        modalAuth.classList.remove("active");
        await loadCollections();
        loadTaxonomyTree();
        loadBooks();
        if (currentMainTab === "discovery") loadShelves();
        else if (currentMainTab === "social") loadSocialFeed();
        else if (currentMainTab === "admin" && currentUser.role === "admin") loadAdminPanel();
      } else {
        currentUser = null;
        userBadgeContainer.style.display = "none";
        btnOpenLogin.style.display = "inline-flex";
        if (adminControls) adminControls.style.display = "none";
        if (tabNavAdmin) tabNavAdmin.style.display = "none";
        if (currentMainTab === "admin") {
          setMainTab("discovery");
        }
        
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
        loadTaxonomyTree();
        loadBooks();
        if (currentMainTab === "discovery") loadShelves();
      }
    } catch (err) {
      console.warn("Falha ao inicializar autenticação:", err);
      await loadCollections();
      loadBooks();
      if (currentMainTab === "discovery") loadShelves();
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

  // =========================================================================
  // IMPLEMENTAÇÃO DE DESCOBERTA, PRATELEIRAS E METADADOS (v1.4.0)
  // =========================================================================

  function setMainTab(tab) {
    if (tab === "admin" && (!currentUser || currentUser.role !== "admin")) {
      tab = "discovery";
    }
    currentMainTab = tab;
    localStorage.setItem("lectoria_main_tab", tab);

    if (tabNavDiscovery) tabNavDiscovery.classList.toggle("active", tab === "discovery");
    if (tabNavLibrary) tabNavLibrary.classList.toggle("active", tab === "library");
    if (tabNavSocial) tabNavSocial.classList.toggle("active", tab === "social");
    if (tabNavAdmin) tabNavAdmin.classList.toggle("active", tab === "admin");

    if (discoverySection) discoverySection.style.display = tab === "discovery" ? "flex" : "none";
    if (librarySection) librarySection.style.display = tab === "library" ? "block" : "none";
    if (socialSection) socialSection.style.display = tab === "social" ? "block" : "none";
    if (adminSection) adminSection.style.display = tab === "admin" ? "block" : "none";

    if (tab === "discovery") {
      loadShelves();
    } else if (tab === "library") {
      loadBooks();
    } else if (tab === "social") {
      loadSocialFeed();
    } else if (tab === "admin") {
      loadAdminPanel();
    }
  }

  if (tabNavDiscovery) tabNavDiscovery.addEventListener("click", () => setMainTab("discovery"));
  if (tabNavLibrary) tabNavLibrary.addEventListener("click", () => setMainTab("library"));
  if (tabNavSocial) tabNavSocial.addEventListener("click", () => setMainTab("social"));
  if (tabNavAdmin) tabNavAdmin.addEventListener("click", () => setMainTab("admin"));

  // --- Prateleiras de Descoberta ---
  async function loadShelves() {
    if (!shelvesContainer) return;
    if (discoveryLoading) discoveryLoading.style.display = "block";

    try {
      const res = await fetch("/api/discovery/shelves");
      if (!res.ok) throw new Error("Erro ao carregar prateleiras de descoberta.");
      const data = await res.json();
      const shelves = data.shelves || [];

      if (discoveryLoading) discoveryLoading.style.display = "none";

      if (shelves.length === 0) {
        shelvesContainer.innerHTML = `
          <div class="empty-state">
            <h3>Nenhuma prateleira disponível</h3>
            <p>Adicione livros ao seu acervo para gerar recomendações e categorias de descoberta.</p>
          </div>
        `;
        return;
      }

      shelvesContainer.innerHTML = shelves.map(shelf => {
        const booksHtml = (shelf.books || []).map(b => {
          const coverUrl = b.cover_url || `/api/books/${encodeURIComponent(b.id)}/cover`;
          const bookFormat = (b.format || "pdf").toUpperCase();
          const primaryCat = (b.taxonomy && b.taxonomy.categories && b.taxonomy.categories[0]) || "";
          return `
            <div class="shelf-card" data-id="${b.id}" tabindex="0" role="button" aria-label="Abrir detalhes de ${escapeHtml(b.title)}">
              <div class="shelf-cover-wrapper">
                <img class="shelf-cover-img" src="${escapeHtml(coverUrl)}" alt="Capa de ${escapeHtml(b.title)}" loading="lazy">
                <div class="book-cover-badges">
                  <span class="tag-badge tag-format tag-format-${bookFormat.toLowerCase()}">${bookFormat}</span>
                </div>
              </div>
              <div class="shelf-info">
                <div class="shelf-book-title" title="${escapeHtml(b.title)}">${escapeHtml(b.title)}</div>
                <div class="shelf-book-author" title="${escapeHtml(b.author || '')}">${escapeHtml(b.author || 'Autor não informado')}</div>
                ${primaryCat ? `<div class="shelf-book-tag">${escapeHtml(primaryCat)}</div>` : ''}
              </div>
            </div>
          `;
        }).join("");

        return `
          <section class="shelf-wrapper" data-shelf-id="${escapeHtml(shelf.id)}">
            <div class="shelf-header">
              <div>
                <h2 class="shelf-title">${escapeHtml(shelf.title)}</h2>
                ${shelf.subtitle ? `<div class="shelf-subtitle">${escapeHtml(shelf.subtitle)}</div>` : ""}
              </div>
              <div class="shelf-nav-buttons">
                <button class="shelf-nav-btn btn-shelf-prev" title="Rolar para esquerda" aria-label="Rolar para esquerda">&larr;</button>
                <button class="shelf-nav-btn btn-shelf-next" title="Rolar para direita" aria-label="Rolar para direita">&rarr;</button>
              </div>
            </div>
            <div class="shelf-row">
              ${booksHtml}
            </div>
          </section>
        `;
      }).join("");

      // Vincula navegação horizontal suave por botões
      shelvesContainer.querySelectorAll(".shelf-wrapper").forEach(wrapper => {
        const row = wrapper.querySelector(".shelf-row");
        const btnPrev = wrapper.querySelector(".btn-shelf-prev");
        const btnNext = wrapper.querySelector(".btn-shelf-next");

        if (btnPrev && row) {
          btnPrev.addEventListener("click", () => {
            row.scrollBy({ left: -340, behavior: "smooth" });
          });
        }
        if (btnNext && row) {
          btnNext.addEventListener("click", () => {
            row.scrollBy({ left: 340, behavior: "smooth" });
          });
        }
      });

      // Vincula abertura de detalhes ao clicar no card da prateleira
      shelvesContainer.querySelectorAll(".shelf-card").forEach(card => {
        card.addEventListener("click", () => {
          openBookDetails(card.getAttribute("data-id"));
        });
        card.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openBookDetails(card.getAttribute("data-id"));
          }
        });
      });
    } catch (err) {
      console.error(err);
      if (discoveryLoading) discoveryLoading.style.display = "none";
      shelvesContainer.innerHTML = `
        <div class="empty-state">
          <h3>Erro ao carregar prateleiras de descoberta</h3>
          <p>Tente novamente mais tarde.</p>
        </div>
      `;
    }
  }

  // --- Árvore Taxonômica e Filtros ---
  async function loadTaxonomyTree() {
    try {
      const res = await fetch("/api/taxonomy/tree");
      if (!res.ok) return;
      const data = await res.json();
      const tree = data.tree || [];

      if (categorySelect) {
        categorySelect.innerHTML = `<option value="">Todas as Categorias</option>` +
          tree.map(cat => `<option value="${escapeHtml(cat.category)}">${escapeHtml(cat.category)} (${cat.total_books})</option>`).join("");
      }

      // Atualiza subcategorias dinamicamente ao selecionar categoria
      if (categorySelect && subcategorySelect) {
        categorySelect.addEventListener("change", () => {
          const selectedCatName = categorySelect.value;
          const found = tree.find(t => t.category === selectedCatName);
          if (found && found.subcategories && found.subcategories.length > 0) {
            subcategorySelect.innerHTML = `<option value="">Todas as Subcategorias</option>` +
              found.subcategories.map(s => `<option value="${escapeHtml(s.name)}">${escapeHtml(s.name)} (${s.book_count})</option>`).join("");
          } else {
            subcategorySelect.innerHTML = `<option value="">Todas as Subcategorias</option>`;
          }
          loadBooks();
        });
      }

      if (subcategorySelect) subcategorySelect.addEventListener("change", () => loadBooks());
      if (genreSelect) genreSelect.addEventListener("change", () => loadBooks());
      if (originSelect) originSelect.addEventListener("change", () => loadBooks());
      if (idStatusSelect) idStatusSelect.addEventListener("change", () => loadBooks());

      if (btnClearFilters) {
        btnClearFilters.addEventListener("click", () => {
          if (searchInput) searchInput.value = "";
          if (collectionSelect) collectionSelect.value = "";
          if (formatSelect) formatSelect.value = "";
          if (categorySelect) categorySelect.value = "";
          if (subcategorySelect) subcategorySelect.innerHTML = `<option value="">Todas as Subcategorias</option>`;
          if (genreSelect) genreSelect.value = "";
          if (originSelect) originSelect.value = "";
          if (idStatusSelect) idStatusSelect.value = "";
          currentCollectionId = "";
          currentFormat = "";
          loadBooks();
        });
      }
    } catch (e) {
      console.error("Erro ao carregar árvore de taxonomia:", e);
    }
  }

  // --- Modal: Detalhes Ricos da Obra e Reconciliação ---
  async function openBookDetails(bookId) {
    if (!modalBookDetails) return;
    try {
      const res = await fetch(`/api/books/${bookId}`);
      if (!res.ok) throw new Error("Livro não encontrado");
      const book = await res.json();
      activeBookForDetails = book;

      if (detailsCoverImg) {
        detailsCoverImg.src = book.cover_url || `/api/books/${encodeURIComponent(book.id)}/cover`;
      }
      if (detailsFormatBadge) {
        const fmt = (book.format || "pdf").toUpperCase();
        detailsFormatBadge.textContent = fmt;
        detailsFormatBadge.className = `tag-badge tag-format tag-format-${fmt.toLowerCase()}`;
      }
      if (btnDetailsRead) {
        btnDetailsRead.href = `/reader/${book.id}`;
      }
      if (btnDetailsDownload) {
        btnDetailsDownload.href = `/api/books/${encodeURIComponent(book.id)}/download`;
      }
      if (detailsTitle) detailsTitle.textContent = book.title || "Sem título";
      if (detailsAuthor) detailsAuthor.textContent = book.author || "Autor não informado";

      // Status de Identificação
      if (detailsIdStatusBadge) {
        const idStatus = book.identification_status || "pending";
        const score = Math.round((book.identification_score || 0));
        if (idStatus === "identified") {
          detailsIdStatusBadge.textContent = `Identificado (${score}%)`;
          detailsIdStatusBadge.className = "source-badge source-badge-active";
        } else if (idStatus === "needs_review") {
          detailsIdStatusBadge.textContent = `Revisão Necessária (${score}%)`;
          detailsIdStatusBadge.className = "source-badge";
          detailsIdStatusBadge.style.backgroundColor = "rgba(217, 155, 38, 0.2)";
          detailsIdStatusBadge.style.color = "var(--warning)";
        } else if (idStatus === "manual_override") {
          detailsIdStatusBadge.textContent = "Edição Manual";
          detailsIdStatusBadge.className = "source-badge source-badge-active";
        } else {
          detailsIdStatusBadge.textContent = "Pendente";
          detailsIdStatusBadge.className = "source-badge source-badge-inactive";
        }
      }

      // Badges Taxonômicas
      if (detailsTaxonomyTags) {
        const tax = book.taxonomy || {};
        let tagsHtml = "";
        (tax.categories || []).forEach(cat => {
          tagsHtml += `<span class="badge-taxonomy badge-category">${escapeHtml(cat)}</span>`;
        });
        (tax.subcategories || []).forEach(subcat => {
          tagsHtml += `<span class="badge-taxonomy badge-category">${escapeHtml(subcat)}</span>`;
        });
        if (tax.genre) {
          tagsHtml += `<span class="badge-taxonomy badge-genre">${escapeHtml(tax.genre)}</span>`;
        }
        if (tax.origin) {
          tagsHtml += `<span class="badge-taxonomy badge-origin">${escapeHtml(tax.origin)}</span>`;
        }
        detailsTaxonomyTags.innerHTML = tagsHtml;
      }

      // Sinopse e Metadados
      if (detailsDescription) {
        detailsDescription.textContent = book.description || "Nenhuma sinopse cadastrada para esta obra.";
      }
      if (detailsMetaPublisher) detailsMetaPublisher.textContent = book.publisher || "--";
      if (detailsMetaYear) detailsMetaYear.textContent = book.publish_year || "--";
      if (detailsMetaLanguage) detailsMetaLanguage.textContent = (book.language || "--").toUpperCase();
      if (detailsMetaIsbn13) detailsMetaIsbn13.textContent = book.isbn13 || "--";
      if (detailsMetaIsbn10) detailsMetaIsbn10.textContent = book.isbn10 || "--";
      if (detailsMetaEdition) detailsMetaEdition.textContent = book.edition || "--";
      if (detailsMetaFile) detailsMetaFile.textContent = `${formatBytes(book.filesize || 0)} (${(book.format || "pdf").toUpperCase()})`;

      // Popula campos do editor manual
      if (editFieldTitle) editFieldTitle.value = book.title || "";
      if (editFieldAuthor) editFieldAuthor.value = book.author || "";
      if (editFieldPublisher) editFieldPublisher.value = book.publisher || "";
      if (editFieldYear) editFieldYear.value = book.publish_year || "";
      if (editFieldLanguage) editFieldLanguage.value = book.language || "";
      if (editFieldIsbn13) editFieldIsbn13.value = book.isbn13 || "";
      if (editFieldIsbn10) editFieldIsbn10.value = book.isbn10 || "";
      if (editFieldDesc) editFieldDesc.value = book.description || "";

      const locks = new Set(book.manual_lock_fields || []);
      if (lockTitle) lockTitle.checked = locks.has("title");
      if (lockAuthor) lockAuthor.checked = locks.has("author");
      if (lockPublisher) lockPublisher.checked = locks.has("publisher");
      if (lockIsbn) lockIsbn.checked = locks.has("isbn13");
      if (lockDesc) lockDesc.checked = locks.has("description");

      // Reset para primeira subaba (Visão Geral)
      switchDetailsSubtab("overview");
      loadBookCandidates(book.id);
      loadBookDetailsSocial(book.id);

      modalBookDetails.classList.add("active");
    } catch (e) {
      alert("Não foi possível carregar os detalhes desta obra.");
    }
  }

  function switchDetailsSubtab(subtabName) {
    document.querySelectorAll(".details-tab-btn").forEach(btn => {
      btn.classList.toggle("active", btn.getAttribute("data-subtab") === subtabName);
    });
    document.querySelectorAll(".details-subtab-pane").forEach(pane => {
      pane.style.display = pane.id === `details-subtab-${subtabName}` ? "block" : "none";
    });
  }

  document.querySelectorAll(".details-tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      switchDetailsSubtab(btn.getAttribute("data-subtab"));
    });
  });

  if (btnCloseDetailsModal) {
    btnCloseDetailsModal.addEventListener("click", () => {
      modalBookDetails.classList.remove("active");
    });
  }

  if (btnDetailsEditCover) {
    btnDetailsEditCover.addEventListener("click", () => {
      if (!activeBookForDetails) return;
      openBookCoverModal(
        activeBookForDetails.id,
        activeBookForDetails.title,
        activeBookForDetails.cover_url,
        activeBookForDetails.cover_type
      );
    });
  }

  if (btnDetailsCollections) {
    btnDetailsCollections.addEventListener("click", () => {
      if (!activeBookForDetails) return;
      openBookCollectionsModal(activeBookForDetails.id, activeBookForDetails.title);
    });
  }

  // --- Reconciliação e Candidatos ---
  async function loadBookCandidates(bookId) {
    if (!detailsCandidatesList) return;
    try {
      const res = await fetch(`/api/books/${bookId}/metadata/candidates`);
      if (!res.ok) return;
      const data = await res.json();
      renderCandidateList(data.candidates || []);
    } catch (e) {
      console.error("Erro ao listar candidatos:", e);
    }
  }

  function renderCandidateList(candidates) {
    if (!detailsCandidatesList) return;
    if (!candidates || candidates.length === 0) {
      detailsCandidatesList.innerHTML = `
        <div style="font-size: 0.85rem; color: var(--text-muted); padding: 0.75rem 0; text-align: center;">
          Nenhum candidato registrado. Clique em "Buscar" acima para consultar Open Library e Google Books.
        </div>
      `;
      return;
    }

    detailsCandidatesList.innerHTML = candidates.map(c => {
      const score = Math.round(c.confidence_score || 0);
      let scoreClass = "score-insufficient";
      if (score >= 85) scoreClass = "score-reliable";
      else if (score >= 50) scoreClass = "score-review";

      return `
        <div class="candidate-card">
          ${c.cover_url ? `<img class="candidate-cover-thumb" src="${escapeHtml(c.cover_url)}" alt="Capa" loading="lazy">` : `<div class="candidate-cover-thumb" style="display:flex;align-items:center;justify-content:center;font-size:0.7rem;color:var(--text-faint);">Sem capa</div>`}
          <div class="candidate-info">
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <strong style="font-size: 0.88rem;">${escapeHtml(c.title)}</strong>
              <span class="candidate-score-badge ${scoreClass}">${score}% match</span>
            </div>
            <div style="font-size: 0.8rem; color: var(--text-muted);">${escapeHtml(c.author || "Autor não informado")} ${c.publish_year ? `&bull; ${c.publish_year}` : ""} &bull; <em style="text-transform: capitalize;">${escapeHtml(c.provider || "externo")}</em></div>
            <div style="font-size: 0.75rem; color: var(--text-faint); margin-top: 0.2rem;">${escapeHtml(c.publisher || "")} ${c.isbn13 ? `| ISBN: ${c.isbn13}` : ""}</div>
          </div>
          <button class="btn btn-primary btn-apply-candidate" data-id="${c.id}" style="padding: 0.35rem 0.65rem; font-size: 0.8rem;">
            Aplicar
          </button>
        </div>
      `;
    }).join("");

    detailsCandidatesList.querySelectorAll(".btn-apply-candidate").forEach(btn => {
      btn.addEventListener("click", async () => {
        if (!activeBookForDetails) return;
        const candidateId = parseInt(btn.getAttribute("data-id"), 10);
        try {
          btn.disabled = true;
          btn.textContent = "Aplicando...";
          const res = await fetch(`/api/books/${activeBookForDetails.id}/metadata/apply`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ candidate_id: candidateId, apply_cover: true }),
          });
          if (!res.ok) {
            const err = await res.json();
            alert(err.detail || "Erro ao aplicar candidato.");
            btn.disabled = false;
            btn.textContent = "Aplicar";
            return;
          }
          await openBookDetails(activeBookForDetails.id);
          loadShelves();
          loadBooks();
        } catch (e) {
          alert("Erro de conexão ao aplicar metadados.");
          btn.disabled = false;
          btn.textContent = "Aplicar";
        }
      });
    });
  }

  if (btnDetailsSearchCandidates) {
    btnDetailsSearchCandidates.addEventListener("click", async () => {
      if (!activeBookForDetails) return;
      const q = detailsReconcileQuery ? detailsReconcileQuery.value.trim() : "";
      if (detailsCandidatesLoading) detailsCandidatesLoading.style.display = "block";
      try {
        const res = await fetch(`/api/books/${activeBookForDetails.id}/metadata/search`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: q || undefined }),
        });
        if (detailsCandidatesLoading) detailsCandidatesLoading.style.display = "none";
        if (res.ok) {
          const data = await res.json();
          renderCandidateList(data.candidates || []);
        } else {
          alert("Falha na busca externa de metadados.");
        }
      } catch (e) {
        if (detailsCandidatesLoading) detailsCandidatesLoading.style.display = "none";
        alert("Erro de conexão ao buscar candidatos.");
      }
    });
  }

  // Salvar Edição Manual de Metadados
  if (btnSaveManualMetadata) {
    btnSaveManualMetadata.addEventListener("click", async () => {
      if (!activeBookForDetails) return;
      const updates = {
        title: editFieldTitle ? editFieldTitle.value.trim() : undefined,
        author: editFieldAuthor ? editFieldAuthor.value.trim() : undefined,
        publisher: editFieldPublisher ? editFieldPublisher.value.trim() : undefined,
        publish_year: editFieldYear && editFieldYear.value ? parseInt(editFieldYear.value, 10) : undefined,
        language: editFieldLanguage ? editFieldLanguage.value.trim() : undefined,
        isbn13: editFieldIsbn13 ? editFieldIsbn13.value.trim() : undefined,
        isbn10: editFieldIsbn10 ? editFieldIsbn10.value.trim() : undefined,
        description: editFieldDesc ? editFieldDesc.value.trim() : undefined,
      };

      const lockFields = [];
      if (lockTitle && lockTitle.checked) lockFields.push("title");
      if (lockAuthor && lockAuthor.checked) lockFields.push("author");
      if (lockPublisher && lockPublisher.checked) lockFields.push("publisher");
      if (lockIsbn && lockIsbn.checked) lockFields.push("isbn13");
      if (lockDesc && lockDesc.checked) lockFields.push("description");
      updates.lock_fields = lockFields;

      try {
        btnSaveManualMetadata.disabled = true;
        const res = await fetch(`/api/books/${activeBookForDetails.id}/metadata`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(updates),
        });
        btnSaveManualMetadata.disabled = false;
        if (res.ok) {
          await openBookDetails(activeBookForDetails.id);
          loadShelves();
          loadBooks();
          alert("Metadados atualizados com sucesso.");
        } else {
          const err = await res.json();
          alert(err.detail || "Erro ao salvar metadados.");
        }
      } catch (e) {
        btnSaveManualMetadata.disabled = false;
        alert("Erro de conexão ao salvar metadados.");
      }
    });
  }

  // --- Central de Metadados (Fila de Enriquecimento) ---
  function updateQueueModalUI(status) {
    if (!queueStatusBadge) return;
    const state = status.status || "idle";
    const stateMap = {
      running: { label: "Em Execução", cls: "source-badge-active" },
      paused: { label: "Pausada", cls: "" },
      completed: { label: "Concluída", cls: "source-badge-active" },
      cancelled: { label: "Cancelada", cls: "source-badge-inactive" },
      idle: { label: "Ociosa", cls: "source-badge-inactive" },
    };
    const sInfo = stateMap[state] || { label: state, cls: "" };
    queueStatusBadge.textContent = sInfo.label;
    queueStatusBadge.className = `source-badge ${sInfo.cls}`;

    const total = status.total || 0;
    const processed = status.processed || 0;
    const percent = total > 0 ? Math.round((processed / total) * 100) : 0;
    if (queueProgressBar) queueProgressBar.style.width = `${percent}%`;

    if (queueMetricTotal) queueMetricTotal.textContent = total;
    if (queueMetricProcessed) queueMetricProcessed.textContent = `${processed} (${percent}%)`;
    if (queueMetricIdentified) queueMetricIdentified.textContent = status.identified || 0;
    if (queueMetricReview) queueMetricReview.textContent = status.needs_review || 0;
    if (queueMetricNotfound) queueMetricNotfound.textContent = status.not_found || 0;
    if (queueMetricFailed) queueMetricFailed.textContent = status.failed || 0;

    // Alterna botões
    if (btnQueuePause) btnQueuePause.style.display = state === "running" ? "inline-flex" : "none";
    if (btnQueueResume) btnQueueResume.style.display = state === "paused" ? "inline-flex" : "none";
    if (btnQueueCancel) btnQueueCancel.style.display = (state === "running" || state === "paused") ? "inline-flex" : "none";
  }

  async function pollQueueStatus() {
    try {
      const res = await fetch("/api/metadata/queue/status");
      if (res.ok) {
        const data = await res.json();
        updateQueueModalUI(data);
      }
    } catch (e) {
      console.error("Erro ao consultar fila:", e);
    }
  }

  function openMetadataCenter() {
    if (!modalMetadataCenter) return;
    pollQueueStatus();
    if (queuePollInterval) clearInterval(queuePollInterval);
    queuePollInterval = setInterval(pollQueueStatus, 2500);
    modalMetadataCenter.classList.add("active");
  }

  function closeMetadataCenter() {
    if (!modalMetadataCenter) return;
    if (queuePollInterval) {
      clearInterval(queuePollInterval);
      queuePollInterval = null;
    }
    modalMetadataCenter.classList.remove("active");
    loadShelves();
    loadBooks();
  }

  if (btnMetadataCenter) btnMetadataCenter.addEventListener("click", openMetadataCenter);
  if (btnCloseMetaCenterModal) btnCloseMetaCenterModal.addEventListener("click", closeMetadataCenter);
  if (btnDoneMetaCenterModal) btnDoneMetaCenterModal.addEventListener("click", closeMetadataCenter);

  if (btnQueueEnqueuePending) {
    btnQueueEnqueuePending.addEventListener("click", async () => {
      try {
        btnQueueEnqueuePending.disabled = true;
        const res = await fetch("/api/metadata/queue/enqueue", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ only_pending: true }),
        });
        btnQueueEnqueuePending.disabled = false;
        if (res.ok) {
          pollQueueStatus();
        }
      } catch (e) {
        btnQueueEnqueuePending.disabled = false;
        alert("Erro ao enfileirar obras.");
      }
    });
  }

  if (btnQueueEnqueueAll) {
    btnQueueEnqueueAll.addEventListener("click", async () => {
      if (!confirm("Deseja re-enfileirar todo o acervo para enriquecimento?")) return;
      try {
        btnQueueEnqueueAll.disabled = true;
        const res = await fetch("/api/metadata/queue/enqueue", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ only_pending: false }),
        });
        btnQueueEnqueueAll.disabled = false;
        if (res.ok) {
          pollQueueStatus();
        }
      } catch (e) {
        btnQueueEnqueueAll.disabled = false;
        alert("Erro ao enfileirar todo o acervo.");
      }
    });
  }

  if (btnQueuePause) {
    btnQueuePause.addEventListener("click", async () => {
      await fetch("/api/metadata/queue/pause", { method: "POST" });
      pollQueueStatus();
    });
  }

  if (btnQueueResume) {
    btnQueueResume.addEventListener("click", async () => {
      await fetch("/api/metadata/queue/start", { method: "POST" });
      pollQueueStatus();
    });
  }

  if (btnQueueCancel) {
    btnQueueCancel.addEventListener("click", async () => {
      if (!confirm("Deseja cancelar e esvaziar a fila de enriquecimento?")) return;
      await fetch("/api/metadata/queue/cancel", { method: "POST" });
      pollQueueStatus();
    });
  }

  // =========================================================================
  // LECTORIA v1.5.0 — BIBLIOTECA VIVA (SOCIAL, PERFIS, AUTOMAÇÃO & ADMIN)
  // =========================================================================

  // --- 1. Avaliações, Favoritos, Quero Ler e Citações no Modal de Detalhes ---
  const btnDetailsFavorite = document.getElementById("btn-details-favorite");
  const btnDetailsWantToRead = document.getElementById("btn-details-want-to-read");
  const btnDetailsRecommend = document.getElementById("btn-details-recommend");
  const detailsAvgRatingBadge = document.getElementById("details-avg-rating-badge");
  const detailsRatingSelect = document.getElementById("details-rating-select");
  const detailsReviewPublic = document.getElementById("details-review-public");
  const detailsReviewText = document.getElementById("details-review-text");
  const btnDetailsSaveReview = document.getElementById("btn-details-save-review");
  const btnDetailsShareQuote = document.getElementById("btn-details-share-quote");
  const detailsSocialReviewsList = document.getElementById("details-social-reviews-list");
  const detailsSocialAnnotationsList = document.getElementById("details-social-annotations-list");

  let currentBookUserReview = null;

  async function loadBookDetailsSocial(bookId) {
    try {
      const [revRes, annRes] = await Promise.all([
        fetch(`/api/books/${encodeURIComponent(bookId)}/reviews`),
        fetch(`/api/books/${encodeURIComponent(bookId)}/shared-annotations`),
      ]);

      if (revRes.ok) {
        const revData = await revRes.json();
        currentBookUserReview = revData.user_review || null;
        const avg = revData.average_rating;
        const count = revData.ratings_count || 0;
        if (detailsAvgRatingBadge) {
          detailsAvgRatingBadge.textContent = avg ? `★ ${avg} (${count} aval.)` : "Sem avaliações";
        }
        const isFav = Boolean(currentBookUserReview && currentBookUserReview.is_favorite);
        const isWtr = Boolean(currentBookUserReview && currentBookUserReview.want_to_read);
        if (btnDetailsFavorite) {
          btnDetailsFavorite.textContent = isFav ? "★ Favorito" : "☆ Favorito";
          btnDetailsFavorite.classList.toggle("btn-active", isFav);
        }
        if (btnDetailsWantToRead) {
          btnDetailsWantToRead.textContent = isWtr ? "✓ Quero Ler" : "+ Quero Ler";
          btnDetailsWantToRead.classList.toggle("btn-active", isWtr);
        }
        if (detailsRatingSelect) {
          detailsRatingSelect.value = currentBookUserReview && currentBookUserReview.rating ? String(currentBookUserReview.rating) : "";
        }
        if (detailsReviewText) {
          detailsReviewText.value = (currentBookUserReview && currentBookUserReview.review_text) || "";
        }
        if (detailsReviewPublic) {
          detailsReviewPublic.checked = Boolean(currentBookUserReview && currentBookUserReview.is_public);
        }

        if (detailsSocialReviewsList) {
          const reviews = (revData.reviews || []).filter(r => r.rating || r.review_text);
          if (reviews.length === 0) {
            detailsSocialReviewsList.innerHTML = `<p style="font-size: 0.82rem; color: var(--text-muted);">Nenhuma resenha compartilhada ainda para esta obra.</p>`;
          } else {
            detailsSocialReviewsList.innerHTML = reviews.map(r => `
              <div class="social-quote-card" style="margin-bottom: 0.5rem;">
                <div style="display: flex; justify-content: space-between; font-size: 0.8rem;">
                  <strong>${escapeHtml(r.display_name || r.username)}</strong>
                  <span style="color: #fbbf24;">${r.rating ? "★".repeat(r.rating) : ""}</span>
                </div>
                ${r.review_text ? `<p style="font-size: 0.84rem; margin-top: 0.25rem;">${escapeHtml(r.review_text)}</p>` : ""}
              </div>
            `).join("");
          }
        }
      }

      if (annRes.ok && detailsSocialAnnotationsList) {
        const annData = await annRes.json();
        const anns = annData.annotations || [];
        renderSharedAnnotationsCards(anns, detailsSocialAnnotationsList, () => loadBookDetailsSocial(bookId));
      }
    } catch (e) {
      console.warn("Erro ao carregar dados sociais do livro:", e);
    }
  }

  async function saveBookReviewQuick(overrides = {}) {
    if (!activeBookForDetails) return;
    const payload = {
      rating: detailsRatingSelect && detailsRatingSelect.value ? parseInt(detailsRatingSelect.value, 10) : (currentBookUserReview ? currentBookUserReview.rating : null),
      review_text: detailsReviewText ? detailsReviewText.value.trim() : (currentBookUserReview ? currentBookUserReview.review_text : ""),
      is_favorite: currentBookUserReview ? Boolean(currentBookUserReview.is_favorite) : false,
      want_to_read: currentBookUserReview ? Boolean(currentBookUserReview.want_to_read) : false,
      is_public: detailsReviewPublic ? detailsReviewPublic.checked : undefined,
      ...overrides,
    };
    const res = await fetch(`/api/books/${encodeURIComponent(activeBookForDetails.id)}/reviews`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.ok) {
      await loadBookDetailsSocial(activeBookForDetails.id);
      if (currentMainTab === "library") loadBooks();
      else if (currentMainTab === "discovery") loadShelves();
    } else if (res.status === 401) {
      alert("Entre na sua conta para avaliar ou favoritar livros.");
    }
  }

  if (btnDetailsFavorite) {
    btnDetailsFavorite.addEventListener("click", () => {
      const nextFav = !(currentBookUserReview && currentBookUserReview.is_favorite);
      saveBookReviewQuick({ is_favorite: nextFav });
    });
  }

  if (btnDetailsWantToRead) {
    btnDetailsWantToRead.addEventListener("click", () => {
      const nextWtr = !(currentBookUserReview && currentBookUserReview.want_to_read);
      saveBookReviewQuick({ want_to_read: nextWtr });
    });
  }

  if (btnDetailsSaveReview) {
    btnDetailsSaveReview.addEventListener("click", () => {
      saveBookReviewQuick({});
    });
  }

  if (btnDetailsShareQuote) {
    btnDetailsShareQuote.addEventListener("click", () => {
      if (!activeBookForDetails) return;
      openShareAnnotationModal(activeBookForDetails.id);
    });
  }

  if (btnDetailsRecommend) {
    btnDetailsRecommend.addEventListener("click", () => {
      if (!activeBookForDetails) return;
      openRecommendModal(activeBookForDetails.id);
    });
  }

  // --- 2. Renderização de Citações Compartilhadas e Comentários ---
  function renderSharedAnnotationsCards(annotations, containerEl, onRefresh) {
    if (!containerEl) return;
    if (!annotations || annotations.length === 0) {
      containerEl.innerHTML = `<p class="text-muted-empty">Nenhuma citação ou anotação compartilhada ainda.</p>`;
      return;
    }

    containerEl.innerHTML = annotations.map(a => {
      const isOwner = currentUser && (currentUser.id === a.user_id || currentUser.role === "admin");
      const comments = a.comments || [];
      const commentsHtml = comments.map(c => {
        const canDelComment = currentUser && (currentUser.id === c.user_id || currentUser.role === "admin");
        return `
          <div class="social-comment-item" data-comment-id="${c.id}">
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <strong style="font-size: 0.78rem;">${escapeHtml(c.display_name || c.username)}</strong>
              <div style="display: flex; gap: 0.35rem; align-items: center;">
                <span style="font-size: 0.7rem; color: var(--text-faint);">${formatDate(c.created_at)}</span>
                ${canDelComment ? `<button type="button" class="btn-link-danger btn-del-comment" data-id="${c.id}" title="Excluir comentário">&times;</button>` : ""}
              </div>
            </div>
            <div style="font-size: 0.82rem; color: var(--text-main); margin-top: 0.15rem;">${escapeHtml(c.content)}</div>
          </div>
        `;
      }).join("");

      return `
        <div class="social-quote-card" data-ann-id="${a.id}">
          <div class="social-quote-header">
            <div>
              <strong>${escapeHtml(a.display_name || a.username)}</strong>
              ${a.book_title ? `<span style="color: var(--text-muted); font-size: 0.8rem;"> em <em>${escapeHtml(a.book_title)}</em></span>` : ""}
              ${a.page_number ? `<span class="social-badge-pill" style="margin-left: 0.35rem;">Pág. ${a.page_number}</span>` : ""}
            </div>
            <div style="display: flex; gap: 0.4rem; align-items: center;">
              <span style="font-size: 0.72rem; color: var(--text-faint);">${formatDate(a.created_at)}</span>
              ${isOwner ? `<button type="button" class="btn-link-danger btn-del-shared-ann" data-id="${a.id}" title="Remover compartilhamento">Excluir</button>` : ""}
            </div>
          </div>
          ${a.quote_text ? `<blockquote class="social-blockquote">&ldquo;${escapeHtml(a.quote_text)}&rdquo;</blockquote>` : ""}
          ${a.content ? `<p class="social-quote-comment">${escapeHtml(a.content)}</p>` : ""}

          <div class="social-comments-section">
            ${commentsHtml}
            <div class="social-comment-form">
              <input type="text" class="form-input input-new-comment" data-ann-id="${a.id}" placeholder="Escrever um comentário..." style="font-size: 0.8rem; padding: 0.3rem 0.55rem;">
              <button type="button" class="btn btn-subtle btn-send-comment" data-ann-id="${a.id}" style="font-size: 0.78rem; padding: 0.3rem 0.6rem;">Comentar</button>
            </div>
          </div>
        </div>
      `;
    }).join("");

    containerEl.querySelectorAll(".btn-send-comment").forEach(btn => {
      btn.addEventListener("click", async () => {
        const annId = btn.getAttribute("data-ann-id");
        const input = containerEl.querySelector(`.input-new-comment[data-ann-id="${annId}"]`);
        const text = input ? input.value.trim() : "";
        if (!text) return;
        const res = await fetch(`/api/shared-annotations/${annId}/comments`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: text }),
        });
        if (res.ok) {
          if (onRefresh) onRefresh();
        } else if (res.status === 401) {
          alert("Entre na sua conta para comentar.");
        }
      });
    });

    containerEl.querySelectorAll(".btn-del-comment").forEach(btn => {
      btn.addEventListener("click", async () => {
        const commentId = btn.getAttribute("data-id");
        const res = await fetch(`/api/annotation-comments/${commentId}`, { method: "DELETE" });
        if (res.ok && onRefresh) onRefresh();
      });
    });

    containerEl.querySelectorAll(".btn-del-shared-ann").forEach(btn => {
      btn.addEventListener("click", async () => {
        const annId = btn.getAttribute("data-id");
        if (!confirm("Remover esta citação compartilhada?")) return;
        const res = await fetch(`/api/shared-annotations/${annId}`, { method: "DELETE" });
        if (res.ok && onRefresh) onRefresh();
      });
    });
  }

  // --- 3. Aba "Nossa Biblioteca" (Feed Social com Privacidade por Padrão) ---
  const socialSharedAnnotationsEl = document.getElementById("social-shared-annotations");
  const socialRecentReviewsEl = document.getElementById("social-recent-reviews");
  const socialRecommendationsEl = document.getElementById("social-recommendations");
  const socialCurrentlyReadingEl = document.getElementById("social-currently-reading");
  const socialRecentlyCompletedEl = document.getElementById("social-recently-completed");

  async function loadSocialFeed() {
    try {
      const res = await fetch("/api/social/feed");
      if (!res.ok) throw new Error("Falha ao carregar Nossa Biblioteca");
      const feed = await res.json();

      renderSharedAnnotationsCards(feed.shared_annotations || [], socialSharedAnnotationsEl, loadSocialFeed);

      if (socialRecentReviewsEl) {
        const reviews = (feed.recent_reviews || []).filter(r => r.rating || r.review_text);
        if (reviews.length === 0) {
          socialRecentReviewsEl.innerHTML = `<p class="text-muted-empty">Nenhuma avaliação pública registrada ainda.</p>`;
        } else {
          socialRecentReviewsEl.innerHTML = reviews.map(r => `
            <div class="social-quote-card" data-book-id="${r.book_id}" style="cursor: pointer;">
              <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                  <strong>${escapeHtml(r.display_name || r.username)}</strong>
                  <span style="font-size: 0.82rem; color: var(--text-muted);"> avaliou <strong>${escapeHtml(r.book_title)}</strong></span>
                </div>
                <span style="color: #fbbf24; font-size: 0.88rem;">${r.rating ? "★".repeat(r.rating) : ""}</span>
              </div>
              ${r.review_text ? `<p style="font-size: 0.85rem; margin-top: 0.35rem; color: var(--text-main);">${escapeHtml(r.review_text)}</p>` : ""}
            </div>
          `).join("");
          socialRecentReviewsEl.querySelectorAll("[data-book-id]").forEach(el => {
            el.addEventListener("click", () => openBookDetails(el.getAttribute("data-book-id")));
          });
        }
      }

      if (socialRecommendationsEl) {
        const recs = feed.recommendations || [];
        if (recs.length === 0) {
          socialRecommendationsEl.innerHTML = `<p class="text-muted-empty">Nenhuma recomendação no mural.</p>`;
        } else {
          socialRecommendationsEl.innerHTML = recs.map(rc => `
            <div class="social-mini-item" data-book-id="${rc.book_id}" style="cursor: pointer;">
              <div>
                <strong style="font-size: 0.84rem;">${escapeHtml(rc.book_title)}</strong>
                <div style="font-size: 0.76rem; color: var(--text-muted);">
                  Indicado por <strong>${escapeHtml(rc.sender_display_name || rc.sender_username)}</strong>
                </div>
                ${rc.message ? `<div style="font-size: 0.8rem; margin-top: 0.2rem; color: var(--text-main);">&ldquo;${escapeHtml(rc.message)}&rdquo;</div>` : ""}
              </div>
            </div>
          `).join("");
          socialRecommendationsEl.querySelectorAll("[data-book-id]").forEach(el => {
            el.addEventListener("click", () => openBookDetails(el.getAttribute("data-book-id")));
          });
        }
      }

      if (socialCurrentlyReadingEl) {
        const list = feed.currently_reading || [];
        if (list.length === 0) {
          socialCurrentlyReadingEl.innerHTML = `<p class="text-muted-empty">Nenhum leitor compartilhou leituras em andamento no momento.</p>`;
        } else {
          socialCurrentlyReadingEl.innerHTML = list.map(item => `
            <div class="social-mini-item" data-book-id="${item.book_id}" style="cursor: pointer;">
              <div style="display: flex; justify-content: space-between;">
                <strong style="font-size: 0.83rem;">${escapeHtml(item.display_name || item.username)}</strong>
                <span class="social-badge-pill">${Math.round(item.percentage || 0)}%</span>
              </div>
              <div style="font-size: 0.8rem; color: var(--text-muted);">${escapeHtml(item.title)}</div>
            </div>
          `).join("");
          socialCurrentlyReadingEl.querySelectorAll("[data-book-id]").forEach(el => {
            el.addEventListener("click", () => openBookDetails(el.getAttribute("data-book-id")));
          });
        }
      }

      if (socialRecentlyCompletedEl) {
        const list = feed.recently_completed || [];
        if (list.length === 0) {
          socialRecentlyCompletedEl.innerHTML = `<p class="text-muted-empty">Nenhuma leitura concluída compartilhada ainda.</p>`;
        } else {
          socialRecentlyCompletedEl.innerHTML = list.map(item => `
            <div class="social-mini-item" data-book-id="${item.book_id}" style="cursor: pointer;">
              <strong style="font-size: 0.83rem;">${escapeHtml(item.display_name || item.username)}</strong>
              <span style="font-size: 0.8rem; color: var(--text-muted);"> concluiu <strong>${escapeHtml(item.title)}</strong></span>
            </div>
          `).join("");
          socialRecentlyCompletedEl.querySelectorAll("[data-book-id]").forEach(el => {
            el.addEventListener("click", () => openBookDetails(el.getAttribute("data-book-id")));
          });
        }
      }
    } catch (e) {
      console.warn("Erro ao carregar feed social:", e);
    }
  }

  // --- 4. Modal de Perfil e Privacidade Granular ---
  const modalUserProfile = document.getElementById("modal-user-profile");
  const btnCloseProfileModal = document.getElementById("btn-close-profile-modal");
  const btnCancelProfileModal = document.getElementById("btn-cancel-profile-modal");
  const btnSaveProfileModal = document.getElementById("btn-save-profile-modal");
  const btnSocialOpenPrivacy = document.getElementById("btn-social-open-privacy");
  const profileDisplayName = document.getElementById("profile-display-name");
  const profileAvatarColor = document.getElementById("profile-avatar-color");
  const profileAvatarPreview = document.getElementById("profile-avatar-preview");
  const profileBio = document.getElementById("profile-bio");
  const privShareCurrent = document.getElementById("priv-share-current");
  const privShareHistory = document.getElementById("priv-share-history");
  const privShareReviews = document.getElementById("priv-share-reviews");
  const privShareLists = document.getElementById("priv-share-lists");
  const privShareAnnotations = document.getElementById("priv-share-annotations");
  const profileSaveFeedback = document.getElementById("profile-save-feedback");

  async function openProfileModal() {
    if (!modalUserProfile) return;
    if (profileSaveFeedback) profileSaveFeedback.style.display = "none";
    try {
      const res = await fetch("/api/profile/me");
      if (res.status === 401) {
        modalAuth.classList.add("active");
        return;
      }
      if (res.ok) {
        const data = await res.json();
        const prof = data.profile || {};
        const priv = prof.privacy || {};
        if (profileDisplayName) profileDisplayName.value = prof.display_name || "";
        if (profileAvatarColor) profileAvatarColor.value = prof.avatar_color || "#6366f1";
        if (profileAvatarPreview) {
          profileAvatarPreview.style.background = prof.avatar_color || "#6366f1";
          profileAvatarPreview.textContent = (prof.display_name || prof.username || "U").charAt(0).toUpperCase();
        }
        if (profileBio) profileBio.value = prof.bio || "";
        if (privShareCurrent) privShareCurrent.checked = Boolean(priv.share_current_reading);
        if (privShareHistory) privShareHistory.checked = Boolean(priv.share_reading_history);
        if (privShareReviews) privShareReviews.checked = Boolean(priv.share_reviews);
        if (privShareLists) privShareLists.checked = Boolean(priv.share_lists);
        if (privShareAnnotations) privShareAnnotations.checked = Boolean(priv.share_annotations_default);
        modalUserProfile.classList.add("active");
      }
    } catch (e) {
      alert("Erro ao abrir perfil.");
    }
  }

  if (btnOpenProfile) btnOpenProfile.addEventListener("click", openProfileModal);
  if (btnSocialOpenPrivacy) btnSocialOpenPrivacy.addEventListener("click", openProfileModal);
  if (btnCloseProfileModal) btnCloseProfileModal.addEventListener("click", () => modalUserProfile.classList.remove("active"));
  if (btnCancelProfileModal) btnCancelProfileModal.addEventListener("click", () => modalUserProfile.classList.remove("active"));

  if (btnSaveProfileModal) {
    btnSaveProfileModal.addEventListener("click", async () => {
      try {
        btnSaveProfileModal.disabled = true;
        await fetch("/api/profile/me", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            display_name: profileDisplayName ? profileDisplayName.value.trim() : "",
            avatar_color: profileAvatarColor ? profileAvatarColor.value : "#6366f1",
            bio: profileBio ? profileBio.value.trim() : "",
          }),
        });
        await fetch("/api/profile/privacy", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            share_current_reading: privShareCurrent ? privShareCurrent.checked : false,
            share_reading_history: privShareHistory ? privShareHistory.checked : false,
            share_reviews: privShareReviews ? privShareReviews.checked : false,
            share_lists: privShareLists ? privShareLists.checked : false,
            share_annotations_default: privShareAnnotations ? privShareAnnotations.checked : false,
          }),
        });
        btnSaveProfileModal.disabled = false;
        if (profileSaveFeedback) {
          profileSaveFeedback.textContent = "Perfil e preferências de privacidade salvos com sucesso!";
          profileSaveFeedback.style.display = "block";
        }
        if (currentMainTab === "social") loadSocialFeed();
      } catch (e) {
        btnSaveProfileModal.disabled = false;
        alert("Erro ao salvar configurações de perfil.");
      }
    });
  }

  // --- 5. Modal de Compartilhar Citação e Modal de Recomendar Livro ---
  const modalShareAnnotation = document.getElementById("modal-share-annotation");
  const btnSocialNewQuote = document.getElementById("btn-social-new-quote");
  const btnCloseShareAnnModal = document.getElementById("btn-close-share-ann-modal");
  const btnCancelShareAnn = document.getElementById("btn-cancel-share-ann");
  const btnSubmitShareAnn = document.getElementById("btn-submit-share-ann");
  const shareAnnBookSelect = document.getElementById("share-ann-book-select");
  const shareAnnPage = document.getElementById("share-ann-page");
  const shareAnnVisibility = document.getElementById("share-ann-visibility");
  const shareAnnQuote = document.getElementById("share-ann-quote");
  const shareAnnContent = document.getElementById("share-ann-content");
  const shareAnnError = document.getElementById("share-ann-error");

  async function populateBookSelectDropdown(selectEl, preselectId) {
    if (!selectEl) return;
    const res = await fetch("/api/books");
    if (!res.ok) return;
    const data = await res.json();
    const books = data.books || [];
    selectEl.innerHTML = books.map(b => `
      <option value="${b.id}" ${String(b.id) === String(preselectId) ? "selected" : ""}>
        ${escapeHtml(b.title)} — ${escapeHtml(b.author || "Autor não informado")}
      </option>
    `).join("");
  }

  async function openShareAnnotationModal(preselectBookId = null) {
    if (!modalShareAnnotation) return;
    if (shareAnnError) shareAnnError.style.display = "none";
    await populateBookSelectDropdown(shareAnnBookSelect, preselectBookId);
    if (shareAnnPage) shareAnnPage.value = "";
    if (shareAnnQuote) shareAnnQuote.value = "";
    if (shareAnnContent) shareAnnContent.value = "";
    modalShareAnnotation.classList.add("active");
  }

  if (btnSocialNewQuote) btnSocialNewQuote.addEventListener("click", () => openShareAnnotationModal());
  if (btnCloseShareAnnModal) btnCloseShareAnnModal.addEventListener("click", () => modalShareAnnotation.classList.remove("active"));
  if (btnCancelShareAnn) btnCancelShareAnn.addEventListener("click", () => modalShareAnnotation.classList.remove("active"));

  if (btnSubmitShareAnn) {
    btnSubmitShareAnn.addEventListener("click", async () => {
      const bookId = shareAnnBookSelect ? parseInt(shareAnnBookSelect.value, 10) : 0;
      const quote = shareAnnQuote ? shareAnnQuote.value.trim() : "";
      const content = shareAnnContent ? shareAnnContent.value.trim() : "";
      const pageNum = shareAnnPage && shareAnnPage.value ? parseInt(shareAnnPage.value, 10) : null;
      const vis = shareAnnVisibility ? shareAnnVisibility.value : "all_users";

      if (!bookId || (!quote && !content)) {
        if (shareAnnError) {
          shareAnnError.textContent = "Informe uma citação ou comentário para publicar.";
          shareAnnError.style.display = "block";
        }
        return;
      }

      const res = await fetch("/api/shared-annotations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          book_id: bookId,
          page_number: pageNum,
          quote_text: quote,
          content: content,
          visibility: vis,
        }),
      });
      if (res.ok) {
        modalShareAnnotation.classList.remove("active");
        if (currentMainTab === "social") loadSocialFeed();
        if (activeBookForDetails && activeBookForDetails.id === bookId) loadBookDetailsSocial(bookId);
      } else {
        const err = await res.json();
        if (shareAnnError) {
          shareAnnError.textContent = err.detail || "Falha ao compartilhar.";
          shareAnnError.style.display = "block";
        }
      }
    });
  }

  // Modal de Recomendar Livro
  const modalRecommendBook = document.getElementById("modal-recommend-book");
  const btnSocialSendRec = document.getElementById("btn-social-send-rec");
  const btnCloseRecModal = document.getElementById("btn-close-rec-modal");
  const btnCancelRecModal = document.getElementById("btn-cancel-rec-modal");
  const btnSubmitRecModal = document.getElementById("btn-submit-rec-modal");
  const recBookSelect = document.getElementById("rec-book-select");
  const recMessage = document.getElementById("rec-message");
  const recError = document.getElementById("rec-error");

  async function openRecommendModal(preselectBookId = null) {
    if (!modalRecommendBook) return;
    if (recError) recError.style.display = "none";
    await populateBookSelectDropdown(recBookSelect, preselectBookId);
    if (recMessage) recMessage.value = "";
    modalRecommendBook.classList.add("active");
  }

  if (btnSocialSendRec) btnSocialSendRec.addEventListener("click", () => openRecommendModal());
  if (btnCloseRecModal) btnCloseRecModal.addEventListener("click", () => modalRecommendBook.classList.remove("active"));
  if (btnCancelRecModal) btnCancelRecModal.addEventListener("click", () => modalRecommendBook.classList.remove("active"));

  if (btnSubmitRecModal) {
    btnSubmitRecModal.addEventListener("click", async () => {
      const bookId = recBookSelect ? parseInt(recBookSelect.value, 10) : 0;
      const msg = recMessage ? recMessage.value.trim() : "";
      if (!bookId) return;
      const res = await fetch("/api/recommendations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: bookId, message: msg }),
      });
      if (res.ok) {
        modalRecommendBook.classList.remove("active");
        if (currentMainTab === "social") loadSocialFeed();
      } else {
        const err = await res.json();
        if (recError) {
          recError.textContent = err.detail || "Falha ao enviar recomendação.";
          recError.style.display = "block";
        }
      }
    });
  }

  // --- 6. Painel Administrativo Dedicado (6 Seções) ---
  let currentAdminTab = "dashboard";

  function switchAdminTab(tabName) {
    currentAdminTab = tabName;
    document.querySelectorAll(".admin-tab-btn").forEach(btn => {
      btn.classList.toggle("active", btn.getAttribute("data-admintab") === tabName);
    });
    document.querySelectorAll(".admin-tab-pane").forEach(pane => {
      pane.style.display = pane.id === `admin-pane-${tabName}` ? "block" : "none";
    });
    loadAdminPanel();
  }

  document.querySelectorAll(".admin-tab-btn").forEach(btn => {
    btn.addEventListener("click", () => switchAdminTab(btn.getAttribute("data-admintab")));
  });

  const btnAdminRefresh = document.getElementById("btn-admin-refresh");
  if (btnAdminRefresh) btnAdminRefresh.addEventListener("click", () => loadAdminPanel());

  async function loadAdminPanel() {
    if (!currentUser || currentUser.role !== "admin") return;
    if (currentAdminTab === "dashboard") await loadAdminDashboard();
    else if (currentAdminTab === "library") await loadAdminLibraryTab();
    else if (currentAdminTab === "users") await loadAdminUsersTab();
    else if (currentAdminTab === "automation") await loadAdminAutomationTab();
    else if (currentAdminTab === "metadata") await loadAdminMetadataReviewTab();
    else if (currentAdminTab === "maintenance") await loadAdminMaintenanceTab();
  }

  async function loadAdminDashboard() {
    const cardsEl = document.getElementById("admin-dashboard-cards");
    const idBreakdownEl = document.getElementById("admin-id-breakdown");
    const providersEl = document.getElementById("admin-providers-health");
    if (!cardsEl) return;

    try {
      const res = await fetch("/api/admin/dashboard");
      if (!res.ok) return;
      const d = await res.json();
      const lib = d.library || {};
      const cov = d.covers || {};
      const usr = d.users || {};
      const stg = d.storage || {};

      cardsEl.innerHTML = `
        <div class="admin-metric-card">
          <div class="metric-label">Total de Livros</div>
          <div class="metric-value">${lib.total_books || 0}</div>
          <div class="metric-sub">${lib.pdf_books || 0} PDFs &bull; ${lib.epub_books || 0} EPUBs</div>
        </div>
        <div class="admin-metric-card">
          <div class="metric-label">Capas Validadas</div>
          <div class="metric-value">${(cov.custom || 0) + (cov.extracted || 0)}</div>
          <div class="metric-sub">${cov.custom || 0} personalizadas &bull; ${cov.editorial_fallback || 0} editoriais</div>
        </div>
        <div class="admin-metric-card">
          <div class="metric-label">Usuários &amp; Sessões</div>
          <div class="metric-value">${usr.active_users || 0} / ${usr.total_users || 0}</div>
          <div class="metric-sub">${usr.active_sessions || 0} sessões ativas</div>
        </div>
        <div class="admin-metric-card">
          <div class="metric-label">Armazenamento</div>
          <div class="metric-value">${formatBytes(stg.books_bytes || 0)}</div>
          <div class="metric-sub">DB: ${formatBytes(stg.db_bytes || 0)} &bull; Livre: ${formatBytes(stg.disk_free_bytes || 0)}</div>
        </div>
      `;

      if (idBreakdownEl) {
        const byStatus = lib.by_identification_status || {};
        const labels = {
          identified: "Identificados Automaticamente",
          manual_override: "Revisados / Editados Manualmente",
          needs_review: "Revisão Necessária",
          ambiguous: "Ambíguos (Múltiplas Edições)",
          not_found: "Não Encontrados nas Bases",
          rate_limited: "Pausados por Rate Limit",
          technical_error: "Erro Técnico de Rede/Parser",
          pending: "Pendentes de Enriquecimento",
        };
        idBreakdownEl.innerHTML = Object.entries(labels).map(([k, label]) => `
          <div class="admin-stat-row">
            <span>${label}</span>
            <strong>${byStatus[k] || 0}</strong>
          </div>
        `).join("");
      }

      if (providersEl) {
        const provs = (d.providers && d.providers.providers) || [];
        if (provs.length === 0) {
          providersEl.innerHTML = `<p style="font-size: 0.83rem; color: var(--text-muted);">Nenhuma chamada externa realizada ainda.</p>`;
        } else {
          providersEl.innerHTML = provs.map(p => `
            <div class="admin-stat-row">
              <span><strong style="text-transform: capitalize;">${escapeHtml(p.provider)}</strong> (${p.total_requests} reqs)</span>
              <span>✓ ${p.success_count} | RL: ${p.rate_limit_count} | Lat: ${Math.round(p.avg_latency_ms || 0)}ms</span>
            </div>
          `).join("");
        }
      }
    } catch (e) {
      console.warn("Erro no dashboard admin:", e);
    }
  }

  async function loadAdminLibraryTab() {
    const diagEl = document.getElementById("admin-library-diagnostics");
    if (!diagEl) return;
    try {
      const res = await fetch("/api/admin/library/diagnostics");
      if (!res.ok) return;
      const d = await res.json();
      const s = d.summary || {};
      diagEl.innerHTML = `
        <div class="admin-metrics-grid">
          <div class="admin-metric-card">
            <div class="metric-label">Arquivos Ausentes no Disco</div>
            <div class="metric-value">${s.missing_files_count || 0}</div>
          </div>
          <div class="admin-metric-card">
            <div class="metric-label">Grupos de Duplicatas (Hash)</div>
            <div class="metric-value">${s.duplicate_groups_count || 0}</div>
          </div>
          <div class="admin-metric-card">
            <div class="metric-label">Metadados Incompletos</div>
            <div class="metric-value">${s.incomplete_metadata_count || 0}</div>
          </div>
          <div class="admin-metric-card">
            <div class="metric-label">Arquivos Não Suportados</div>
            <div class="metric-value">${s.unrecognized_files_count || 0}</div>
          </div>
        </div>
      `;
    } catch (e) {
      console.warn("Erro em diagnósticos da biblioteca:", e);
    }
  }

  const btnAdminOpenSources = document.getElementById("btn-admin-open-sources");
  if (btnAdminOpenSources) {
    btnAdminOpenSources.addEventListener("click", async () => {
      modalSources.classList.add("active");
      await loadSources();
    });
  }

  const btnAdminScanNow = document.getElementById("btn-admin-scan-now");
  if (btnAdminScanNow) {
    btnAdminScanNow.addEventListener("click", async () => {
      btnAdminScanNow.disabled = true;
      btnAdminScanNow.textContent = "Escaneando...";
      await fetch("/api/scan", { method: "POST" });
      btnAdminScanNow.disabled = false;
      btnAdminScanNow.textContent = "Escanear Acervo Agora";
      loadAdminLibraryTab();
      loadBooks();
    });
  }

  const btnAdminBatchApply = document.getElementById("btn-admin-batch-apply");
  if (btnAdminBatchApply) {
    btnAdminBatchApply.addEventListener("click", async () => {
      const idsRaw = (document.getElementById("batch-book-ids")?.value || "").split(",");
      const bookIds = idsRaw.map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n) && n > 0);
      const cat = document.getElementById("batch-category")?.value.trim() || undefined;
      const lang = document.getElementById("batch-language")?.value.trim() || undefined;
      const pub = document.getElementById("batch-publisher")?.value.trim() || undefined;
      const fb = document.getElementById("batch-update-feedback");

      if (bookIds.length === 0) {
        if (fb) fb.textContent = "Informe pelo menos um ID de livro válido.";
        return;
      }

      const res = await fetch("/api/metadata/batch-update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_ids: bookIds, primary_category: cat, language: lang, publisher: pub }),
      });
      if (res.ok) {
        const data = await res.json();
        if (fb) fb.textContent = `Atualizados ${data.updated_count} livros com sucesso.`;
        loadBooks();
      }
    });
  }

  async function loadAdminUsersTab() {
    const container = document.getElementById("admin-users-table-container");
    if (!container) return;
    const res = await fetch("/api/users");
    if (!res.ok) return;
    const data = await res.json();
    const users = data.users || [];
    container.innerHTML = users.map(u => `
      <div class="admin-stat-row" style="padding: 0.65rem 0;">
        <div>
          <strong>${escapeHtml(u.display_name || u.username)}</strong>
          <code style="font-size: 0.78rem; color: var(--text-muted); margin-left: 0.35rem;">@${escapeHtml(u.username)}</code>
          <span class="user-role-tag ${u.role === 'admin' ? 'role-admin' : 'role-reader'}" style="margin-left: 0.4rem;">${u.role}</span>
        </div>
        <div style="display: flex; gap: 0.4rem;">
          <button class="btn btn-subtle btn-revoke-user-sessions" data-id="${u.id}" style="font-size: 0.76rem;">Encerrar Sessões</button>
        </div>
      </div>
    `).join("");

    container.querySelectorAll(".btn-revoke-user-sessions").forEach(btn => {
      btn.addEventListener("click", async () => {
        const uid = btn.getAttribute("data-id");
        const r = await fetch(`/api/users/${uid}/revoke-sessions`, { method: "POST" });
        if (r.ok) {
          const d = await r.json();
          alert(`Sessões encerradas: ${d.revoked_sessions}`);
        }
      });
    });
  }

  const btnAdminOpenCreateUser = document.getElementById("btn-admin-open-create-user");
  if (btnAdminOpenCreateUser) {
    btnAdminOpenCreateUser.addEventListener("click", async () => {
      modalUsers.classList.add("active");
      await loadUsers();
    });
  }

  async function loadAdminAutomationTab() {
    const schedEl = document.getElementById("admin-schedules-list");
    const jobsEl = document.getElementById("admin-jobs-list");
    if (!schedEl || !jobsEl) return;

    const [sRes, jRes] = await Promise.all([
      fetch("/api/automation/schedules"),
      fetch("/api/automation/jobs?limit=25"),
    ]);

    if (sRes.ok) {
      const sData = await sRes.json();
      const schedules = sData.schedules || [];
      schedEl.innerHTML = schedules.map(sc => `
        <div class="admin-schedule-card" data-job-type="${escapeHtml(sc.job_type)}">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
            <strong>${escapeHtml(sc.job_type)}</strong>
            <label style="font-size: 0.8rem; display: flex; align-items: center; gap: 0.3rem;">
              <input type="checkbox" class="sched-enabled" ${sc.enabled ? "checked" : ""}> Ativo
            </label>
          </div>
          <div style="display: flex; gap: 0.4rem; flex-wrap: wrap; margin-bottom: 0.5rem;">
            <select class="form-input sched-mode" style="flex: 1; min-width: 110px; font-size: 0.8rem;">
              <option value="manual" ${sc.schedule_mode === "manual" ? "selected" : ""}>Manual</option>
              <option value="interval" ${sc.schedule_mode === "interval" ? "selected" : ""}>Intervalo (min)</option>
              <option value="daily" ${sc.schedule_mode === "daily" ? "selected" : ""}>Diário</option>
              <option value="weekly" ${sc.schedule_mode === "weekly" ? "selected" : ""}>Semanal</option>
            </select>
            <input type="number" class="form-input sched-interval" value="${sc.interval_minutes || 60}" min="5" style="width: 80px; font-size: 0.8rem;" title="Intervalo em minutos">
            <input type="time" class="form-input sched-time" value="${escapeHtml(sc.run_time || '03:00')}" style="width: 100px; font-size: 0.8rem;" title="Horário de execução">
          </div>
          <div style="font-size: 0.74rem; color: var(--text-muted); margin-bottom: 0.5rem;">
            Última: ${sc.last_run_at ? formatDate(sc.last_run_at) : "Nunca"} (${escapeHtml(sc.last_status || "idle")})
          </div>
          <div style="display: flex; gap: 0.4rem; justify-content: flex-end;">
            <button class="btn btn-subtle btn-save-schedule" data-job-type="${escapeHtml(sc.job_type)}" style="font-size: 0.76rem;">Salvar</button>
            <button class="btn btn-primary btn-run-job-now" data-job-type="${escapeHtml(sc.job_type)}" style="font-size: 0.76rem;">Executar Agora</button>
          </div>
        </div>
      `).join("");

      schedEl.querySelectorAll(".btn-save-schedule").forEach(btn => {
        btn.addEventListener("click", async () => {
          const jt = btn.getAttribute("data-job-type");
          const card = schedEl.querySelector(`.admin-schedule-card[data-job-type="${jt}"]`);
          const enabled = card.querySelector(".sched-enabled").checked;
          const mode = card.querySelector(".sched-mode").value;
          const interval = parseInt(card.querySelector(".sched-interval").value, 10) || 60;
          const runTime = card.querySelector(".sched-time").value || "03:00";

          const r = await fetch(`/api/automation/schedules/${encodeURIComponent(jt)}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled, schedule_mode: mode, interval_minutes: interval, run_time: runTime }),
          });
          if (r.ok) loadAdminAutomationTab();
        });
      });

      schedEl.querySelectorAll(".btn-run-job-now").forEach(btn => {
        btn.addEventListener("click", async () => {
          const jt = btn.getAttribute("data-job-type");
          btn.disabled = true;
          await fetch("/api/automation/jobs/run", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ job_type: jt }),
          });
          setTimeout(() => loadAdminAutomationTab(), 800);
        });
      });
    }

    if (jRes.ok) {
      const jData = await jRes.json();
      const jobs = jData.jobs || [];
      if (jobs.length === 0) {
        jobsEl.innerHTML = `<p style="font-size: 0.83rem; color: var(--text-muted);">Nenhuma tarefa executada ainda.</p>`;
      } else {
        jobsEl.innerHTML = jobs.map(j => `
          <div class="admin-stat-row">
            <div>
              <strong>#${j.id} — ${escapeHtml(j.job_type)}</strong>
              <span class="source-badge" style="margin-left: 0.4rem;">${escapeHtml(j.status)}</span>
              <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 0.4rem;">
                (${j.processed_items || 0}/${j.total_items || 0} itens)
              </span>
            </div>
            <div style="display: flex; gap: 0.4rem; align-items: center;">
              <span style="font-size: 0.74rem; color: var(--text-faint);">${formatDate(j.created_at)}</span>
              ${(j.status === "running" || j.status === "pending") ? `<button class="btn btn-danger btn-cancel-bg-job" data-id="${j.id}" style="font-size: 0.72rem; padding: 0.15rem 0.4rem;">Cancelar</button>` : ""}
            </div>
          </div>
        `).join("");

        jobsEl.querySelectorAll(".btn-cancel-bg-job").forEach(btn => {
          btn.addEventListener("click", async () => {
            const jid = btn.getAttribute("data-id");
            await fetch(`/api/automation/jobs/${jid}/cancel`, { method: "POST" });
            loadAdminAutomationTab();
          });
        });
      }
    }
  }

  const btnAdminRefreshJobs = document.getElementById("btn-admin-refresh-jobs");
  if (btnAdminRefreshJobs) btnAdminRefreshJobs.addEventListener("click", loadAdminAutomationTab);

  async function loadAdminMetadataReviewTab() {
    const listEl = document.getElementById("admin-metadata-review-list");
    const filterEl = document.getElementById("admin-meta-filter-status");
    if (!listEl) return;

    const statusFilter = filterEl ? filterEl.value : "needs_review";
    const res = await fetch("/api/books");
    if (!res.ok) return;
    const data = await res.json();
    let books = data.books || [];

    if (statusFilter === "needs_review") {
      books = books.filter(b => b.identification_status === "needs_review" || b.identification_status === "ambiguous");
    } else if (statusFilter !== "all") {
      books = books.filter(b => b.identification_status === statusFilter);
    }

    if (books.length === 0) {
      listEl.innerHTML = `<p style="font-size: 0.85rem; color: var(--text-muted); padding: 1rem 0;">Nenhum livro encontrado com este filtro de revisão.</p>`;
      return;
    }

    listEl.innerHTML = books.slice(0, 40).map(b => `
      <div class="admin-review-card" data-book-id="${b.id}">
        <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 0.75rem; flex-wrap: wrap;">
          <div>
            <strong style="font-size: 0.92rem;">${escapeHtml(b.title)}</strong>
            <div style="font-size: 0.8rem; color: var(--text-muted);">
              Autor: ${escapeHtml(b.author || "Não informado")} &bull; Arquivo: <code>${escapeHtml(b.filename)}</code>
            </div>
            <div style="font-size: 0.76rem; color: var(--text-faint); margin-top: 0.2rem;">
              Status: <strong>${escapeHtml(b.identification_status || "pending")}</strong> (${Math.round(b.identification_score || 0)}%)
              ${b.last_error_reason ? ` &bull; Diagnóstico: ${escapeHtml(b.last_error_reason)}` : ""}
            </div>
          </div>
          <div style="display: flex; gap: 0.4rem; flex-wrap: wrap;">
            <button class="btn btn-subtle btn-review-open-details" data-id="${b.id}" style="font-size: 0.76rem;">Comparar / Editar</button>
            <button class="btn btn-subtle btn-review-reprocess" data-id="${b.id}" style="font-size: 0.76rem;">Reprocessar</button>
            <button class="btn btn-subtle btn-review-reject" data-id="${b.id}" style="font-size: 0.76rem;">Rejeitar Sugestões</button>
            <button class="btn btn-primary btn-review-approve" data-id="${b.id}" style="font-size: 0.76rem;">Marcar Revisado</button>
          </div>
        </div>
      </div>
    `).join("");

    listEl.querySelectorAll(".btn-review-open-details").forEach(btn => {
      btn.addEventListener("click", () => openBookDetails(btn.getAttribute("data-id")));
    });
    listEl.querySelectorAll(".btn-review-reprocess").forEach(btn => {
      btn.addEventListener("click", async () => {
        const bid = btn.getAttribute("data-id");
        btn.disabled = true;
        await fetch(`/api/books/${bid}/metadata/reprocess`, { method: "POST" });
        loadAdminMetadataReviewTab();
      });
    });
    listEl.querySelectorAll(".btn-review-reject").forEach(btn => {
      btn.addEventListener("click", async () => {
        const bid = btn.getAttribute("data-id");
        await fetch(`/api/books/${bid}/metadata/reject-candidates`, { method: "POST" });
        loadAdminMetadataReviewTab();
      });
    });
    listEl.querySelectorAll(".btn-review-approve").forEach(btn => {
      btn.addEventListener("click", async () => {
        const bid = btn.getAttribute("data-id");
        await fetch(`/api/books/${bid}/metadata/review`, { method: "POST" });
        loadAdminMetadataReviewTab();
      });
    });
  }

  const adminMetaFilterStatus = document.getElementById("admin-meta-filter-status");
  if (adminMetaFilterStatus) adminMetaFilterStatus.addEventListener("change", loadAdminMetadataReviewTab);

  const btnAdminEnqueuePending = document.getElementById("btn-admin-enqueue-pending");
  if (btnAdminEnqueuePending) {
    btnAdminEnqueuePending.addEventListener("click", async () => {
      await fetch("/api/metadata/queue/enqueue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ only_pending: true }),
      });
      openMetadataCenter();
    });
  }

  const btnAdminEnqueueAll = document.getElementById("btn-admin-enqueue-all");
  if (btnAdminEnqueueAll) {
    btnAdminEnqueueAll.addEventListener("click", async () => {
      await fetch("/api/metadata/queue/enqueue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ only_pending: false }),
      });
      openMetadataCenter();
    });
  }

  async function loadAdminMaintenanceTab() {
    const statusEl = document.getElementById("admin-maintenance-status");
    const logsEl = document.getElementById("admin-audit-logs-list");
    if (!statusEl) return;

    const [mRes, lRes] = await Promise.all([
      fetch("/api/admin/maintenance/status"),
      fetch("/api/admin/audit-logs?limit=25"),
    ]);

    if (mRes.ok) {
      const m = await mRes.json();
      statusEl.innerHTML = `
        <div class="admin-stat-row"><span>Versão do Schema SQLite</span><strong>v${m.schema_version || 8}</strong></div>
        <div class="admin-stat-row"><span>Integridade do Banco</span><strong>${escapeHtml(m.db_integrity || "ok")}</strong></div>
        <div class="admin-stat-row"><span>Tamanho do Banco</span><strong>${formatBytes(m.db_size_bytes || 0)}</strong></div>
        <div class="admin-stat-row"><span>Cache de Consultas Externas</span><strong>${m.external_cache_entries || 0} entradas</strong></div>
        <div class="admin-stat-row"><span>Capas em Disco</span><strong>${formatBytes(m.covers_size_bytes || 0)} (${m.covers_files_count || 0} arqs)</strong></div>
        <div class="admin-stat-row"><span>Espaço Livre no Servidor</span><strong>${formatBytes(m.disk_free_bytes || 0)}</strong></div>
      `;
    }

    if (lRes.ok && logsEl) {
      const lData = await lRes.json();
      const logs = lData.logs || [];
      if (logs.length === 0) {
        logsEl.innerHTML = `<p style="font-size: 0.83rem; color: var(--text-muted);">Nenhuma ação administrativa registrada ainda.</p>`;
      } else {
        logsEl.innerHTML = logs.map(lg => `
          <div class="admin-stat-row" style="font-size: 0.8rem;">
            <div>
              <strong>${escapeHtml(lg.action)}</strong>
              ${lg.target_type ? `<span style="color: var(--text-muted);"> (${escapeHtml(lg.target_type)})</span>` : ""}
            </div>
            <span style="color: var(--text-faint); font-size: 0.74rem;">${formatDate(lg.created_at)}</span>
          </div>
        `).join("");
      }
    }
  }

  const btnAdminClearApiCache = document.getElementById("btn-admin-clear-api-cache");
  if (btnAdminClearApiCache) {
    btnAdminClearApiCache.addEventListener("click", async () => {
      await fetch("/api/admin/maintenance/clear-cache", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clear_api_cache: true, clear_auto_covers: false }),
      });
      loadAdminMaintenanceTab();
    });
  }

  const btnAdminClearAutoCovers = document.getElementById("btn-admin-clear-auto-covers");
  if (btnAdminClearAutoCovers) {
    btnAdminClearAutoCovers.addEventListener("click", async () => {
      await fetch("/api/admin/maintenance/clear-cache", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clear_api_cache: false, clear_auto_covers: true }),
      });
      loadAdminMaintenanceTab();
      loadBooks();
    });
  }

  const btnAdminRunDbMaint = document.getElementById("btn-admin-run-db-maint");
  if (btnAdminRunDbMaint) {
    btnAdminRunDbMaint.addEventListener("click", async () => {
      await fetch("/api/automation/jobs/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_type: "db_maintenance" }),
      });
      setTimeout(() => loadAdminMaintenanceTab(), 500);
    });
  }

  const btnAdminCreateBackup = document.getElementById("btn-admin-create-backup");
  if (btnAdminCreateBackup) {
    btnAdminCreateBackup.addEventListener("click", async () => {
      const res = await fetch("/api/backup", { method: "POST" });
      if (res.ok) {
        const d = await res.json();
        alert(`Backup criado: ${d.backup.filename}`);
        loadAdminMaintenanceTab();
      }
    });
  }

  // Fecha modais com tecla ESC
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      modalCollections.classList.remove("active");
      modalBookCol.classList.remove("active");
      if (modalBookDetails) modalBookDetails.classList.remove("active");
      if (modalMetadataCenter) closeMetadataCenter();
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
  setMainTab(currentMainTab);
  loadTaxonomyTree();
  initAuth();
});
