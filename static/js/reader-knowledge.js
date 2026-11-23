/**
 * Lectoria v1.9.0 (Fase 3) — Módulo compartilhado de Conhecimento Conectado (Atlas)
 * para os leitores PDF e EPUB.
 *
 * Responsabilidades:
 * - Comunicação autenticada com /api/knowledge/* (CSRF via cabeçalho X-CSRF-Token).
 * - Associação e criação contextual de Conceitos para destaques e notas persistidos.
 * - Criação, edição de justificativa/relação e exclusão de Conexões Semânticas.
 * - Consulta sob demanda de Conexões de saída e Backlinks recebidos.
 * - Renderização segura (zero innerHTML com texto da API) e tratamento de referências indisponíveis.
 * - Navegação segura entre páginas PDF, CFIs EPUB e obras distintas preservando progresso.
 */
(function (global) {
  "use strict";

  const api = global.LectoriaKnowledgeApi;
  const RELATION_TYPES = api.RELATION_TYPES;
  const RELATION_MAP = api.RELATION_MAP;
  const ENTITY_TYPE_LABELS = api.ENTITY_TYPE_LABELS;
  const DEFAULT_CONCEPT_COLOR = api.DEFAULT_CONCEPT_COLOR;
  const sanitizeHexColor = api.sanitizeHexColor;
  const truncateText = api.truncateText;
  const clearNode = api.clearNode;
  const createEl = api.createEl;
  const buildSafeNavigationSpec = api.buildSafeNavigationSpec;
  const formatLocationMeta = api.formatLocationMeta;

  const LOOKUP_DEBOUNCE_MS = 220;
  const PAGE_LIMIT = 25;

  function createReaderKnowledgeController() {
    let adapter = null;
    let isInitialized = false;
    let isOpen = false;
    let activeEntity = null; // { entityType: 'highlight'|'note', entityId, excerpt, isOwn, authorName, bookId, page, epubCfi }
    let lastTriggerEl = null;
    let onMutatedCallback = null;

    // Transient UI state (never persisted to localStorage)
    let activeTab = "outgoing"; // 'outgoing' | 'incoming'
    let outgoingLinks = [];
    let incomingLinks = [];
    let totalOutgoing = 0;
    let totalIncoming = 0;
    let hasMoreOutgoing = false;
    let hasMoreIncoming = false;
    let outgoingOffset = 0;
    let incomingOffset = 0;

    let selectedTarget = null; // lookup item selected in New Connection form
    let editingLinkId = null;
    let confirmingDeleteLinkId = null;
    let activeConceptPreviewId = null;

    // Concurrency guards
    let connectionsAbortCtrl = null;
    let connectionsSeq = 0;
    let conceptLookupAbortCtrl = null;
    let conceptLookupSeq = 0;
    let conceptLookupTimer = null;
    let targetLookupAbortCtrl = null;
    let targetLookupSeq = 0;
    let targetLookupTimer = null;
    const submitLocks = new Set();

    // Cached DOM references
    const dom = {};

    function cacheDom() {
      dom.modal = document.getElementById("modal-reader-knowledge");
      dom.dialog = dom.modal ? dom.modal.querySelector(".rk-modal-content") : null;
      dom.title = document.getElementById("rk-modal-title");
      dom.closeBtn = document.getElementById("btn-close-reader-knowledge");
      dom.contextBox = document.getElementById("rk-entity-context");
      dom.statusBanner = document.getElementById("rk-status-banner");

      // Concepts section
      dom.conceptsList = document.getElementById("rk-associated-concepts");
      dom.conceptPreview = document.getElementById("rk-concept-preview");
      dom.conceptSearchInput = document.getElementById("rk-concept-search");
      dom.conceptSearchResults = document.getElementById("rk-concept-search-results");
      dom.btnToggleCreateConcept = document.getElementById("rk-btn-toggle-create-concept");
      dom.createConceptForm = document.getElementById("rk-create-concept-form");
      dom.conceptNameInput = document.getElementById("rk-concept-name");
      dom.conceptDescInput = document.getElementById("rk-concept-desc");
      dom.conceptTagsInput = document.getElementById("rk-concept-tags");
      dom.conceptColorInput = document.getElementById("rk-concept-color");
      dom.btnCancelCreateConcept = document.getElementById("rk-btn-cancel-create-concept");
      dom.btnSubmitCreateConcept = document.getElementById("rk-btn-submit-create-concept");

      // Connections & Backlinks section
      dom.tabOutgoing = document.getElementById("rk-tab-outgoing");
      dom.tabIncoming = document.getElementById("rk-tab-incoming");
      dom.btnToggleNewLink = document.getElementById("rk-btn-toggle-new-link");
      dom.newLinkForm = document.getElementById("rk-new-link-form");
      dom.newLinkOriginLabel = document.getElementById("rk-new-link-origin");
      dom.targetTypeSelect = document.getElementById("rk-target-type");
      dom.targetBookOnlyCheck = document.getElementById("rk-target-book-only");
      dom.targetSearchInput = document.getElementById("rk-target-search");
      dom.targetSearchResults = document.getElementById("rk-target-search-results");
      dom.selectedTargetBox = document.getElementById("rk-selected-target");
      dom.relationSelect = document.getElementById("rk-relation-select");
      dom.directionPreview = document.getElementById("rk-direction-preview");
      dom.rationaleInput = document.getElementById("rk-rationale-input");
      dom.rationaleCounter = document.getElementById("rk-rationale-counter");
      dom.btnCancelNewLink = document.getElementById("rk-btn-cancel-new-link");
      dom.btnSubmitNewLink = document.getElementById("rk-btn-submit-new-link");

      dom.linksContainer = document.getElementById("rk-links-list");
      dom.btnLoadMore = document.getElementById("rk-btn-load-more");
    }

    function getCsrfToken() {
      if (adapter && typeof adapter.getCsrfToken === "function") {
        return adapter.getCsrfToken() || "";
      }
      const match = document.cookie.match(/(?:^|;\s*)lectoria_csrf=([^;]*)/);
      return match ? decodeURIComponent(match[1]) : "";
    }

    function showBanner(message, type) {
      if (!dom.statusBanner) return;
      if (!message) {
        dom.statusBanner.textContent = "";
        dom.statusBanner.className = "rk-status-banner hidden";
        dom.statusBanner.setAttribute("role", "status");
        return;
      }
      dom.statusBanner.textContent = String(message);
      dom.statusBanner.className = `rk-status-banner rk-banner-${type || "info"}`;
      dom.statusBanner.setAttribute("role", type === "error" ? "alert" : "status");
    }

    function notifyToast(message, isError) {
      if (adapter && typeof adapter.showToast === "function") {
        adapter.showToast(message, !!isError);
      }
    }

    async function apiRequest(path, options) {
      const opts = Object.assign({}, options || {}, { csrfToken: getCsrfToken() });
      return api.apiRequest(path, opts);
    }

    function notifyMutated() {
      if (typeof onMutatedCallback === "function") {
        try {
          onMutatedCallback(activeEntity);
        } catch (_) {}
      }
    }

    function abortPendingRequests() {
      if (connectionsAbortCtrl) {
        connectionsAbortCtrl.abort();
        connectionsAbortCtrl = null;
      }
      if (conceptLookupAbortCtrl) {
        conceptLookupAbortCtrl.abort();
        conceptLookupAbortCtrl = null;
      }
      if (targetLookupAbortCtrl) {
        targetLookupAbortCtrl.abort();
        targetLookupAbortCtrl = null;
      }
      if (conceptLookupTimer) {
        clearTimeout(conceptLookupTimer);
        conceptLookupTimer = null;
      }
      if (targetLookupTimer) {
        clearTimeout(targetLookupTimer);
        targetLookupTimer = null;
      }
    }

    function resetTransientState() {
      activeTab = "outgoing";
      outgoingLinks = [];
      incomingLinks = [];
      totalOutgoing = 0;
      totalIncoming = 0;
      hasMoreOutgoing = false;
      hasMoreIncoming = false;
      outgoingOffset = 0;
      incomingOffset = 0;
      selectedTarget = null;
      editingLinkId = null;
      confirmingDeleteLinkId = null;
      activeConceptPreviewId = null;
      submitLocks.clear();

      if (dom.conceptSearchInput) dom.conceptSearchInput.value = "";
      clearNode(dom.conceptSearchResults);
      if (dom.conceptSearchResults) dom.conceptSearchResults.classList.add("hidden");
      if (dom.createConceptForm) dom.createConceptForm.classList.add("hidden");
      if (dom.conceptNameInput) dom.conceptNameInput.value = "";
      if (dom.conceptDescInput) dom.conceptDescInput.value = "";
      if (dom.conceptTagsInput) dom.conceptTagsInput.value = "";
      if (dom.conceptColorInput) dom.conceptColorInput.value = DEFAULT_CONCEPT_COLOR;

      if (dom.newLinkForm) dom.newLinkForm.classList.add("hidden");
      if (dom.targetTypeSelect) dom.targetTypeSelect.value = "";
      if (dom.targetBookOnlyCheck) dom.targetBookOnlyCheck.checked = false;
      if (dom.targetSearchInput) dom.targetSearchInput.value = "";
      clearNode(dom.targetSearchResults);
      if (dom.targetSearchResults) dom.targetSearchResults.classList.add("hidden");
      if (dom.relationSelect) dom.relationSelect.value = "relates_to";
      if (dom.rationaleInput) dom.rationaleInput.value = "";
      updateRationaleCounter();
      renderSelectedTarget();
      renderConceptPreview(null);
      showBanner("", "info");
    }

    function renderEntityContext() {
      if (!dom.contextBox || !activeEntity) return;
      clearNode(dom.contextBox);

      const topRow = createEl("div", "rk-context-meta");
      const typeLabel = ENTITY_TYPE_LABELS[activeEntity.entityType] || "Anotação";
      topRow.appendChild(createEl("span", "rk-badge rk-badge-type", typeLabel));

      if (activeEntity.isOwn === false) {
        const authorTxt = activeEntity.authorName
          ? `Pública de ${activeEntity.authorName} (somente leitura)`
          : "Anotação pública (somente leitura)";
        topRow.appendChild(createEl("span", "rk-badge rk-badge-public", authorTxt));
      } else {
        topRow.appendChild(createEl("span", "rk-badge rk-badge-own", "Sua anotação"));
      }

      if (activeEntity.chapterLabel) {
        topRow.appendChild(createEl("span", "rk-context-loc", activeEntity.chapterLabel));
      } else if (activeEntity.page) {
        topRow.appendChild(createEl("span", "rk-context-loc", `Página ${activeEntity.page}`));
      }

      dom.contextBox.appendChild(topRow);

      const excerptText = truncateText(activeEntity.excerpt || "Anotação selecionada", 180);
      dom.contextBox.appendChild(createEl("p", "rk-context-excerpt", excerptText));

      if (dom.newLinkOriginLabel) {
        dom.newLinkOriginLabel.textContent = `${typeLabel} atual: "${truncateText(activeEntity.excerpt || typeLabel, 64)}"`;
      }
      updateDirectionPreview();
    }

    function getConceptLinks() {
      const seen = new Set();
      const result = [];
      outgoingLinks.forEach((link) => {
        if (link && link.target_type === "concept" && !seen.has(link.id)) {
          seen.add(link.id);
          result.push({ link, concept: link.target, direction: "outgoing" });
        }
      });
      incomingLinks.forEach((link) => {
        if (link && link.source_type === "concept" && !seen.has(link.id)) {
          seen.add(link.id);
          result.push({ link, concept: link.source, direction: "incoming" });
        }
      });
      return result;
    }

    function getAssociatedConceptIds() {
      const ids = new Set();
      getConceptLinks().forEach((item) => {
        if (item.concept && item.concept.id) {
          ids.add(item.concept.id);
        }
      });
      return ids;
    }

    function renderConceptPreview(concept) {
      if (!dom.conceptPreview) return;
      clearNode(dom.conceptPreview);
      if (!concept || concept.available !== true) {
        dom.conceptPreview.classList.add("hidden");
        return;
      }
      dom.conceptPreview.classList.remove("hidden");

      const header = createEl("div", "rk-concept-preview-header");
      const titleWrap = createEl("div", "rk-concept-preview-title");
      const dot = createEl("span", "rk-color-dot");
      dot.style.backgroundColor = sanitizeHexColor(concept.color);
      titleWrap.appendChild(dot);
      titleWrap.appendChild(createEl("strong", "", concept.label || concept.title || "Conceito"));
      header.appendChild(titleWrap);

      const closePreviewBtn = createEl("button", "rk-icon-btn", "×");
      closePreviewBtn.type = "button";
      closePreviewBtn.setAttribute("aria-label", "Fechar detalhes do conceito");
      closePreviewBtn.addEventListener("click", () => {
        activeConceptPreviewId = null;
        renderConceptPreview(null);
      });
      header.appendChild(closePreviewBtn);
      dom.conceptPreview.appendChild(header);

      if (concept.excerpt) {
        dom.conceptPreview.appendChild(createEl("p", "rk-concept-preview-desc", concept.excerpt));
      } else {
        dom.conceptPreview.appendChild(createEl("p", "rk-muted-text", "Sem descrição cadastrada no Atlas."));
      }

      if (Array.isArray(concept.tags) && concept.tags.length > 0) {
        const tagsRow = createEl("div", "rk-tags-row");
        concept.tags.forEach((tag) => {
          tagsRow.appendChild(createEl("span", "rk-tag-pill", `#${tag}`));
        });
        dom.conceptPreview.appendChild(tagsRow);
      }

      const atlasUrl = api.buildAtlasDeepLink && concept.id ? api.buildAtlasDeepLink("concept", concept.id) : null;
      if (atlasUrl) {
        const openAtlasBtn = createEl("button", "btn btn-sm btn-secondary rk-btn-open-atlas", "Abrir conceito no Atlas");
        openAtlasBtn.type = "button";
        openAtlasBtn.addEventListener("click", () => handleNavigateToAtlas(atlasUrl, openAtlasBtn));
        dom.conceptPreview.appendChild(openAtlasBtn);
      }
    }

    function renderAssociatedConcepts() {
      if (!dom.conceptsList) return;
      clearNode(dom.conceptsList);

      const conceptLinks = getConceptLinks();
      if (conceptLinks.length === 0) {
        dom.conceptsList.appendChild(
          createEl("span", "rk-muted-text", "Nenhum conceito associado a esta anotação ainda.")
        );
        activeConceptPreviewId = null;
        renderConceptPreview(null);
        return;
      }

      let previewStillPresent = null;

      conceptLinks.forEach(({ link, concept }) => {
        const isAvailable = concept && concept.available === true;
        const chip = createEl("div", isAvailable ? "rk-concept-chip" : "rk-concept-chip rk-chip-unavailable");
        chip.dataset.linkId = link.id;
        if (concept && concept.id) chip.dataset.conceptId = concept.id;

        const nameBtn = createEl("button", "rk-concept-chip-btn");
        nameBtn.type = "button";
        if (isAvailable) {
          const dot = createEl("span", "rk-color-dot");
          dot.style.backgroundColor = sanitizeHexColor(concept.color);
          nameBtn.appendChild(dot);
          nameBtn.appendChild(createEl("span", "rk-concept-chip-label", concept.label || concept.title || "Conceito"));
          nameBtn.setAttribute("title", "Ver resumo do conceito");
          nameBtn.addEventListener("click", () => {
            if (activeConceptPreviewId === concept.id) {
              activeConceptPreviewId = null;
              renderConceptPreview(null);
            } else {
              activeConceptPreviewId = concept.id;
              renderConceptPreview(concept);
            }
          });
          if (activeConceptPreviewId && activeConceptPreviewId === concept.id) {
            previewStillPresent = concept;
          }
        } else {
          nameBtn.disabled = true;
          nameBtn.appendChild(createEl("span", "rk-concept-chip-label", "Referência indisponível"));
        }

        const unlinkBtn = createEl("button", "rk-concept-unlink-btn", "×");
        unlinkBtn.type = "button";
        unlinkBtn.setAttribute(
          "aria-label",
          isAvailable
            ? `Remover associação com o conceito ${concept.label || ""}`
            : "Remover associação indisponível"
        );
        unlinkBtn.setAttribute("title", "Remover associação desta anotação (mantém o conceito no Atlas)");
        unlinkBtn.addEventListener("click", () => handleUnlinkConcept(link.id, unlinkBtn));

        chip.appendChild(nameBtn);
        chip.appendChild(unlinkBtn);
        dom.conceptsList.appendChild(chip);
      });

      if (previewStillPresent) {
        renderConceptPreview(previewStillPresent);
      } else {
        activeConceptPreviewId = null;
        renderConceptPreview(null);
      }
    }

    function updateTabLabels() {
      if (dom.tabOutgoing) {
        dom.tabOutgoing.textContent = `Conexões de saída (${totalOutgoing})`;
        dom.tabOutgoing.classList.toggle("active", activeTab === "outgoing");
        dom.tabOutgoing.setAttribute("aria-selected", activeTab === "outgoing" ? "true" : "false");
      }
      if (dom.tabIncoming) {
        dom.tabIncoming.textContent = `Backlinks recebidos (${totalIncoming})`;
        dom.tabIncoming.classList.toggle("active", activeTab === "incoming");
        dom.tabIncoming.setAttribute("aria-selected", activeTab === "incoming" ? "true" : "false");
      }
    }

    function renderLinksList() {
      if (!dom.linksContainer) return;
      clearNode(dom.linksContainer);
      updateTabLabels();

      const items = activeTab === "outgoing" ? outgoingLinks : incomingLinks;
      const hasMore = activeTab === "outgoing" ? hasMoreOutgoing : hasMoreIncoming;

      if (items.length === 0) {
        const emptyMsg = activeTab === "outgoing"
          ? "Nenhuma conexão de saída registrada para esta anotação."
          : "Nenhum backlink aponta para esta anotação até o momento.";
        dom.linksContainer.appendChild(createEl("div", "rk-empty-state", emptyMsg));
      } else {
        items.forEach((link) => {
          dom.linksContainer.appendChild(buildLinkCard(link, activeTab));
        });
      }

      if (dom.btnLoadMore) {
        dom.btnLoadMore.classList.toggle("hidden", !hasMore);
      }
    }

    function buildLinkCard(link, direction) {
      const card = createEl("article", "rk-link-card");
      card.dataset.linkId = link.id;

      const counterpart = direction === "outgoing"
        ? (link.target || link.counterpart)
        : (link.source || link.counterpart);
      const isAvailable = Boolean(counterpart && counterpart.available === true);

      const header = createEl("div", "rk-link-card-header");
      const badges = createEl("div", "rk-badges-row");

      const relDef = RELATION_MAP[link.relation_type] || RELATION_MAP.relates_to;
      const relLabel = direction === "outgoing"
        ? (link.outgoing_label || relDef.outgoing)
        : (link.incoming_label || relDef.incoming);
      badges.appendChild(createEl("span", "rk-badge rk-badge-relation", relLabel));

      if (isAvailable) {
        const typeLabel = ENTITY_TYPE_LABELS[counterpart.type] || counterpart.type;
        badges.appendChild(createEl("span", "rk-badge rk-badge-type", typeLabel));
      } else {
        badges.appendChild(createEl("span", "rk-badge rk-badge-unavailable", "Indisponível"));
      }

      header.appendChild(badges);

      // Actions for own link
      const actions = createEl("div", "rk-link-card-actions");

      if (isAvailable) {
        const navSpec = buildSafeNavigationSpec(
          counterpart,
          adapter && typeof adapter.getBookId === "function" ? adapter.getBookId() : ""
        );
        if (navSpec) {
          const openBtn = createEl("button", "btn btn-sm btn-secondary rk-btn-navigate", "Abrir");
          openBtn.type = "button";
          openBtn.setAttribute("title", navSpec.sameBook ? "Ir para este trecho no livro atual" : "Abrir obra relacionada");
          openBtn.addEventListener("click", () => handleNavigateToTarget(navSpec, openBtn));
          actions.appendChild(openBtn);
        } else if (
          api.buildAtlasDeepLink &&
          (counterpart.type === "concept" || counterpart.type === "reflection") &&
          counterpart.id
        ) {
          const atlasUrl = api.buildAtlasDeepLink(counterpart.type, counterpart.id);
          if (atlasUrl) {
            const atlasBtn = createEl("button", "btn btn-sm btn-secondary rk-btn-open-atlas", "Abrir no Atlas");
            atlasBtn.type = "button";
            atlasBtn.addEventListener("click", () => handleNavigateToAtlas(atlasUrl, atlasBtn));
            actions.appendChild(atlasBtn);
          }
        }
      }

      const editBtn = createEl("button", "btn btn-sm btn-ghost rk-btn-edit-link", "Editar");
      editBtn.type = "button";
      editBtn.addEventListener("click", () => {
        editingLinkId = editingLinkId === link.id ? null : link.id;
        confirmingDeleteLinkId = null;
        renderLinksList();
      });
      actions.appendChild(editBtn);

      if (confirmingDeleteLinkId === link.id) {
        const confirmBtn = createEl("button", "btn btn-sm btn-danger rk-btn-confirm-delete", "Confirmar");
        confirmBtn.type = "button";
        confirmBtn.addEventListener("click", () => handleDeleteLink(link.id, confirmBtn));
        actions.appendChild(confirmBtn);

        const cancelDelBtn = createEl("button", "btn btn-sm btn-ghost", "Cancelar");
        cancelDelBtn.type = "button";
        cancelDelBtn.addEventListener("click", () => {
          confirmingDeleteLinkId = null;
          renderLinksList();
        });
        actions.appendChild(cancelDelBtn);
      } else {
        const deleteBtn = createEl("button", "btn btn-sm btn-ghost rk-btn-delete-link", "Excluir");
        deleteBtn.type = "button";
        deleteBtn.addEventListener("click", () => {
          confirmingDeleteLinkId = link.id;
          editingLinkId = null;
          renderLinksList();
        });
        actions.appendChild(deleteBtn);
      }

      header.appendChild(actions);
      card.appendChild(header);

      // Counterpart body
      const body = createEl("div", "rk-link-card-body");
      if (isAvailable) {
        const titleText = counterpart.label || counterpart.title || "Item conectado";
        body.appendChild(createEl("div", "rk-link-target-title", titleText));

        const locMeta = formatLocationMeta(counterpart);
        if (locMeta) {
          body.appendChild(createEl("div", "rk-link-target-meta", locMeta));
        }

        if ((counterpart.type === "concept" || counterpart.type === "reflection") && counterpart.excerpt) {
          body.appendChild(createEl("p", "rk-link-target-excerpt", truncateText(counterpart.excerpt, 180)));
        }
        if (counterpart.type === "concept" || counterpart.type === "reflection") {
          body.appendChild(createEl("div", "rk-link-atlas-hint", "Entidade do seu Atlas"));
        }
      } else {
        card.classList.add("rk-link-card-unavailable");
        body.appendChild(createEl("div", "rk-link-unavailable-title", "Referência indisponível"));
        body.appendChild(
          createEl(
            "div",
            "rk-link-target-meta",
            "O item referenciado foi removido, tornado privado ou não está acessível."
          )
        );
      }

      if (link.rationale && String(link.rationale).trim()) {
        const ratBox = createEl("div", "rk-link-rationale");
        ratBox.appendChild(createEl("span", "rk-link-rationale-label", "Justificativa: "));
        ratBox.appendChild(createEl("span", "rk-link-rationale-text", link.rationale));
        body.appendChild(ratBox);
      }

      card.appendChild(body);

      // Inline edit form (relation_type + rationale)
      if (editingLinkId === link.id) {
        const editForm = createEl("form", "rk-inline-edit-form");
        editForm.addEventListener("submit", (e) => {
          e.preventDefault();
          handleUpdateLink(link.id, relSelect.value, ratTextarea.value, saveBtn);
        });

        const relLabelEl = createEl("label", "rk-field-label", "Tipo de relação");
        const relSelect = createEl("select", "rk-select rk-edit-relation-select");
        RELATION_TYPES.forEach((r) => {
          const opt = document.createElement("option");
          opt.value = r.value;
          opt.textContent = r.outgoing;
          if (r.value === link.relation_type) opt.selected = true;
          relSelect.appendChild(opt);
        });
        relLabelEl.appendChild(relSelect);
        editForm.appendChild(relLabelEl);

        const ratLabelEl = createEl("label", "rk-field-label", "Justificativa (opcional)");
        const ratTextarea = createEl("textarea", "rk-textarea rk-edit-rationale-input");
        ratTextarea.rows = 2;
        ratTextarea.maxLength = 2000;
        ratTextarea.value = link.rationale || "";
        ratLabelEl.appendChild(ratTextarea);
        editForm.appendChild(ratLabelEl);

        const editBtns = createEl("div", "rk-inline-form-actions");
        const saveBtn = createEl("button", "btn btn-sm btn-primary rk-btn-save-edit", "Salvar");
        saveBtn.type = "submit";
        const cancelEditBtn = createEl("button", "btn btn-sm btn-ghost", "Cancelar");
        cancelEditBtn.type = "button";
        cancelEditBtn.addEventListener("click", () => {
          editingLinkId = null;
          renderLinksList();
        });
        editBtns.appendChild(cancelEditBtn);
        editBtns.appendChild(saveBtn);
        editForm.appendChild(editBtns);

        card.appendChild(editForm);
      }

      return card;
    }

    async function loadEntityConnections(append) {
      if (!activeEntity) return;

      if (connectionsAbortCtrl) {
        connectionsAbortCtrl.abort();
      }
      connectionsAbortCtrl = new AbortController();
      const mySeq = ++connectionsSeq;

      const limit = PAGE_LIMIT;
      const offset = append
        ? (activeTab === "outgoing" ? outgoingOffset : incomingOffset)
        : 0;
      const direction = append ? activeTab : "both";

      if (!append) {
        showBanner("Carregando conexões do Atlas…", "info");
      }

      const url = `/api/knowledge/entities/${encodeURIComponent(activeEntity.entityType)}/${encodeURIComponent(activeEntity.entityId)}/connections?direction=${encodeURIComponent(direction)}&limit=${limit}&offset=${offset}`;

      try {
        const data = await apiRequest(url, { signal: connectionsAbortCtrl.signal });
        if (mySeq !== connectionsSeq || !isOpen) return;

        if (!append) {
          outgoingLinks = Array.isArray(data.outgoing_links) ? data.outgoing_links : [];
          incomingLinks = Array.isArray(data.incoming_backlinks) ? data.incoming_backlinks : [];
          totalOutgoing = Number(data.total_outgoing) || outgoingLinks.length;
          totalIncoming = Number(data.total_incoming) || incomingLinks.length;
          hasMoreOutgoing = Boolean(data.has_more_outgoing);
          hasMoreIncoming = Boolean(data.has_more_incoming);
          outgoingOffset = outgoingLinks.length;
          incomingOffset = incomingLinks.length;
        } else if (activeTab === "outgoing") {
          const next = Array.isArray(data.outgoing_links) ? data.outgoing_links : [];
          outgoingLinks = outgoingLinks.concat(next);
          totalOutgoing = Number(data.total_outgoing) || outgoingLinks.length;
          hasMoreOutgoing = Boolean(data.has_more_outgoing);
          outgoingOffset = outgoingLinks.length;
        } else {
          const next = Array.isArray(data.incoming_backlinks) ? data.incoming_backlinks : [];
          incomingLinks = incomingLinks.concat(next);
          totalIncoming = Number(data.total_incoming) || incomingLinks.length;
          hasMoreIncoming = Boolean(data.has_more_incoming);
          incomingOffset = incomingLinks.length;
        }

        showBanner("", "info");
        renderAssociatedConcepts();
        renderLinksList();
      } catch (err) {
        if (err && err.name === "AbortError") return;
        if (mySeq !== connectionsSeq || !isOpen) return;
        showBanner(err.message || "Erro ao carregar conexões do Atlas.", "error");
        renderAssociatedConcepts();
        renderLinksList();
      }
    }

    async function handleConceptSearch(queryText) {
      if (conceptLookupAbortCtrl) {
        conceptLookupAbortCtrl.abort();
      }
      conceptLookupAbortCtrl = new AbortController();
      const mySeq = ++conceptLookupSeq;

      const q = String(queryText || "").trim();
      const url = `/api/knowledge/lookup?entity_type=concept&q=${encodeURIComponent(q)}&limit=12`;

      try {
        const data = await apiRequest(url, { signal: conceptLookupAbortCtrl.signal });
        if (mySeq !== conceptLookupSeq || !isOpen) return;

        const items = Array.isArray(data && data.items) ? data.items : [];
        renderConceptSearchResults(items, q);
      } catch (err) {
        if (err && err.name === "AbortError") return;
        if (mySeq !== conceptLookupSeq || !isOpen) return;
        showBanner(err.message || "Não foi possível buscar conceitos.", "error");
      }
    }

    function renderConceptSearchResults(items, queryText) {
      if (!dom.conceptSearchResults) return;
      clearNode(dom.conceptSearchResults);
      dom.conceptSearchResults.classList.remove("hidden");

      const associatedIds = getAssociatedConceptIds();

      if (items.length === 0) {
        const emptyRow = createEl("div", "rk-lookup-empty");
        emptyRow.appendChild(
          createEl(
            "span",
            "rk-muted-text",
            queryText
              ? `Nenhum conceito encontrado para "${queryText}".`
              : "Você ainda não possui conceitos cadastrados."
          )
        );
        dom.conceptSearchResults.appendChild(emptyRow);
        return;
      }

      items.forEach((item) => {
        const alreadyLinked = associatedIds.has(item.id);
        const row = createEl("button", "rk-lookup-item");
        row.type = "button";
        row.dataset.conceptId = item.id;
        row.disabled = alreadyLinked;

        const left = createEl("div", "rk-lookup-item-main");
        const dot = createEl("span", "rk-color-dot");
        dot.style.backgroundColor = sanitizeHexColor(item.color);
        left.appendChild(dot);

        const textWrap = createEl("div", "rk-lookup-item-text");
        textWrap.appendChild(createEl("span", "rk-lookup-item-title", item.label || item.title || "Conceito"));
        if (item.subtitle) {
          textWrap.appendChild(createEl("span", "rk-lookup-item-sub", item.subtitle));
        }
        left.appendChild(textWrap);
        row.appendChild(left);

        row.appendChild(
          createEl("span", alreadyLinked ? "rk-lookup-tag-linked" : "rk-lookup-tag-action", alreadyLinked ? "Associado" : "Associar")
        );

        if (!alreadyLinked) {
          row.addEventListener("click", () => handleAssociateConcept(item, row));
        }

        dom.conceptSearchResults.appendChild(row);
      });
    }

    async function handleAssociateConcept(conceptItem, triggerBtn) {
      if (!activeEntity || !conceptItem || !conceptItem.id) return;
      const lockKey = `assoc:${conceptItem.id}`;
      if (submitLocks.has(lockKey)) return;
      submitLocks.add(lockKey);
      if (triggerBtn) triggerBtn.disabled = true;

      try {
        await apiRequest("/api/knowledge/links", {
          method: "POST",
          body: {
            source_type: activeEntity.entityType,
            source_id: activeEntity.entityId,
            target_type: "concept",
            target_id: conceptItem.id,
            relation_type: "relates_to",
            rationale: "",
          },
        });

        if (dom.conceptSearchInput) dom.conceptSearchInput.value = "";
        if (dom.conceptSearchResults) {
          clearNode(dom.conceptSearchResults);
          dom.conceptSearchResults.classList.add("hidden");
        }
        showBanner(`Conceito "${conceptItem.label || conceptItem.title}" associado à anotação.`, "success");
        notifyToast("Conceito associado", false);
        notifyMutated();
        await loadEntityConnections(false);
      } catch (err) {
        showBanner(err.message || "Não foi possível associar o conceito.", "error");
        if (triggerBtn) triggerBtn.disabled = false;
      } finally {
        submitLocks.delete(lockKey);
      }
    }

    async function handleCreateAndAssociateConcept(e) {
      e.preventDefault();
      if (!activeEntity) return;
      if (submitLocks.has("create-concept")) return;

      const name = dom.conceptNameInput ? dom.conceptNameInput.value.trim() : "";
      if (!name) {
        showBanner("Informe o nome do conceito antes de criar.", "error");
        if (dom.conceptNameInput) dom.conceptNameInput.focus();
        return;
      }

      const description = dom.conceptDescInput ? dom.conceptDescInput.value.trim() : "";
      const rawTags = dom.conceptTagsInput ? dom.conceptTagsInput.value : "";
      const tags = rawTags
        .split(",")
        .map((t) => t.trim())
        .filter((t) => t.length > 0)
        .slice(0, 30);
      const color = sanitizeHexColor(dom.conceptColorInput ? dom.conceptColorInput.value : DEFAULT_CONCEPT_COLOR);

      submitLocks.add("create-concept");
      if (dom.btnSubmitCreateConcept) dom.btnSubmitCreateConcept.disabled = true;

      let createdConcept = null;
      try {
        // Step 1: Create concept in Atlas
        const createdResp = await apiRequest("/api/knowledge/concepts", {
          method: "POST",
          body: {
            name,
            description,
            tags,
            color,
          },
        });
        createdConcept = createdResp && createdResp.concept ? createdResp.concept : null;
      } catch (err) {
        if (err && err.status === 409) {
          showBanner(
            `Já existe um conceito com o nome "${name}" no seu Atlas. Busque-o acima para associá-lo.`,
            "error"
          );
          if (dom.conceptSearchInput) {
            dom.conceptSearchInput.value = name;
            handleConceptSearch(name);
          }
        } else {
          showBanner(err.message || "Não foi possível criar o conceito.", "error");
        }
        submitLocks.delete("create-concept");
        if (dom.btnSubmitCreateConcept) dom.btnSubmitCreateConcept.disabled = false;
        return;
      }

      if (!createdConcept || !createdConcept.id) {
        showBanner("Resposta inesperada ao criar o conceito.", "error");
        submitLocks.delete("create-concept");
        if (dom.btnSubmitCreateConcept) dom.btnSubmitCreateConcept.disabled = false;
        return;
      }

      // Step 2: Associate created concept with current annotation
      try {
        await apiRequest("/api/knowledge/links", {
          method: "POST",
          body: {
            source_type: activeEntity.entityType,
            source_id: activeEntity.entityId,
            target_type: "concept",
            target_id: createdConcept.id,
            relation_type: "relates_to",
            rationale: "",
          },
        });

        if (dom.conceptNameInput) dom.conceptNameInput.value = "";
        if (dom.conceptDescInput) dom.conceptDescInput.value = "";
        if (dom.conceptTagsInput) dom.conceptTagsInput.value = "";
        if (dom.createConceptForm) dom.createConceptForm.classList.add("hidden");
        if (dom.conceptSearchInput) dom.conceptSearchInput.value = "";
        if (dom.conceptSearchResults) {
          clearNode(dom.conceptSearchResults);
          dom.conceptSearchResults.classList.add("hidden");
        }

        showBanner(`Conceito "${createdConcept.name}" criado e associado com sucesso.`, "success");
        notifyToast("Conceito criado e associado", false);
        notifyMutated();
        await loadEntityConnections(false);
      } catch (err) {
        // Explicit distinction between step 1 success and step 2 failure
        showBanner(
          `Conceito "${createdConcept.name}" criado no Atlas, mas não foi possível vinculá-lo a esta anotação: ${err.message}`,
          "error"
        );
      } finally {
        submitLocks.delete("create-concept");
        if (dom.btnSubmitCreateConcept) dom.btnSubmitCreateConcept.disabled = false;
      }
    }

    async function handleUnlinkConcept(linkId, triggerBtn) {
      if (!linkId) return;
      const lockKey = `delete:${linkId}`;
      if (submitLocks.has(lockKey)) return;
      submitLocks.add(lockKey);
      if (triggerBtn) triggerBtn.disabled = true;

      try {
        await apiRequest(`/api/knowledge/links/${encodeURIComponent(linkId)}`, {
          method: "DELETE",
        });
        showBanner("Associação removida desta anotação. O conceito permanece no Atlas.", "success");
        notifyMutated();
        await loadEntityConnections(false);
      } catch (err) {
        showBanner(err.message || "Não foi possível remover a associação.", "error");
        if (triggerBtn) triggerBtn.disabled = false;
      } finally {
        submitLocks.delete(lockKey);
      }
    }

    async function handleTargetSearch(queryText) {
      if (targetLookupAbortCtrl) {
        targetLookupAbortCtrl.abort();
      }
      targetLookupAbortCtrl = new AbortController();
      const mySeq = ++targetLookupSeq;

      const q = String(queryText || "").trim();
      const entityType = dom.targetTypeSelect ? dom.targetTypeSelect.value : "";
      const bookOnly = Boolean(dom.targetBookOnlyCheck && dom.targetBookOnlyCheck.checked);
      const currentBookId = adapter && typeof adapter.getBookId === "function" ? adapter.getBookId() : "";

      const params = new URLSearchParams();
      params.set("q", q);
      params.set("limit", "15");
      if (entityType) params.set("entity_type", entityType);
      if (bookOnly && currentBookId) params.set("book_id", currentBookId);

      try {
        const data = await apiRequest(`/api/knowledge/lookup?${params.toString()}`, {
          signal: targetLookupAbortCtrl.signal,
        });
        if (mySeq !== targetLookupSeq || !isOpen) return;

        const items = Array.isArray(data && data.items) ? data.items : [];
        const filtered = items.filter(
          (it) => !(activeEntity && it.type === activeEntity.entityType && String(it.id) === String(activeEntity.entityId))
        );
        renderTargetSearchResults(filtered, q);
      } catch (err) {
        if (err && err.name === "AbortError") return;
        if (mySeq !== targetLookupSeq || !isOpen) return;
        showBanner(err.message || "Não foi possível localizar destinos para conexão.", "error");
      }
    }

    function renderTargetSearchResults(items, queryText) {
      if (!dom.targetSearchResults) return;
      clearNode(dom.targetSearchResults);
      dom.targetSearchResults.classList.remove("hidden");

      if (items.length === 0) {
        dom.targetSearchResults.appendChild(
          createEl(
            "div",
            "rk-lookup-empty",
            queryText ? `Nenhum item encontrado para "${queryText}".` : "Nenhum item disponível com os filtros atuais."
          )
        );
        return;
      }

      items.forEach((item) => {
        const btn = createEl("button", "rk-lookup-item");
        btn.type = "button";
        btn.dataset.entityType = item.type;
        btn.dataset.entityId = item.id;

        const main = createEl("div", "rk-lookup-item-main");
        const typeBadge = createEl("span", "rk-badge rk-badge-type", ENTITY_TYPE_LABELS[item.type] || item.type);
        main.appendChild(typeBadge);

        const textWrap = createEl("div", "rk-lookup-item-text");
        textWrap.appendChild(createEl("span", "rk-lookup-item-title", truncateText(item.label || item.title || "Item", 90)));

        const metaParts = [];
        if (item.book_title) metaParts.push(item.book_title);
        if (item.chapter_label) metaParts.push(item.chapter_label);
        else if (Number.isInteger(Number(item.page)) && Number(item.page) >= 1) metaParts.push(`Pág. ${item.page}`);
        if (item.subtitle && !item.book_title) metaParts.push(item.subtitle);

        if (metaParts.length > 0) {
          textWrap.appendChild(createEl("span", "rk-lookup-item-sub", metaParts.join(" · ")));
        }

        main.appendChild(textWrap);
        btn.appendChild(main);

        btn.addEventListener("click", () => {
          selectedTarget = item;
          renderSelectedTarget();
          clearNode(dom.targetSearchResults);
          dom.targetSearchResults.classList.add("hidden");
        });

        dom.targetSearchResults.appendChild(btn);
      });
    }

    function renderSelectedTarget() {
      if (!dom.selectedTargetBox) return;
      clearNode(dom.selectedTargetBox);

      if (!selectedTarget) {
        dom.selectedTargetBox.classList.add("hidden");
        updateDirectionPreview();
        return;
      }

      dom.selectedTargetBox.classList.remove("hidden");
      const pill = createEl("div", "rk-selected-target-pill");
      pill.appendChild(
        createEl("span", "rk-badge rk-badge-type", ENTITY_TYPE_LABELS[selectedTarget.type] || selectedTarget.type)
      );
      pill.appendChild(
        createEl("span", "rk-selected-target-label", truncateText(selectedTarget.label || selectedTarget.title || "Destino", 90))
      );

      const clearBtn = createEl("button", "rk-icon-btn", "×");
      clearBtn.type = "button";
      clearBtn.setAttribute("aria-label", "Remover destino selecionado");
      clearBtn.addEventListener("click", () => {
        selectedTarget = null;
        renderSelectedTarget();
      });
      pill.appendChild(clearBtn);

      dom.selectedTargetBox.appendChild(pill);
      updateDirectionPreview();
    }

    function updateDirectionPreview() {
      if (!dom.directionPreview) return;
      const relVal = dom.relationSelect ? dom.relationSelect.value : "relates_to";
      const relDef = RELATION_MAP[relVal] || RELATION_MAP.relates_to;
      const originType = activeEntity ? (ENTITY_TYPE_LABELS[activeEntity.entityType] || "Anotação") : "Origem";
      const targetName = selectedTarget
        ? `${ENTITY_TYPE_LABELS[selectedTarget.type] || selectedTarget.type}: ${truncateText(selectedTarget.label || selectedTarget.title || "", 40)}`
        : "Selecione um destino";

      dom.directionPreview.textContent = `${originType} atual — ${relDef.outgoing} → ${targetName}`;
    }

    function updateRationaleCounter() {
      if (!dom.rationaleCounter || !dom.rationaleInput) return;
      dom.rationaleCounter.textContent = `${dom.rationaleInput.value.length}/2000`;
    }

    async function handleCreateLink(e) {
      e.preventDefault();
      if (!activeEntity) return;
      if (submitLocks.has("create-link")) return;

      if (!selectedTarget || !selectedTarget.id || !selectedTarget.type) {
        showBanner("Selecione um destino antes de criar a conexão.", "error");
        if (dom.targetSearchInput) dom.targetSearchInput.focus();
        return;
      }

      const relationType = dom.relationSelect ? dom.relationSelect.value : "relates_to";
      const rationale = dom.rationaleInput ? dom.rationaleInput.value.trim() : "";

      submitLocks.add("create-link");
      if (dom.btnSubmitNewLink) dom.btnSubmitNewLink.disabled = true;

      try {
        await apiRequest("/api/knowledge/links", {
          method: "POST",
          body: {
            source_type: activeEntity.entityType,
            source_id: activeEntity.entityId,
            target_type: selectedTarget.type,
            target_id: selectedTarget.id,
            relation_type: relationType,
            rationale,
          },
        });

        selectedTarget = null;
        renderSelectedTarget();
        if (dom.targetSearchInput) dom.targetSearchInput.value = "";
        if (dom.rationaleInput) dom.rationaleInput.value = "";
        updateRationaleCounter();
        if (dom.newLinkForm) dom.newLinkForm.classList.add("hidden");

        activeTab = "outgoing";
        showBanner("Conexão semântica criada com sucesso.", "success");
        notifyToast("Conexão criada no Atlas", false);
        notifyMutated();
        await loadEntityConnections(false);
      } catch (err) {
        showBanner(err.message || "Não foi possível criar a conexão.", "error");
      } finally {
        submitLocks.delete("create-link");
        if (dom.btnSubmitNewLink) dom.btnSubmitNewLink.disabled = false;
      }
    }

    async function handleUpdateLink(linkId, relationType, rationale, triggerBtn) {
      if (!linkId) return;
      const lockKey = `update:${linkId}`;
      if (submitLocks.has(lockKey)) return;
      submitLocks.add(lockKey);
      if (triggerBtn) triggerBtn.disabled = true;

      try {
        await apiRequest(`/api/knowledge/links/${encodeURIComponent(linkId)}`, {
          method: "PATCH",
          body: {
            relation_type: relationType,
            rationale: String(rationale || "").trim(),
          },
        });
        editingLinkId = null;
        showBanner("Conexão atualizada com sucesso.", "success");
        notifyMutated();
        await loadEntityConnections(false);
      } catch (err) {
        showBanner(err.message || "Não foi possível atualizar a conexão.", "error");
        if (triggerBtn) triggerBtn.disabled = false;
      } finally {
        submitLocks.delete(lockKey);
      }
    }

    async function handleDeleteLink(linkId, triggerBtn) {
      if (!linkId) return;
      const lockKey = `delete:${linkId}`;
      if (submitLocks.has(lockKey)) return;
      submitLocks.add(lockKey);
      if (triggerBtn) triggerBtn.disabled = true;

      try {
        await apiRequest(`/api/knowledge/links/${encodeURIComponent(linkId)}`, {
          method: "DELETE",
        });
        confirmingDeleteLinkId = null;
        showBanner("Conexão removida.", "success");
        notifyMutated();
        await loadEntityConnections(false);
      } catch (err) {
        showBanner(err.message || "Não foi possível excluir a conexão.", "error");
        if (triggerBtn) triggerBtn.disabled = false;
      } finally {
        submitLocks.delete(lockKey);
      }
    }

    async function handleNavigateToTarget(navSpec, triggerBtn) {
      if (!navSpec) return;
      if (submitLocks.has("navigate")) return;
      submitLocks.add("navigate");
      if (triggerBtn) triggerBtn.disabled = true;

      try {
        // Persist current reading progress before jumping
        if (adapter && typeof adapter.persistProgress === "function") {
          try {
            await adapter.persistProgress();
          } catch (_) {
            // Do not block navigation if progress save fails
          }
        }

        if (navSpec.sameBook) {
          if (navSpec.epubCfi && adapter && typeof adapter.navigateToEpubCfi === "function") {
            await adapter.navigateToEpubCfi(navSpec.epubCfi, navSpec.chapterLabel || "");
          } else if (navSpec.page && adapter && typeof adapter.goToPage === "function") {
            await adapter.goToPage(navSpec.page);
          } else {
            notifyToast("Você já está nesta obra.", false);
          }
          close();
          return;
        }

        // Cross-book navigation using validated relative /reader/{book_id}#... URL
        close();
        global.location.assign(navSpec.url);
      } finally {
        submitLocks.delete("navigate");
        if (triggerBtn) triggerBtn.disabled = false;
      }
    }

    async function handleNavigateToAtlas(atlasUrl, triggerBtn) {
      if (!atlasUrl || submitLocks.has("navigate")) return;
      submitLocks.add("navigate");
      if (triggerBtn) triggerBtn.disabled = true;
      try {
        if (adapter && typeof adapter.persistProgress === "function") {
          try {
            await adapter.persistProgress();
          } catch (_) {}
        }
        close();
        global.location.assign(atlasUrl);
      } finally {
        submitLocks.delete("navigate");
        if (triggerBtn) triggerBtn.disabled = false;
      }
    }

    function bindEvents() {
      if (!dom.modal) return;

      if (dom.closeBtn) {
        dom.closeBtn.addEventListener("click", () => close());
      }

      // Close on backdrop click
      dom.modal.addEventListener("click", (e) => {
        if (e.target === dom.modal) {
          close();
        }
      });

      // Concept search with debounce
      if (dom.conceptSearchInput) {
        dom.conceptSearchInput.addEventListener("input", () => {
          if (conceptLookupTimer) clearTimeout(conceptLookupTimer);
          if (conceptLookupAbortCtrl) {
            conceptLookupAbortCtrl.abort();
            conceptLookupAbortCtrl = null;
          }
          if (dom.conceptSearchResults) {
            clearNode(dom.conceptSearchResults);
            dom.conceptSearchResults.classList.remove("hidden");
            dom.conceptSearchResults.appendChild(createEl("div", "rk-lookup-empty", "Buscando conceitos…"));
          }
          conceptLookupTimer = setTimeout(() => {
            handleConceptSearch(dom.conceptSearchInput.value);
          }, LOOKUP_DEBOUNCE_MS);
        });
        dom.conceptSearchInput.addEventListener("focus", () => {
          handleConceptSearch(dom.conceptSearchInput.value);
        });
      }

      if (dom.btnToggleCreateConcept) {
        dom.btnToggleCreateConcept.addEventListener("click", () => {
          if (!dom.createConceptForm) return;
          const willShow = dom.createConceptForm.classList.contains("hidden");
          dom.createConceptForm.classList.toggle("hidden", !willShow);
          if (willShow && dom.conceptNameInput) {
            if (!dom.conceptNameInput.value.trim() && dom.conceptSearchInput && dom.conceptSearchInput.value.trim()) {
              dom.conceptNameInput.value = dom.conceptSearchInput.value.trim();
            }
            dom.conceptNameInput.focus();
          }
        });
      }

      if (dom.btnCancelCreateConcept) {
        dom.btnCancelCreateConcept.addEventListener("click", () => {
          if (dom.createConceptForm) dom.createConceptForm.classList.add("hidden");
        });
      }

      if (dom.createConceptForm) {
        dom.createConceptForm.addEventListener("submit", handleCreateAndAssociateConcept);
      }

      // Tabs: outgoing vs incoming backlinks
      if (dom.tabOutgoing) {
        dom.tabOutgoing.addEventListener("click", () => {
          if (activeTab === "outgoing") return;
          activeTab = "outgoing";
          editingLinkId = null;
          confirmingDeleteLinkId = null;
          renderLinksList();
        });
      }

      if (dom.tabIncoming) {
        dom.tabIncoming.addEventListener("click", () => {
          if (activeTab === "incoming") return;
          activeTab = "incoming";
          editingLinkId = null;
          confirmingDeleteLinkId = null;
          renderLinksList();
        });
      }

      // New connection form toggle & assisted target lookup
      if (dom.btnToggleNewLink) {
        dom.btnToggleNewLink.addEventListener("click", () => {
          if (!dom.newLinkForm) return;
          const willShow = dom.newLinkForm.classList.contains("hidden");
          dom.newLinkForm.classList.toggle("hidden", !willShow);
          if (willShow) {
            updateDirectionPreview();
            if (dom.targetSearchInput) {
              dom.targetSearchInput.focus();
              handleTargetSearch(dom.targetSearchInput.value);
            }
          }
        });
      }

      if (dom.btnCancelNewLink) {
        dom.btnCancelNewLink.addEventListener("click", () => {
          if (dom.newLinkForm) dom.newLinkForm.classList.add("hidden");
        });
      }

      if (dom.targetSearchInput) {
        dom.targetSearchInput.addEventListener("input", () => {
          if (targetLookupTimer) clearTimeout(targetLookupTimer);
          if (targetLookupAbortCtrl) {
            targetLookupAbortCtrl.abort();
            targetLookupAbortCtrl = null;
          }
          if (dom.targetSearchResults) {
            clearNode(dom.targetSearchResults);
            dom.targetSearchResults.classList.remove("hidden");
            dom.targetSearchResults.appendChild(createEl("div", "rk-lookup-empty", "Buscando destinos…"));
          }
          targetLookupTimer = setTimeout(() => {
            handleTargetSearch(dom.targetSearchInput.value);
          }, LOOKUP_DEBOUNCE_MS);
        });
        dom.targetSearchInput.addEventListener("focus", () => {
          handleTargetSearch(dom.targetSearchInput.value);
        });
      }

      if (dom.targetTypeSelect) {
        dom.targetTypeSelect.addEventListener("change", () => {
          handleTargetSearch(dom.targetSearchInput ? dom.targetSearchInput.value : "");
        });
      }

      if (dom.targetBookOnlyCheck) {
        dom.targetBookOnlyCheck.addEventListener("change", () => {
          handleTargetSearch(dom.targetSearchInput ? dom.targetSearchInput.value : "");
        });
      }

      if (dom.relationSelect) {
        dom.relationSelect.addEventListener("change", updateDirectionPreview);
      }

      if (dom.rationaleInput) {
        dom.rationaleInput.addEventListener("input", updateRationaleCounter);
      }

      if (dom.newLinkForm) {
        dom.newLinkForm.addEventListener("submit", handleCreateLink);
      }

      if (dom.btnLoadMore) {
        dom.btnLoadMore.addEventListener("click", () => {
          loadEntityConnections(true);
        });
      }

      // Populate relation <select> options once
      if (dom.relationSelect && dom.relationSelect.options.length === 0) {
        RELATION_TYPES.forEach((r) => {
          const opt = document.createElement("option");
          opt.value = r.value;
          opt.textContent = r.outgoing;
          dom.relationSelect.appendChild(opt);
        });
        dom.relationSelect.value = "relates_to";
      }
    }

    function init(readerAdapter) {
      adapter = readerAdapter || {};
      if (isInitialized) return;
      cacheDom();
      bindEvents();
      isInitialized = true;
    }

    function openForAnnotation(entity, triggerEl, opts) {
      if (!isInitialized) {
        init(adapter || {});
      }
      if (!dom.modal || !entity || !entity.entityId || !entity.entityType) {
        return;
      }

      abortPendingRequests();
      resetTransientState();

      onMutatedCallback = opts && typeof opts.onMutated === "function" ? opts.onMutated : null;

      activeEntity = {
        entityType: entity.entityType === "note" ? "note" : "highlight",
        entityId: String(entity.entityId),
        excerpt: entity.excerpt || "",
        isOwn: entity.isOwn !== false,
        authorName: entity.authorName || "",
        bookId: entity.bookId || (adapter && adapter.getBookId ? adapter.getBookId() : ""),
        page: entity.page || null,
        epubCfi: entity.epubCfi || null,
        chapterLabel: entity.chapterLabel || "",
      };
      lastTriggerEl = triggerEl || document.activeElement || null;

      renderEntityContext();
      renderAssociatedConcepts();
      renderLinksList();

      isOpen = true;
      dom.modal.classList.remove("hidden");
      dom.modal.setAttribute("aria-hidden", "false");

      // Move focus inside dialog for keyboard accessibility
      setTimeout(() => {
        if (isOpen && dom.conceptSearchInput) {
          dom.conceptSearchInput.focus();
        } else if (isOpen && dom.closeBtn) {
          dom.closeBtn.focus();
        }
      }, 0);

      loadEntityConnections(false);
    }

    function close() {
      if (!isOpen) return;
      isOpen = false;
      abortPendingRequests();
      if (dom.modal) {
        dom.modal.classList.add("hidden");
        dom.modal.setAttribute("aria-hidden", "true");
      }
      const toFocus = lastTriggerEl;
      lastTriggerEl = null;
      activeEntity = null;
      onMutatedCallback = null;
      resetTransientState();

      if (toFocus && typeof toFocus.focus === "function" && document.contains(toFocus)) {
        try {
          toFocus.focus();
        } catch (_) {
          // Ignore focus errors if trigger is no longer focusable
        }
      }
    }

    function isModalOpen() {
      return isOpen;
    }

    function cleanup() {
      abortPendingRequests();
      if (isOpen) {
        close();
      }
    }

    return {
      init,
      openForAnnotation,
      close,
      isModalOpen,
      cleanup,
      RELATION_TYPES,
      buildSafeNavigationSpec,
    };
  }

  global.LectoriaReaderKnowledge = createReaderKnowledgeController();
})(window);
