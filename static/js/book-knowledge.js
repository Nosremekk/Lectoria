/**
 * Lectoria v1.9.0 (Fase 4) — Integração contextual mínima do Atlas
 * no modal de Detalhes da Obra (#modal-book-details -> subaba Conhecimento).
 *
 * Reutiliza integralmente window.LectoriaKnowledgeApi.
 * Carregamento estritamente sob demanda ao abrir a subaba Conhecimento.
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
  const PAGE_LIMIT = 20;

  function createBookKnowledgeController() {
    let rootEl = null;
    let activeBook = null; // { id, title }
    let loadedForBookId = null;

    let activeTab = "outgoing";
    let bookSummary = null;
    let outgoingLinks = [];
    let incomingLinks = [];
    let totalOutgoing = 0;
    let totalIncoming = 0;
    let hasMoreOutgoing = false;
    let hasMoreIncoming = false;
    let outgoingOffset = 0;
    let incomingOffset = 0;

    let selectedTarget = null;
    let editingLinkId = null;
    let confirmingDeleteLinkId = null;
    let activeConceptPreviewId = null;

    let loadAbortCtrl = null;
    let loadSeq = 0;
    let conceptLookupAbortCtrl = null;
    let conceptLookupSeq = 0;
    let conceptLookupTimer = null;
    let targetLookupAbortCtrl = null;
    let targetLookupSeq = 0;
    let targetLookupTimer = null;
    const submitLocks = new Set();

    const dom = {};

    function abortAll() {
      if (loadAbortCtrl) {
        loadAbortCtrl.abort();
        loadAbortCtrl = null;
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

    function showBanner(message, type) {
      if (!dom.banner) return;
      if (!message) {
        dom.banner.textContent = "";
        dom.banner.className = "rk-status-banner hidden";
        dom.banner.setAttribute("role", "status");
        return;
      }
      dom.banner.textContent = String(message);
      dom.banner.className = `rk-status-banner rk-banner-${type || "info"}`;
      dom.banner.setAttribute("role", type === "error" ? "alert" : "status");
    }

    function getConceptLinks() {
      const seen = new Set();
      const result = [];
      outgoingLinks.forEach((link) => {
        if (link && link.target_type === "concept" && !seen.has(link.id)) {
          seen.add(link.id);
          result.push({ link, concept: link.target });
        }
      });
      incomingLinks.forEach((link) => {
        if (link && link.source_type === "concept" && !seen.has(link.id)) {
          seen.add(link.id);
          result.push({ link, concept: link.source });
        }
      });
      return result;
    }

    function getAssociatedConceptIds() {
      const ids = new Set();
      getConceptLinks().forEach((item) => {
        if (item.concept && item.concept.id) ids.add(item.concept.id);
      });
      return ids;
    }

    function buildShell(container) {
      clearNode(container);

      // Summary bar + shortcut to Study Notebook
      const summaryRow = createEl("div", "bk-summary-bar");
      dom.summaryStats = createEl("div", "bk-summary-stats");
      summaryRow.appendChild(dom.summaryStats);

      dom.notebookLink = createEl("a", "btn btn-sm btn-secondary bk-open-notebook-link", "Abrir Caderno de Estudos");
      dom.notebookLink.href = "#";
      summaryRow.appendChild(dom.notebookLink);
      container.appendChild(summaryRow);

      dom.banner = createEl("div", "rk-status-banner hidden");
      dom.banner.setAttribute("role", "status");
      dom.banner.setAttribute("aria-live", "polite");
      container.appendChild(dom.banner);

      // Section 1: Concepts associated with the book
      const secConcepts = createEl("section", "rk-section");
      const secCHead = createEl("div", "rk-section-header");
      secCHead.appendChild(createEl("h4", "rk-section-title", "Conceitos pessoais desta obra"));
      dom.btnToggleCreateConcept = createEl("button", "btn btn-sm btn-secondary", "+ Criar conceito");
      dom.btnToggleCreateConcept.type = "button";
      dom.btnToggleCreateConcept.id = "bk-btn-toggle-create-concept";
      secCHead.appendChild(dom.btnToggleCreateConcept);
      secConcepts.appendChild(secCHead);

      dom.conceptsList = createEl("div", "rk-concepts-chips");
      dom.conceptsList.id = "bk-associated-concepts";
      secConcepts.appendChild(dom.conceptsList);

      dom.conceptPreview = createEl("div", "rk-concept-preview hidden");
      dom.conceptPreview.id = "bk-concept-preview";
      secConcepts.appendChild(dom.conceptPreview);

      const lookupWrap = createEl("div", "rk-lookup-wrap");
      dom.conceptSearchInput = createEl("input", "rk-input");
      dom.conceptSearchInput.id = "bk-concept-search";
      dom.conceptSearchInput.type = "search";
      dom.conceptSearchInput.placeholder = "Buscar conceito do Atlas para associar a esta obra...";
      dom.conceptSearchInput.autocomplete = "off";
      dom.conceptSearchInput.maxLength = 160;
      lookupWrap.appendChild(dom.conceptSearchInput);

      dom.conceptSearchResults = createEl("div", "rk-lookup-results hidden");
      dom.conceptSearchResults.id = "bk-concept-search-results";
      dom.conceptSearchResults.setAttribute("role", "listbox");
      lookupWrap.appendChild(dom.conceptSearchResults);
      secConcepts.appendChild(lookupWrap);

      // Inline create concept form
      dom.createConceptForm = createEl("form", "rk-subform hidden");
      dom.createConceptForm.id = "bk-create-concept-form";
      dom.createConceptForm.appendChild(createEl("div", "rk-subform-title", "Novo conceito no Atlas"));

      const lblCName = createEl("label", "rk-field-label", "Nome do conceito *");
      dom.conceptNameInput = createEl("input", "rk-input");
      dom.conceptNameInput.id = "bk-concept-name";
      dom.conceptNameInput.type = "text";
      dom.conceptNameInput.required = true;
      dom.conceptNameInput.maxLength = 160;
      lblCName.appendChild(dom.conceptNameInput);
      dom.createConceptForm.appendChild(lblCName);

      const lblCDesc = createEl("label", "rk-field-label", "Descrição (opcional)");
      dom.conceptDescInput = createEl("textarea", "rk-textarea");
      dom.conceptDescInput.id = "bk-concept-desc";
      dom.conceptDescInput.rows = 2;
      dom.conceptDescInput.maxLength = 10000;
      lblCDesc.appendChild(dom.conceptDescInput);
      dom.createConceptForm.appendChild(lblCDesc);

      const cActions = createEl("div", "rk-inline-form-actions");
      const btnCancelC = createEl("button", "btn btn-sm btn-ghost", "Cancelar");
      btnCancelC.type = "button";
      btnCancelC.addEventListener("click", () => dom.createConceptForm.classList.add("hidden"));
      dom.btnSubmitCreateConcept = createEl("button", "btn btn-sm btn-primary", "Criar e associar");
      dom.btnSubmitCreateConcept.id = "bk-btn-submit-create-concept";
      dom.btnSubmitCreateConcept.type = "submit";
      cActions.appendChild(btnCancelC);
      cActions.appendChild(dom.btnSubmitCreateConcept);
      dom.createConceptForm.appendChild(cActions);
      secConcepts.appendChild(dom.createConceptForm);
      container.appendChild(secConcepts);

      // Section 2: Book connections & backlinks
      const secLinks = createEl("section", "rk-section");
      const secLHead = createEl("div", "rk-section-header");
      secLHead.appendChild(createEl("h4", "rk-section-title", "Conexões e Backlinks da obra"));
      dom.btnToggleNewLink = createEl("button", "btn btn-sm btn-secondary", "+ Nova conexão");
      dom.btnToggleNewLink.type = "button";
      dom.btnToggleNewLink.id = "bk-btn-toggle-new-link";
      secLHead.appendChild(dom.btnToggleNewLink);
      secLinks.appendChild(secLHead);

      // Inline new link form
      dom.newLinkForm = createEl("form", "rk-subform hidden");
      dom.newLinkForm.id = "bk-new-link-form";
      dom.newLinkForm.appendChild(createEl("div", "rk-subform-title", "Conectar esta obra a outra entidade"));

      const lblTType = createEl("label", "rk-field-label", "Tipo de destino");
      dom.targetTypeSelect = createEl("select", "rk-select");
      dom.targetTypeSelect.id = "bk-target-type";
      [
        ["", "Todos os tipos"],
        ["book", "Livro"],
        ["concept", "Conceito"],
        ["reflection", "Reflexão"],
        ["highlight", "Destaque"],
        ["note", "Nota"],
      ].forEach(([val, label]) => {
        const opt = document.createElement("option");
        opt.value = val;
        opt.textContent = label;
        dom.targetTypeSelect.appendChild(opt);
      });
      lblTType.appendChild(dom.targetTypeSelect);
      dom.newLinkForm.appendChild(lblTType);

      const tLookupWrap = createEl("div", "rk-lookup-wrap");
      const lblTSearch = createEl("label", "rk-field-label", "Buscar destino *");
      dom.targetSearchInput = createEl("input", "rk-input");
      dom.targetSearchInput.id = "bk-target-search";
      dom.targetSearchInput.type = "search";
      dom.targetSearchInput.placeholder = "Digite para localizar livro, conceito, destaque, nota ou reflexão...";
      dom.targetSearchInput.autocomplete = "off";
      dom.targetSearchInput.maxLength = 300;
      lblTSearch.appendChild(dom.targetSearchInput);
      tLookupWrap.appendChild(lblTSearch);

      dom.targetSearchResults = createEl("div", "rk-lookup-results hidden");
      dom.targetSearchResults.id = "bk-target-search-results";
      dom.targetSearchResults.setAttribute("role", "listbox");
      tLookupWrap.appendChild(dom.targetSearchResults);
      dom.newLinkForm.appendChild(tLookupWrap);

      dom.selectedTargetBox = createEl("div", "rk-selected-target hidden");
      dom.selectedTargetBox.id = "bk-selected-target";
      dom.newLinkForm.appendChild(dom.selectedTargetBox);

      const lblRel = createEl("label", "rk-field-label", "Tipo de relação *");
      dom.relationSelect = createEl("select", "rk-select");
      dom.relationSelect.id = "bk-relation-select";
      RELATION_TYPES.forEach((r) => {
        const opt = document.createElement("option");
        opt.value = r.value;
        opt.textContent = r.outgoing;
        dom.relationSelect.appendChild(opt);
      });
      dom.relationSelect.value = "relates_to";
      lblRel.appendChild(dom.relationSelect);
      dom.newLinkForm.appendChild(lblRel);

      dom.directionPreview = createEl("div", "rk-direction-preview");
      dom.newLinkForm.appendChild(dom.directionPreview);

      const lblRat = createEl("label", "rk-field-label", "Justificativa (opcional)");
      dom.rationaleInput = createEl("textarea", "rk-textarea");
      dom.rationaleInput.id = "bk-rationale-input";
      dom.rationaleInput.rows = 2;
      dom.rationaleInput.maxLength = 2000;
      lblRat.appendChild(dom.rationaleInput);
      dom.newLinkForm.appendChild(lblRat);

      const lActions = createEl("div", "rk-inline-form-actions");
      const btnCancelL = createEl("button", "btn btn-sm btn-ghost", "Cancelar");
      btnCancelL.type = "button";
      btnCancelL.addEventListener("click", () => dom.newLinkForm.classList.add("hidden"));
      dom.btnSubmitNewLink = createEl("button", "btn btn-sm btn-primary", "Criar conexão");
      dom.btnSubmitNewLink.id = "bk-btn-submit-new-link";
      dom.btnSubmitNewLink.type = "submit";
      lActions.appendChild(btnCancelL);
      lActions.appendChild(dom.btnSubmitNewLink);
      dom.newLinkForm.appendChild(lActions);
      secLinks.appendChild(dom.newLinkForm);

      const tabsRow = createEl("div", "rk-tabs");
      tabsRow.setAttribute("role", "tablist");
      dom.tabOutgoing = createEl("button", "rk-tab-btn active", "Conexões de saída (0)");
      dom.tabOutgoing.id = "bk-tab-outgoing";
      dom.tabOutgoing.type = "button";
      dom.tabOutgoing.setAttribute("role", "tab");
      dom.tabOutgoing.setAttribute("aria-selected", "true");
      dom.tabIncoming = createEl("button", "rk-tab-btn", "Backlinks recebidos (0)");
      dom.tabIncoming.id = "bk-tab-incoming";
      dom.tabIncoming.type = "button";
      dom.tabIncoming.setAttribute("role", "tab");
      dom.tabIncoming.setAttribute("aria-selected", "false");
      tabsRow.appendChild(dom.tabOutgoing);
      tabsRow.appendChild(dom.tabIncoming);
      secLinks.appendChild(tabsRow);

      dom.linksContainer = createEl("div", "rk-links-list");
      dom.linksContainer.id = "bk-links-list";
      secLinks.appendChild(dom.linksContainer);

      const loadMoreWrap = createEl("div", "rk-load-more-wrap");
      dom.btnLoadMore = createEl("button", "btn btn-sm btn-ghost hidden", "Carregar mais");
      dom.btnLoadMore.id = "bk-btn-load-more";
      dom.btnLoadMore.type = "button";
      loadMoreWrap.appendChild(dom.btnLoadMore);
      secLinks.appendChild(loadMoreWrap);

      container.appendChild(secLinks);
      bindEvents();
    }

    function renderSummary() {
      if (!dom.summaryStats) return;
      clearNode(dom.summaryStats);
      const s = bookSummary || {
        distinct_concepts_count: 0,
        personal_links_count: 0,
        connected_annotations_count: 0,
      };

      const pills = [
        `Conceitos: ${s.distinct_concepts_count}`,
        `Conexões: ${s.personal_links_count}`,
        `Anotações conectadas: ${s.connected_annotations_count}`,
      ];
      pills.forEach((txt) => {
        dom.summaryStats.appendChild(createEl("span", "bk-stat-pill", txt));
      });

      if (dom.notebookLink && activeBook && activeBook.id) {
        dom.notebookLink.href = `/reader/${encodeURIComponent(activeBook.id)}?drawer=1`;
      }
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
      const closeBtn = createEl("button", "rk-icon-btn", "×");
      closeBtn.type = "button";
      closeBtn.setAttribute("aria-label", "Fechar detalhes do conceito");
      closeBtn.addEventListener("click", () => {
        activeConceptPreviewId = null;
        renderConceptPreview(null);
      });
      header.appendChild(closeBtn);
      dom.conceptPreview.appendChild(header);
      dom.conceptPreview.appendChild(
        createEl("p", "rk-concept-preview-desc", concept.excerpt || "Sem descrição cadastrada no Atlas.")
      );
    }

    function renderConcepts() {
      if (!dom.conceptsList) return;
      clearNode(dom.conceptsList);
      const conceptLinks = getConceptLinks();
      if (conceptLinks.length === 0) {
        dom.conceptsList.appendChild(
          createEl("span", "rk-muted-text", "Nenhum conceito associado diretamente a esta obra ainda.")
        );
        renderConceptPreview(null);
        return;
      }
      conceptLinks.forEach(({ link, concept }) => {
        const isAvail = Boolean(concept && concept.available === true);
        const chip = createEl("div", isAvail ? "rk-concept-chip" : "rk-concept-chip rk-chip-unavailable");
        const nameBtn = createEl("button", "rk-concept-chip-btn");
        nameBtn.type = "button";
        if (isAvail) {
          const dot = createEl("span", "rk-color-dot");
          dot.style.backgroundColor = sanitizeHexColor(concept.color);
          nameBtn.appendChild(dot);
          nameBtn.appendChild(createEl("span", "rk-concept-chip-label", concept.label || concept.title || "Conceito"));
          nameBtn.addEventListener("click", () => {
            if (activeConceptPreviewId === concept.id) {
              activeConceptPreviewId = null;
              renderConceptPreview(null);
            } else {
              activeConceptPreviewId = concept.id;
              renderConceptPreview(concept);
            }
          });
        } else {
          nameBtn.disabled = true;
          nameBtn.appendChild(createEl("span", "rk-concept-chip-label", "Referência indisponível"));
        }
        const unlinkBtn = createEl("button", "rk-concept-unlink-btn", "×");
        unlinkBtn.type = "button";
        unlinkBtn.setAttribute("aria-label", "Remover associação deste conceito da obra");
        unlinkBtn.addEventListener("click", () => handleDeleteLink(link.id, unlinkBtn, true));
        chip.appendChild(nameBtn);
        chip.appendChild(unlinkBtn);
        dom.conceptsList.appendChild(chip);
      });
    }

    function renderLinks() {
      if (!dom.linksContainer) return;
      clearNode(dom.linksContainer);

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

      const items = activeTab === "outgoing" ? outgoingLinks : incomingLinks;
      const hasMore = activeTab === "outgoing" ? hasMoreOutgoing : hasMoreIncoming;

      if (items.length === 0) {
        dom.linksContainer.appendChild(
          createEl(
            "div",
            "rk-empty-state",
            activeTab === "outgoing"
              ? "Nenhuma conexão de saída registrada diretamente para esta obra."
              : "Nenhum backlink aponta diretamente para esta obra até o momento."
          )
        );
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
      const isAvail = Boolean(counterpart && counterpart.available === true);

      const header = createEl("div", "rk-link-card-header");
      const badges = createEl("div", "rk-badges-row");
      const relDef = RELATION_MAP[link.relation_type] || RELATION_MAP.relates_to;
      const relLabel = direction === "outgoing"
        ? (link.outgoing_label || relDef.outgoing)
        : (link.incoming_label || relDef.incoming);
      badges.appendChild(createEl("span", "rk-badge rk-badge-relation", relLabel));
      badges.appendChild(
        createEl(
          "span",
          isAvail ? "rk-badge rk-badge-type" : "rk-badge rk-badge-unavailable",
          isAvail ? (ENTITY_TYPE_LABELS[counterpart.type] || counterpart.type) : "Indisponível"
        )
      );
      header.appendChild(badges);

      const actions = createEl("div", "rk-link-card-actions");
      if (isAvail) {
        const navSpec = buildSafeNavigationSpec(counterpart, "");
        if (navSpec) {
          const openLink = createEl("a", "btn btn-sm btn-secondary rk-btn-navigate", "Abrir");
          openLink.href = navSpec.url;
          actions.appendChild(openLink);
        } else if (
          api.buildAtlasDeepLink &&
          (counterpart.type === "concept" || counterpart.type === "reflection") &&
          counterpart.id
        ) {
          const atlasUrl = api.buildAtlasDeepLink(counterpart.type, counterpart.id);
          if (atlasUrl) {
            const atlasBtn = createEl("button", "btn btn-sm btn-secondary rk-btn-open-atlas", "Abrir no Atlas");
            atlasBtn.type = "button";
            atlasBtn.addEventListener("click", () => {
              const modal = document.getElementById("modal-book-details");
              if (modal) modal.classList.remove("active");
              cleanup();
              if (global.LectoriaAtlas && typeof global.LectoriaAtlas.openEntity === "function") {
                global.LectoriaAtlas.openEntity(counterpart.type, counterpart.id);
              } else {
                global.location.assign(atlasUrl);
              }
            });
            actions.appendChild(atlasBtn);
          }
        }
      }

      const editBtn = createEl("button", "btn btn-sm btn-ghost rk-btn-edit-link", "Editar");
      editBtn.type = "button";
      editBtn.addEventListener("click", () => {
        editingLinkId = editingLinkId === link.id ? null : link.id;
        confirmingDeleteLinkId = null;
        renderLinks();
      });
      actions.appendChild(editBtn);

      if (confirmingDeleteLinkId === link.id) {
        const confirmBtn = createEl("button", "btn btn-sm btn-danger rk-btn-confirm-delete", "Confirmar");
        confirmBtn.type = "button";
        confirmBtn.addEventListener("click", () => handleDeleteLink(link.id, confirmBtn, false));
        actions.appendChild(confirmBtn);
        const cancelBtn = createEl("button", "btn btn-sm btn-ghost", "Cancelar");
        cancelBtn.type = "button";
        cancelBtn.addEventListener("click", () => {
          confirmingDeleteLinkId = null;
          renderLinks();
        });
        actions.appendChild(cancelBtn);
      } else {
        const delBtn = createEl("button", "btn btn-sm btn-ghost rk-btn-delete-link", "Excluir");
        delBtn.type = "button";
        delBtn.addEventListener("click", () => {
          confirmingDeleteLinkId = link.id;
          editingLinkId = null;
          renderLinks();
        });
        actions.appendChild(delBtn);
      }

      header.appendChild(actions);
      card.appendChild(header);

      const body = createEl("div", "rk-link-card-body");
      if (isAvail) {
        body.appendChild(createEl("div", "rk-link-target-title", counterpart.label || counterpart.title || "Item"));
        const meta = formatLocationMeta(counterpart);
        if (meta) body.appendChild(createEl("div", "rk-link-target-meta", meta));
      } else {
        card.classList.add("rk-link-card-unavailable");
        body.appendChild(createEl("div", "rk-link-unavailable-title", "Referência indisponível"));
        body.appendChild(
          createEl("div", "rk-link-target-meta", "O item referenciado foi removido, tornado privado ou não está acessível.")
        );
      }

      if (link.rationale && String(link.rationale).trim()) {
        const rat = createEl("div", "rk-link-rationale");
        rat.appendChild(createEl("span", "rk-link-rationale-label", "Justificativa: "));
        rat.appendChild(createEl("span", "rk-link-rationale-text", link.rationale));
        body.appendChild(rat);
      }
      card.appendChild(body);

      if (editingLinkId === link.id) {
        const editForm = createEl("form", "rk-inline-edit-form");
        editForm.addEventListener("submit", (e) => {
          e.preventDefault();
          handleUpdateLink(link.id, relSel.value, ratTa.value, saveBtn);
        });
        const relSel = createEl("select", "rk-select rk-edit-relation-select");
        RELATION_TYPES.forEach((r) => {
          const opt = document.createElement("option");
          opt.value = r.value;
          opt.textContent = r.outgoing;
          if (r.value === link.relation_type) opt.selected = true;
          relSel.appendChild(opt);
        });
        const ratTa = createEl("textarea", "rk-textarea rk-edit-rationale-input");
        ratTa.rows = 2;
        ratTa.maxLength = 2000;
        ratTa.value = link.rationale || "";
        const btns = createEl("div", "rk-inline-form-actions");
        const saveBtn = createEl("button", "btn btn-sm btn-primary rk-btn-save-edit", "Salvar");
        saveBtn.type = "submit";
        const cancelEdit = createEl("button", "btn btn-sm btn-ghost", "Cancelar");
        cancelEdit.type = "button";
        cancelEdit.addEventListener("click", () => {
          editingLinkId = null;
          renderLinks();
        });
        btns.appendChild(cancelEdit);
        btns.appendChild(saveBtn);
        editForm.appendChild(relSel);
        editForm.appendChild(ratTa);
        editForm.appendChild(btns);
        card.appendChild(editForm);
      }

      return card;
    }

    function updateDirectionPreview() {
      if (!dom.directionPreview) return;
      const relVal = dom.relationSelect ? dom.relationSelect.value : "relates_to";
      const relDef = RELATION_MAP[relVal] || RELATION_MAP.relates_to;
      const targetTxt = selectedTarget
        ? `${ENTITY_TYPE_LABELS[selectedTarget.type] || selectedTarget.type}: ${truncateText(selectedTarget.label || selectedTarget.title || "", 40)}`
        : "Selecione um destino";
      dom.directionPreview.textContent = `Obra atual — ${relDef.outgoing} → ${targetTxt}`;
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
      pill.appendChild(createEl("span", "rk-badge rk-badge-type", ENTITY_TYPE_LABELS[selectedTarget.type] || selectedTarget.type));
      pill.appendChild(createEl("span", "rk-selected-target-label", truncateText(selectedTarget.label || selectedTarget.title || "", 80)));
      const clr = createEl("button", "rk-icon-btn", "×");
      clr.type = "button";
      clr.setAttribute("aria-label", "Remover destino selecionado");
      clr.addEventListener("click", () => {
        selectedTarget = null;
        renderSelectedTarget();
      });
      pill.appendChild(clr);
      dom.selectedTargetBox.appendChild(pill);
      updateDirectionPreview();
    }

    async function loadBookData(append) {
      if (!activeBook || !activeBook.id) return;
      if (loadAbortCtrl) loadAbortCtrl.abort();
      loadAbortCtrl = new AbortController();
      const mySeq = ++loadSeq;
      const signal = loadAbortCtrl.signal;

      const offset = append ? (activeTab === "outgoing" ? outgoingOffset : incomingOffset) : 0;
      const direction = append ? activeTab : "both";
      if (!append) showBanner("Carregando conhecimento conectado da obra…", "info");

      try {
        const reqs = [
          api.getEntityConnections("book", activeBook.id, { direction, limit: PAGE_LIMIT, offset }, signal),
        ];
        if (!append) {
          reqs.push(api.getBookSummary(activeBook.id, signal));
        }
        const [connData, sumData] = await Promise.all(reqs);
        if (mySeq !== loadSeq) return;

        if (!append) {
          bookSummary = sumData || null;
          outgoingLinks = Array.isArray(connData.outgoing_links) ? connData.outgoing_links : [];
          incomingLinks = Array.isArray(connData.incoming_backlinks) ? connData.incoming_backlinks : [];
          totalOutgoing = Number(connData.total_outgoing) || outgoingLinks.length;
          totalIncoming = Number(connData.total_incoming) || incomingLinks.length;
          hasMoreOutgoing = Boolean(connData.has_more_outgoing);
          hasMoreIncoming = Boolean(connData.has_more_incoming);
          outgoingOffset = outgoingLinks.length;
          incomingOffset = incomingLinks.length;
          loadedForBookId = activeBook.id;
        } else if (activeTab === "outgoing") {
          const next = Array.isArray(connData.outgoing_links) ? connData.outgoing_links : [];
          outgoingLinks = outgoingLinks.concat(next);
          totalOutgoing = Number(connData.total_outgoing) || outgoingLinks.length;
          hasMoreOutgoing = Boolean(connData.has_more_outgoing);
          outgoingOffset = outgoingLinks.length;
        } else {
          const next = Array.isArray(connData.incoming_backlinks) ? connData.incoming_backlinks : [];
          incomingLinks = incomingLinks.concat(next);
          totalIncoming = Number(connData.total_incoming) || incomingLinks.length;
          hasMoreIncoming = Boolean(connData.has_more_incoming);
          incomingOffset = incomingLinks.length;
        }

        showBanner("", "info");
        renderSummary();
        renderConcepts();
        renderLinks();
      } catch (err) {
        if (err && err.name === "AbortError") return;
        if (mySeq !== loadSeq) return;
        showBanner(err.message || "Não foi possível carregar o conhecimento desta obra.", "error");
        renderSummary();
        renderConcepts();
        renderLinks();
      }
    }

    async function handleConceptSearch(q) {
      if (conceptLookupAbortCtrl) conceptLookupAbortCtrl.abort();
      conceptLookupAbortCtrl = new AbortController();
      const mySeq = ++conceptLookupSeq;
      try {
        const data = await api.lookupEntities({ entity_type: "concept", q, limit: 10 }, conceptLookupAbortCtrl.signal);
        if (mySeq !== conceptLookupSeq) return;
        const items = Array.isArray(data && data.items) ? data.items : [];
        clearNode(dom.conceptSearchResults);
        dom.conceptSearchResults.classList.remove("hidden");
        const associated = getAssociatedConceptIds();
        if (items.length === 0) {
          dom.conceptSearchResults.appendChild(createEl("div", "rk-lookup-empty", "Nenhum conceito encontrado."));
          return;
        }
        items.forEach((item) => {
          const linked = associated.has(item.id);
          const row = createEl("button", "rk-lookup-item");
          row.type = "button";
          row.disabled = linked;
          const left = createEl("div", "rk-lookup-item-main");
          const dot = createEl("span", "rk-color-dot");
          dot.style.backgroundColor = sanitizeHexColor(item.color);
          left.appendChild(dot);
          left.appendChild(createEl("span", "rk-lookup-item-title", item.label || item.title || "Conceito"));
          row.appendChild(left);
          row.appendChild(createEl("span", linked ? "rk-lookup-tag-linked" : "rk-lookup-tag-action", linked ? "Associado" : "Associar"));
          if (!linked) {
            row.addEventListener("click", () => handleAssociateConcept(item, row));
          }
          dom.conceptSearchResults.appendChild(row);
        });
      } catch (err) {
        if (err && err.name === "AbortError") return;
        showBanner(err.message || "Erro ao buscar conceitos.", "error");
      }
    }

    async function handleAssociateConcept(item, btn) {
      if (!activeBook || submitLocks.has(`assoc:${item.id}`)) return;
      submitLocks.add(`assoc:${item.id}`);
      if (btn) btn.disabled = true;
      try {
        await api.createLink({
          source_type: "book",
          source_id: activeBook.id,
          target_type: "concept",
          target_id: item.id,
          relation_type: "relates_to",
          rationale: "",
        });
        dom.conceptSearchInput.value = "";
        clearNode(dom.conceptSearchResults);
        dom.conceptSearchResults.classList.add("hidden");
        showBanner(`Conceito "${item.label || item.title}" associado à obra.`, "success");
        await loadBookData(false);
      } catch (err) {
        showBanner(err.message || "Erro ao associar conceito.", "error");
        if (btn) btn.disabled = false;
      } finally {
        submitLocks.delete(`assoc:${item.id}`);
      }
    }

    async function handleCreateConcept(e) {
      e.preventDefault();
      if (!activeBook || submitLocks.has("create-concept")) return;
      const name = dom.conceptNameInput.value.trim();
      if (!name) return;
      submitLocks.add("create-concept");
      dom.btnSubmitCreateConcept.disabled = true;
      let created = null;
      try {
        const resp = await api.createConcept({
          name,
          description: dom.conceptDescInput.value.trim(),
          tags: [],
          color: DEFAULT_CONCEPT_COLOR,
        });
        created = resp && resp.concept;
      } catch (err) {
        showBanner(err.message || "Erro ao criar conceito.", "error");
        submitLocks.delete("create-concept");
        dom.btnSubmitCreateConcept.disabled = false;
        return;
      }
      try {
        await api.createLink({
          source_type: "book",
          source_id: activeBook.id,
          target_type: "concept",
          target_id: created.id,
          relation_type: "relates_to",
          rationale: "",
        });
        dom.conceptNameInput.value = "";
        dom.conceptDescInput.value = "";
        dom.createConceptForm.classList.add("hidden");
        showBanner(`Conceito "${created.name}" criado e associado à obra.`, "success");
        await loadBookData(false);
      } catch (err) {
        showBanner(`Conceito "${created.name}" criado no Atlas, mas não foi possível vinculá-lo: ${err.message}`, "error");
      } finally {
        submitLocks.delete("create-concept");
        dom.btnSubmitCreateConcept.disabled = false;
      }
    }

    async function handleTargetSearch(q) {
      if (targetLookupAbortCtrl) targetLookupAbortCtrl.abort();
      targetLookupAbortCtrl = new AbortController();
      const mySeq = ++targetLookupSeq;
      try {
        const data = await api.lookupEntities(
          { q, entity_type: dom.targetTypeSelect.value || undefined, limit: 12 },
          targetLookupAbortCtrl.signal
        );
        if (mySeq !== targetLookupSeq) return;
        const items = (Array.isArray(data && data.items) ? data.items : []).filter(
          (it) => !(activeBook && it.type === "book" && String(it.id) === String(activeBook.id))
        );
        clearNode(dom.targetSearchResults);
        dom.targetSearchResults.classList.remove("hidden");
        if (items.length === 0) {
          dom.targetSearchResults.appendChild(createEl("div", "rk-lookup-empty", "Nenhum destino encontrado."));
          return;
        }
        items.forEach((item) => {
          const btn = createEl("button", "rk-lookup-item");
          btn.type = "button";
          const main = createEl("div", "rk-lookup-item-main");
          main.appendChild(createEl("span", "rk-badge rk-badge-type", ENTITY_TYPE_LABELS[item.type] || item.type));
          main.appendChild(createEl("span", "rk-lookup-item-title", truncateText(item.label || item.title || "Item", 80)));
          btn.appendChild(main);
          btn.addEventListener("click", () => {
            selectedTarget = item;
            renderSelectedTarget();
            clearNode(dom.targetSearchResults);
            dom.targetSearchResults.classList.add("hidden");
          });
          dom.targetSearchResults.appendChild(btn);
        });
      } catch (err) {
        if (err && err.name === "AbortError") return;
        showBanner(err.message || "Erro ao localizar destinos.", "error");
      }
    }

    async function handleCreateLink(e) {
      e.preventDefault();
      if (!activeBook || submitLocks.has("create-link")) return;
      if (!selectedTarget) {
        showBanner("Selecione um destino antes de criar a conexão.", "error");
        return;
      }
      submitLocks.add("create-link");
      dom.btnSubmitNewLink.disabled = true;
      try {
        await api.createLink({
          source_type: "book",
          source_id: activeBook.id,
          target_type: selectedTarget.type,
          target_id: selectedTarget.id,
          relation_type: dom.relationSelect.value || "relates_to",
          rationale: dom.rationaleInput.value.trim(),
        });
        selectedTarget = null;
        renderSelectedTarget();
        dom.targetSearchInput.value = "";
        dom.rationaleInput.value = "";
        dom.newLinkForm.classList.add("hidden");
        activeTab = "outgoing";
        showBanner("Conexão criada para esta obra.", "success");
        await loadBookData(false);
      } catch (err) {
        showBanner(err.message || "Erro ao criar conexão.", "error");
      } finally {
        submitLocks.delete("create-link");
        dom.btnSubmitNewLink.disabled = false;
      }
    }

    async function handleUpdateLink(linkId, relationType, rationale, btn) {
      const lockKey = `upd:${linkId}`;
      if (submitLocks.has(lockKey)) return;
      submitLocks.add(lockKey);
      if (btn) btn.disabled = true;
      try {
        await api.updateLink(linkId, { relation_type: relationType, rationale: String(rationale || "").trim() });
        editingLinkId = null;
        showBanner("Conexão atualizada.", "success");
        await loadBookData(false);
      } catch (err) {
        showBanner(err.message || "Erro ao atualizar conexão.", "error");
        if (btn) btn.disabled = false;
      } finally {
        submitLocks.delete(lockKey);
      }
    }

    async function handleDeleteLink(linkId, btn, isConceptUnlink) {
      const lockKey = `del:${linkId}`;
      if (submitLocks.has(lockKey)) return;
      submitLocks.add(lockKey);
      if (btn) btn.disabled = true;
      try {
        await api.deleteLink(linkId);
        confirmingDeleteLinkId = null;
        showBanner(
          isConceptUnlink ? "Associação removida. O conceito permanece no Atlas." : "Conexão removida.",
          "success"
        );
        await loadBookData(false);
      } catch (err) {
        showBanner(err.message || "Erro ao remover conexão.", "error");
        if (btn) btn.disabled = false;
      } finally {
        submitLocks.delete(lockKey);
      }
    }

    function bindEvents() {
      dom.btnToggleCreateConcept.addEventListener("click", () => {
        const show = dom.createConceptForm.classList.contains("hidden");
        dom.createConceptForm.classList.toggle("hidden", !show);
        if (show) {
          if (!dom.conceptNameInput.value.trim() && dom.conceptSearchInput.value.trim()) {
            dom.conceptNameInput.value = dom.conceptSearchInput.value.trim();
          }
          dom.conceptNameInput.focus();
        }
      });
      dom.createConceptForm.addEventListener("submit", handleCreateConcept);

      dom.conceptSearchInput.addEventListener("input", () => {
        if (conceptLookupTimer) clearTimeout(conceptLookupTimer);
        if (conceptLookupAbortCtrl) {
          conceptLookupAbortCtrl.abort();
          conceptLookupAbortCtrl = null;
        }
        clearNode(dom.conceptSearchResults);
        dom.conceptSearchResults.classList.remove("hidden");
        dom.conceptSearchResults.appendChild(createEl("div", "rk-lookup-empty", "Buscando conceitos…"));
        conceptLookupTimer = setTimeout(() => handleConceptSearch(dom.conceptSearchInput.value), LOOKUP_DEBOUNCE_MS);
      });
      dom.conceptSearchInput.addEventListener("focus", () => handleConceptSearch(dom.conceptSearchInput.value));

      dom.btnToggleNewLink.addEventListener("click", () => {
        const show = dom.newLinkForm.classList.contains("hidden");
        dom.newLinkForm.classList.toggle("hidden", !show);
        if (show) {
          updateDirectionPreview();
          dom.targetSearchInput.focus();
          handleTargetSearch(dom.targetSearchInput.value);
        }
      });
      dom.newLinkForm.addEventListener("submit", handleCreateLink);
      dom.targetTypeSelect.addEventListener("change", () => handleTargetSearch(dom.targetSearchInput.value));
      dom.relationSelect.addEventListener("change", updateDirectionPreview);
      dom.targetSearchInput.addEventListener("input", () => {
        if (targetLookupTimer) clearTimeout(targetLookupTimer);
        if (targetLookupAbortCtrl) {
          targetLookupAbortCtrl.abort();
          targetLookupAbortCtrl = null;
        }
        clearNode(dom.targetSearchResults);
        dom.targetSearchResults.classList.remove("hidden");
        dom.targetSearchResults.appendChild(createEl("div", "rk-lookup-empty", "Buscando destinos…"));
        targetLookupTimer = setTimeout(() => handleTargetSearch(dom.targetSearchInput.value), LOOKUP_DEBOUNCE_MS);
      });
      dom.targetSearchInput.addEventListener("focus", () => handleTargetSearch(dom.targetSearchInput.value));

      dom.tabOutgoing.addEventListener("click", () => {
        activeTab = "outgoing";
        renderLinks();
      });
      dom.tabIncoming.addEventListener("click", () => {
        activeTab = "incoming";
        renderLinks();
      });
      dom.btnLoadMore.addEventListener("click", () => loadBookData(true));
    }

    function setBook(book) {
      abortAll();
      activeBook = book ? { id: book.id, title: book.title || "" } : null;
      loadedForBookId = null;
      activeTab = "outgoing";
      bookSummary = null;
      outgoingLinks = [];
      incomingLinks = [];
      totalOutgoing = 0;
      totalIncoming = 0;
      selectedTarget = null;
      editingLinkId = null;
      confirmingDeleteLinkId = null;
      activeConceptPreviewId = null;
      submitLocks.clear();
      if (rootEl) clearNode(rootEl);
    }

    function loadIfNeeded() {
      if (!activeBook || !activeBook.id) return;
      rootEl = document.getElementById("book-details-knowledge-root");
      if (!rootEl) return;
      if (loadedForBookId === activeBook.id && rootEl.childElementCount > 0) return;
      buildShell(rootEl);
      renderSummary();
      updateDirectionPreview();
      loadBookData(false);
    }

    function cleanup() {
      abortAll();
      loadedForBookId = null;
    }

    return {
      setBook,
      loadIfNeeded,
      cleanup,
    };
  }

  global.LectoriaBookKnowledge = createBookKnowledgeController();
})(window);
