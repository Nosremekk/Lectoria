/**
 * Lectoria — Painel Integrado de Leitura Compartilhada (v1.7.0)
 * Vanilla ES6, carregamento sob demanda, AbortController e barreira real contra spoilers.
 */
(function () {
  "use strict";

  const ROLE_LABELS = {
    owner: "Proprietário",
    moderator: "Moderador",
    member: "Membro",
  };

  const STATUS_LABELS = {
    planned: "Planejada",
    in_progress: "Em andamento",
    completed: "Concluída",
    cancelled: "Cancelada",
  };

  const GOAL_STATUS_OPTIONS = [
    { value: "pending", label: "Pendente" },
    { value: "in_progress", label: "Em andamento" },
    { value: "completed", label: "Concluída" },
  ];

  const state = {
    generation: 0,
    readId: null,
    clubId: null,
    readMeta: null,
    activeTab: "overview",
    loaded: { overview: false, discussions: false, goals: false, quotes: false },
    controllers: {},
    topics: { page: 1, limit: 20, filter: "all", selectedTopicId: null, postsPage: 1 },
    goals: { includeArchived: false },
    quotes: { page: 1, limit: 20 },
  };

  // --- Referências DOM ---
  const el = {
    clubsListView: () => document.getElementById("clubs-list-view"),
    clubDetailView: () => document.getElementById("club-detail-view"),
    view: () => document.getElementById("shared-reading-view"),
    btnBack: () => document.getElementById("btn-back-from-shared-reading"),
    openBookLink: () => document.getElementById("sr-open-book-link"),
    bookCover: () => document.getElementById("sr-book-cover"),
    clubBadge: () => document.getElementById("sr-club-badge"),
    statusBadge: () => document.getElementById("sr-status-badge"),
    roleBadge: () => document.getElementById("sr-role-badge"),
    readTitle: () => document.getElementById("sr-read-title"),
    bookMeta: () => document.getElementById("sr-book-meta"),
    readonlyBanner: () => document.getElementById("sr-readonly-banner"),
    tabButtons: () => document.querySelectorAll("[data-sr-tab]"),
    panels: {
      overview: () => document.getElementById("sr-panel-overview"),
      discussions: () => document.getElementById("sr-panel-discussions"),
      goals: () => document.getElementById("sr-panel-goals"),
      quotes: () => document.getElementById("sr-panel-quotes"),
    },
    overviewContent: () => document.getElementById("sr-overview-content"),
    topicsListContainer: () => document.getElementById("sr-topics-list-container"),
    topicsList: () => document.getElementById("sr-topics-list"),
    topicsPagination: () => document.getElementById("sr-topics-pagination"),
    topicFilterBtns: () => document.querySelectorAll("[data-sr-topic-filter]"),
    btnNewTopic: () => document.getElementById("btn-sr-new-topic"),
    topicDetailContainer: () => document.getElementById("sr-topic-detail-container"),
    topicDetailBody: () => document.getElementById("sr-topic-detail-body"),
    btnBackTopics: () => document.getElementById("btn-sr-back-topics"),
    goalsIncludeArchived: () => document.getElementById("sr-goals-include-archived"),
    btnNewGoal: () => document.getElementById("btn-sr-new-goal"),
    goalsList: () => document.getElementById("sr-goals-list"),
    btnNewQuote: () => document.getElementById("btn-sr-new-quote"),
    quotesList: () => document.getElementById("sr-quotes-list"),
    quotesPagination: () => document.getElementById("sr-quotes-pagination"),
  };

  // --- Helpers de DOM e Segurança ---
  function node(tag, className, text) {
    const n = document.createElement(tag);
    if (className) n.className = className;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  function clearNode(container) {
    if (container) container.replaceChildren();
  }

  function currentUserId() {
    const u = window.LectoriaGetCurrentUser ? window.LectoriaGetCurrentUser() : null;
    return u ? u.id : null;
  }

  function isModeratorOrOwner() {
    const role = state.readMeta ? state.readMeta.user_club_role : null;
    return role === "owner" || role === "moderator";
  }

  function isReadOnly() {
    if (!state.readMeta) return false;
    return Boolean(
      state.readMeta.club_is_archived ||
      state.readMeta.status === "cancelled" ||
      state.readMeta.status === "completed"
    );
  }

  function abortKey(key) {
    if (state.controllers[key]) {
      state.controllers[key].abort();
      delete state.controllers[key];
    }
  }

  function abortAll() {
    Object.keys(state.controllers).forEach(abortKey);
  }

  async function apiRequest(url, options = {}, controllerKey = "shared") {
    abortKey(controllerKey);
    const controller = new AbortController();
    state.controllers[controllerKey] = controller;
    const gen = state.generation;

    const res = await fetch(url, {
      ...options,
      signal: controller.signal,
    });

    if (gen !== state.generation) {
      throw new DOMException("Stale request", "AbortError");
    }

    let data = null;
    try {
      data = await res.json();
    } catch {
      data = null;
    }

    if (!res.ok) {
      const msg = window.formatApiError
        ? window.formatApiError(data, `Erro HTTP ${res.status}`)
        : (data && data.detail) || `Erro HTTP ${res.status}`;
      const err = new Error(msg);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  async function withSubmitLock(form, fn) {
    if (!form || form.dataset.submitting === "1") return;
    form.dataset.submitting = "1";
    const submitBtn = form.querySelector('button[type="submit"]');
    if (submitBtn) submitBtn.disabled = true;
    try {
      await fn();
    } finally {
      delete form.dataset.submitting;
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  function renderLoading(container, msg = "Carregando...") {
    if (!container) return;
    clearNode(container);
    container.appendChild(node("div", "sr-state-box sr-loading", msg));
  }

  function renderError(container, err, onRetry) {
    if (!container) return;
    clearNode(container);
    const box = node("div", "sr-state-box sr-error-box");
    box.appendChild(node("p", "sr-error-text", err.message || "Não foi possível carregar os dados."));
    if (onRetry && err.status !== 403 && err.status !== 404) {
      const btn = node("button", "btn btn-subtle btn-sm", "Tentar novamente");
      btn.type = "button";
      btn.addEventListener("click", onRetry);
      box.appendChild(btn);
    }
    container.appendChild(box);
  }

  /**
   * Barreira real de spoiler: o texto secreto permanece apenas em closure JS
   * e não é inserido no DOM (nem para leitores de tela) até clique ou Enter/Espaço explícito.
   */
  function createSpoilerElement(secretText, extraText) {
    const wrapper = node("div", "sr-spoiler-wrapper");
    const btn = node(
      "button",
      "sr-spoiler-barrier",
      "Conteúdo marcado como spoiler — Clique ou pressione Enter para revelar"
    );
    btn.type = "button";
    btn.setAttribute("aria-expanded", "false");

    const reveal = () => {
      const revealedBox = node("div", "sr-spoiler-revealed");
      revealedBox.setAttribute("aria-live", "polite");
      revealedBox.appendChild(node("span", "tag-badge tag-spoiler-revealed", "Spoiler revelado"));
      revealedBox.appendChild(node("p", "sr-revealed-text", secretText));
      if (extraText) {
        revealedBox.appendChild(node("p", "sr-quote-comment", extraText));
      }
      wrapper.replaceChildren(revealedBox);
    };

    btn.addEventListener("click", reveal);
    wrapper.appendChild(btn);
    return wrapper;
  }

  function formatShortDate(iso) {
    if (!iso) return "";
    const parts = String(iso).slice(0, 10).split("-");
    if (parts.length === 3) return `${parts[2]}/${parts[1]}/${parts[0]}`;
    return String(iso);
  }

  function renderPagination(container, page, limit, total, onPageChange) {
    if (!container) return;
    clearNode(container);
    const totalPages = Math.max(1, Math.ceil((total || 0) / limit));
    if (totalPages <= 1) return;

    const prev = node("button", "btn btn-subtle btn-xs", "Anterior");
    prev.type = "button";
    prev.disabled = page <= 1;
    prev.addEventListener("click", () => onPageChange(page - 1));

    const info = node("span", "sr-page-info", `Página ${page} de ${totalPages}`);

    const next = node("button", "btn btn-subtle btn-xs", "Próxima");
    next.type = "button";
    next.disabled = page >= totalPages;
    next.addEventListener("click", () => onPageChange(page + 1));

    container.append(prev, info, next);
  }

  // --- Cabeçalho e Navegação ---
  function updateHeader(read) {
    state.readMeta = read;
    if (read && read.club_id) state.clubId = read.club_id;

    if (el.bookCover()) {
      el.bookCover().src = read.cover_url || `/api/books/${read.book_id}/cover`;
      el.bookCover().alt = `Capa de ${read.book_title || "livro"}`;
    }
    if (el.openBookLink()) el.openBookLink().href = `/reader/${read.book_id}`;
    if (el.clubBadge()) el.clubBadge().textContent = read.club_name || "Clube";
    if (el.statusBadge()) {
      el.statusBadge().textContent = STATUS_LABELS[read.status] || read.status || "";
      el.statusBadge().className = `tag-badge ${
        read.status === "in_progress"
          ? "tag-in-progress"
          : read.status === "completed"
          ? "tag-completed"
          : "tag-subtle"
      }`;
    }
    if (el.roleBadge()) {
      el.roleBadge().textContent = ROLE_LABELS[read.user_club_role] || "Membro";
    }
    if (el.readTitle()) el.readTitle().textContent = read.title || read.book_title || "Leitura Coletiva";
    if (el.bookMeta()) {
      el.bookMeta().textContent = `${read.book_title || ""}${read.book_author ? " — " + read.book_author : ""}`;
    }

    const ro = isReadOnly();
    const banner = el.readonlyBanner();
    if (banner) {
      if (ro) {
        banner.style.display = "block";
        banner.textContent = read.club_is_archived
          ? "Clube arquivado: histórico preservado em modo somente leitura."
          : `Leitura ${STATUS_LABELS[read.status] || "encerrada"}: histórico preservado em modo somente leitura.`;
      } else {
        banner.style.display = "none";
        banner.textContent = "";
      }
    }

    if (el.btnNewTopic()) el.btnNewTopic().style.display = ro ? "none" : "inline-flex";
    if (el.btnNewGoal()) el.btnNewGoal().style.display = !ro && isModeratorOrOwner() ? "inline-flex" : "none";
    if (el.btnNewQuote()) el.btnNewQuote().style.display = ro ? "none" : "inline-flex";
  }

  function switchTab(tab, force = false) {
    state.activeTab = tab;
    el.tabButtons().forEach((btn) => {
      const isSelected = btn.getAttribute("data-sr-tab") === tab;
      btn.classList.toggle("active", isSelected);
      btn.setAttribute("aria-selected", isSelected ? "true" : "false");
    });

    Object.keys(el.panels).forEach((key) => {
      const panel = el.panels[key]();
      if (panel) panel.style.display = key === tab ? "block" : "none";
    });

    if (tab === "overview" && (!state.loaded.overview || force)) {
      loadOverview();
    } else if (tab === "discussions" && (!state.loaded.discussions || force)) {
      showTopicsList();
      loadTopics();
    } else if (tab === "goals" && (!state.loaded.goals || force)) {
      loadGoals();
    } else if (tab === "quotes" && (!state.loaded.quotes || force)) {
      loadQuotes();
    }
  }

  function open(readId, clubId = null) {
    if (!readId) return;
    abortAll();
    state.generation += 1;
    state.readId = readId;
    state.clubId = clubId;
    state.readMeta = null;
    state.loaded = { overview: false, discussions: false, goals: false, quotes: false };
    state.topics = { page: 1, limit: 20, filter: "all", selectedTopicId: null, postsPage: 1 };
    state.goals = { includeArchived: false };
    state.quotes = { page: 1, limit: 20 };

    if (el.goalsIncludeArchived()) el.goalsIncludeArchived().checked = false;
    el.topicFilterBtns().forEach((b) => b.classList.toggle("active", b.getAttribute("data-sr-topic-filter") === "all"));

    if (el.clubsListView()) el.clubsListView().style.display = "none";
    if (el.clubDetailView()) el.clubDetailView().style.display = "none";
    if (el.view()) el.view().style.display = "block";

    switchTab("overview", true);
  }

  function reset() {
    abortAll();
    state.generation += 1;
    state.readId = null;
    state.clubId = null;
    state.readMeta = null;
    state.loaded = { overview: false, discussions: false, goals: false, quotes: false };
    if (el.view()) el.view().style.display = "none";
  }

  // --- Aba 1: Visão Geral ---
  async function loadOverview() {
    const container = el.overviewContent();
    renderLoading(container, "Carregando visão geral...");
    try {
      const data = await apiRequest(`/api/collective-reads/${state.readId}/overview`, {}, "overview");
      updateHeader(data.read);
      state.loaded.overview = true;
      renderOverview(data);
    } catch (err) {
      if (err.name === "AbortError") return;
      renderError(container, err, loadOverview);
    }
  }

  function renderOverview(data) {
    const container = el.overviewContent();
    clearNode(container);

    const counts = data.counts || { topics: 0, goals: 0, quotes: 0 };
    const grid = node("div", "sr-overview-stats");
    [
      { label: "Discussões", val: counts.topics, tab: "discussions" },
      { label: "Metas ativas", val: counts.goals, tab: "goals" },
      { label: "Citações", val: counts.quotes, tab: "quotes" },
    ].forEach((s) => {
      const card = node("button", "sr-stat-card");
      card.type = "button";
      card.appendChild(node("span", "sr-stat-value", String(s.val)));
      card.appendChild(node("span", "sr-stat-label", s.label));
      card.addEventListener("click", () => switchTab(s.tab));
      grid.appendChild(card);
    });
    container.appendChild(grid);

    const cols = node("div", "sr-overview-columns");

    // Próximas metas
    const goalsCol = node("div", "sr-overview-block");
    const goalsHdr = node("div", "sr-block-header");
    goalsHdr.appendChild(node("h4", "", "Próximas Metas"));
    const seeGoals = node("button", "btn btn-subtle btn-xs", "Ver Metas");
    seeGoals.type = "button";
    seeGoals.addEventListener("click", () => switchTab("goals"));
    goalsHdr.appendChild(seeGoals);
    goalsCol.appendChild(goalsHdr);

    const upcoming = data.upcoming_goals || [];
    if (upcoming.length === 0) {
      goalsCol.appendChild(node("p", "sr-empty-text", "Nenhuma meta cadastrada para esta leitura."));
    } else {
      upcoming.forEach((g) => {
        const item = node("div", "sr-mini-card");
        item.appendChild(node("strong", "", g.title));
        const metaParts = [];
        if (g.target_date) metaParts.push(`Prazo: ${formatShortDate(g.target_date)}`);
        if (g.chapter_ref) metaParts.push(g.chapter_ref);
        if (g.target_page) metaParts.push(`Pág. ${g.target_page}`);
        if (metaParts.length > 0) item.appendChild(node("span", "sr-mini-meta", metaParts.join(" · ")));
        goalsCol.appendChild(item);
      });
    }

    // Discussões recentes (títulos e badges apenas, sem conteúdo spoiler)
    const topicsCol = node("div", "sr-overview-block");
    const topicsHdr = node("div", "sr-block-header");
    topicsHdr.appendChild(node("h4", "", "Discussões Recentes"));
    const seeTopics = node("button", "btn btn-subtle btn-xs", "Ver Discussões");
    seeTopics.type = "button";
    seeTopics.addEventListener("click", () => switchTab("discussions"));
    topicsHdr.appendChild(seeTopics);
    topicsCol.appendChild(topicsHdr);

    const recent = data.recent_topics || [];
    if (recent.length === 0) {
      topicsCol.appendChild(node("p", "sr-empty-text", "Nenhum tópico criado ainda. Inicie o debate!"));
    } else {
      recent.forEach((t) => {
        const item = node("button", "sr-mini-card sr-mini-card-btn");
        item.type = "button";
        const topRow = node("div", "sr-mini-title-row");
        topRow.appendChild(node("strong", "", t.title));
        if (t.is_spoiler) topRow.appendChild(node("span", "tag-badge tag-danger", "Spoiler"));
        if (t.is_pinned) topRow.appendChild(node("span", "tag-badge tag-in-progress", "Fixado"));
        item.appendChild(topRow);
        item.appendChild(
          node(
            "span",
            "sr-mini-meta",
            `${t.author_name || "Participante"} · ${t.posts_count || 0} resposta(s)`
          )
        );
        item.addEventListener("click", () => {
          state.topics.selectedTopicId = t.id;
          switchTab("discussions");
          openTopicDetail(t.id);
        });
        topicsCol.appendChild(item);
      });
    }

    cols.append(goalsCol, topicsCol);
    container.appendChild(cols);
  }

  // --- Aba 2: Discussões ---
  function showTopicsList() {
    state.topics.selectedTopicId = null;
    if (el.topicsListContainer()) el.topicsListContainer().style.display = "block";
    if (el.topicDetailContainer()) el.topicDetailContainer().style.display = "none";
  }

  async function loadTopics() {
    const container = el.topicsList();
    renderLoading(container, "Carregando tópicos...");
    try {
      const qs = new URLSearchParams({
        page: String(state.topics.page),
        limit: String(state.topics.limit),
        filter: state.topics.filter,
      });
      const data = await apiRequest(`/api/collective-reads/${state.readId}/topics?${qs}`, {}, "topics");
      state.loaded.discussions = true;
      renderTopicsList(data);
    } catch (err) {
      if (err.name === "AbortError") return;
      renderError(container, err, loadTopics);
    }
  }

  function renderTopicsList(data) {
    const container = el.topicsList();
    clearNode(container);
    const items = data.topics || [];
    if (items.length === 0) {
      container.appendChild(node("div", "sr-state-box", "Nenhum tópico encontrado para este filtro."));
    } else {
      items.forEach((t) => {
        const card = node("article", "sr-card sr-topic-card");
        const hdr = node("div", "sr-card-header");
        const titleBtn = node("button", "sr-topic-title-btn", t.title);
        titleBtn.type = "button";
        titleBtn.addEventListener("click", () => openTopicDetail(t.id));
        hdr.appendChild(titleBtn);

        const badges = node("div", "sr-badges-row");
        if (t.is_pinned) badges.appendChild(node("span", "tag-badge tag-in-progress", "Fixado"));
        if (t.is_locked) badges.appendChild(node("span", "tag-badge tag-subtle", "Trancado"));
        if (t.is_spoiler) badges.appendChild(node("span", "tag-badge tag-danger", "Spoiler"));
        hdr.appendChild(badges);
        card.appendChild(hdr);

        const preview = t.is_spoiler
          ? "Prévia ocultada por conter spoiler. Abra o tópico para revelar manualmente."
          : t.content_preview || "";
        card.appendChild(node("p", t.is_spoiler ? "sr-spoiler-preview-hint" : "sr-card-excerpt", preview));

        const meta = node("div", "sr-card-footer");
        const refParts = [t.author_display_name || "Participante", `${t.posts_count || 0} resposta(s)`];
        if (t.chapter_ref) refParts.push(t.chapter_ref);
        if (t.page_start) refParts.push(`Pág. ${t.page_start}${t.page_end ? "–" + t.page_end : ""}`);
        meta.appendChild(node("span", "sr-card-meta", refParts.join(" · ")));

        const openBtn = node("button", "btn btn-subtle btn-xs", "Abrir Debate");
        openBtn.type = "button";
        openBtn.addEventListener("click", () => openTopicDetail(t.id));
        meta.appendChild(openBtn);
        card.appendChild(meta);

        container.appendChild(card);
      });
    }

    renderPagination(el.topicsPagination(), data.page || 1, data.limit || 20, data.total || 0, (newPage) => {
      state.topics.page = newPage;
      loadTopics();
    });
  }

  async function openTopicDetail(topicId, postsPage = 1) {
    state.topics.selectedTopicId = topicId;
    state.topics.postsPage = postsPage;
    if (el.topicsListContainer()) el.topicsListContainer().style.display = "none";
    if (el.topicDetailContainer()) el.topicDetailContainer().style.display = "block";

    const body = el.topicDetailBody();
    renderLoading(body, "Carregando discussão...");
    try {
      const qs = new URLSearchParams({ page: String(postsPage), limit: "50" });
      const data = await apiRequest(
        `/api/collective-reads/${state.readId}/topics/${topicId}?${qs}`,
        {},
        "topicDetail"
      );
      renderTopicDetail(data);
    } catch (err) {
      if (err.name === "AbortError") return;
      renderError(body, err, () => openTopicDetail(topicId, postsPage));
    }
  }

  function renderTopicDetail(data) {
    const body = el.topicDetailBody();
    clearNode(body);

    const t = data.topic;
    const ro = isReadOnly();
    const uid = currentUserId();
    const isAuthor = uid && t.user_id === uid;
    const canMod = isModeratorOrOwner();

    const topicBox = node("article", "sr-card sr-topic-detail-card");
    const hdr = node("div", "sr-card-header");
    hdr.appendChild(node("h4", "sr-detail-title", t.title));

    const badges = node("div", "sr-badges-row");
    if (t.is_pinned) badges.appendChild(node("span", "tag-badge tag-in-progress", "Fixado"));
    if (t.is_locked) badges.appendChild(node("span", "tag-badge tag-subtle", "Trancado"));
    if (t.is_spoiler) badges.appendChild(node("span", "tag-badge tag-danger", "Spoiler"));
    if (t.is_deleted) badges.appendChild(node("span", "tag-badge tag-danger", "Removido"));
    hdr.appendChild(badges);
    topicBox.appendChild(hdr);

    const metaParts = [t.author_display_name || "Participante", formatShortDate(t.created_at)];
    if (t.chapter_ref) metaParts.push(t.chapter_ref);
    if (t.page_start) metaParts.push(`Pág. ${t.page_start}${t.page_end ? "–" + t.page_end : ""}`);
    topicBox.appendChild(node("div", "sr-card-meta", metaParts.join(" · ")));

    if (t.is_spoiler && !t.is_deleted) {
      topicBox.appendChild(createSpoilerElement(t.content));
    } else {
      topicBox.appendChild(node("div", "sr-topic-body-text", t.content));
    }

    if (!ro && !t.is_deleted) {
      const actions = node("div", "sr-card-actions");
      if (isAuthor && !t.is_locked) {
        const editBtn = node("button", "btn btn-subtle btn-xs", "Editar");
        editBtn.type = "button";
        editBtn.addEventListener("click", () => openTopicModal(t));
        actions.appendChild(editBtn);
      }
      if (canMod) {
        const pinBtn = node("button", "btn btn-subtle btn-xs", t.is_pinned ? "Desafixar" : "Fixar");
        pinBtn.type = "button";
        pinBtn.addEventListener("click", () => moderateTopic(t.id, { is_pinned: !t.is_pinned }));
        const lockBtn = node("button", "btn btn-subtle btn-xs", t.is_locked ? "Destrancar" : "Trancar");
        lockBtn.type = "button";
        lockBtn.addEventListener("click", () => moderateTopic(t.id, { is_locked: !t.is_locked }));
        actions.append(pinBtn, lockBtn);
      }
      if (isAuthor || canMod) {
        const delBtn = node("button", "btn btn-danger btn-xs", "Excluir");
        delBtn.type = "button";
        delBtn.addEventListener("click", () => deleteTopic(t.id));
        actions.appendChild(delBtn);
      }
      if (actions.childNodes.length > 0) topicBox.appendChild(actions);
    }
    body.appendChild(topicBox);

    // Formulário de resposta raiz
    if (!ro && !t.is_deleted && !t.is_locked) {
      const replyForm = node("form", "sr-reply-form");
      const textarea = document.createElement("textarea");
      textarea.className = "form-input";
      textarea.rows = 3;
      textarea.maxLength = 5000;
      textarea.required = true;
      textarea.placeholder = "Escreva uma resposta para este tópico...";

      const formFoot = node("div", "sr-reply-form-footer");
      const spLabel = node("label", "sr-checkbox-label");
      const spCheck = document.createElement("input");
      spCheck.type = "checkbox";
      spLabel.append(spCheck, node("span", "", "Marcar resposta como spoiler"));

      const submitBtn = node("button", "btn btn-primary btn-sm", "Responder");
      submitBtn.type = "submit";
      formFoot.append(spLabel, submitBtn);

      const errBox = node("div", "sr-form-error");
      errBox.style.display = "none";

      replyForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        await withSubmitLock(replyForm, async () => {
          errBox.style.display = "none";
          try {
            await apiRequest(
              `/api/collective-reads/${state.readId}/topics/${t.id}/posts`,
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ content: textarea.value, is_spoiler: spCheck.checked }),
              },
              "postCreate"
            );
            state.loaded.overview = false;
            state.loaded.discussions = false;
            openTopicDetail(t.id, state.topics.postsPage);
          } catch (err) {
            errBox.textContent = err.message;
            errBox.style.display = "block";
          }
        });
      });

      replyForm.append(textarea, formFoot, errBox);
      body.appendChild(replyForm);
    } else if (t.is_locked) {
      body.appendChild(node("div", "sr-readonly-banner", "Este tópico está trancado para novas respostas."));
    }

    // Lista de respostas (profundidade 0 e 1)
    const postsHdr = node("h5", "sr-section-subtitle", `Respostas (${data.total_posts || 0})`);
    body.appendChild(postsHdr);

    const postsWrap = node("div", "sr-posts-list");
    const posts = data.posts || [];
    if (posts.length === 0) {
      postsWrap.appendChild(node("p", "sr-empty-text", "Nenhuma resposta ainda."));
    } else {
      posts.forEach((p) => {
        const pCard = node("div", `sr-post-card ${p.depth >= 1 ? "sr-post-nested" : ""}`);
        const pHdr = node("div", "sr-post-header");
        pHdr.appendChild(node("strong", "", p.author_display_name || "Participante"));
        const pMeta = node("span", "sr-card-meta", formatShortDate(p.created_at));
        pHdr.appendChild(pMeta);
        if (p.is_spoiler && !p.is_deleted) pHdr.appendChild(node("span", "tag-badge tag-danger", "Spoiler"));
        pCard.appendChild(pHdr);

        const contentSlot = node("div", "sr-post-content");
        if (p.is_deleted) {
          contentSlot.appendChild(node("em", "sr-deleted-text", p.content));
        } else if (p.is_spoiler) {
          contentSlot.appendChild(createSpoilerElement(p.content));
        } else {
          contentSlot.appendChild(node("p", "sr-post-text", p.content));
        }
        pCard.appendChild(contentSlot);

        if (!ro && !t.is_deleted && !t.is_locked && !p.is_deleted) {
          const pActions = node("div", "sr-card-actions");
          if (p.depth === 0) {
            const replyBtn = node("button", "btn btn-subtle btn-xs", "Responder");
            replyBtn.type = "button";
            replyBtn.addEventListener("click", () => toggleInlineReply(pCard, t.id, p.id));
            pActions.appendChild(replyBtn);
          }
          if (uid && p.user_id === uid) {
            const editBtn = node("button", "btn btn-subtle btn-xs", "Editar");
            editBtn.type = "button";
            editBtn.addEventListener("click", () => toggleInlineEditPost(contentSlot, t.id, p));
            pActions.appendChild(editBtn);
          }
          if ((uid && p.user_id === uid) || canMod) {
            const delBtn = node("button", "btn btn-danger btn-xs", "Excluir");
            delBtn.type = "button";
            delBtn.addEventListener("click", () => deletePost(t.id, p.id));
            pActions.appendChild(delBtn);
          }
          if (pActions.childNodes.length > 0) pCard.appendChild(pActions);
        }
        postsWrap.appendChild(pCard);
      });
    }
    body.appendChild(postsWrap);

    const pag = node("div", "sr-pagination");
    renderPagination(pag, data.page || 1, data.limit || 50, data.total_posts || 0, (np) => {
      openTopicDetail(t.id, np);
    });
    body.appendChild(pag);
  }

  function toggleInlineReply(parentCard, topicId, parentPostId) {
    const existing = parentCard.querySelector(".sr-inline-reply-form");
    if (existing) {
      existing.remove();
      return;
    }
    const form = node("form", "sr-inline-reply-form");
    const input = document.createElement("textarea");
    input.className = "form-input";
    input.rows = 2;
    input.maxLength = 5000;
    input.required = true;
    input.placeholder = "Responder a este comentário...";

    const row = node("div", "sr-inline-actions");
    const spLabel = node("label", "sr-checkbox-label");
    const spCheck = document.createElement("input");
    spCheck.type = "checkbox";
    spLabel.append(spCheck, node("span", "", "Spoiler"));

    const send = node("button", "btn btn-primary btn-xs", "Enviar");
    send.type = "submit";
    const cancel = node("button", "btn btn-subtle btn-xs", "Cancelar");
    cancel.type = "button";
    cancel.addEventListener("click", () => form.remove());
    row.append(spLabel, cancel, send);

    const errBox = node("div", "sr-form-error");
    errBox.style.display = "none";

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      errBox.style.display = "none";
      try {
        await apiRequest(
          `/api/collective-reads/${state.readId}/topics/${topicId}/posts`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              content: input.value,
              parent_post_id: parentPostId,
              is_spoiler: spCheck.checked,
            }),
          },
          "postReply"
        );
        state.loaded.overview = false;
        state.loaded.discussions = false;
        openTopicDetail(topicId, state.topics.postsPage);
      } catch (err) {
        errBox.textContent = err.message;
        errBox.style.display = "block";
      }
    });

    form.append(input, row, errBox);
    parentCard.appendChild(form);
    input.focus();
  }

  function toggleInlineEditPost(contentSlot, topicId, post) {
    const form = node("form", "sr-inline-reply-form");
    const input = document.createElement("textarea");
    input.className = "form-input";
    input.rows = 2;
    input.maxLength = 5000;
    input.required = true;
    input.value = post.content || "";

    const row = node("div", "sr-inline-actions");
    const save = node("button", "btn btn-primary btn-xs", "Salvar");
    save.type = "submit";
    const cancel = node("button", "btn btn-subtle btn-xs", "Cancelar");
    cancel.type = "button";
    cancel.addEventListener("click", () => openTopicDetail(topicId, state.topics.postsPage));
    row.append(cancel, save);

    const errBox = node("div", "sr-form-error");
    errBox.style.display = "none";

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await apiRequest(
          `/api/collective-reads/${state.readId}/topics/${topicId}/posts/${post.id}`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content: input.value }),
          },
          "postEdit"
        );
        openTopicDetail(topicId, state.topics.postsPage);
      } catch (err) {
        errBox.textContent = err.message;
        errBox.style.display = "block";
      }
    });

    form.append(input, row, errBox);
    contentSlot.replaceChildren(form);
    input.focus();
  }

  async function moderateTopic(topicId, payload) {
    try {
      await apiRequest(
        `/api/collective-reads/${state.readId}/topics/${topicId}/moderate`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
        "topicMod"
      );
      state.loaded.overview = false;
      state.loaded.discussions = false;
      openTopicDetail(topicId, state.topics.postsPage);
    } catch (err) {
      alert(err.message);
    }
  }

  async function deleteTopic(topicId) {
    if (!confirm("Remover este tópico? O histórico de respostas será preservado.")) return;
    try {
      await apiRequest(`/api/collective-reads/${state.readId}/topics/${topicId}`, { method: "DELETE" }, "topicDel");
      state.loaded.overview = false;
      state.loaded.discussions = false;
      showTopicsList();
      loadTopics();
    } catch (err) {
      alert(err.message);
    }
  }

  async function deletePost(topicId, postId) {
    if (!confirm("Remover este comentário?")) return;
    try {
      await apiRequest(
        `/api/collective-reads/${state.readId}/topics/${topicId}/posts/${postId}`,
        { method: "DELETE" },
        "postDel"
      );
      state.loaded.overview = false;
      state.loaded.discussions = false;
      openTopicDetail(topicId, state.topics.postsPage);
    } catch (err) {
      alert(err.message);
    }
  }

  // --- Aba 3: Metas ---
  async function loadGoals() {
    const container = el.goalsList();
    renderLoading(container, "Carregando metas...");
    try {
      const qs = new URLSearchParams({
        include_archived: state.goals.includeArchived ? "true" : "false",
      });
      const data = await apiRequest(`/api/collective-reads/${state.readId}/goals?${qs}`, {}, "goals");
      state.loaded.goals = true;
      renderGoals(data);
    } catch (err) {
      if (err.name === "AbortError") return;
      renderError(container, err, loadGoals);
    }
  }

  function renderGoals(data) {
    const container = el.goalsList();
    clearNode(container);
    const goals = data.goals || [];
    const totalParticipants = data.total_participants || 0;
    const ro = isReadOnly();
    const canMod = isModeratorOrOwner();

    if (goals.length === 0) {
      container.appendChild(node("div", "sr-state-box", "Nenhuma meta cadastrada para esta leitura."));
      return;
    }

    goals.forEach((g, idx) => {
      const card = node("article", `sr-card sr-goal-card ${g.is_archived ? "sr-goal-archived" : ""}`);
      const hdr = node("div", "sr-card-header");
      hdr.appendChild(node("h4", "sr-goal-title", g.title));

      const badges = node("div", "sr-badges-row");
      if (g.is_archived) badges.appendChild(node("span", "tag-badge tag-subtle", "Arquivada"));
      const counts = g.counts || {};
      const compCount = counts.completed || 0;
      badges.appendChild(
        node(
          "span",
          "tag-badge tag-completed",
          totalParticipants > 0
            ? `${compCount}/${totalParticipants} concluíram`
            : `${compCount} concluíram`
        )
      );
      hdr.appendChild(badges);
      card.appendChild(hdr);

      const metaParts = [];
      if (g.target_date) metaParts.push(`Prazo: ${formatShortDate(g.target_date)}`);
      if (g.chapter_ref) metaParts.push(g.chapter_ref);
      if (g.target_page) metaParts.push(`Pág. ${g.target_page}`);
      if (metaParts.length > 0) card.appendChild(node("div", "sr-card-meta", metaParts.join(" · ")));

      if (g.description) card.appendChild(node("p", "sr-card-excerpt", g.description));

      // Progresso individual estritamente voluntário
      const progRow = node("div", "sr-goal-progress-row");
      progRow.appendChild(node("span", "sr-progress-label", "Meu status voluntário:"));
      const btnGroup = node("div", "sr-progress-btn-group");
      const myStatus = g.my_status || "pending";
      GOAL_STATUS_OPTIONS.forEach((opt) => {
        const b = node("button", `btn btn-xs ${myStatus === opt.value ? "btn-primary" : "btn-subtle"}`, opt.label);
        b.type = "button";
        b.disabled = ro || Boolean(g.is_archived);
        b.setAttribute("aria-pressed", myStatus === opt.value ? "true" : "false");
        b.addEventListener("click", () => updateGoalProgress(g.id, opt.value));
        btnGroup.appendChild(b);
      });
      progRow.appendChild(btnGroup);
      card.appendChild(progRow);

      if (!ro && canMod) {
        const actions = node("div", "sr-card-actions");
        if (idx > 0) {
          const upBtn = node("button", "btn btn-subtle btn-xs", "Subir");
          upBtn.type = "button";
          upBtn.addEventListener("click", () => reorderGoal(goals, idx, -1));
          actions.appendChild(upBtn);
        }
        if (idx < goals.length - 1) {
          const downBtn = node("button", "btn btn-subtle btn-xs", "Descer");
          downBtn.type = "button";
          downBtn.addEventListener("click", () => reorderGoal(goals, idx, 1));
          actions.appendChild(downBtn);
        }
        const editBtn = node("button", "btn btn-subtle btn-xs", "Editar");
        editBtn.type = "button";
        editBtn.addEventListener("click", () => openGoalModal(g));
        actions.appendChild(editBtn);

        if (!g.is_archived) {
          const archBtn = node("button", "btn btn-danger btn-xs", "Arquivar");
          archBtn.type = "button";
          archBtn.addEventListener("click", () => archiveGoal(g.id));
          actions.appendChild(archBtn);
        }
        card.appendChild(actions);
      }

      container.appendChild(card);
    });
  }

  async function updateGoalProgress(goalId, statusVal) {
    try {
      await apiRequest(
        `/api/collective-reads/${state.readId}/goals/${goalId}/progress`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: statusVal }),
        },
        "goalProgress"
      );
      state.loaded.overview = false;
      loadGoals();
    } catch (err) {
      alert(err.message);
    }
  }

  async function reorderGoal(goals, idx, delta) {
    const a = goals[idx];
    const b = goals[idx + delta];
    if (!a || !b) return;
    try {
      await apiRequest(
        `/api/collective-reads/${state.readId}/goals/${a.id}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ display_order: idx + delta }),
        },
        "goalOrderA"
      );
      await apiRequest(
        `/api/collective-reads/${state.readId}/goals/${b.id}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ display_order: idx }),
        },
        "goalOrderB"
      );
      loadGoals();
    } catch (err) {
      alert(err.message);
    }
  }

  async function archiveGoal(goalId) {
    if (!confirm("Arquivar esta meta? O progresso registrado será preservado.")) return;
    try {
      await apiRequest(`/api/collective-reads/${state.readId}/goals/${goalId}`, { method: "DELETE" }, "goalArch");
      state.loaded.overview = false;
      loadGoals();
    } catch (err) {
      alert(err.message);
    }
  }

  // --- Aba 4: Citações ---
  async function loadQuotes() {
    const container = el.quotesList();
    renderLoading(container, "Carregando citações...");
    try {
      const qs = new URLSearchParams({
        page: String(state.quotes.page),
        limit: String(state.quotes.limit),
      });
      const data = await apiRequest(`/api/collective-reads/${state.readId}/quotes?${qs}`, {}, "quotes");
      state.loaded.quotes = true;
      renderQuotes(data);
    } catch (err) {
      if (err.name === "AbortError") return;
      renderError(container, err, loadQuotes);
    }
  }

  function renderQuotes(data) {
    const container = el.quotesList();
    clearNode(container);
    const items = data.quotes || [];
    const uid = currentUserId();
    const canMod = isModeratorOrOwner();
    const ro = isReadOnly();

    if (items.length === 0) {
      container.appendChild(node("div", "sr-state-box", "Nenhuma citação compartilhada ainda."));
    } else {
      items.forEach((q) => {
        const card = node("article", "sr-card sr-quote-card");
        const hdr = node("div", "sr-card-header");
        hdr.appendChild(node("strong", "", q.author_display_name || "Participante"));
        const badges = node("div", "sr-badges-row");
        if (q.is_spoiler) badges.appendChild(node("span", "tag-badge tag-danger", "Spoiler"));
        hdr.appendChild(badges);
        card.appendChild(hdr);

        if (q.is_spoiler) {
          card.appendChild(createSpoilerElement(`“${q.quote_text}”`, q.comment_text));
        } else {
          const bq = node("blockquote", "sr-quote-blockquote", `“${q.quote_text}”`);
          card.appendChild(bq);
          if (q.comment_text) card.appendChild(node("p", "sr-quote-comment", q.comment_text));
        }

        const foot = node("div", "sr-card-footer");
        const metaParts = [formatShortDate(q.created_at)];
        if (q.chapter_name) metaParts.push(q.chapter_name);
        if (q.page_number) metaParts.push(`Pág. ${q.page_number}`);
        foot.appendChild(node("span", "sr-card-meta", metaParts.join(" · ")));

        const actions = node("div", "sr-card-actions");
        if (state.readMeta && state.readMeta.book_id) {
          const openReader = node("a", "btn btn-subtle btn-xs", "Abrir no Leitor");
          let href = `/reader/${state.readMeta.book_id}`;
          if (q.epub_cfi) href += `#cfi=${encodeURIComponent(q.epub_cfi)}`;
          else if (q.page_number) href += `#page=${q.page_number}`;
          openReader.href = href;
          openReader.target = "_blank";
          actions.appendChild(openReader);
        }
        if (!ro && ((uid && q.user_id === uid) || canMod)) {
          const delBtn = node("button", "btn btn-danger btn-xs", "Excluir");
          delBtn.type = "button";
          delBtn.addEventListener("click", () => deleteQuote(q.id));
          actions.appendChild(delBtn);
        }
        foot.appendChild(actions);
        card.appendChild(foot);
        container.appendChild(card);
      });
    }

    renderPagination(el.quotesPagination(), data.page || 1, data.limit || 20, data.total || 0, (np) => {
      state.quotes.page = np;
      loadQuotes();
    });
  }

  async function deleteQuote(quoteId) {
    if (!confirm("Remover esta citação compartilhada?")) return;
    try {
      await apiRequest(`/api/collective-reads/${state.readId}/quotes/${quoteId}`, { method: "DELETE" }, "quoteDel");
      state.loaded.overview = false;
      loadQuotes();
    } catch (err) {
      alert(err.message);
    }
  }

  // --- Modais ---
  function openTopicModal(topic = null) {
    const modal = document.getElementById("modal-sr-topic");
    if (!modal) return;
    document.getElementById("sr-topic-form-id").value = topic ? topic.id : "";
    document.getElementById("modal-sr-topic-title").textContent = topic ? "Editar Tópico" : "Novo Tópico de Discussão";
    document.getElementById("sr-topic-title").value = topic ? topic.title || "" : "";
    document.getElementById("sr-topic-content").value = topic ? topic.content || "" : "";
    document.getElementById("sr-topic-chapter").value = topic ? topic.chapter_ref || "" : "";
    document.getElementById("sr-topic-page-start").value = topic && topic.page_start ? topic.page_start : "";
    document.getElementById("sr-topic-page-end").value = topic && topic.page_end ? topic.page_end : "";
    document.getElementById("sr-topic-spoiler").checked = Boolean(topic && topic.is_spoiler);
    const err = document.getElementById("sr-topic-form-error");
    err.style.display = "none";
    modal.classList.add("active");
    document.getElementById("sr-topic-title").focus();
  }

  function openGoalModal(goal = null) {
    const modal = document.getElementById("modal-sr-goal");
    if (!modal) return;
    document.getElementById("sr-goal-form-id").value = goal ? goal.id : "";
    document.getElementById("modal-sr-goal-title").textContent = goal ? "Editar Meta Coletiva" : "Nova Meta Coletiva";
    document.getElementById("sr-goal-title").value = goal ? goal.title || "" : "";
    document.getElementById("sr-goal-desc").value = goal ? goal.description || "" : "";
    document.getElementById("sr-goal-date").value = goal && goal.target_date ? goal.target_date.slice(0, 10) : "";
    document.getElementById("sr-goal-chapter").value = goal ? goal.chapter_ref || "" : "";
    document.getElementById("sr-goal-page").value = goal && goal.target_page ? goal.target_page : "";
    const err = document.getElementById("sr-goal-form-error");
    err.style.display = "none";
    modal.classList.add("active");
    document.getElementById("sr-goal-title").focus();
  }

  function openQuoteModal() {
    const modal = document.getElementById("modal-sr-quote");
    if (!modal) return;
    document.getElementById("sr-quote-text").value = "";
    document.getElementById("sr-quote-comment").value = "";
    document.getElementById("sr-quote-chapter").value = "";
    document.getElementById("sr-quote-page").value = "";
    document.getElementById("sr-quote-spoiler").checked = false;
    document.getElementById("sr-quote-char-counter").textContent = "0 / 1500";
    document.getElementById("sr-quote-form-error").style.display = "none";
    modal.classList.add("active");
    document.getElementById("sr-quote-text").focus();
  }

  function bindEvents() {
    if (el.btnBack()) {
      el.btnBack().addEventListener("click", () => {
        const cid = state.clubId;
        reset();
        if (window.LectoriaReturnToClub) window.LectoriaReturnToClub(cid);
      });
    }

    el.tabButtons().forEach((btn) => {
      btn.addEventListener("click", () => switchTab(btn.getAttribute("data-sr-tab")));
    });

    el.topicFilterBtns().forEach((btn) => {
      btn.addEventListener("click", () => {
        state.topics.filter = btn.getAttribute("data-sr-topic-filter") || "all";
        state.topics.page = 1;
        el.topicFilterBtns().forEach((b) => b.classList.toggle("active", b === btn));
        loadTopics();
      });
    });

    if (el.btnNewTopic()) el.btnNewTopic().addEventListener("click", () => openTopicModal(null));
    if (el.btnBackTopics()) {
      el.btnBackTopics().addEventListener("click", () => {
        showTopicsList();
        if (!state.loaded.discussions) loadTopics();
      });
    }

    if (el.goalsIncludeArchived()) {
      el.goalsIncludeArchived().addEventListener("change", () => {
        state.goals.includeArchived = el.goalsIncludeArchived().checked;
        loadGoals();
      });
    }
    if (el.btnNewGoal()) el.btnNewGoal().addEventListener("click", () => openGoalModal(null));
    if (el.btnNewQuote()) el.btnNewQuote().addEventListener("click", openQuoteModal);

    // Submits dos modais
    const formTopic = document.getElementById("form-sr-topic");
    if (formTopic) {
      ["btn-close-sr-topic", "btn-cancel-sr-topic"].forEach((id) => {
        const b = document.getElementById(id);
        if (b) b.addEventListener("click", () => document.getElementById("modal-sr-topic").classList.remove("active"));
      });
      formTopic.addEventListener("submit", async (e) => {
        e.preventDefault();
        await withSubmitLock(formTopic, async () => {
          const err = document.getElementById("sr-topic-form-error");
          err.style.display = "none";
          const tid = document.getElementById("sr-topic-form-id").value;
          const pStart = document.getElementById("sr-topic-page-start").value;
          const pEnd = document.getElementById("sr-topic-page-end").value;
          const payload = {
            title: document.getElementById("sr-topic-title").value,
            content: document.getElementById("sr-topic-content").value,
            chapter_ref: document.getElementById("sr-topic-chapter").value.trim() || null,
            page_start: pStart ? parseInt(pStart, 10) : null,
            page_end: pEnd ? parseInt(pEnd, 10) : null,
            is_spoiler: document.getElementById("sr-topic-spoiler").checked,
          };
          try {
            const url = tid
              ? `/api/collective-reads/${state.readId}/topics/${tid}`
              : `/api/collective-reads/${state.readId}/topics`;
            await apiRequest(
              url,
              { method: tid ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
              "topicSave"
            );
            document.getElementById("modal-sr-topic").classList.remove("active");
            state.loaded.overview = false;
            state.loaded.discussions = false;
            if (tid && state.topics.selectedTopicId === tid) openTopicDetail(tid, state.topics.postsPage);
            else {
              showTopicsList();
              loadTopics();
            }
          } catch (ex) {
            err.textContent = ex.message;
            err.style.display = "block";
          }
        });
      });
    }

    const formGoal = document.getElementById("form-sr-goal");
    if (formGoal) {
      ["btn-close-sr-goal", "btn-cancel-sr-goal"].forEach((id) => {
        const b = document.getElementById(id);
        if (b) b.addEventListener("click", () => document.getElementById("modal-sr-goal").classList.remove("active"));
      });
      formGoal.addEventListener("submit", async (e) => {
        e.preventDefault();
        await withSubmitLock(formGoal, async () => {
          const err = document.getElementById("sr-goal-form-error");
          err.style.display = "none";
          const gid = document.getElementById("sr-goal-form-id").value;
          const tPage = document.getElementById("sr-goal-page").value;
          const payload = {
            title: document.getElementById("sr-goal-title").value,
            description: document.getElementById("sr-goal-desc").value,
            target_date: document.getElementById("sr-goal-date").value || null,
            chapter_ref: document.getElementById("sr-goal-chapter").value.trim() || null,
            target_page: tPage ? parseInt(tPage, 10) : null,
          };
          try {
            const url = gid
              ? `/api/collective-reads/${state.readId}/goals/${gid}`
              : `/api/collective-reads/${state.readId}/goals`;
            await apiRequest(
              url,
              { method: gid ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
              "goalSave"
            );
            document.getElementById("modal-sr-goal").classList.remove("active");
            state.loaded.overview = false;
            loadGoals();
          } catch (ex) {
            err.textContent = ex.message;
            err.style.display = "block";
          }
        });
      });
    }

    const formQuote = document.getElementById("form-sr-quote");
    if (formQuote) {
      const qText = document.getElementById("sr-quote-text");
      const qCounter = document.getElementById("sr-quote-char-counter");
      if (qText && qCounter) {
        qText.addEventListener("input", () => {
          qCounter.textContent = `${qText.value.length} / 1500`;
        });
      }
      ["btn-close-sr-quote", "btn-cancel-sr-quote"].forEach((id) => {
        const b = document.getElementById(id);
        if (b) b.addEventListener("click", () => document.getElementById("modal-sr-quote").classList.remove("active"));
      });
      formQuote.addEventListener("submit", async (e) => {
        e.preventDefault();
        await withSubmitLock(formQuote, async () => {
          const err = document.getElementById("sr-quote-form-error");
          err.style.display = "none";
          const qPage = document.getElementById("sr-quote-page").value;
          const payload = {
            quote_text: document.getElementById("sr-quote-text").value,
            comment_text: document.getElementById("sr-quote-comment").value.trim() || null,
            chapter_name: document.getElementById("sr-quote-chapter").value.trim() || null,
            page_number: qPage ? parseInt(qPage, 10) : null,
            is_spoiler: document.getElementById("sr-quote-spoiler").checked,
          };
          try {
            await apiRequest(
              `/api/collective-reads/${state.readId}/quotes`,
              { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
              "quoteSave"
            );
            document.getElementById("modal-sr-quote").classList.remove("active");
            state.loaded.overview = false;
            state.quotes.page = 1;
            loadQuotes();
          } catch (ex) {
            err.textContent = ex.message;
            err.style.display = "block";
          }
        });
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindEvents);
  } else {
    bindEvents();
  }

  window.LectoriaSharedReading = {
    open,
    reset,
    switchTab,
  };
})();
