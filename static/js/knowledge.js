/**
 * Lectoria v1.9.0 (Fase 5) — Interface Principal do Atlas e Editor de Reflexões Autorais.
 *
 * Áreas principais:
 * 1. Visão geral (overview): resumo pessoal real, conceitos e reflexões recentes, ações rápidas.
 * 2. Conceitos (concepts): listagem paginada, busca por nome, ordenação, criação, edição (preservando UUID),
 *    exclusão com confirmação explícita e visão de detalhes com conexões diretas, backlinks e obras/anotações.
 * 3. Reflexões (reflections): listagem paginada, editor Markdown autoral (modos Editar, Visualizar e Dividido),
 *    preview 100% seguro construído via nós DOM (zero innerHTML, zero recursos remotos), contagem de palavras,
 *    salvamento explícito com detecção de concorrência entre abas, proteção de alterações não salvas e
 *    inserção assistida de referências canônicas por UUID.
 * 4. Conexões (connections): explorador textual paginado das 8 relações semânticas, criação independente
 *    entre quaisquer duas entidades autorizadas, edição de justificativa/tipo e exclusão.
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
  const countWords = api.countWords;
  const renderSafeMarkdownToDom = api.renderSafeMarkdownToDom;

  const DEBOUNCE_MS = 220;
  const PAGE_LIMIT = 24;

  function createAtlasController() {
    let isInitialized = false;
    let activeSubtab = "overview"; // 'overview' | 'concepts' | 'reflections' | 'connections' | 'search' | 'graph'

    // Overview state
    let overviewData = null;
    let overviewAbortCtrl = null;

    // Concepts list & detail state
    let conceptsItems = [];
    let conceptsTotal = 0;
    let conceptsOffset = 0;
    let conceptsHasMore = false;
    let conceptsSearch = "";
    let conceptsSort = "updated_desc";
    let conceptsIncludeArchived = false;
    let conceptsAbortCtrl = null;
    let conceptsSearchTimer = null;

    let activeConceptDetail = null;
    let conceptOutgoing = [];
    let conceptIncoming = [];
    let conceptTotalOutgoing = 0;
    let conceptTotalIncoming = 0;
    let conceptHasMoreOut = false;
    let conceptHasMoreIn = false;
    let conceptOutOffset = 0;
    let conceptInOffset = 0;
    let conceptDetailTab = "outgoing";
    let conceptDetailAbortCtrl = null;
    let editingConceptId = null; // null = create mode in form
    let confirmingDeleteConceptId = null;

    // Concept new link subform state
    let conceptLinkTarget = null;
    let conceptTargetAbortCtrl = null;
    let conceptTargetTimer = null;

    // Reflections list & editor state
    let reflectionsItems = [];
    let reflectionsTotal = 0;
    let reflectionsOffset = 0;
    let reflectionsHasMore = false;
    let reflectionsSearch = "";
    let reflectionsSort = "updated_desc";
    let reflectionsIncludeArchived = false;
    let reflectionsAbortCtrl = null;
    let reflectionsSearchTimer = null;

    // Active reflection editor state
    let editorOpen = false;
    let currentReflection = null; // null when creating new unsaved reflection
    let loadedUpdatedAt = null;
    let concurrencyWarningShown = false;
    let editorDirty = false;
    let editorMode = "split"; // 'edit' | 'preview' | 'split'
    let editorSaveState = "new"; // 'new' | 'saved' | 'dirty' | 'saving' | 'error'
    let reflectionReferences = [];
    let reflectionBacklinks = [];
    let refPanelTab = "references";
    let confirmingDeleteReflection = false;
    let pendingLeaveAction = null;

    // Assisted reference insertion inside reflection editor
    let refLookupTarget = null;
    let refLookupAbortCtrl = null;
    let refLookupTimer = null;

    // Connections explorer state
    let linksItems = [];
    let linksTotal = 0;
    let linksOffset = 0;
    let linksHasMore = false;
    let linksRelationFilter = "";
    let linksEntityTypeFilter = "";
    let linksSort = "created_desc";
    let linksAbortCtrl = null;

    let newConnSource = null;
    let newConnTarget = null;
    let connSourceAbortCtrl = null;
    let connSourceTimer = null;
    let connTargetAbortCtrl = null;
    let connTargetTimer = null;

    // Integrated Atlas Search state (Fase 6)
    let searchQuery = "";
    let searchEntityTypeFilter = "";
    let searchBookIdFilter = "";
    let searchIncludeArchived = false;
    let searchItems = [];
    let searchTotal = 0;
    let searchOffset = 0;
    let searchHasMore = false;
    let searchEngineUsed = "fts5";
    let searchHasSearched = false;
    let searchLoading = false;
    let searchAbortCtrl = null;
    let searchDebounceTimer = null;
    let searchRequestSeq = 0;

    // Local Knowledge Graph state (Fase 6)
    let graphCenterType = "";
    let graphCenterId = "";
    let graphDepth = 1;
    let graphEntityTypeFilter = "";
    let graphRelationTypeFilter = "";
    let graphViewMode = "svg"; // 'svg' | 'list'
    let graphData = null;
    let graphLoading = false;
    let graphAbortCtrl = null;
    let graphRequestSeq = 0;
    let graphSelectedNodeKey = null;
    let graphSelectedEdgeId = null;
    let graphZoom = 1;
    let graphPanX = 0;
    let graphPanY = 0;
    let graphPickerCtrl = null;
    let graphPickerTimer = null;

    // Export state (Fase 7)
    let exportFormat = "markdown_zip";
    let exportWikilinks = false;
    let exportIncludeArchived = true;
    let exportInProgress = false;
    let exportAbortCtrl = null;

    // Shared link inline editing / deletion
    let editingLinkId = null;
    let confirmingDeleteLinkId = null;
    const submitLocks = new Set();

    const dom = {};

    function onBeforeUnload(e) {
      if (editorOpen && editorDirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    }

    function setEditorDirty(dirty) {
      editorDirty = Boolean(dirty);
      if (editorDirty) {
        global.addEventListener("beforeunload", onBeforeUnload);
        setEditorSaveBadge("dirty", "Alterações não salvas");
      } else {
        global.removeEventListener("beforeunload", onBeforeUnload);
      }
    }

    function setEditorSaveBadge(state, label) {
      editorSaveState = state;
      if (!dom.editorStatusBadge) return;
      dom.editorStatusBadge.textContent = label;
      dom.editorStatusBadge.className = `atlas-save-badge atlas-save-${state}`;
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

    function abortAllRequests() {
      [
        overviewAbortCtrl,
        conceptsAbortCtrl,
        conceptDetailAbortCtrl,
        conceptTargetAbortCtrl,
        reflectionsAbortCtrl,
        refLookupAbortCtrl,
        linksAbortCtrl,
        connSourceAbortCtrl,
        connTargetAbortCtrl,
        searchAbortCtrl,
        graphAbortCtrl,
        graphPickerCtrl,
        exportAbortCtrl,
      ].forEach((c) => {
        if (c) {
          try {
            c.abort();
          } catch (_) {}
        }
      });
      [
        conceptsSearchTimer,
        conceptTargetTimer,
        reflectionsSearchTimer,
        refLookupTimer,
        connSourceTimer,
        connTargetTimer,
        searchDebounceTimer,
        graphPickerTimer,
      ].forEach((t) => {
        if (t) clearTimeout(t);
      });
    }

    function buildDom() {
      const root = document.getElementById("atlas-section");
      if (!root) return;
      clearNode(root);

      const header = createEl("div", "atlas-header");
      const titleCol = createEl("div", "atlas-header-titles");
      titleCol.appendChild(createEl("h2", "atlas-main-title", "Atlas — Conhecimento Conectado"));
      titleCol.appendChild(
        createEl(
          "p",
          "atlas-main-subtitle",
          "Espaço pessoal de conceitos, reflexões autorais e conexões intelectuais entre leituras"
        )
      );
      header.appendChild(titleCol);

      const nav = createEl("div", "atlas-subnav");
      nav.setAttribute("role", "tablist");
      nav.setAttribute("aria-label", "Áreas do Atlas");

      const tabs = [
        { id: "overview", label: "Visão geral" },
        { id: "concepts", label: "Conceitos" },
        { id: "reflections", label: "Reflexões" },
        { id: "connections", label: "Conexões" },
        { id: "search", label: "Pesquisa" },
        { id: "graph", label: "Mapa" },
      ];
      dom.subtabBtns = {};
      tabs.forEach((t) => {
        const btn = createEl("button", `subtab-btn ${t.id === activeSubtab ? "active" : ""}`, t.label);
        btn.id = `atlas-subtab-${t.id}`;
        btn.type = "button";
        btn.dataset.atlasTab = t.id;
        btn.setAttribute("role", "tab");
        btn.setAttribute("aria-selected", t.id === activeSubtab ? "true" : "false");
        btn.addEventListener("click", () => requestSubtabSwitch(t.id));
        dom.subtabBtns[t.id] = btn;
        nav.appendChild(btn);
      });
      header.appendChild(nav);

      const exportTriggerBtn = createEl("button", "btn btn-sm btn-secondary atlas-btn-open-export", "Exportar conhecimento");
      exportTriggerBtn.id = "atlas-btn-open-export";
      exportTriggerBtn.type = "button";
      exportTriggerBtn.addEventListener("click", () => openExportModal());
      header.appendChild(exportTriggerBtn);

      root.appendChild(header);

      dom.banner = createEl("div", "rk-status-banner hidden");
      dom.banner.id = "atlas-status-banner";
      dom.banner.setAttribute("role", "status");
      dom.banner.setAttribute("aria-live", "polite");
      root.appendChild(dom.banner);

      // Pane 1: Visão geral
      dom.paneOverview = createEl("div", "atlas-pane");
      dom.paneOverview.id = "atlas-pane-overview";
      root.appendChild(dom.paneOverview);

      // Pane 2: Conceitos
      dom.paneConcepts = createEl("div", "atlas-pane hidden");
      dom.paneConcepts.id = "atlas-pane-concepts";
      root.appendChild(dom.paneConcepts);

      // Pane 3: Reflexões
      dom.paneReflections = createEl("div", "atlas-pane hidden");
      dom.paneReflections.id = "atlas-pane-reflections";
      root.appendChild(dom.paneReflections);

      // Pane 4: Conexões
      dom.paneConnections = createEl("div", "atlas-pane hidden");
      dom.paneConnections.id = "atlas-pane-connections";
      root.appendChild(dom.paneConnections);

      // Pane 5: Pesquisa integrada (Fase 6)
      dom.paneSearch = createEl("div", "atlas-pane hidden");
      dom.paneSearch.id = "atlas-pane-search";
      root.appendChild(dom.paneSearch);

      // Pane 6: Mapa local de conhecimento (Fase 6)
      dom.paneGraph = createEl("div", "atlas-pane hidden");
      dom.paneGraph.id = "atlas-pane-graph";
      root.appendChild(dom.paneGraph);

      // Modal de alterações não salvas
      dom.unsavedModal = createEl("div", "rk-modal-overlay hidden");
      dom.unsavedModal.id = "atlas-unsaved-modal";
      dom.unsavedModal.setAttribute("role", "dialog");
      dom.unsavedModal.setAttribute("aria-modal", "true");
      dom.unsavedModal.setAttribute("aria-labelledby", "atlas-unsaved-title");

      const uBox = createEl("div", "rk-modal-content atlas-unsaved-box");
      const uHead = createEl("div", "rk-modal-header");
      const uTitle = createEl("h3", "rk-modal-title", "Alterações não salvas na reflexão");
      uTitle.id = "atlas-unsaved-title";
      uHead.appendChild(uTitle);
      uBox.appendChild(uHead);

      const uBody = createEl("div", "rk-modal-body");
      uBody.appendChild(
        createEl(
          "p",
          "atlas-unsaved-text",
          "Você possui alterações ainda não salvas no editor de reflexão. Deseja salvar antes de sair?"
        )
      );
      const uActions = createEl("div", "rk-inline-form-actions");
      const btnCancelLeave = createEl("button", "btn btn-sm btn-ghost", "Continuar editando");
      btnCancelLeave.id = "atlas-btn-stay-editor";
      btnCancelLeave.type = "button";
      btnCancelLeave.addEventListener("click", () => {
        pendingLeaveAction = null;
        dom.unsavedModal.classList.add("hidden");
      });

      const btnDiscardLeave = createEl("button", "btn btn-sm btn-danger", "Descartar alterações");
      btnDiscardLeave.id = "atlas-btn-discard-changes";
      btnDiscardLeave.type = "button";
      btnDiscardLeave.addEventListener("click", () => {
        dom.unsavedModal.classList.add("hidden");
        setEditorDirty(false);
        const fn = pendingLeaveAction;
        pendingLeaveAction = null;
        if (typeof fn === "function") fn();
      });

      const btnSaveLeave = createEl("button", "btn btn-sm btn-primary", "Salvar e sair");
      btnSaveLeave.id = "atlas-btn-save-and-leave";
      btnSaveLeave.type = "button";
      btnSaveLeave.addEventListener("click", async () => {
        const ok = await handleSaveReflection();
        if (ok) {
          dom.unsavedModal.classList.add("hidden");
          const fn = pendingLeaveAction;
          pendingLeaveAction = null;
          if (typeof fn === "function") fn();
        }
      });

      uActions.appendChild(btnCancelLeave);
      uActions.appendChild(btnDiscardLeave);
      uActions.appendChild(btnSaveLeave);
      uBody.appendChild(uActions);
      uBox.appendChild(uBody);
      dom.unsavedModal.appendChild(uBox);
      root.appendChild(dom.unsavedModal);

      // Modal de Exportação Portátil do Atlas (Fase 7)
      dom.exportModal = buildExportModal();
      root.appendChild(dom.exportModal);
    }

    function setExportModalStatus(message, type) {
      if (!dom.exportStatusEl) return;
      if (!message) {
        dom.exportStatusEl.textContent = "";
        dom.exportStatusEl.className = "rk-status-banner hidden";
        dom.exportStatusEl.setAttribute("role", "status");
        return;
      }
      dom.exportStatusEl.textContent = String(message);
      dom.exportStatusEl.className = `rk-status-banner rk-banner-${type || "info"}`;
      dom.exportStatusEl.setAttribute("role", type === "error" ? "alert" : "status");
    }

    function openExportModal() {
      if (!dom.exportModal) return;
      setExportModalStatus("", "info");
      dom.exportModal.classList.remove("hidden");
      if (dom.exportConfirmBtn) dom.exportConfirmBtn.focus();
    }

    function closeExportModal() {
      if (exportInProgress && exportAbortCtrl) {
        try {
          exportAbortCtrl.abort();
        } catch (_) {}
      }
      if (dom.exportModal) {
        dom.exportModal.classList.add("hidden");
      }
    }

    function buildExportModal() {
      const overlay = createEl("div", "rk-modal-overlay hidden");
      overlay.id = "atlas-export-modal";
      overlay.setAttribute("role", "dialog");
      overlay.setAttribute("aria-modal", "true");
      overlay.setAttribute("aria-labelledby", "atlas-export-modal-title");

      const box = createEl("div", "rk-modal-content atlas-export-modal-box");
      const head = createEl("div", "rk-modal-header");
      const title = createEl("h3", "rk-modal-title", "Exportar conhecimento do Atlas");
      title.id = "atlas-export-modal-title";
      head.appendChild(title);

      const closeX = createEl("button", "rk-icon-btn", "×");
      closeX.id = "atlas-btn-close-export-x";
      closeX.type = "button";
      closeX.setAttribute("aria-label", "Fechar janela de exportação");
      closeX.addEventListener("click", () => closeExportModal());
      head.appendChild(closeX);
      box.appendChild(head);

      const body = createEl("div", "rk-modal-body atlas-export-modal-body");

      body.appendChild(
        createEl(
          "p",
          "atlas-export-intro",
          "Escolha o formato aberto para preservar seus conceitos, reflexões autorais, conexões semânticas e referências autorizadas fora da Lectoria."
        )
      );

      const formatDefs = [
        {
          value: "markdown_zip",
          title: "Markdown interligado (.zip)",
          desc: "Documentos .md com frontmatter YAML, links relativos, índice e manifesto SHA-256. Compatível com leitores Markdown e Obsidian.",
        },
        {
          value: "json",
          title: "JSON estruturado v2.0 (.json)",
          desc: "Representação canônica versionada com UUIDs estáveis, conexões direcionadas, localizações PDF/EPUB e política explícita de referências.",
        },
        {
          value: "csv",
          title: "CSV tabular (.zip)",
          desc: "Conjunto de planilhas UTF-8 (conceitos, reflexões, conexões, livros e anotações) com proteção contra injeção de fórmulas.",
        },
      ];

      const optionsGroup = createEl("div", "atlas-export-options");
      optionsGroup.setAttribute("role", "radiogroup");
      optionsGroup.setAttribute("aria-label", "Formato de exportação");

      const wikiRow = createEl("label", "rk-checkbox-row atlas-export-wiki-row");
      const wikiCheck = document.createElement("input");
      wikiCheck.id = "atlas-export-wikilinks";
      wikiCheck.type = "checkbox";
      wikiCheck.checked = exportWikilinks;
      wikiCheck.addEventListener("change", () => {
        exportWikilinks = wikiCheck.checked;
      });
      wikiRow.appendChild(wikiCheck);
      wikiRow.appendChild(createEl("span", "", "Incluir wikilinks [[...]] compatíveis com Obsidian (mantendo links Markdown padrão)"));

      function syncWikiRowVisibility() {
        wikiRow.classList.toggle("hidden", exportFormat !== "markdown_zip");
      }

      formatDefs.forEach((f) => {
        const card = createEl("label", `atlas-export-format-card ${exportFormat === f.value ? "selected" : ""}`);
        card.dataset.exportFormat = f.value;
        const radio = document.createElement("input");
        radio.type = "radio";
        radio.name = "atlas_export_format";
        radio.id = `atlas-export-fmt-${f.value}`;
        radio.value = f.value;
        radio.checked = exportFormat === f.value;
        radio.addEventListener("change", () => {
          exportFormat = f.value;
          optionsGroup.querySelectorAll(".atlas-export-format-card").forEach((el) => {
            el.classList.toggle("selected", el.dataset.exportFormat === exportFormat);
          });
          syncWikiRowVisibility();
        });

        const textCol = createEl("div", "atlas-export-format-info");
        textCol.appendChild(createEl("strong", "atlas-export-format-title", f.title));
        textCol.appendChild(createEl("span", "atlas-export-format-desc", f.desc));
        card.appendChild(radio);
        card.appendChild(textCol);
        optionsGroup.appendChild(card);
      });
      body.appendChild(optionsGroup);
      body.appendChild(wikiRow);
      syncWikiRowVisibility();

      const archRow = createEl("label", "rk-checkbox-row");
      const archCheck = document.createElement("input");
      archCheck.id = "atlas-export-include-archived";
      archCheck.type = "checkbox";
      archCheck.checked = exportIncludeArchived;
      archCheck.addEventListener("change", () => {
        exportIncludeArchived = archCheck.checked;
      });
      archRow.appendChild(archCheck);
      archRow.appendChild(createEl("span", "", "Incluir conceitos e reflexões arquivados"));
      body.appendChild(archRow);

      // Avisos de privacidade obrigatórios (Seção 21)
      const privacyBox = createEl("div", "atlas-export-privacy-box");
      privacyBox.id = "atlas-export-privacy-notice";
      privacyBox.appendChild(createEl("strong", "", "Aviso de privacidade e guarda local"));
      const pList = createEl("ul", "atlas-export-privacy-list");
      [
        "A exportação contém suas informações pessoais e autorais do Atlas.",
        "O arquivo gerado será baixado e armazenado diretamente no seu dispositivo.",
        "Você controla a guarda e eventual compartilhamento desse arquivo.",
      ].forEach((msg) => {
        pList.appendChild(createEl("li", "", msg));
      });
      privacyBox.appendChild(pList);
      body.appendChild(privacyBox);

      dom.exportStatusEl = createEl("div", "rk-status-banner hidden");
      dom.exportStatusEl.id = "atlas-export-status";
      dom.exportStatusEl.setAttribute("role", "status");
      dom.exportStatusEl.setAttribute("aria-live", "polite");
      body.appendChild(dom.exportStatusEl);

      const actions = createEl("div", "rk-inline-form-actions");
      const btnCancel = createEl("button", "btn btn-sm btn-ghost", "Cancelar");
      btnCancel.id = "atlas-btn-cancel-export";
      btnCancel.type = "button";
      btnCancel.addEventListener("click", () => closeExportModal());

      dom.exportConfirmBtn = createEl("button", "btn btn-sm btn-primary", "Baixar exportação");
      dom.exportConfirmBtn.id = "atlas-btn-confirm-export";
      dom.exportConfirmBtn.type = "button";
      dom.exportConfirmBtn.addEventListener("click", () => handleExecuteExport());

      actions.appendChild(btnCancel);
      actions.appendChild(dom.exportConfirmBtn);
      body.appendChild(actions);

      box.appendChild(body);
      overlay.appendChild(box);
      return overlay;
    }

    async function handleExecuteExport() {
      if (exportInProgress || submitLocks.has("atlas-export")) return;
      exportInProgress = true;
      submitLocks.add("atlas-export");
      if (exportAbortCtrl) exportAbortCtrl.abort();
      exportAbortCtrl = new AbortController();

      if (dom.exportConfirmBtn) {
        dom.exportConfirmBtn.disabled = true;
        dom.exportConfirmBtn.textContent = "Gerando exportação…";
      }
      setExportModalStatus("Preparando pacote portátil de exportação…", "info");

      try {
        const res = await api.downloadAtlasExport(
          {
            format: exportFormat,
            wikilinks: exportFormat === "markdown_zip" ? exportWikilinks : false,
            include_archived: exportIncludeArchived,
          },
          exportAbortCtrl.signal
        );

        const blobUrl = URL.createObjectURL(res.blob);
        try {
          const a = document.createElement("a");
          a.href = blobUrl;
          a.download = res.filename;
          a.style.display = "none";
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
        } finally {
          setTimeout(() => URL.revokeObjectURL(blobUrl), 2500);
        }

        setExportModalStatus(`Exportação concluída: ${res.filename}`, "success");
      } catch (err) {
        if (err && err.name === "AbortError") {
          setExportModalStatus("Exportação cancelada pelo usuário.", "info");
        } else {
          setExportModalStatus(
            (err && err.message) || "Não foi possível concluir a exportação do Atlas.",
            "error"
          );
        }
      } finally {
        exportInProgress = false;
        submitLocks.delete("atlas-export");
        if (dom.exportConfirmBtn) {
          dom.exportConfirmBtn.disabled = false;
          dom.exportConfirmBtn.textContent = "Baixar exportação";
        }
      }
    }

    function confirmLeaveIfDirty(nextAction) {
      if (editorOpen && editorDirty) {
        pendingLeaveAction = nextAction;
        if (dom.unsavedModal) {
          dom.unsavedModal.classList.remove("hidden");
        }
        return false;
      }
      if (typeof nextAction === "function") {
        nextAction();
      }
      return true;
    }

    function requestSubtabSwitch(targetTab, opts) {
      const options = opts || {};
      const proceed = () => {
        activeSubtab = targetTab;
        showBanner("", "info");
        Object.keys(dom.subtabBtns || {}).forEach((k) => {
          const active = k === targetTab;
          dom.subtabBtns[k].classList.toggle("active", active);
          dom.subtabBtns[k].setAttribute("aria-selected", active ? "true" : "false");
        });
        if (dom.paneOverview) dom.paneOverview.classList.toggle("hidden", targetTab !== "overview");
        if (dom.paneConcepts) dom.paneConcepts.classList.toggle("hidden", targetTab !== "concepts");
        if (dom.paneReflections) dom.paneReflections.classList.toggle("hidden", targetTab !== "reflections");
        if (dom.paneConnections) dom.paneConnections.classList.toggle("hidden", targetTab !== "connections");
        if (dom.paneSearch) dom.paneSearch.classList.toggle("hidden", targetTab !== "search");
        if (dom.paneGraph) dom.paneGraph.classList.toggle("hidden", targetTab !== "graph");

        if (targetTab === "overview") {
          editorOpen = false;
          loadOverview();
        } else if (targetTab === "concepts") {
          editorOpen = false;
          if (options.conceptId) {
            openConceptDetail(options.conceptId);
          } else {
            activeConceptDetail = null;
            loadConceptsList(false, options.openNewConcept === true);
          }
        } else if (targetTab === "reflections") {
          if (options.reflectionId) {
            openReflectionEditorById(options.reflectionId);
          } else if (options.openNewReflection) {
            openNewReflectionEditor();
          } else {
            editorOpen = false;
            loadReflectionsList(false);
          }
        } else if (targetTab === "connections") {
          editorOpen = false;
          loadConnectionsExplorer(false, options.openNewConnection === true);
        } else if (targetTab === "search") {
          editorOpen = false;
          if (typeof options.searchQuery === "string") {
            searchQuery = options.searchQuery;
          }
          renderSearchPaneView();
          if (searchQuery.trim()) {
            loadIntegratedSearch(false);
          }
        } else if (targetTab === "graph") {
          editorOpen = false;
          if (options.graphEntityType && options.graphEntityId) {
            graphCenterType = options.graphEntityType;
            graphCenterId = options.graphEntityId;
            if (options.graphDepth === 1 || options.graphDepth === 2) {
              graphDepth = options.graphDepth;
            }
          }
          if (graphCenterType && graphCenterId) {
            loadLocalKnowledgeGraph();
          } else {
            renderGraphPaneView();
          }
        }
      };

      if (editorOpen && editorDirty && targetTab !== "reflections") {
        confirmLeaveIfDirty(proceed);
      } else {
        proceed();
      }
    }

    // =========================================================================
    // 1. VISÃO GERAL (OVERVIEW)
    // =========================================================================

    async function loadOverview() {
      if (!dom.paneOverview) return;
      if (overviewAbortCtrl) overviewAbortCtrl.abort();
      overviewAbortCtrl = new AbortController();

      clearNode(dom.paneOverview);
      dom.paneOverview.appendChild(createEl("div", "rk-empty-state", "Carregando visão geral do Atlas…"));

      try {
        overviewData = await api.getAtlasSummary(6, overviewAbortCtrl.signal);
        renderOverview();
      } catch (err) {
        if (err && err.name === "AbortError") return;
        showBanner(err.message || "Não foi possível carregar a visão geral do Atlas.", "error");
        renderOverview();
      }
    }

    function renderOverview() {
      if (!dom.paneOverview) return;
      clearNode(dom.paneOverview);

      const s = overviewData || {
        concepts_count: 0,
        reflections_count: 0,
        links_count: 0,
        connected_books_count: 0,
        recent_concepts: [],
        recent_reflections: [],
      };

      // Quick actions bar
      const actionsRow = createEl("div", "atlas-quick-actions");
      const btnNewConcept = createEl("button", "btn btn-sm btn-primary", "+ Novo conceito");
      btnNewConcept.id = "atlas-overview-new-concept";
      btnNewConcept.type = "button";
      btnNewConcept.addEventListener("click", () => requestSubtabSwitch("concepts", { openNewConcept: true }));

      const btnNewReflection = createEl("button", "btn btn-sm btn-primary", "+ Nova reflexão");
      btnNewReflection.id = "atlas-overview-new-reflection";
      btnNewReflection.type = "button";
      btnNewReflection.addEventListener("click", () => requestSubtabSwitch("reflections", { openNewReflection: true }));

      const btnNewConn = createEl("button", "btn btn-sm btn-secondary", "+ Nova conexão");
      btnNewConn.id = "atlas-overview-new-connection";
      btnNewConn.type = "button";
      btnNewConn.addEventListener("click", () => requestSubtabSwitch("connections", { openNewConnection: true }));

      actionsRow.appendChild(btnNewConcept);
      actionsRow.appendChild(btnNewReflection);
      actionsRow.appendChild(btnNewConn);
      dom.paneOverview.appendChild(actionsRow);

      // Stats cards
      const statsGrid = createEl("div", "atlas-stats-grid");
      const statDefs = [
        { id: "concepts", value: s.concepts_count, label: "Conceitos ativos", tab: "concepts" },
        { id: "reflections", value: s.reflections_count, label: "Reflexões autorais", tab: "reflections" },
        { id: "links", value: s.links_count, label: "Conexões semânticas", tab: "connections" },
        { id: "books", value: s.connected_books_count, label: "Obras conectadas", tab: "connections" },
      ];
      statDefs.forEach((st) => {
        const card = createEl("button", "atlas-stat-card");
        card.type = "button";
        card.id = `atlas-stat-${st.id}`;
        card.appendChild(createEl("div", "atlas-stat-value", String(st.value)));
        card.appendChild(createEl("div", "atlas-stat-label", st.label));
        card.addEventListener("click", () => requestSubtabSwitch(st.tab));
        statsGrid.appendChild(card);
      });
      dom.paneOverview.appendChild(statsGrid);

      // Empty state for brand-new users
      if (s.concepts_count === 0 && s.reflections_count === 0 && s.links_count === 0) {
        const emptyBox = createEl("div", "atlas-welcome-empty");
        emptyBox.appendChild(createEl("h3", "atlas-welcome-title", "Seu Atlas intelectual está pronto para começar"));
        emptyBox.appendChild(
          createEl(
            "p",
            "atlas-welcome-text",
            "Crie conceitos independentes, escreva reflexões autorais em Markdown fundamentadas nas suas leituras ou associe ideias diretamente nos leitores PDF e EPUB e no Caderno de Estudos."
          )
        );
        dom.paneOverview.appendChild(emptyBox);
        return;
      }

      // Two columns: Recent Concepts & Recent Reflections
      const cols = createEl("div", "atlas-overview-columns");

      // Col 1: Conceitos recentes
      const colC = createEl("section", "atlas-overview-col");
      const headC = createEl("div", "rk-section-header");
      headC.appendChild(createEl("h3", "rk-section-title", "Conceitos recentes"));
      const seeAllC = createEl("button", "btn btn-sm btn-ghost", "Ver todos →");
      seeAllC.type = "button";
      seeAllC.addEventListener("click", () => requestSubtabSwitch("concepts"));
      headC.appendChild(seeAllC);
      colC.appendChild(headC);

      const listC = Array.isArray(s.recent_concepts) ? s.recent_concepts : [];
      if (listC.length === 0) {
        colC.appendChild(createEl("div", "rk-empty-state", "Nenhum conceito cadastrado ainda."));
      } else {
        listC.forEach((c) => {
          const item = createEl("button", "atlas-recent-card");
          item.type = "button";
          item.dataset.conceptId = c.id;
          const top = createEl("div", "atlas-recent-card-top");
          const left = createEl("div", "rk-concept-preview-title");
          const dot = createEl("span", "rk-color-dot");
          dot.style.backgroundColor = sanitizeHexColor(c.color);
          left.appendChild(dot);
          left.appendChild(createEl("strong", "", c.name));
          top.appendChild(left);
          top.appendChild(createEl("span", "rk-badge", `${c.links_count || 0} vínculo(s)`));
          item.appendChild(top);
          if (c.description) {
            item.appendChild(createEl("p", "atlas-recent-excerpt", truncateText(c.description, 120)));
          }
          item.addEventListener("click", () => requestSubtabSwitch("concepts", { conceptId: c.id }));
          colC.appendChild(item);
        });
      }
      cols.appendChild(colC);

      // Col 2: Reflexões recentes
      const colR = createEl("section", "atlas-overview-col");
      const headR = createEl("div", "rk-section-header");
      headR.appendChild(createEl("h3", "rk-section-title", "Reflexões recentes"));
      const seeAllR = createEl("button", "btn btn-sm btn-ghost", "Ver todas →");
      seeAllR.type = "button";
      seeAllR.addEventListener("click", () => requestSubtabSwitch("reflections"));
      headR.appendChild(seeAllR);
      colR.appendChild(headR);

      const listR = Array.isArray(s.recent_reflections) ? s.recent_reflections : [];
      if (listR.length === 0) {
        colR.appendChild(createEl("div", "rk-empty-state", "Nenhuma reflexão autoral escrita ainda."));
      } else {
        listR.forEach((r) => {
          const item = createEl("button", "atlas-recent-card");
          item.type = "button";
          item.dataset.reflectionId = r.id;
          const top = createEl("div", "atlas-recent-card-top");
          top.appendChild(createEl("strong", "", r.title));
          if (r.is_pinned) {
            top.appendChild(createEl("span", "rk-badge rk-badge-relation", "Fixada"));
          }
          item.appendChild(top);
          if (r.excerpt) {
            item.appendChild(createEl("p", "atlas-recent-excerpt", r.excerpt));
          }
          const metaParts = [`${r.references_count || 0} referência(s)`, `${r.backlinks_count || 0} backlink(s)`];
          if (r.primary_book_title) metaParts.unshift(r.primary_book_title);
          item.appendChild(createEl("div", "rk-link-target-meta", metaParts.join(" · ")));
          item.addEventListener("click", () => requestSubtabSwitch("reflections", { reflectionId: r.id }));
          colR.appendChild(item);
        });
      }
      cols.appendChild(colR);

      dom.paneOverview.appendChild(cols);
    }

    // =========================================================================
    // 2. GESTÃO INDEPENDENTE DE CONCEITOS (CONCEPTS)
    // =========================================================================

    async function loadConceptsList(append, openCreateForm) {
      if (!dom.paneConcepts) return;
      if (conceptsAbortCtrl) conceptsAbortCtrl.abort();
      conceptsAbortCtrl = new AbortController();

      const offset = append ? conceptsOffset : 0;
      try {
        const data = await api.listConcepts(
          {
            search: conceptsSearch,
            sort: conceptsSort,
            include_archived: conceptsIncludeArchived,
            limit: PAGE_LIMIT,
            offset,
          },
          conceptsAbortCtrl.signal
        );
        const batch = Array.isArray(data && data.items) ? data.items : [];
        conceptsItems = append ? conceptsItems.concat(batch) : batch;
        conceptsTotal = Number(data && data.total) || conceptsItems.length;
        conceptsHasMore = Boolean(data && data.has_more);
        conceptsOffset = conceptsItems.length;
        renderConceptsListView(openCreateForm);
      } catch (err) {
        if (err && err.name === "AbortError") return;
        showBanner(err.message || "Erro ao carregar conceitos.", "error");
        renderConceptsListView(openCreateForm);
      }
    }

    function renderConceptsListView(openCreateForm) {
      if (!dom.paneConcepts) return;
      clearNode(dom.paneConcepts);

      const toolbar = createEl("div", "atlas-toolbar");
      const searchInput = createEl("input", "rk-input atlas-search-input");
      searchInput.id = "atlas-concepts-search";
      searchInput.type = "search";
      searchInput.placeholder = "Filtrar conceitos por nome, descrição ou etiqueta...";
      searchInput.value = conceptsSearch;
      searchInput.addEventListener("input", () => {
        conceptsSearch = searchInput.value;
        if (conceptsSearchTimer) clearTimeout(conceptsSearchTimer);
        conceptsSearchTimer = setTimeout(() => loadConceptsList(false, false), DEBOUNCE_MS);
      });
      toolbar.appendChild(searchInput);

      const sortSel = createEl("select", "rk-select atlas-sort-select");
      sortSel.id = "atlas-concepts-sort";
      [
        ["updated_desc", "Mais recentes"],
        ["created_desc", "Data de criação"],
        ["name_asc", "Nome (A–Z)"],
      ].forEach(([val, lbl]) => {
        const opt = document.createElement("option");
        opt.value = val;
        opt.textContent = lbl;
        if (val === conceptsSort) opt.selected = true;
        sortSel.appendChild(opt);
      });
      sortSel.addEventListener("change", () => {
        conceptsSort = sortSel.value;
        loadConceptsList(false, false);
      });
      toolbar.appendChild(sortSel);

      const archLabel = createEl("label", "rk-checkbox-row");
      const archCheck = document.createElement("input");
      archCheck.id = "atlas-concepts-include-archived";
      archCheck.type = "checkbox";
      archCheck.checked = conceptsIncludeArchived;
      archCheck.addEventListener("change", () => {
        conceptsIncludeArchived = archCheck.checked;
        loadConceptsList(false, false);
      });
      archLabel.appendChild(archCheck);
      archLabel.appendChild(createEl("span", "", "Incluir arquivados"));
      toolbar.appendChild(archLabel);

      const btnNew = createEl("button", "btn btn-sm btn-primary", "+ Novo conceito");
      btnNew.id = "atlas-btn-new-concept";
      btnNew.type = "button";
      toolbar.appendChild(btnNew);
      dom.paneConcepts.appendChild(toolbar);

      // Collapsible Create / Edit Concept Form
      const form = buildConceptForm(null, () => loadConceptsList(false, false));
      if (!openCreateForm) form.classList.add("hidden");
      btnNew.addEventListener("click", () => {
        const willShow = form.classList.contains("hidden");
        form.classList.toggle("hidden", !willShow);
        if (willShow) {
          const nameIn = form.querySelector("#atlas-concept-form-name");
          if (nameIn) nameIn.focus();
        }
      });
      dom.paneConcepts.appendChild(form);

      if (conceptsItems.length === 0) {
        dom.paneConcepts.appendChild(
          createEl(
            "div",
            "rk-empty-state",
            conceptsSearch
              ? `Nenhum conceito encontrado para "${conceptsSearch}".`
              : "Você ainda não criou conceitos no seu Atlas."
          )
        );
        return;
      }

      const grid = createEl("div", "atlas-cards-grid");
      grid.id = "atlas-concepts-grid";
      conceptsItems.forEach((c) => {
        const card = createEl("article", "atlas-entity-card");
        card.dataset.conceptId = c.id;

        const top = createEl("div", "atlas-entity-card-header");
        const titleBtn = createEl("button", "atlas-card-title-btn");
        titleBtn.type = "button";
        const dot = createEl("span", "rk-color-dot");
        dot.style.backgroundColor = sanitizeHexColor(c.color);
        titleBtn.appendChild(dot);
        titleBtn.appendChild(createEl("span", "", c.name));
        titleBtn.addEventListener("click", () => openConceptDetail(c.id));
        top.appendChild(titleBtn);

        const badges = createEl("div", "rk-badges-row");
        if (c.is_archived) badges.appendChild(createEl("span", "rk-badge", "Arquivado"));
        badges.appendChild(createEl("span", "rk-badge rk-badge-relation", `${c.links_count || 0} vínculo(s)`));
        top.appendChild(badges);
        card.appendChild(top);

        if (c.description) {
          card.appendChild(createEl("p", "atlas-card-desc", truncateText(c.description, 180)));
        }

        if (Array.isArray(c.tags) && c.tags.length > 0) {
          const tRow = createEl("div", "rk-tags-row");
          c.tags.forEach((t) => tRow.appendChild(createEl("span", "rk-tag-pill", `#${t}`)));
          card.appendChild(tRow);
        }

        const foot = createEl("div", "atlas-card-footer");
        const btnOpen = createEl("button", "btn btn-sm btn-secondary atlas-btn-open-concept", "Abrir detalhes");
        btnOpen.type = "button";
        btnOpen.addEventListener("click", () => openConceptDetail(c.id));
        foot.appendChild(btnOpen);
        card.appendChild(foot);

        grid.appendChild(card);
      });
      dom.paneConcepts.appendChild(grid);

      if (conceptsHasMore) {
        const moreWrap = createEl("div", "rk-load-more-wrap");
        const btnMore = createEl("button", "btn btn-sm btn-ghost", "Carregar mais conceitos");
        btnMore.id = "atlas-concepts-load-more";
        btnMore.type = "button";
        btnMore.addEventListener("click", () => loadConceptsList(true, false));
        moreWrap.appendChild(btnMore);
        dom.paneConcepts.appendChild(moreWrap);
      }
    }

    function buildConceptForm(existingConcept, onSaved) {
      const isEdit = Boolean(existingConcept && existingConcept.id);
      const form = createEl("form", "rk-subform atlas-concept-form");
      form.id = isEdit ? "atlas-concept-edit-form" : "atlas-concept-create-form";
      form.appendChild(
        createEl("div", "rk-subform-title", isEdit ? "Editar conceito (preserva UUID e conexões)" : "Criar novo conceito")
      );

      const lblName = createEl("label", "rk-field-label", "Nome do conceito *");
      const nameInput = createEl("input", "rk-input");
      nameInput.id = "atlas-concept-form-name";
      nameInput.type = "text";
      nameInput.required = true;
      nameInput.maxLength = 160;
      nameInput.value = isEdit ? existingConcept.name : "";
      lblName.appendChild(nameInput);
      form.appendChild(lblName);

      const lblDesc = createEl("label", "rk-field-label", "Descrição (opcional)");
      const descInput = createEl("textarea", "rk-textarea");
      descInput.id = "atlas-concept-form-desc";
      descInput.rows = 3;
      descInput.maxLength = 10000;
      descInput.value = isEdit ? (existingConcept.description || "") : "";
      lblDesc.appendChild(descInput);
      form.appendChild(lblDesc);

      const grid2 = createEl("div", "rk-form-grid-2");
      const lblTags = createEl("label", "rk-field-label", "Etiquetas (separadas por vírgula)");
      const tagsInput = createEl("input", "rk-input");
      tagsInput.id = "atlas-concept-form-tags";
      tagsInput.type = "text";
      tagsInput.maxLength = 300;
      tagsInput.value = isEdit && Array.isArray(existingConcept.tags) ? existingConcept.tags.join(", ") : "";
      lblTags.appendChild(tagsInput);
      grid2.appendChild(lblTags);

      const lblColor = createEl("label", "rk-field-label", "Cor");
      const colorInput = createEl("input", "rk-color-input");
      colorInput.id = "atlas-concept-form-color";
      colorInput.type = "color";
      colorInput.value = isEdit ? sanitizeHexColor(existingConcept.color) : DEFAULT_CONCEPT_COLOR;
      lblColor.appendChild(colorInput);
      grid2.appendChild(lblColor);
      form.appendChild(grid2);

      const archLabel = createEl("label", "rk-checkbox-row");
      const archInput = document.createElement("input");
      archInput.id = "atlas-concept-form-archived";
      archInput.type = "checkbox";
      archInput.checked = isEdit ? Boolean(existingConcept.is_archived) : false;
      archLabel.appendChild(archInput);
      archLabel.appendChild(createEl("span", "", "Arquivar este conceito"));
      form.appendChild(archLabel);

      const actions = createEl("div", "rk-inline-form-actions");
      const cancelBtn = createEl("button", "btn btn-sm btn-ghost", "Cancelar");
      cancelBtn.type = "button";
      cancelBtn.addEventListener("click", () => form.classList.add("hidden"));

      const submitBtn = createEl("button", "btn btn-sm btn-primary", isEdit ? "Salvar alterações" : "Criar conceito");
      submitBtn.id = "atlas-concept-form-submit";
      submitBtn.type = "submit";
      actions.appendChild(cancelBtn);
      actions.appendChild(submitBtn);
      form.appendChild(actions);

      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const lockKey = isEdit ? `edit-concept:${existingConcept.id}` : "create-concept";
        if (submitLocks.has(lockKey)) return;
        const name = nameInput.value.trim();
        if (!name) {
          showBanner("O nome do conceito é obrigatório.", "error");
          return;
        }
        const tags = tagsInput.value
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean)
          .slice(0, 30);
        const payload = {
          name,
          description: descInput.value.trim(),
          tags,
          color: sanitizeHexColor(colorInput.value),
          is_archived: archInput.checked,
        };

        submitLocks.add(lockKey);
        submitBtn.disabled = true;
        try {
          if (isEdit) {
            const res = await api.updateConcept(existingConcept.id, payload);
            showBanner(`Conceito "${res.concept.name}" atualizado com sucesso.`, "success");
          } else {
            const res = await api.createConcept(payload);
            nameInput.value = "";
            descInput.value = "";
            tagsInput.value = "";
            form.classList.add("hidden");
            showBanner(`Conceito "${res.concept.name}" criado no Atlas.`, "success");
          }
          if (typeof onSaved === "function") onSaved();
        } catch (err) {
          showBanner(err.message || "Não foi possível salvar o conceito.", "error");
        } finally {
          submitLocks.delete(lockKey);
          submitBtn.disabled = false;
        }
      });

      return form;
    }

    async function openConceptDetail(conceptId, appendLinks) {
      if (!dom.paneConcepts || !conceptId) return;
      if (conceptDetailAbortCtrl) conceptDetailAbortCtrl.abort();
      conceptDetailAbortCtrl = new AbortController();
      const signal = conceptDetailAbortCtrl.signal;

      const offset = appendLinks ? (conceptDetailTab === "outgoing" ? conceptOutOffset : conceptInOffset) : 0;
      const direction = appendLinks ? conceptDetailTab : "both";

      try {
        const reqs = [
          api.getEntityConnections("concept", conceptId, { direction, limit: PAGE_LIMIT, offset }, signal),
        ];
        if (!appendLinks) {
          reqs.push(api.getConcept(conceptId, signal));
          confirmingDeleteConceptId = null;
          editingLinkId = null;
          confirmingDeleteLinkId = null;
        }
        const [connData, conceptResp] = await Promise.all(reqs);

        if (!appendLinks && conceptResp && conceptResp.concept) {
          activeConceptDetail = conceptResp.concept;
          conceptOutgoing = Array.isArray(connData.outgoing_links) ? connData.outgoing_links : [];
          conceptIncoming = Array.isArray(connData.incoming_backlinks) ? connData.incoming_backlinks : [];
          conceptTotalOutgoing = Number(connData.total_outgoing) || conceptOutgoing.length;
          conceptTotalIncoming = Number(connData.total_incoming) || conceptIncoming.length;
          conceptHasMoreOut = Boolean(connData.has_more_outgoing);
          conceptHasMoreIn = Boolean(connData.has_more_incoming);
          conceptOutOffset = conceptOutgoing.length;
          conceptInOffset = conceptIncoming.length;
        } else if (appendLinks && conceptDetailTab === "outgoing") {
          const next = Array.isArray(connData.outgoing_links) ? connData.outgoing_links : [];
          conceptOutgoing = conceptOutgoing.concat(next);
          conceptHasMoreOut = Boolean(connData.has_more_outgoing);
          conceptOutOffset = conceptOutgoing.length;
        } else if (appendLinks) {
          const next = Array.isArray(connData.incoming_backlinks) ? connData.incoming_backlinks : [];
          conceptIncoming = conceptIncoming.concat(next);
          conceptHasMoreIn = Boolean(connData.has_more_incoming);
          conceptInOffset = conceptIncoming.length;
        }

        renderConceptDetailView();
      } catch (err) {
        if (err && err.name === "AbortError") return;
        showBanner(err.message || "Conceito não encontrado ou inacessível.", "error");
        loadConceptsList(false, false);
      }
    }

    function renderConceptDetailView() {
      if (!dom.paneConcepts || !activeConceptDetail) return;
      clearNode(dom.paneConcepts);
      const c = activeConceptDetail;

      const topBar = createEl("div", "atlas-detail-topbar");
      const backBtn = createEl("button", "btn btn-sm btn-ghost", "← Voltar aos conceitos");
      backBtn.id = "atlas-btn-back-concepts";
      backBtn.type = "button";
      backBtn.addEventListener("click", () => {
        activeConceptDetail = null;
        loadConceptsList(false, false);
      });
      topBar.appendChild(backBtn);

      const actGroup = createEl("div", "rk-link-card-actions");
      const btnExploreMap = createEl("button", "btn btn-sm btn-secondary", "Explorar no Mapa");
      btnExploreMap.id = "atlas-btn-explore-concept-graph";
      btnExploreMap.type = "button";
      btnExploreMap.addEventListener("click", () => {
        requestSubtabSwitch("graph", { graphEntityType: "concept", graphEntityId: c.id });
      });
      actGroup.appendChild(btnExploreMap);

      const btnEdit = createEl("button", "btn btn-sm btn-secondary", "Editar conceito");
      btnEdit.id = "atlas-btn-edit-concept";
      btnEdit.type = "button";
      actGroup.appendChild(btnEdit);

      if (confirmingDeleteConceptId === c.id) {
        const confirmDel = createEl("button", "btn btn-sm btn-danger", "Confirmar exclusão definitiva");
        confirmDel.id = "atlas-btn-confirm-delete-concept";
        confirmDel.type = "button";
        confirmDel.addEventListener("click", () => handleDeleteConcept(c.id, confirmDel));
        const cancelDel = createEl("button", "btn btn-sm btn-ghost", "Cancelar");
        cancelDel.type = "button";
        cancelDel.addEventListener("click", () => {
          confirmingDeleteConceptId = null;
          renderConceptDetailView();
        });
        actGroup.appendChild(confirmDel);
        actGroup.appendChild(cancelDel);
      } else {
        const btnDel = createEl("button", "btn btn-sm btn-danger", "Excluir conceito");
        btnDel.id = "atlas-btn-delete-concept";
        btnDel.type = "button";
        btnDel.addEventListener("click", () => {
          confirmingDeleteConceptId = c.id;
          renderConceptDetailView();
        });
        actGroup.appendChild(btnDel);
      }
      topBar.appendChild(actGroup);
      dom.paneConcepts.appendChild(topBar);

      if (confirmingDeleteConceptId === c.id) {
        dom.paneConcepts.appendChild(
          createEl(
            "div",
            "rk-status-banner rk-banner-error",
            "Atenção: excluir este conceito removerá o conceito e os vínculos diretos associados a ele em knowledge_links. Seus livros, destaques, notas e reflexões permanecem intactos."
          )
        );
      }

      // Header card of the concept
      const detailCard = createEl("div", "atlas-detail-hero");
      const titleRow = createEl("div", "rk-concept-preview-title");
      const dot = createEl("span", "rk-color-dot");
      dot.style.backgroundColor = sanitizeHexColor(c.color);
      titleRow.appendChild(dot);
      titleRow.appendChild(createEl("h3", "atlas-detail-title", c.name));
      if (c.is_archived) titleRow.appendChild(createEl("span", "rk-badge", "Arquivado"));
      detailCard.appendChild(titleRow);

      detailCard.appendChild(
        createEl(
          "p",
          "atlas-detail-description",
          c.description || "Sem descrição cadastrada para este conceito."
        )
      );

      if (Array.isArray(c.tags) && c.tags.length > 0) {
        const tRow = createEl("div", "rk-tags-row");
        c.tags.forEach((t) => tRow.appendChild(createEl("span", "rk-tag-pill", `#${t}`)));
        detailCard.appendChild(tRow);
      }
      dom.paneConcepts.appendChild(detailCard);

      // Edit concept form (hidden by default)
      const editForm = buildConceptForm(c, () => openConceptDetail(c.id, false));
      editForm.classList.add("hidden");
      btnEdit.addEventListener("click", () => editForm.classList.toggle("hidden"));
      dom.paneConcepts.appendChild(editForm);

      // Direct Relations Breakdown (Obras, Anotações e Reflexões diretamente vinculadas)
      const allDirect = conceptOutgoing.map((l) => l.target || l.counterpart).concat(
        conceptIncoming.map((l) => l.source || l.counterpart)
      );
      const relatedBooks = new Map();
      let relatedAnnotationsCount = 0;
      let relatedReflectionsCount = 0;
      allDirect.forEach((ent) => {
        if (!ent || ent.available !== true) return;
        if (ent.type === "book" && ent.id) {
          relatedBooks.set(ent.id, ent.title || ent.label || ent.id);
        } else if ((ent.type === "highlight" || ent.type === "note") && ent.book_id) {
          relatedAnnotationsCount++;
          relatedBooks.set(ent.book_id, ent.book_title || ent.book_id);
        } else if (ent.type === "reflection") {
          relatedReflectionsCount++;
        }
      });

      const directSummary = createEl("div", "bk-summary-bar");
      const statsWrap = createEl("div", "bk-summary-stats");
      statsWrap.appendChild(
        createEl("span", "bk-stat-pill", `Obras com vínculo direto: ${relatedBooks.size}`)
      );
      statsWrap.appendChild(
        createEl("span", "bk-stat-pill", `Anotações diretamente vinculadas: ${relatedAnnotationsCount}`)
      );
      statsWrap.appendChild(
        createEl("span", "bk-stat-pill", `Reflexões diretamente vinculadas: ${relatedReflectionsCount}`)
      );
      directSummary.appendChild(statsWrap);

      const btnNewConceptLink = createEl("button", "btn btn-sm btn-secondary", "+ Conectar conceito");
      btnNewConceptLink.id = "atlas-btn-connect-concept";
      btnNewConceptLink.type = "button";
      directSummary.appendChild(btnNewConceptLink);
      dom.paneConcepts.appendChild(directSummary);

      // New link form for this concept
      const newLinkForm = buildEntityNewLinkForm("concept", c.id, c.name, () => openConceptDetail(c.id, false));
      newLinkForm.classList.add("hidden");
      btnNewConceptLink.addEventListener("click", () => newLinkForm.classList.toggle("hidden"));
      dom.paneConcepts.appendChild(newLinkForm);

      // Outgoing Connections & Incoming Backlinks Tabs
      const tabs = createEl("div", "rk-tabs");
      const btnOut = createEl(
        "button",
        `rk-tab-btn ${conceptDetailTab === "outgoing" ? "active" : ""}`,
        `Conexões de saída (${conceptTotalOutgoing})`
      );
      btnOut.id = "atlas-concept-tab-outgoing";
      btnOut.type = "button";
      btnOut.addEventListener("click", () => {
        conceptDetailTab = "outgoing";
        renderConceptDetailView();
      });

      const btnIn = createEl(
        "button",
        `rk-tab-btn ${conceptDetailTab === "incoming" ? "active" : ""}`,
        `Backlinks recebidos (${conceptTotalIncoming})`
      );
      btnIn.id = "atlas-concept-tab-incoming";
      btnIn.type = "button";
      btnIn.addEventListener("click", () => {
        conceptDetailTab = "incoming";
        renderConceptDetailView();
      });
      tabs.appendChild(btnOut);
      tabs.appendChild(btnIn);
      dom.paneConcepts.appendChild(tabs);

      const listWrap = createEl("div", "rk-links-list");
      listWrap.id = "atlas-concept-links-list";
      const activeList = conceptDetailTab === "outgoing" ? conceptOutgoing : conceptIncoming;
      if (activeList.length === 0) {
        listWrap.appendChild(
          createEl(
            "div",
            "rk-empty-state",
            conceptDetailTab === "outgoing"
              ? "Nenhuma conexão de saída parte deste conceito."
              : "Nenhum backlink aponta para este conceito."
          )
        );
      } else {
        activeList.forEach((link) => {
          listWrap.appendChild(
            buildSharedLinkCard(link, conceptDetailTab, () => openConceptDetail(c.id, false))
          );
        });
      }
      dom.paneConcepts.appendChild(listWrap);

      const hasMore = conceptDetailTab === "outgoing" ? conceptHasMoreOut : conceptHasMoreIn;
      if (hasMore) {
        const moreWrap = createEl("div", "rk-load-more-wrap");
        const moreBtn = createEl("button", "btn btn-sm btn-ghost", "Carregar mais");
        moreBtn.type = "button";
        moreBtn.addEventListener("click", () => openConceptDetail(c.id, true));
        moreWrap.appendChild(moreBtn);
        dom.paneConcepts.appendChild(moreWrap);
      }
    }

    async function handleDeleteConcept(conceptId, btn) {
      const lockKey = `del-concept:${conceptId}`;
      if (submitLocks.has(lockKey)) return;
      submitLocks.add(lockKey);
      if (btn) btn.disabled = true;
      try {
        await api.deleteConcept(conceptId);
        confirmingDeleteConceptId = null;
        activeConceptDetail = null;
        showBanner("Conceito excluído e vínculos diretos removidos.", "success");
        loadConceptsList(false, false);
      } catch (err) {
        showBanner(err.message || "Erro ao excluir conceito.", "error");
        if (btn) btn.disabled = false;
      } finally {
        submitLocks.delete(lockKey);
      }
    }

    // =========================================================================
    // 3. REFLEXÕES AUTORAIS E EDITOR MARKDOWN SEGURO (REFLECTIONS)
    // =========================================================================

    async function loadReflectionsList(append) {
      if (!dom.paneReflections) return;
      if (reflectionsAbortCtrl) reflectionsAbortCtrl.abort();
      reflectionsAbortCtrl = new AbortController();

      const offset = append ? reflectionsOffset : 0;
      try {
        const data = await api.listReflections(
          {
            search: reflectionsSearch,
            sort: reflectionsSort,
            include_archived: reflectionsIncludeArchived,
            limit: PAGE_LIMIT,
            offset,
          },
          reflectionsAbortCtrl.signal
        );
        const batch = Array.isArray(data && data.items) ? data.items : [];
        reflectionsItems = append ? reflectionsItems.concat(batch) : batch;
        reflectionsTotal = Number(data && data.total) || reflectionsItems.length;
        reflectionsHasMore = Boolean(data && data.has_more);
        reflectionsOffset = reflectionsItems.length;
        renderReflectionsListView();
      } catch (err) {
        if (err && err.name === "AbortError") return;
        showBanner(err.message || "Erro ao carregar reflexões.", "error");
        renderReflectionsListView();
      }
    }

    function renderReflectionsListView() {
      if (!dom.paneReflections) return;
      editorOpen = false;
      clearNode(dom.paneReflections);

      const toolbar = createEl("div", "atlas-toolbar");
      const searchInput = createEl("input", "rk-input atlas-search-input");
      searchInput.id = "atlas-reflections-search";
      searchInput.type = "search";
      searchInput.placeholder = "Localizar reflexões por título, conteúdo ou etiqueta...";
      searchInput.value = reflectionsSearch;
      searchInput.addEventListener("input", () => {
        reflectionsSearch = searchInput.value;
        if (reflectionsSearchTimer) clearTimeout(reflectionsSearchTimer);
        reflectionsSearchTimer = setTimeout(() => loadReflectionsList(false), DEBOUNCE_MS);
      });
      toolbar.appendChild(searchInput);

      const sortSel = createEl("select", "rk-select atlas-sort-select");
      sortSel.id = "atlas-reflections-sort";
      [
        ["updated_desc", "Atualizadas recentemente"],
        ["created_desc", "Data de criação"],
        ["title_asc", "Título (A–Z)"],
      ].forEach(([val, lbl]) => {
        const opt = document.createElement("option");
        opt.value = val;
        opt.textContent = lbl;
        if (val === reflectionsSort) opt.selected = true;
        sortSel.appendChild(opt);
      });
      sortSel.addEventListener("change", () => {
        reflectionsSort = sortSel.value;
        loadReflectionsList(false);
      });
      toolbar.appendChild(sortSel);

      const archLabel = createEl("label", "rk-checkbox-row");
      const archCheck = document.createElement("input");
      archCheck.id = "atlas-reflections-include-archived";
      archCheck.type = "checkbox";
      archCheck.checked = reflectionsIncludeArchived;
      archCheck.addEventListener("change", () => {
        reflectionsIncludeArchived = archCheck.checked;
        loadReflectionsList(false);
      });
      archLabel.appendChild(archCheck);
      archLabel.appendChild(createEl("span", "", "Incluir arquivadas"));
      toolbar.appendChild(archLabel);

      const btnNew = createEl("button", "btn btn-sm btn-primary", "+ Nova reflexão");
      btnNew.id = "atlas-btn-new-reflection";
      btnNew.type = "button";
      btnNew.addEventListener("click", () => openNewReflectionEditor());
      toolbar.appendChild(btnNew);
      dom.paneReflections.appendChild(toolbar);

      if (reflectionsItems.length === 0) {
        dom.paneReflections.appendChild(
          createEl(
            "div",
            "rk-empty-state",
            reflectionsSearch
              ? `Nenhuma reflexão encontrada para "${reflectionsSearch}".`
              : "Nenhuma reflexão autoral criada ainda. Clique em '+ Nova reflexão' para escrever em Markdown."
          )
        );
        return;
      }

      const list = createEl("div", "atlas-cards-grid");
      list.id = "atlas-reflections-grid";
      reflectionsItems.forEach((r) => {
        const card = createEl("article", "atlas-entity-card");
        card.dataset.reflectionId = r.id;

        const top = createEl("div", "atlas-entity-card-header");
        const titleBtn = createEl("button", "atlas-card-title-btn", r.title);
        titleBtn.type = "button";
        titleBtn.addEventListener("click", () => openReflectionEditorById(r.id));
        top.appendChild(titleBtn);

        const badges = createEl("div", "rk-badges-row");
        if (r.is_pinned) badges.appendChild(createEl("span", "rk-badge rk-badge-relation", "Fixada"));
        if (r.is_archived) badges.appendChild(createEl("span", "rk-badge", "Arquivada"));
        badges.appendChild(createEl("span", "rk-badge", `${r.references_count || 0} ref.`));
        badges.appendChild(createEl("span", "rk-badge", `${r.backlinks_count || 0} backlink(s)`));
        top.appendChild(badges);
        card.appendChild(top);

        const previewStr = truncateText(r.body_markdown || "", 190);
        if (previewStr) {
          card.appendChild(createEl("p", "atlas-card-desc", previewStr));
        }

        if (r.primary_book_title) {
          card.appendChild(createEl("div", "rk-link-target-meta", `Obra principal: ${r.primary_book_title}`));
        }

        if (Array.isArray(r.tags) && r.tags.length > 0) {
          const tRow = createEl("div", "rk-tags-row");
          r.tags.forEach((t) => tRow.appendChild(createEl("span", "rk-tag-pill", `#${t}`)));
          card.appendChild(tRow);
        }

        const foot = createEl("div", "atlas-card-footer");
        const btnOpen = createEl("button", "btn btn-sm btn-secondary atlas-btn-open-reflection", "Abrir editor");
        btnOpen.type = "button";
        btnOpen.addEventListener("click", () => openReflectionEditorById(r.id));
        foot.appendChild(btnOpen);
        card.appendChild(foot);

        list.appendChild(card);
      });
      dom.paneReflections.appendChild(list);

      if (reflectionsHasMore) {
        const moreWrap = createEl("div", "rk-load-more-wrap");
        const btnMore = createEl("button", "btn btn-sm btn-ghost", "Carregar mais reflexões");
        btnMore.id = "atlas-reflections-load-more";
        btnMore.type = "button";
        btnMore.addEventListener("click", () => loadReflectionsList(true));
        moreWrap.appendChild(btnMore);
        dom.paneReflections.appendChild(moreWrap);
      }
    }

    function openNewReflectionEditor() {
      currentReflection = null;
      loadedUpdatedAt = null;
      concurrencyWarningShown = false;
      reflectionReferences = [];
      reflectionBacklinks = [];
      confirmingDeleteReflection = false;
      refLookupTarget = null;
      editorOpen = true;
      setEditorDirty(false);
      renderReflectionEditorView();
      setEditorSaveBadge("new", "Nova reflexão");
    }

    async function openReflectionEditorById(reflectionId) {
      if (!reflectionId) return;
      if (reflectionsAbortCtrl) reflectionsAbortCtrl.abort();
      reflectionsAbortCtrl = new AbortController();
      try {
        const data = await api.getReflection(reflectionId, reflectionsAbortCtrl.signal);
        currentReflection = data.reflection;
        loadedUpdatedAt = currentReflection.updated_at || null;
        concurrencyWarningShown = false;
        reflectionReferences = Array.isArray(data.references) ? data.references : [];
        reflectionBacklinks = Array.isArray(data.backlinks) ? data.backlinks : [];
        confirmingDeleteReflection = false;
        refLookupTarget = null;
        editorOpen = true;
        setEditorDirty(false);
        renderReflectionEditorView();
        setEditorSaveBadge("saved", "Salva");
      } catch (err) {
        if (err && err.name === "AbortError") return;
        showBanner(err.message || "Reflexão não encontrada.", "error");
        loadReflectionsList(false);
      }
    }

    function renderReflectionEditorView() {
      if (!dom.paneReflections) return;
      clearNode(dom.paneReflections);

      const isPersisted = Boolean(currentReflection && currentReflection.id);

      // Top bar
      const topBar = createEl("div", "atlas-detail-topbar");
      const backBtn = createEl("button", "btn btn-sm btn-ghost", "← Voltar às reflexões");
      backBtn.id = "atlas-btn-back-reflections";
      backBtn.type = "button";
      backBtn.addEventListener("click", () => {
        confirmLeaveIfDirty(() => {
          editorOpen = false;
          loadReflectionsList(false);
        });
      });
      topBar.appendChild(backBtn);

      const rightControls = createEl("div", "atlas-editor-top-controls");
      dom.editorStatusBadge = createEl(
        "span",
        `atlas-save-badge atlas-save-${editorSaveState}`,
        isPersisted ? "Salva" : "Nova reflexão"
      );
      dom.editorStatusBadge.id = "atlas-reflection-save-status";
      rightControls.appendChild(dom.editorStatusBadge);

      dom.editorWordCounter = createEl("span", "atlas-word-counter", "0 palavras · 0/50000");
      dom.editorWordCounter.id = "atlas-reflection-word-counter";
      rightControls.appendChild(dom.editorWordCounter);

      dom.btnSaveReflection = createEl("button", "btn btn-sm btn-primary", "Salvar reflexão");
      dom.btnSaveReflection.id = "atlas-btn-save-reflection";
      dom.btnSaveReflection.type = "button";
      dom.btnSaveReflection.addEventListener("click", () => handleSaveReflection());
      rightControls.appendChild(dom.btnSaveReflection);

      if (isPersisted) {
        const btnExploreRefMap = createEl("button", "btn btn-sm btn-secondary", "Explorar no Mapa");
        btnExploreRefMap.id = "atlas-btn-explore-reflection-graph";
        btnExploreRefMap.type = "button";
        btnExploreRefMap.addEventListener("click", () => {
          requestSubtabSwitch("graph", {
            graphEntityType: "reflection",
            graphEntityId: currentReflection.id,
          });
        });
        rightControls.appendChild(btnExploreRefMap);

        if (confirmingDeleteReflection) {
          const btnConfirmDel = createEl("button", "btn btn-sm btn-danger", "Confirmar exclusão");
          btnConfirmDel.id = "atlas-btn-confirm-delete-reflection";
          btnConfirmDel.type = "button";
          btnConfirmDel.addEventListener("click", () => handleDeleteReflection(currentReflection.id, btnConfirmDel));
          const btnCancelDel = createEl("button", "btn btn-sm btn-ghost", "Cancelar");
          btnCancelDel.type = "button";
          btnCancelDel.addEventListener("click", () => {
            confirmingDeleteReflection = false;
            renderReflectionEditorView();
          });
          rightControls.appendChild(btnConfirmDel);
          rightControls.appendChild(btnCancelDel);
        } else {
          const btnDel = createEl("button", "btn btn-sm btn-danger", "Excluir");
          btnDel.id = "atlas-btn-delete-reflection";
          btnDel.type = "button";
          btnDel.addEventListener("click", () => {
            confirmingDeleteReflection = true;
            renderReflectionEditorView();
          });
          rightControls.appendChild(btnDel);
        }
      }
      topBar.appendChild(rightControls);
      dom.paneReflections.appendChild(topBar);

      // Metadata inputs: Title, tags, pinned, archived
      const metaCard = createEl("div", "atlas-editor-meta-card");
      const lblTitle = createEl("label", "rk-field-label", "Título da reflexão *");
      dom.editorTitleInput = createEl("input", "rk-input atlas-reflection-title-input");
      dom.editorTitleInput.id = "atlas-reflection-title";
      dom.editorTitleInput.type = "text";
      dom.editorTitleInput.required = true;
      dom.editorTitleInput.maxLength = 300;
      dom.editorTitleInput.placeholder = "Ex.: Ellul e Heidegger — Duas leituras sobre a técnica moderna";
      dom.editorTitleInput.value = isPersisted ? currentReflection.title : "";
      dom.editorTitleInput.addEventListener("input", () => setEditorDirty(true));
      lblTitle.appendChild(dom.editorTitleInput);
      metaCard.appendChild(lblTitle);

      const metaRow2 = createEl("div", "atlas-editor-meta-row");
      const lblTags = createEl("label", "rk-field-label", "Etiquetas (separadas por vírgula)");
      dom.editorTagsInput = createEl("input", "rk-input");
      dom.editorTagsInput.id = "atlas-reflection-tags";
      dom.editorTagsInput.type = "text";
      dom.editorTagsInput.maxLength = 300;
      dom.editorTagsInput.placeholder = "ensaio, filosofia, síntese";
      dom.editorTagsInput.value =
        isPersisted && Array.isArray(currentReflection.tags) ? currentReflection.tags.join(", ") : "";
      dom.editorTagsInput.addEventListener("input", () => setEditorDirty(true));
      lblTags.appendChild(dom.editorTagsInput);
      metaRow2.appendChild(lblTags);

      const flagsWrap = createEl("div", "atlas-editor-flags");
      const pinLbl = createEl("label", "rk-checkbox-row");
      dom.editorPinCheck = document.createElement("input");
      dom.editorPinCheck.id = "atlas-reflection-pinned";
      dom.editorPinCheck.type = "checkbox";
      dom.editorPinCheck.checked = isPersisted ? Boolean(currentReflection.is_pinned) : false;
      dom.editorPinCheck.addEventListener("change", () => setEditorDirty(true));
      pinLbl.appendChild(dom.editorPinCheck);
      pinLbl.appendChild(createEl("span", "", "Fixar reflexão"));
      flagsWrap.appendChild(pinLbl);

      const archLbl = createEl("label", "rk-checkbox-row");
      dom.editorArchCheck = document.createElement("input");
      dom.editorArchCheck.id = "atlas-reflection-archived";
      dom.editorArchCheck.type = "checkbox";
      dom.editorArchCheck.checked = isPersisted ? Boolean(currentReflection.is_archived) : false;
      dom.editorArchCheck.addEventListener("change", () => setEditorDirty(true));
      archLbl.appendChild(dom.editorArchCheck);
      archLbl.appendChild(createEl("span", "", "Arquivar"));
      flagsWrap.appendChild(archLbl);

      metaRow2.appendChild(flagsWrap);
      metaCard.appendChild(metaRow2);
      dom.paneReflections.appendChild(metaCard);

      // Markdown Toolbar + Mode Switcher (Editar / Visualizar / Dividido)
      const mdToolbar = createEl("div", "atlas-md-toolbar");
      const formatBtns = createEl("div", "atlas-md-format-btns");

      const snippets = [
        { id: "h2", label: "Título", prefix: "## ", suffix: "" },
        { id: "bold", label: "Negrito", prefix: "**", suffix: "**" },
        { id: "italic", label: "Itálico", prefix: "*", suffix: "*" },
        { id: "list", label: "Lista", prefix: "- ", suffix: "" },
        { id: "quote", label: "Citação", prefix: "> ", suffix: "" },
        { id: "code", label: "Código", prefix: "`", suffix: "`" },
      ];
      snippets.forEach((sn) => {
        const b = createEl("button", "btn btn-sm btn-ghost atlas-md-tool-btn", sn.label);
        b.type = "button";
        b.dataset.mdTool = sn.id;
        b.addEventListener("click", () => insertMarkdownSyntax(sn.prefix, sn.suffix));
        formatBtns.appendChild(b);
      });

      const btnAssistRef = createEl("button", "btn btn-sm btn-secondary", "+ Inserir referência assistida");
      btnAssistRef.id = "atlas-btn-insert-reference";
      btnAssistRef.type = "button";
      formatBtns.appendChild(btnAssistRef);
      mdToolbar.appendChild(formatBtns);

      const modeGroup = createEl("div", "atlas-md-mode-group");
      [
        ["edit", "Editar"],
        ["preview", "Visualizar"],
        ["split", "Dividido"],
      ].forEach(([mVal, mLbl]) => {
        const mb = createEl("button", `rk-tab-btn ${editorMode === mVal ? "active" : ""}`, mLbl);
        mb.id = `atlas-md-mode-${mVal}`;
        mb.type = "button";
        mb.addEventListener("click", () => {
          editorMode = mVal;
          applyEditorViewMode();
        });
        modeGroup.appendChild(mb);
      });
      mdToolbar.appendChild(modeGroup);
      dom.paneReflections.appendChild(mdToolbar);

      // Assisted Reference Panel (collapsible)
      dom.assistRefPanel = buildAssistedReferencePanel();
      dom.assistRefPanel.classList.add("hidden");
      btnAssistRef.addEventListener("click", () => {
        dom.assistRefPanel.classList.toggle("hidden");
      });
      dom.paneReflections.appendChild(dom.assistRefPanel);

      // Editor & Safe Preview Workspace
      dom.editorWorkspace = createEl("div", `atlas-editor-workspace mode-${editorMode}`);
      const editPane = createEl("div", "atlas-editor-pane-write");
      const editLabel = createEl("label", "sr-only", "Corpo Markdown da reflexão");
      editLabel.setAttribute("for", "atlas-reflection-body");
      editPane.appendChild(editLabel);

      dom.editorBodyInput = createEl("textarea", "rk-textarea atlas-reflection-textarea");
      dom.editorBodyInput.id = "atlas-reflection-body";
      dom.editorBodyInput.rows = 14;
      dom.editorBodyInput.maxLength = 50000;
      dom.editorBodyInput.placeholder =
        "Escreva sua reflexão autoral em Markdown (# títulos, **negrito**, *itálico*, > citações, listas e blocos de código)...";
      dom.editorBodyInput.value = isPersisted ? (currentReflection.body_markdown || "") : "";
      dom.editorBodyInput.addEventListener("input", () => {
        setEditorDirty(true);
        updateEditorMetricsAndPreview();
      });
      editPane.appendChild(dom.editorBodyInput);

      const previewPane = createEl("div", "atlas-editor-pane-preview");
      previewPane.appendChild(createEl("div", "atlas-preview-caption", "Visualização formatada segura"));
      dom.editorPreviewBox = createEl("div", "atlas-md-preview-body");
      dom.editorPreviewBox.id = "atlas-reflection-preview";
      previewPane.appendChild(dom.editorPreviewBox);

      dom.editorWorkspace.appendChild(editPane);
      dom.editorWorkspace.appendChild(previewPane);
      dom.paneReflections.appendChild(dom.editorWorkspace);

      updateEditorMetricsAndPreview();
      applyEditorViewMode();

      // Structured References & Backlinks Section for this Reflection
      const refSec = createEl("section", "rk-section atlas-reflection-links-section");
      const refTabs = createEl("div", "rk-tabs");
      const tabRefs = createEl(
        "button",
        `rk-tab-btn ${refPanelTab === "references" ? "active" : ""}`,
        `Referências citadas (${reflectionReferences.length})`
      );
      tabRefs.id = "atlas-reflection-tab-references";
      tabRefs.type = "button";
      tabRefs.addEventListener("click", () => {
        refPanelTab = "references";
        renderReflectionLinksList();
      });

      const tabBks = createEl(
        "button",
        `rk-tab-btn ${refPanelTab === "backlinks" ? "active" : ""}`,
        `Backlinks recebidos (${reflectionBacklinks.length})`
      );
      tabBks.id = "atlas-reflection-tab-backlinks";
      tabBks.type = "button";
      tabBks.addEventListener("click", () => {
        refPanelTab = "backlinks";
        renderReflectionLinksList();
      });
      refTabs.appendChild(tabRefs);
      refTabs.appendChild(tabBks);
      refSec.appendChild(refTabs);

      dom.reflectionLinksList = createEl("div", "rk-links-list");
      dom.reflectionLinksList.id = "atlas-reflection-links-list";
      refSec.appendChild(dom.reflectionLinksList);
      dom.paneReflections.appendChild(refSec);

      renderReflectionLinksList();
    }

    function applyEditorViewMode() {
      if (!dom.editorWorkspace) return;
      dom.editorWorkspace.className = `atlas-editor-workspace mode-${editorMode}`;
      ["edit", "preview", "split"].forEach((m) => {
        const btn = document.getElementById(`atlas-md-mode-${m}`);
        if (btn) btn.classList.toggle("active", editorMode === m);
      });
      updateEditorMetricsAndPreview();
    }

    function updateEditorMetricsAndPreview() {
      const bodyText = dom.editorBodyInput ? dom.editorBodyInput.value : "";
      if (dom.editorWordCounter) {
        const words = countWords(bodyText);
        dom.editorWordCounter.textContent = `${words} palavra(s) · ${bodyText.length}/50000`;
      }
      if (dom.editorPreviewBox && (editorMode === "preview" || editorMode === "split")) {
        if (dom.editorPreviewBox.dataset.lastRenderedMd !== bodyText) {
          renderSafeMarkdownToDom(dom.editorPreviewBox, bodyText);
          dom.editorPreviewBox.dataset.lastRenderedMd = bodyText;
        }
      }
    }

    function insertMarkdownSyntax(prefix, suffix) {
      if (!dom.editorBodyInput) return;
      const ta = dom.editorBodyInput;
      const start = ta.selectionStart || 0;
      const end = ta.selectionEnd || 0;
      const selected = ta.value.slice(start, end) || "texto";
      const replacement = `${prefix}${selected}${suffix}`;
      ta.value = ta.value.slice(0, start) + replacement + ta.value.slice(end);
      ta.focus();
      ta.setSelectionRange(start + prefix.length, start + prefix.length + selected.length);
      setEditorDirty(true);
      updateEditorMetricsAndPreview();
    }

    function buildAssistedReferencePanel() {
      const box = createEl("div", "rk-subform atlas-assist-ref-box");
      box.id = "atlas-assisted-reference-panel";
      box.appendChild(createEl("div", "rk-subform-title", "Inserção assistida de referência canônica (UUID)"));

      if (!currentReflection || !currentReflection.id) {
        box.appendChild(
          createEl(
            "p",
            "rk-muted-text",
            "Salve a reflexão primeiro para vincular referências rastreáveis em knowledge_links."
          )
        );
        const btnSaveNow = createEl("button", "btn btn-sm btn-primary", "Salvar reflexão agora e continuar");
        btnSaveNow.id = "atlas-btn-save-before-reference";
        btnSaveNow.type = "button";
        btnSaveNow.addEventListener("click", async () => {
          const ok = await handleSaveReflection();
          if (ok && dom.assistRefPanel) {
            dom.assistRefPanel.classList.remove("hidden");
          }
        });
        box.appendChild(btnSaveNow);
        return box;
      }

      const form = buildEntityNewLinkForm(
        "reflection",
        currentReflection.id,
        currentReflection.title,
        async (createdLink) => {
          // Optionally insert a clean Markdown blockquote citation at cursor
          if (createdLink && createdLink.target && createdLink.target.available && dom.editorBodyInput) {
            const t = createdLink.target;
            const loc = formatLocationMeta(t);
            const citeLine = `\n> Referência (${createdLink.outgoing_label || "Referencia"}): **${t.label || t.title}**${loc ? ` (${loc})` : ""}\n`;
            dom.editorBodyInput.value += citeLine;
            setEditorDirty(true);
            updateEditorMetricsAndPreview();
          }
          const refreshed = await api.getReflection(currentReflection.id);
          reflectionReferences = Array.isArray(refreshed.references) ? refreshed.references : [];
          reflectionBacklinks = Array.isArray(refreshed.backlinks) ? refreshed.backlinks : [];
          renderReflectionLinksList();
        },
        "references"
      );
      box.appendChild(form);
      return box;
    }

    function renderReflectionLinksList() {
      if (!dom.reflectionLinksList) return;
      clearNode(dom.reflectionLinksList);

      const tabRefs = document.getElementById("atlas-reflection-tab-references");
      const tabBks = document.getElementById("atlas-reflection-tab-backlinks");
      if (tabRefs) {
        tabRefs.textContent = `Referências citadas (${reflectionReferences.length})`;
        tabRefs.classList.toggle("active", refPanelTab === "references");
      }
      if (tabBks) {
        tabBks.textContent = `Backlinks recebidos (${reflectionBacklinks.length})`;
        tabBks.classList.toggle("active", refPanelTab === "backlinks");
      }

      const items = refPanelTab === "references" ? reflectionReferences : reflectionBacklinks;
      const direction = refPanelTab === "references" ? "outgoing" : "incoming";

      if (items.length === 0) {
        dom.reflectionLinksList.appendChild(
          createEl(
            "div",
            "rk-empty-state",
            refPanelTab === "references"
              ? "Nenhuma referência vinculada a esta reflexão ainda."
              : "Nenhum backlink aponta para esta reflexão ainda."
          )
        );
        return;
      }

      items.forEach((link) => {
        dom.reflectionLinksList.appendChild(
          buildSharedLinkCard(link, direction, async () => {
            if (currentReflection && currentReflection.id) {
              const refreshed = await api.getReflection(currentReflection.id);
              reflectionReferences = Array.isArray(refreshed.references) ? refreshed.references : [];
              reflectionBacklinks = Array.isArray(refreshed.backlinks) ? refreshed.backlinks : [];
              renderReflectionLinksList();
            }
          })
        );
      });
    }

    async function handleSaveReflection() {
      if (submitLocks.has("save-reflection")) return false;
      const title = dom.editorTitleInput ? dom.editorTitleInput.value.trim() : "";
      if (!title) {
        showBanner("Informe o título da reflexão antes de salvar.", "error");
        setEditorSaveBadge("error", "Erro ao salvar");
        if (dom.editorTitleInput) dom.editorTitleInput.focus();
        return false;
      }

      const bodyMarkdown = dom.editorBodyInput ? dom.editorBodyInput.value : "";
      const tags = (dom.editorTagsInput ? dom.editorTagsInput.value : "")
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean)
        .slice(0, 30);
      const isPinned = Boolean(dom.editorPinCheck && dom.editorPinCheck.checked);
      const isArchived = Boolean(dom.editorArchCheck && dom.editorArchCheck.checked);

      submitLocks.add("save-reflection");
      if (dom.btnSaveReflection) dom.btnSaveReflection.disabled = true;
      setEditorSaveBadge("saving", "Salvando…");

      try {
        if (currentReflection && currentReflection.id) {
          const updatePayload = {
            title,
            body_markdown: bodyMarkdown,
            tags,
            is_pinned: isPinned,
            is_archived: isArchived,
          };
          if (loadedUpdatedAt && !concurrencyWarningShown) {
            updatePayload.expected_updated_at = loadedUpdatedAt;
          }

          const res = await api.updateReflection(currentReflection.id, updatePayload);
          currentReflection = res.reflection;
          loadedUpdatedAt = currentReflection.updated_at || null;
          concurrencyWarningShown = false;
          setEditorDirty(false);
          setEditorSaveBadge("saved", "Salva");
          showBanner("Reflexão salva com sucesso.", "success");
          return true;
        } else {
          const res = await api.createReflection({
            title,
            body_markdown: bodyMarkdown,
            tags,
            is_pinned: isPinned,
            is_archived: isArchived,
          });
          currentReflection = res.reflection;
          loadedUpdatedAt = currentReflection.updated_at || null;
          concurrencyWarningShown = false;
          setEditorDirty(false);
          renderReflectionEditorView();
          setEditorSaveBadge("saved", "Salva");
          showBanner("Reflexão criada e salva no Atlas.", "success");
          return true;
        }
      } catch (err) {
        if (err && err.status === 409 && currentReflection && currentReflection.id) {
          concurrencyWarningShown = true;
          setEditorSaveBadge("dirty", "Conflito detectado");
          showBanner(
            err.message ||
              "Atenção: esta reflexão foi modificada em outra aba ou sessão. Seu texto local foi preservado; clique em 'Salvar reflexão' novamente para confirmar a sobrescrita.",
            "error"
          );
          return false;
        }
        setEditorSaveBadge("error", "Erro ao salvar");
        showBanner(err.message || "Não foi possível salvar a reflexão. Seu conteúdo local foi preservado.", "error");
        return false;
      } finally {
        submitLocks.delete("save-reflection");
        if (dom.btnSaveReflection) dom.btnSaveReflection.disabled = false;
      }
    }

    async function handleDeleteReflection(reflectionId, btn) {
      const lockKey = `del-ref:${reflectionId}`;
      if (submitLocks.has(lockKey)) return;
      submitLocks.add(lockKey);
      if (btn) btn.disabled = true;
      try {
        await api.deleteReflection(reflectionId);
        setEditorDirty(false);
        editorOpen = false;
        currentReflection = null;
        showBanner("Reflexão excluída do Atlas.", "success");
        loadReflectionsList(false);
      } catch (err) {
        showBanner(err.message || "Não foi possível excluir a reflexão.", "error");
        if (btn) btn.disabled = false;
      } finally {
        submitLocks.delete(lockKey);
      }
    }

    // =========================================================================
    // 4. EXPLORADOR TEXTUAL DE CONEXÕES (CONNECTIONS)
    // =========================================================================

    async function loadConnectionsExplorer(append, openCreateForm) {
      if (!dom.paneConnections) return;
      if (linksAbortCtrl) linksAbortCtrl.abort();
      linksAbortCtrl = new AbortController();

      const offset = append ? linksOffset : 0;
      try {
        const data = await api.listLinks(
          {
            relation_type: linksRelationFilter || undefined,
            entity_type: linksEntityTypeFilter || undefined,
            sort: linksSort,
            limit: PAGE_LIMIT,
            offset,
          },
          linksAbortCtrl.signal
        );
        const batch = Array.isArray(data && data.items) ? data.items : [];
        linksItems = append ? linksItems.concat(batch) : batch;
        linksTotal = Number(data && data.total) || linksItems.length;
        linksHasMore = Boolean(data && data.has_more);
        linksOffset = linksItems.length;
        renderConnectionsExplorerView(openCreateForm);
      } catch (err) {
        if (err && err.name === "AbortError") return;
        showBanner(err.message || "Erro ao carregar conexões.", "error");
        renderConnectionsExplorerView(openCreateForm);
      }
    }

    function renderConnectionsExplorerView(openCreateForm) {
      if (!dom.paneConnections) return;
      clearNode(dom.paneConnections);

      const toolbar = createEl("div", "atlas-toolbar");

      const relSel = createEl("select", "rk-select atlas-sort-select");
      relSel.id = "atlas-connections-relation-filter";
      const allRelOpt = document.createElement("option");
      allRelOpt.value = "";
      allRelOpt.textContent = "Todas as 8 relações";
      relSel.appendChild(allRelOpt);
      RELATION_TYPES.forEach((r) => {
        const opt = document.createElement("option");
        opt.value = r.value;
        opt.textContent = r.outgoing;
        if (r.value === linksRelationFilter) opt.selected = true;
        relSel.appendChild(opt);
      });
      relSel.addEventListener("change", () => {
        linksRelationFilter = relSel.value;
        loadConnectionsExplorer(false, false);
      });
      toolbar.appendChild(relSel);

      const typeSel = createEl("select", "rk-select atlas-sort-select");
      typeSel.id = "atlas-connections-entity-filter";
      [
        ["", "Todos os tipos de entidade"],
        ["concept", "Conceito"],
        ["reflection", "Reflexão"],
        ["highlight", "Destaque"],
        ["note", "Nota"],
        ["book", "Livro"],
      ].forEach(([val, lbl]) => {
        const opt = document.createElement("option");
        opt.value = val;
        opt.textContent = lbl;
        if (val === linksEntityTypeFilter) opt.selected = true;
        typeSel.appendChild(opt);
      });
      typeSel.addEventListener("change", () => {
        linksEntityTypeFilter = typeSel.value;
        loadConnectionsExplorer(false, false);
      });
      toolbar.appendChild(typeSel);

      const btnNew = createEl("button", "btn btn-sm btn-primary", "+ Nova conexão independente");
      btnNew.id = "atlas-btn-new-connection";
      btnNew.type = "button";
      toolbar.appendChild(btnNew);
      dom.paneConnections.appendChild(toolbar);

      const createForm = buildIndependentConnectionForm(() => loadConnectionsExplorer(false, false));
      if (!openCreateForm) createForm.classList.add("hidden");
      btnNew.addEventListener("click", () => createForm.classList.toggle("hidden"));
      dom.paneConnections.appendChild(createForm);

      if (linksItems.length === 0) {
        dom.paneConnections.appendChild(
          createEl("div", "rk-empty-state", "Nenhuma conexão semântica encontrada com os filtros atuais.")
        );
        return;
      }

      const list = createEl("div", "rk-links-list");
      list.id = "atlas-connections-list";
      linksItems.forEach((link) => {
        list.appendChild(buildIndependentLinkCard(link, () => loadConnectionsExplorer(false, false)));
      });
      dom.paneConnections.appendChild(list);

      if (linksHasMore) {
        const moreWrap = createEl("div", "rk-load-more-wrap");
        const btnMore = createEl("button", "btn btn-sm btn-ghost", "Carregar mais conexões");
        btnMore.id = "atlas-connections-load-more";
        btnMore.type = "button";
        btnMore.addEventListener("click", () => loadConnectionsExplorer(true, false));
        moreWrap.appendChild(btnMore);
        dom.paneConnections.appendChild(moreWrap);
      }
    }

    // =========================================================================
    // COMPONENTES COMPARTILHADOS DE CONEXÕES E NAVEGAÇÃO NO ATLAS
    // =========================================================================

    function buildEntityNewLinkForm(sourceType, sourceId, sourceLabel, onCreated, defaultRelation) {
      let selectedTargetItem = null;
      let lookupCtrl = null;
      let lookupTimer = null;

      const form = createEl("form", "rk-subform");
      form.appendChild(createEl("div", "rk-subform-title", `Conectar "${truncateText(sourceLabel, 60)}" a outra entidade`));

      const lblType = createEl("label", "rk-field-label", "Tipo de destino");
      const typeSelect = createEl("select", "rk-select atlas-link-target-type");
      [
        ["", "Todos os tipos"],
        ["concept", "Conceito"],
        ["reflection", "Reflexão"],
        ["book", "Livro"],
        ["highlight", "Destaque"],
        ["note", "Nota"],
      ].forEach(([val, lbl]) => {
        const opt = document.createElement("option");
        opt.value = val;
        opt.textContent = lbl;
        typeSelect.appendChild(opt);
      });
      lblType.appendChild(typeSelect);
      form.appendChild(lblType);

      const lookupWrap = createEl("div", "rk-lookup-wrap");
      const searchIn = createEl("input", "rk-input atlas-link-target-search");
      searchIn.type = "search";
      searchIn.placeholder = "Digite para localizar destino...";
      searchIn.autocomplete = "off";
      const resultsBox = createEl("div", "rk-lookup-results hidden");
      lookupWrap.appendChild(searchIn);
      lookupWrap.appendChild(resultsBox);
      form.appendChild(lookupWrap);

      const selectedBox = createEl("div", "rk-selected-target hidden");
      form.appendChild(selectedBox);

      const lblRel = createEl("label", "rk-field-label", "Tipo de relação *");
      const relSelect = createEl("select", "rk-select atlas-link-relation-select");
      RELATION_TYPES.forEach((r) => {
        const opt = document.createElement("option");
        opt.value = r.value;
        opt.textContent = r.outgoing;
        relSelect.appendChild(opt);
      });
      relSelect.value = defaultRelation || "relates_to";
      lblRel.appendChild(relSelect);
      form.appendChild(lblRel);

      const dirPreview = createEl("div", "rk-direction-preview");
      form.appendChild(dirPreview);

      const lblRat = createEl("label", "rk-field-label", "Justificativa (opcional)");
      const ratIn = createEl("textarea", "rk-textarea atlas-link-rationale-input");
      ratIn.rows = 2;
      ratIn.maxLength = 2000;
      lblRat.appendChild(ratIn);
      form.appendChild(lblRat);

      const actions = createEl("div", "rk-inline-form-actions");
      const submitBtn = createEl("button", "btn btn-sm btn-primary atlas-link-submit-btn", "Criar conexão");
      submitBtn.type = "submit";
      actions.appendChild(submitBtn);
      form.appendChild(actions);

      function updatePreview() {
        const rDef = RELATION_MAP[relSelect.value] || RELATION_MAP.relates_to;
        const tText = selectedTargetItem
          ? `${ENTITY_TYPE_LABELS[selectedTargetItem.type] || selectedTargetItem.type}: ${truncateText(selectedTargetItem.label || selectedTargetItem.title || "", 40)}`
          : "Selecione um destino";
        dirPreview.textContent = `${ENTITY_TYPE_LABELS[sourceType] || sourceType}: ${truncateText(sourceLabel, 36)} — ${rDef.outgoing} → ${tText}`;
      }

      function renderSelected() {
        clearNode(selectedBox);
        if (!selectedTargetItem) {
          selectedBox.classList.add("hidden");
          updatePreview();
          return;
        }
        selectedBox.classList.remove("hidden");
        const pill = createEl("div", "rk-selected-target-pill");
        pill.appendChild(createEl("span", "rk-badge rk-badge-type", ENTITY_TYPE_LABELS[selectedTargetItem.type] || selectedTargetItem.type));
        pill.appendChild(createEl("span", "rk-selected-target-label", truncateText(selectedTargetItem.label || selectedTargetItem.title || "", 80)));
        const clr = createEl("button", "rk-icon-btn", "×");
        clr.type = "button";
        clr.addEventListener("click", () => {
          selectedTargetItem = null;
          renderSelected();
        });
        pill.appendChild(clr);
        selectedBox.appendChild(pill);
        updatePreview();
      }

      async function doSearch(q) {
        if (lookupCtrl) lookupCtrl.abort();
        lookupCtrl = new AbortController();
        try {
          const data = await api.lookupEntities(
            { q, entity_type: typeSelect.value || undefined, limit: 12 },
            lookupCtrl.signal
          );
          const items = (Array.isArray(data && data.items) ? data.items : []).filter(
            (it) => !(it.type === sourceType && String(it.id) === String(sourceId))
          );
          clearNode(resultsBox);
          resultsBox.classList.remove("hidden");
          if (items.length === 0) {
            resultsBox.appendChild(createEl("div", "rk-lookup-empty", "Nenhum destino encontrado."));
            return;
          }
          items.forEach((item) => {
            const b = createEl("button", "rk-lookup-item");
            b.type = "button";
            const main = createEl("div", "rk-lookup-item-main");
            main.appendChild(createEl("span", "rk-badge rk-badge-type", ENTITY_TYPE_LABELS[item.type] || item.type));
            main.appendChild(createEl("span", "rk-lookup-item-title", truncateText(item.label || item.title || "Item", 80)));
            b.appendChild(main);
            b.addEventListener("click", () => {
              selectedTargetItem = item;
              renderSelected();
              clearNode(resultsBox);
              resultsBox.classList.add("hidden");
            });
            resultsBox.appendChild(b);
          });
        } catch (_) {}
      }

      searchIn.addEventListener("input", () => {
        if (lookupTimer) clearTimeout(lookupTimer);
        if (lookupCtrl) lookupCtrl.abort();
        clearNode(resultsBox);
        resultsBox.classList.remove("hidden");
        resultsBox.appendChild(createEl("div", "rk-lookup-empty", "Buscando…"));
        lookupTimer = setTimeout(() => doSearch(searchIn.value), DEBOUNCE_MS);
      });
      searchIn.addEventListener("focus", () => doSearch(searchIn.value));
      typeSelect.addEventListener("change", () => doSearch(searchIn.value));
      relSelect.addEventListener("change", updatePreview);
      updatePreview();

      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!selectedTargetItem || submitLocks.has("atlas-entity-link")) return;
        submitLocks.add("atlas-entity-link");
        submitBtn.disabled = true;
        try {
          const res = await api.createLink({
            source_type: sourceType,
            source_id: sourceId,
            target_type: selectedTargetItem.type,
            target_id: selectedTargetItem.id,
            relation_type: relSelect.value || "relates_to",
            rationale: ratIn.value.trim(),
          });
          selectedTargetItem = null;
          renderSelected();
          searchIn.value = "";
          ratIn.value = "";
          showBanner("Conexão criada no Atlas.", "success");
          if (typeof onCreated === "function") await onCreated(res && res.link);
        } catch (err) {
          showBanner(err.message || "Não foi possível criar a conexão.", "error");
        } finally {
          submitLocks.delete("atlas-entity-link");
          submitBtn.disabled = false;
        }
      });

      return form;
    }

    function buildIndependentConnectionForm(onCreated) {
      let srcItem = null;
      let tgtItem = null;

      const form = createEl("form", "rk-subform");
      form.id = "atlas-independent-link-form";
      form.appendChild(createEl("div", "rk-subform-title", "Criar conexão semântica entre duas entidades"));

      function createEndpointPicker(rolePrefix, captionText, onSelect) {
        let ctrl = null;
        let timer = null;
        const wrap = createEl("div", "atlas-endpoint-picker");
        wrap.appendChild(createEl("div", "rk-origin-caption", captionText));

        const searchIn = createEl("input", "rk-input");
        searchIn.id = `atlas-${rolePrefix}-search`;
        searchIn.type = "search";
        searchIn.placeholder = `Buscar ${captionText.toLowerCase()} (conceito, reflexão, livro, destaque ou nota)...`;
        searchIn.autocomplete = "off";
        const resBox = createEl("div", "rk-lookup-results hidden");
        resBox.id = `atlas-${rolePrefix}-results`;
        const selBox = createEl("div", "rk-selected-target hidden");
        selBox.id = `atlas-${rolePrefix}-selected`;

        async function runLookup(q) {
          if (ctrl) ctrl.abort();
          ctrl = new AbortController();
          try {
            const data = await api.lookupEntities({ q, limit: 12 }, ctrl.signal);
            const items = Array.isArray(data && data.items) ? data.items : [];
            clearNode(resBox);
            resBox.classList.remove("hidden");
            if (items.length === 0) {
              resBox.appendChild(createEl("div", "rk-lookup-empty", "Nenhum item encontrado."));
              return;
            }
            items.forEach((it) => {
              const b = createEl("button", "rk-lookup-item");
              b.type = "button";
              const m = createEl("div", "rk-lookup-item-main");
              m.appendChild(createEl("span", "rk-badge rk-badge-type", ENTITY_TYPE_LABELS[it.type] || it.type));
              m.appendChild(createEl("span", "rk-lookup-item-title", truncateText(it.label || it.title || "Item", 80)));
              b.appendChild(m);
              b.addEventListener("click", () => {
                onSelect(it);
                clearNode(resBox);
                resBox.classList.add("hidden");
                renderChosen(it);
              });
              resBox.appendChild(b);
            });
          } catch (_) {}
        }

        function renderChosen(it) {
          clearNode(selBox);
          if (!it) {
            selBox.classList.add("hidden");
            return;
          }
          selBox.classList.remove("hidden");
          const pill = createEl("div", "rk-selected-target-pill");
          pill.appendChild(createEl("span", "rk-badge rk-badge-type", ENTITY_TYPE_LABELS[it.type] || it.type));
          pill.appendChild(createEl("span", "rk-selected-target-label", truncateText(it.label || it.title || "", 80)));
          const clr = createEl("button", "rk-icon-btn", "×");
          clr.type = "button";
          clr.addEventListener("click", () => {
            onSelect(null);
            renderChosen(null);
          });
          pill.appendChild(clr);
          selBox.appendChild(pill);
        }

        searchIn.addEventListener("input", () => {
          if (timer) clearTimeout(timer);
          if (ctrl) ctrl.abort();
          clearNode(resBox);
          resBox.classList.remove("hidden");
          resBox.appendChild(createEl("div", "rk-lookup-empty", "Buscando…"));
          timer = setTimeout(() => runLookup(searchIn.value), DEBOUNCE_MS);
        });
        searchIn.addEventListener("focus", () => runLookup(searchIn.value));

        wrap.appendChild(searchIn);
        wrap.appendChild(resBox);
        wrap.appendChild(selBox);
        return { wrap, reset: () => { searchIn.value = ""; renderChosen(null); } };
      }

      const dirPreview = createEl("div", "rk-direction-preview");
      const relSelect = createEl("select", "rk-select");
      relSelect.id = "atlas-indep-relation-select";
      RELATION_TYPES.forEach((r) => {
        const opt = document.createElement("option");
        opt.value = r.value;
        opt.textContent = r.outgoing;
        relSelect.appendChild(opt);
      });
      relSelect.value = "relates_to";

      function updateDir() {
        const rDef = RELATION_MAP[relSelect.value] || RELATION_MAP.relates_to;
        const sTxt = srcItem ? truncateText(srcItem.label || srcItem.title || "", 35) : "Selecione a origem";
        const tTxt = tgtItem ? truncateText(tgtItem.label || tgtItem.title || "", 35) : "Selecione o destino";
        dirPreview.textContent = `${sTxt} — ${rDef.outgoing} → ${tTxt}`;
      }

      const srcPicker = createEndpointPicker("conn-source", "Entidade de Origem *", (it) => {
        srcItem = it;
        updateDir();
      });
      const tgtPicker = createEndpointPicker("conn-target", "Entidade de Destino *", (it) => {
        tgtItem = it;
        updateDir();
      });

      form.appendChild(srcPicker.wrap);
      form.appendChild(tgtPicker.wrap);

      const lblRel = createEl("label", "rk-field-label", "Tipo de relação *");
      relSelect.addEventListener("change", updateDir);
      lblRel.appendChild(relSelect);
      form.appendChild(lblRel);
      form.appendChild(dirPreview);

      const lblRat = createEl("label", "rk-field-label", "Justificativa (opcional)");
      const ratIn = createEl("textarea", "rk-textarea");
      ratIn.id = "atlas-indep-rationale-input";
      ratIn.rows = 2;
      ratIn.maxLength = 2000;
      lblRat.appendChild(ratIn);
      form.appendChild(lblRat);

      const actions = createEl("div", "rk-inline-form-actions");
      const submitBtn = createEl("button", "btn btn-sm btn-primary", "Criar conexão");
      submitBtn.id = "atlas-indep-submit-btn";
      submitBtn.type = "submit";
      actions.appendChild(submitBtn);
      form.appendChild(actions);
      updateDir();

      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!srcItem || !tgtItem) {
          showBanner("Selecione a entidade de origem e a entidade de destino.", "error");
          return;
        }
        if (submitLocks.has("indep-link")) return;
        submitLocks.add("indep-link");
        submitBtn.disabled = true;
        try {
          await api.createLink({
            source_type: srcItem.type,
            source_id: srcItem.id,
            target_type: tgtItem.type,
            target_id: tgtItem.id,
            relation_type: relSelect.value || "relates_to",
            rationale: ratIn.value.trim(),
          });
          srcItem = null;
          tgtItem = null;
          srcPicker.reset();
          tgtPicker.reset();
          ratIn.value = "";
          updateDir();
          form.classList.add("hidden");
          showBanner("Conexão semântica criada com sucesso.", "success");
          if (typeof onCreated === "function") onCreated();
        } catch (err) {
          showBanner(err.message || "Erro ao criar conexão.", "error");
        } finally {
          submitLocks.delete("indep-link");
          submitBtn.disabled = false;
        }
      });

      return form;
    }

    function appendNavigationButtonForEntity(container, entity) {
      if (!entity || entity.available !== true) return;
      if (entity.type === "concept" && entity.id) {
        const b = createEl("button", "btn btn-sm btn-secondary rk-btn-navigate", "Abrir conceito");
        b.type = "button";
        b.addEventListener("click", () => requestSubtabSwitch("concepts", { conceptId: entity.id }));
        container.appendChild(b);
        return;
      }
      if (entity.type === "reflection" && entity.id) {
        const b = createEl("button", "btn btn-sm btn-secondary rk-btn-navigate", "Abrir reflexão");
        b.type = "button";
        b.addEventListener("click", () => requestSubtabSwitch("reflections", { reflectionId: entity.id }));
        container.appendChild(b);
        return;
      }
      const navSpec = buildSafeNavigationSpec(entity, "");
      if (navSpec) {
        const a = createEl("a", "btn btn-sm btn-secondary rk-btn-navigate", "Abrir no leitor");
        a.href = navSpec.url;
        container.appendChild(a);
      }
    }

    function buildSharedLinkCard(link, direction, onRefresh) {
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
        appendNavigationButtonForEntity(actions, counterpart);
        const btnMap = createEl("button", "btn btn-sm btn-ghost rk-btn-explore-graph", "Explorar no Mapa");
        btnMap.type = "button";
        btnMap.addEventListener("click", () => {
          requestSubtabSwitch("graph", {
            graphEntityType: counterpart.type,
            graphEntityId: counterpart.id,
          });
        });
        actions.appendChild(btnMap);
      }
      appendEditAndDeleteLinkControls(card, actions, link, onRefresh);
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
      appendInlineLinkEditFormIfActive(card, link, onRefresh);
      return card;
    }

    function buildIndependentLinkCard(link, onRefresh) {
      const card = createEl("article", "rk-link-card");
      card.dataset.linkId = link.id;
      const src = link.source;
      const tgt = link.target;
      const srcAvail = Boolean(src && src.available === true);
      const tgtAvail = Boolean(tgt && tgt.available === true);
      if (!srcAvail || !tgtAvail) card.classList.add("rk-link-card-unavailable");

      const header = createEl("div", "rk-link-card-header");
      const badges = createEl("div", "rk-badges-row");
      const relDef = RELATION_MAP[link.relation_type] || RELATION_MAP.relates_to;
      badges.appendChild(
        createEl("span", "rk-badge rk-badge-type", srcAvail ? (ENTITY_TYPE_LABELS[src.type] || src.type) : "Indisponível")
      );
      badges.appendChild(createEl("span", "rk-badge rk-badge-relation", link.outgoing_label || relDef.outgoing));
      badges.appendChild(
        createEl("span", "rk-badge rk-badge-type", tgtAvail ? (ENTITY_TYPE_LABELS[tgt.type] || tgt.type) : "Indisponível")
      );
      header.appendChild(badges);

      const actions = createEl("div", "rk-link-card-actions");
      if (tgtAvail) {
        appendNavigationButtonForEntity(actions, tgt);
        const btnMap = createEl("button", "btn btn-sm btn-ghost rk-btn-explore-graph", "Explorar no Mapa");
        btnMap.type = "button";
        btnMap.addEventListener("click", () => {
          requestSubtabSwitch("graph", {
            graphEntityType: tgt.type,
            graphEntityId: tgt.id,
          });
        });
        actions.appendChild(btnMap);
      } else if (srcAvail) {
        const btnMap = createEl("button", "btn btn-sm btn-ghost rk-btn-explore-graph", "Explorar no Mapa");
        btnMap.type = "button";
        btnMap.addEventListener("click", () => {
          requestSubtabSwitch("graph", {
            graphEntityType: src.type,
            graphEntityId: src.id,
          });
        });
        actions.appendChild(btnMap);
      }
      appendEditAndDeleteLinkControls(card, actions, link, onRefresh);
      header.appendChild(actions);
      card.appendChild(header);

      const body = createEl("div", "rk-link-card-body");
      const srcLabel = srcAvail ? (src.label || src.title || "Origem") : "Referência indisponível";
      const tgtLabel = tgtAvail ? (tgt.label || tgt.title || "Destino") : "Referência indisponível";
      body.appendChild(createEl("div", "rk-link-target-title", `${srcLabel} → ${tgtLabel}`));

      const metaParts = [];
      if (srcAvail && formatLocationMeta(src)) metaParts.push(`Origem: ${formatLocationMeta(src)}`);
      if (tgtAvail && formatLocationMeta(tgt)) metaParts.push(`Destino: ${formatLocationMeta(tgt)}`);
      if (metaParts.length > 0) {
        body.appendChild(createEl("div", "rk-link-target-meta", metaParts.join(" | ")));
      }

      if (link.rationale && String(link.rationale).trim()) {
        const rat = createEl("div", "rk-link-rationale");
        rat.appendChild(createEl("span", "rk-link-rationale-label", "Justificativa: "));
        rat.appendChild(createEl("span", "rk-link-rationale-text", link.rationale));
        body.appendChild(rat);
      }
      card.appendChild(body);
      appendInlineLinkEditFormIfActive(card, link, onRefresh);
      return card;
    }

    function appendEditAndDeleteLinkControls(card, actionsContainer, link, onRefresh) {
      const editBtn = createEl("button", "btn btn-sm btn-ghost rk-btn-edit-link", "Editar");
      editBtn.type = "button";
      editBtn.addEventListener("click", () => {
        editingLinkId = editingLinkId === link.id ? null : link.id;
        confirmingDeleteLinkId = null;
        if (typeof onRefresh === "function") onRefresh();
      });
      actionsContainer.appendChild(editBtn);

      if (confirmingDeleteLinkId === link.id) {
        const confirmBtn = createEl("button", "btn btn-sm btn-danger rk-btn-confirm-delete", "Confirmar");
        confirmBtn.type = "button";
        confirmBtn.addEventListener("click", async () => {
          const lockKey = `del-link:${link.id}`;
          if (submitLocks.has(lockKey)) return;
          submitLocks.add(lockKey);
          confirmBtn.disabled = true;
          try {
            await api.deleteLink(link.id);
            confirmingDeleteLinkId = null;
            showBanner("Conexão removida.", "success");
            if (typeof onRefresh === "function") onRefresh();
          } catch (err) {
            showBanner(err.message || "Erro ao excluir conexão.", "error");
            confirmBtn.disabled = false;
          } finally {
            submitLocks.delete(lockKey);
          }
        });
        const cancelBtn = createEl("button", "btn btn-sm btn-ghost", "Cancelar");
        cancelBtn.type = "button";
        cancelBtn.addEventListener("click", () => {
          confirmingDeleteLinkId = null;
          if (typeof onRefresh === "function") onRefresh();
        });
        actionsContainer.appendChild(confirmBtn);
        actionsContainer.appendChild(cancelBtn);
      } else {
        const delBtn = createEl("button", "btn btn-sm btn-ghost rk-btn-delete-link", "Excluir");
        delBtn.type = "button";
        delBtn.addEventListener("click", () => {
          confirmingDeleteLinkId = link.id;
          editingLinkId = null;
          if (typeof onRefresh === "function") onRefresh();
        });
        actionsContainer.appendChild(delBtn);
      }
    }

    function appendInlineLinkEditFormIfActive(card, link, onRefresh) {
      if (editingLinkId !== link.id) return;
      const editForm = createEl("form", "rk-inline-edit-form");
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
      const cancelBtn = createEl("button", "btn btn-sm btn-ghost", "Cancelar");
      cancelBtn.type = "button";
      cancelBtn.addEventListener("click", () => {
        editingLinkId = null;
        if (typeof onRefresh === "function") onRefresh();
      });
      btns.appendChild(cancelBtn);
      btns.appendChild(saveBtn);
      editForm.appendChild(relSel);
      editForm.appendChild(ratTa);
      editForm.appendChild(btns);

      editForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const lockKey = `upd-link:${link.id}`;
        if (submitLocks.has(lockKey)) return;
        submitLocks.add(lockKey);
        saveBtn.disabled = true;
        try {
          await api.updateLink(link.id, {
            relation_type: relSel.value,
            rationale: ratTa.value.trim(),
          });
          editingLinkId = null;
          showBanner("Conexão atualizada.", "success");
          if (typeof onRefresh === "function") onRefresh();
        } catch (err) {
          showBanner(err.message || "Erro ao atualizar conexão.", "error");
          saveBtn.disabled = false;
        } finally {
          submitLocks.delete(lockKey);
        }
      });
      card.appendChild(editForm);
    }

    // =========================================================================
    // 5. PESQUISA INTEGRADA DO ATLAS (SEARCH — KD-008, FASE 6)
    // =========================================================================

    function buildSafeSnippetDom(snippetSegments, fallbackText) {
      const p = createEl("p", "atlas-search-snippet");
      if (Array.isArray(snippetSegments) && snippetSegments.length > 0) {
        snippetSegments.forEach((seg) => {
          if (!seg || typeof seg.text !== "string" || !seg.text) return;
          if (seg.highlight === true) {
            p.appendChild(createEl("mark", "atlas-search-mark", seg.text));
          } else {
            p.appendChild(document.createTextNode(seg.text));
          }
        });
        return p;
      }
      p.textContent = String(fallbackText || "");
      return p;
    }

    async function loadIntegratedSearch(append) {
      if (!dom.paneSearch) return;
      const trimmed = String(searchQuery || "").trim();
      if (!trimmed) {
        if (searchAbortCtrl) searchAbortCtrl.abort();
        searchItems = [];
        searchTotal = 0;
        searchOffset = 0;
        searchHasMore = false;
        searchHasSearched = false;
        searchLoading = false;
        renderSearchResultsArea();
        return;
      }

      if (searchAbortCtrl) searchAbortCtrl.abort();
      searchAbortCtrl = new AbortController();
      const seq = ++searchRequestSeq;
      const offset = append ? searchOffset : 0;
      searchLoading = true;
      if (!append) {
        renderSearchResultsArea();
      }

      try {
        const data = await api.searchKnowledge(
          {
            q: trimmed,
            entity_types: searchEntityTypeFilter || undefined,
            book_id: searchBookIdFilter || undefined,
            include_archived: searchIncludeArchived,
            limit: PAGE_LIMIT,
            offset,
          },
          searchAbortCtrl.signal
        );
        if (seq !== searchRequestSeq) return;
        const batch = Array.isArray(data && data.items) ? data.items : [];
        searchItems = append ? searchItems.concat(batch) : batch;
        searchTotal = Number(data && data.total) || searchItems.length;
        searchHasMore = Boolean(data && data.has_more);
        searchOffset = searchItems.length;
        searchEngineUsed = (data && data.search_engine) || "fts5";
        searchHasSearched = true;
        searchLoading = false;
        renderSearchResultsArea();
      } catch (err) {
        if (err && err.name === "AbortError") return;
        if (seq !== searchRequestSeq) return;
        searchLoading = false;
        searchHasSearched = true;
        showBanner(err.message || "Não foi possível realizar a pesquisa no Atlas.", "error");
        renderSearchResultsArea();
      }
    }

    function renderSearchPaneView() {
      if (!dom.paneSearch) return;
      clearNode(dom.paneSearch);

      const toolbar = createEl("div", "atlas-toolbar atlas-search-toolbar");
      const searchIn = createEl("input", "rk-input atlas-search-input");
      searchIn.id = "atlas-integrated-search-input";
      searchIn.type = "search";
      searchIn.maxLength = 300;
      searchIn.placeholder =
        "Pesquisar conceitos, reflexões, destaques, notas, conexões e livros (com ou sem acentos)...";
      searchIn.value = searchQuery;
      searchIn.addEventListener("input", () => {
        searchQuery = searchIn.value;
        if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
        searchDebounceTimer = setTimeout(() => loadIntegratedSearch(false), DEBOUNCE_MS);
      });
      searchIn.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
          loadIntegratedSearch(false);
        }
      });
      toolbar.appendChild(searchIn);

      const typeSel = createEl("select", "rk-select atlas-sort-select");
      typeSel.id = "atlas-integrated-search-type";
      [
        ["", "Todos os tipos"],
        ["concept", "[C] Conceitos"],
        ["reflection", "[R] Reflexões"],
        ["highlight", "[D] Destaques"],
        ["note", "[N] Notas"],
        ["link", "[V] Conexões"],
        ["book", "[L] Livros"],
      ].forEach(([val, lbl]) => {
        const opt = document.createElement("option");
        opt.value = val;
        opt.textContent = lbl;
        if (val === searchEntityTypeFilter) opt.selected = true;
        typeSel.appendChild(opt);
      });
      typeSel.addEventListener("change", () => {
        searchEntityTypeFilter = typeSel.value;
        loadIntegratedSearch(false);
      });
      toolbar.appendChild(typeSel);

      const archLbl = createEl("label", "rk-checkbox-row");
      const archChk = document.createElement("input");
      archChk.id = "atlas-integrated-search-archived";
      archChk.type = "checkbox";
      archChk.checked = searchIncludeArchived;
      archChk.addEventListener("change", () => {
        searchIncludeArchived = archChk.checked;
        loadIntegratedSearch(false);
      });
      archLbl.appendChild(archChk);
      archLbl.appendChild(createEl("span", "", "Incluir arquivados"));
      toolbar.appendChild(archLbl);

      const btnExec = createEl("button", "btn btn-sm btn-primary", "Pesquisar");
      btnExec.id = "atlas-btn-execute-search";
      btnExec.type = "button";
      btnExec.addEventListener("click", () => {
        if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
        loadIntegratedSearch(false);
      });
      toolbar.appendChild(btnExec);

      const btnClear = createEl("button", "btn btn-sm btn-ghost", "Limpar");
      btnClear.id = "atlas-btn-clear-search";
      btnClear.type = "button";
      btnClear.addEventListener("click", () => {
        searchQuery = "";
        searchEntityTypeFilter = "";
        searchBookIdFilter = "";
        searchIncludeArchived = false;
        searchIn.value = "";
        typeSel.value = "";
        archChk.checked = false;
        loadIntegratedSearch(false);
      });
      toolbar.appendChild(btnClear);

      dom.paneSearch.appendChild(toolbar);

      dom.searchResultsArea = createEl("div", "atlas-search-results-area");
      dom.searchResultsArea.id = "atlas-search-results-area";
      dom.paneSearch.appendChild(dom.searchResultsArea);

      renderSearchResultsArea();
    }

    function renderSearchResultsArea() {
      if (!dom.searchResultsArea) return;
      clearNode(dom.searchResultsArea);

      const trimmed = String(searchQuery || "").trim();
      if (!trimmed && !searchHasSearched) {
        const emptyInit = createEl("div", "rk-empty-state");
        emptyInit.id = "atlas-search-initial-state";
        emptyInit.textContent =
          "Digite um termo acima para pesquisar de forma integrada em seus conceitos, reflexões autorais, destaques, notas, justificativas de conexões e metadados de livros.";
        dom.searchResultsArea.appendChild(emptyInit);
        return;
      }

      if (searchLoading && searchItems.length === 0) {
        dom.searchResultsArea.appendChild(createEl("div", "rk-empty-state", "Pesquisando no Atlas…"));
        return;
      }

      const metaBar = createEl("div", "atlas-search-summary-bar");
      metaBar.id = "atlas-search-summary-bar";
      metaBar.appendChild(
        createEl(
          "span",
          "atlas-search-count",
          `${searchTotal} resultado(s) encontrado(s) para "${trimmed}"`
        )
      );
      dom.searchResultsArea.appendChild(metaBar);

      if (searchItems.length === 0) {
        const emptyRes = createEl("div", "rk-empty-state");
        emptyRes.id = "atlas-search-empty-results";
        emptyRes.textContent = `Nenhum resultado autorizado encontrado para "${trimmed}".`;
        dom.searchResultsArea.appendChild(emptyRes);
        return;
      }

      const list = createEl("div", "rk-links-list atlas-search-results-list");
      list.id = "atlas-search-results-list";

      searchItems.forEach((item) => {
        const card = createEl("article", "rk-link-card atlas-search-result-card");
        card.dataset.entityType = item.type;
        card.dataset.entityId = item.id;

        const header = createEl("div", "rk-link-card-header");
        const badges = createEl("div", "rk-badges-row");
        badges.appendChild(
          createEl(
            "span",
            "rk-badge rk-badge-type",
            `${item.type_prefix || ""} ${item.type_label || ENTITY_TYPE_LABELS[item.type] || item.type}`.trim()
          )
        );
        if (item.relation_label) {
          badges.appendChild(createEl("span", "rk-badge rk-badge-relation", item.relation_label));
        }
        if (item.is_own === false && item.author_display_name) {
          badges.appendChild(createEl("span", "rk-badge", `Público · ${item.author_display_name}`));
        }
        header.appendChild(badges);

        const actions = createEl("div", "rk-link-card-actions");
        if (item.type === "concept") {
          const btnOpen = createEl("button", "btn btn-sm btn-secondary", "Abrir conceito");
          btnOpen.type = "button";
          btnOpen.addEventListener("click", () => requestSubtabSwitch("concepts", { conceptId: item.id }));
          actions.appendChild(btnOpen);
        } else if (item.type === "reflection") {
          const btnOpen = createEl("button", "btn btn-sm btn-secondary", "Abrir reflexão");
          btnOpen.type = "button";
          btnOpen.addEventListener("click", () => requestSubtabSwitch("reflections", { reflectionId: item.id }));
          actions.appendChild(btnOpen);
        } else if (item.type === "highlight" || item.type === "note" || item.type === "book") {
          const navSpec = buildSafeNavigationSpec({ ...item, available: true }, "");
          if (navSpec) {
            const a = createEl("a", "btn btn-sm btn-secondary rk-btn-navigate", "Abrir no leitor");
            a.href = navSpec.url;
            actions.appendChild(a);
          }
        } else if (item.type === "link") {
          const btnOpenConn = createEl("button", "btn btn-sm btn-secondary", "Ver nas conexões");
          btnOpenConn.type = "button";
          btnOpenConn.addEventListener("click", () => requestSubtabSwitch("connections"));
          actions.appendChild(btnOpenConn);
        }

        if (item.can_open_in_graph) {
          const btnGraph = createEl("button", "btn btn-sm btn-ghost atlas-btn-open-in-graph", "Explorar no Mapa");
          btnGraph.type = "button";
          btnGraph.addEventListener("click", () => {
            requestSubtabSwitch("graph", {
              graphEntityType: item.type,
              graphEntityId: item.id,
            });
          });
          actions.appendChild(btnGraph);
        } else if (item.type === "link") {
          const endpointForGraph =
            item.source && item.source.available
              ? item.source
              : item.target && item.target.available
                ? item.target
                : null;
          if (endpointForGraph) {
            const btnGraph = createEl("button", "btn btn-sm btn-ghost atlas-btn-open-in-graph", "Explorar no Mapa");
            btnGraph.type = "button";
            btnGraph.addEventListener("click", () => {
              requestSubtabSwitch("graph", {
                graphEntityType: endpointForGraph.type,
                graphEntityId: endpointForGraph.id,
              });
            });
            actions.appendChild(btnGraph);
          }
        }

        header.appendChild(actions);
        card.appendChild(header);

        const body = createEl("div", "rk-link-card-body");
        const titleRow = createEl("div", "rk-link-target-title");
        if (item.color) {
          const dot = createEl("span", "rk-color-dot");
          dot.style.backgroundColor = sanitizeHexColor(item.color);
          titleRow.appendChild(dot);
          titleRow.appendChild(document.createTextNode(" "));
        }
        titleRow.appendChild(document.createTextNode(item.title || item.label || "Item"));
        body.appendChild(titleRow);

        if (item.subtitle) {
          body.appendChild(createEl("div", "rk-link-target-meta", item.subtitle));
        }

        if (item.snippet || (Array.isArray(item.snippet_segments) && item.snippet_segments.length > 0)) {
          body.appendChild(buildSafeSnippetDom(item.snippet_segments, item.snippet));
        }

        if (Array.isArray(item.tags) && item.tags.length > 0) {
          const tRow = createEl("div", "rk-tags-row");
          item.tags.slice(0, 6).forEach((t) => tRow.appendChild(createEl("span", "rk-tag-pill", `#${t}`)));
          body.appendChild(tRow);
        }

        card.appendChild(body);
        list.appendChild(card);
      });

      dom.searchResultsArea.appendChild(list);

      if (searchHasMore) {
        const moreWrap = createEl("div", "rk-load-more-wrap");
        const btnMore = createEl("button", "btn btn-sm btn-ghost", "Carregar mais resultados");
        btnMore.id = "atlas-search-load-more";
        btnMore.type = "button";
        btnMore.addEventListener("click", () => loadIntegratedSearch(true));
        moreWrap.appendChild(btnMore);
        dom.searchResultsArea.appendChild(moreWrap);
      }
    }

    // =========================================================================
    // 6. MAPA LOCAL DE CONHECIMENTO (SVG + LISTA TEXTUAL ACESSÍVEL — KD-006, FASE 6)
    // =========================================================================

    const SVG_NS = "http://www.w3.org/2000/svg";

    function createSvgEl(tag, attrs) {
      const el = document.createElementNS(SVG_NS, tag);
      if (attrs) {
        Object.keys(attrs).forEach((k) => {
          if (attrs[k] !== undefined && attrs[k] !== null) {
            el.setAttribute(k, String(attrs[k]));
          }
        });
      }
      return el;
    }

    async function loadLocalKnowledgeGraph() {
      if (!dom.paneGraph) return;
      if (!graphCenterType || !graphCenterId) {
        renderGraphPaneView();
        return;
      }

      if (graphAbortCtrl) graphAbortCtrl.abort();
      graphAbortCtrl = new AbortController();
      const seq = ++graphRequestSeq;
      graphLoading = true;
      renderGraphPaneView();

      try {
        const data = await api.getKnowledgeGraph(
          {
            entity_type: graphCenterType,
            entity_id: graphCenterId,
            depth: graphDepth,
            entity_types: graphEntityTypeFilter || undefined,
            relation_types: graphRelationTypeFilter || undefined,
          },
          graphAbortCtrl.signal
        );
        if (seq !== graphRequestSeq) return;
        graphData = data;
        graphLoading = false;
        graphZoom = 1;
        graphPanX = 0;
        graphPanY = 0;
        graphSelectedEdgeId = null;
        graphSelectedNodeKey = data && data.center ? data.center.key : null;
        renderGraphPaneView();
      } catch (err) {
        if (err && err.name === "AbortError") return;
        if (seq !== graphRequestSeq) return;
        graphLoading = false;
        graphData = null;
        showBanner(err.message || "Não foi possível carregar o mapa local de conhecimento.", "error");
        renderGraphPaneView();
      }
    }

    function computeDeterministicRadialLayout(nodes, edges, width, height) {
      const cx = width / 2;
      const cy = height / 2;
      const posMap = Object.create(null);

      const centerNodes = nodes.filter((n) => n.is_center);
      const hop1Nodes = nodes.filter((n) => !n.is_center && n.hop === 1);
      const hop2Nodes = nodes.filter((n) => !n.is_center && n.hop >= 2);

      centerNodes.forEach((n) => {
        posMap[n.key] = { x: cx, y: cy, angle: 0 };
      });

      const r1 = hop2Nodes.length > 0 ? Math.min(width, height) * 0.23 : Math.min(width, height) * 0.34;
      const r2 = Math.min(width, height) * 0.42;

      hop1Nodes.forEach((n, idx) => {
        let ringOffset = 0;
        if (hop1Nodes.length >= 24) {
          ringOffset = [-54, 0, 56][idx % 3];
        } else if (hop1Nodes.length >= 14) {
          ringOffset = idx % 2 === 0 ? -24 : 26;
        }
        const radius = r1 + ringOffset;
        const angle = (2 * Math.PI * idx) / Math.max(1, hop1Nodes.length) - Math.PI / 2;
        posMap[n.key] = {
          x: Math.round((cx + radius * Math.cos(angle)) * 10) / 10,
          y: Math.round((cy + radius * Math.sin(angle)) * 10) / 10,
          angle,
        };
      });

      // Ordena nós de 2º salto pelo ângulo médio de seus vizinhos de 1º salto para minimizar cruzamentos
      const hop2WithAngles = hop2Nodes.map((n, idx) => {
        let sumSin = 0;
        let sumCos = 0;
        let count = 0;
        edges.forEach((e) => {
          let neighborKey = null;
          if (e.source_key === n.key) neighborKey = e.target_key;
          else if (e.target_key === n.key) neighborKey = e.source_key;
          if (neighborKey && posMap[neighborKey] && neighborKey !== (centerNodes[0] && centerNodes[0].key)) {
            sumCos += Math.cos(posMap[neighborKey].angle);
            sumSin += Math.sin(posMap[neighborKey].angle);
            count++;
          }
        });
        const prefAngle =
          count > 0
            ? Math.atan2(sumSin / count, sumCos / count)
            : (2 * Math.PI * idx) / Math.max(1, hop2Nodes.length) - Math.PI / 2;
        return { node: n, prefAngle };
      });

      hop2WithAngles.sort((a, b) => a.prefAngle - b.prefAngle);

      hop2WithAngles.forEach((entry, idx) => {
        const ringOffset =
          hop2WithAngles.length >= 16 ? (idx % 2 === 0 ? -18 : 18) : 0;
        const radius = r2 + ringOffset;
        const angle = (2 * Math.PI * idx) / Math.max(1, hop2WithAngles.length) - Math.PI / 2 + Math.PI / Math.max(4, hop2WithAngles.length);
        posMap[entry.node.key] = {
          x: Math.round((cx + radius * Math.cos(angle)) * 10) / 10,
          y: Math.round((cy + radius * Math.sin(angle)) * 10) / 10,
          angle,
        };
      });

      return posMap;
    }

    function renderGraphPaneView() {
      if (!dom.paneGraph) return;
      clearNode(dom.paneGraph);

      // Top Controls Bar: Center entity picker, Depth (1 / 2), Entity Type Filter, Relation Filter, SVG/List Toggle
      const toolbar = createEl("div", "atlas-toolbar atlas-graph-toolbar");

      // Center Entity Picker
      const pickerWrap = createEl("div", "rk-lookup-wrap atlas-graph-center-picker");
      const pickerIn = createEl("input", "rk-input");
      pickerIn.id = "atlas-graph-center-search";
      pickerIn.type = "search";
      pickerIn.placeholder =
        graphData && graphData.center
          ? `Centro: ${graphData.center.type_prefix} ${graphData.center.label} (digite para trocar)...`
          : "Escolher entidade central do mapa (conceito, reflexão, livro, destaque ou nota)...";
      pickerIn.autocomplete = "off";
      const pickerResults = createEl("div", "rk-lookup-results hidden");
      pickerResults.id = "atlas-graph-center-results";

      async function runCenterLookup(q) {
        if (graphPickerCtrl) graphPickerCtrl.abort();
        graphPickerCtrl = new AbortController();
        try {
          const data = await api.lookupEntities({ q, limit: 12 }, graphPickerCtrl.signal);
          const items = Array.isArray(data && data.items) ? data.items : [];
          clearNode(pickerResults);
          pickerResults.classList.remove("hidden");
          if (items.length === 0) {
            pickerResults.appendChild(createEl("div", "rk-lookup-empty", "Nenhuma entidade encontrada."));
            return;
          }
          items.forEach((it) => {
            const b = createEl("button", "rk-lookup-item");
            b.type = "button";
            const m = createEl("div", "rk-lookup-item-main");
            m.appendChild(createEl("span", "rk-badge rk-badge-type", ENTITY_TYPE_LABELS[it.type] || it.type));
            m.appendChild(createEl("span", "rk-lookup-item-title", truncateText(it.label || it.title || "Item", 80)));
            b.appendChild(m);
            b.addEventListener("click", () => {
              graphCenterType = it.type;
              graphCenterId = it.id;
              clearNode(pickerResults);
              pickerResults.classList.add("hidden");
              loadLocalKnowledgeGraph();
            });
            pickerResults.appendChild(b);
          });
        } catch (_) {}
      }

      pickerIn.addEventListener("input", () => {
        if (graphPickerTimer) clearTimeout(graphPickerTimer);
        if (graphPickerCtrl) graphPickerCtrl.abort();
        graphPickerTimer = setTimeout(() => runCenterLookup(pickerIn.value), DEBOUNCE_MS);
      });
      pickerIn.addEventListener("focus", () => runCenterLookup(pickerIn.value));
      pickerWrap.appendChild(pickerIn);
      pickerWrap.appendChild(pickerResults);
      toolbar.appendChild(pickerWrap);

      // Depth Selector (1 or 2 levels)
      const depthSel = createEl("select", "rk-select atlas-sort-select");
      depthSel.id = "atlas-graph-depth-select";
      [
        [1, "Profundidade: 1 salto (direto)"],
        [2, "Profundidade: 2 saltos (vizinhança)"],
      ].forEach(([val, lbl]) => {
        const opt = document.createElement("option");
        opt.value = String(val);
        opt.textContent = lbl;
        if (Number(graphDepth) === val) opt.selected = true;
        depthSel.appendChild(opt);
      });
      depthSel.addEventListener("change", () => {
        graphDepth = Number(depthSel.value) === 2 ? 2 : 1;
        if (graphCenterType && graphCenterId) loadLocalKnowledgeGraph();
      });
      toolbar.appendChild(depthSel);

      // Entity Type Filter
      const entSel = createEl("select", "rk-select atlas-sort-select");
      entSel.id = "atlas-graph-entity-filter";
      [
        ["", "Todos os tipos de nó"],
        ["concept", "[C] Conceitos"],
        ["reflection", "[R] Reflexões"],
        ["book", "[L] Livros"],
        ["highlight", "[D] Destaques"],
        ["note", "[N] Notas"],
      ].forEach(([val, lbl]) => {
        const opt = document.createElement("option");
        opt.value = val;
        opt.textContent = lbl;
        if (val === graphEntityTypeFilter) opt.selected = true;
        entSel.appendChild(opt);
      });
      entSel.addEventListener("change", () => {
        graphEntityTypeFilter = entSel.value;
        if (graphCenterType && graphCenterId) loadLocalKnowledgeGraph();
      });
      toolbar.appendChild(entSel);

      // Relation Type Filter
      const relSel = createEl("select", "rk-select atlas-sort-select");
      relSel.id = "atlas-graph-relation-filter";
      const allRel = document.createElement("option");
      allRel.value = "";
      allRel.textContent = "Todas as relações";
      relSel.appendChild(allRel);
      RELATION_TYPES.forEach((r) => {
        const opt = document.createElement("option");
        opt.value = r.value;
        opt.textContent = r.outgoing;
        if (r.value === graphRelationTypeFilter) opt.selected = true;
        relSel.appendChild(opt);
      });
      relSel.addEventListener("change", () => {
        graphRelationTypeFilter = relSel.value;
        if (graphCenterType && graphCenterId) loadLocalKnowledgeGraph();
      });
      toolbar.appendChild(relSel);

      // Mode Toggle: SVG Map vs Accessible Textual List
      const viewSwitch = createEl("div", "atlas-md-mode-group");
      const btnViewSvg = createEl(
        "button",
        `rk-tab-btn ${graphViewMode === "svg" ? "active" : ""}`,
        "Mapa visual (SVG)"
      );
      btnViewSvg.id = "atlas-graph-view-svg";
      btnViewSvg.type = "button";
      btnViewSvg.addEventListener("click", () => {
        graphViewMode = "svg";
        renderGraphPaneView();
      });

      const btnViewList = createEl(
        "button",
        `rk-tab-btn ${graphViewMode === "list" ? "active" : ""}`,
        "Lista textual acessível"
      );
      btnViewList.id = "atlas-graph-view-list";
      btnViewList.type = "button";
      btnViewList.addEventListener("click", () => {
        graphViewMode = "list";
        renderGraphPaneView();
      });
      viewSwitch.appendChild(btnViewSvg);
      viewSwitch.appendChild(btnViewList);
      toolbar.appendChild(viewSwitch);

      dom.paneGraph.appendChild(toolbar);

      if (graphLoading) {
        dom.paneGraph.appendChild(createEl("div", "rk-empty-state", "Construindo mapa local de conhecimento…"));
        return;
      }

      if (!graphData || !graphData.center) {
        const emptyGraph = createEl("div", "rk-empty-state");
        emptyGraph.id = "atlas-graph-empty-state";
        emptyGraph.textContent =
          "Selecione um conceito, reflexão, livro, destaque ou nota no seletor acima (ou clique em 'Explorar no Mapa' em qualquer item do Atlas) para visualizar suas conexões locais em 1 ou 2 saltos.";
        dom.paneGraph.appendChild(emptyGraph);
        return;
      }

      // Truncation Warning Banner (when truncated === true)
      if (graphData.truncated) {
        const truncNotice = createEl(
          "div",
          "rk-status-banner rk-banner-info atlas-graph-truncation-banner",
          `Subgrafo limitado automaticamente para preservar a legibilidade e o desempenho (${graphData.node_count}/${graphData.max_nodes} nós, ${graphData.edge_count}/${graphData.max_edges} arestas). Utilize os filtros de tipo ou recentralize em um nó vizinho para explorar mais.`
        );
        truncNotice.id = "atlas-graph-truncation-notice";
        dom.paneGraph.appendChild(truncNotice);
      }

      // Main Graph Layout: Left = SVG Canvas or Accessible Textual List | Right = On-Demand Detail Panel
      const mainSplit = createEl("div", "atlas-graph-workspace");

      const stageCol = createEl("div", "atlas-graph-stage-col");
      if (graphViewMode === "list") {
        stageCol.appendChild(buildAccessibleGraphTextView(graphData));
      } else {
        stageCol.appendChild(buildLocalSvgGraphView(graphData));
      }
      mainSplit.appendChild(stageCol);

      dom.graphDetailPanel = createEl("aside", "atlas-graph-detail-panel");
      dom.graphDetailPanel.id = "atlas-graph-detail-panel";
      dom.graphDetailPanel.setAttribute("aria-label", "Detalhes do elemento selecionado no mapa");
      mainSplit.appendChild(dom.graphDetailPanel);

      dom.paneGraph.appendChild(mainSplit);
      renderGraphDetailPanel();
    }

    function buildLocalSvgGraphView(data) {
      const container = createEl("div", "atlas-graph-svg-container");
      container.id = "atlas-graph-svg-container";

      // Controls bar inside SVG stage (Zoom, Reset, Legend)
      const stageBar = createEl("div", "atlas-graph-stage-bar");
      const metaSummary = createEl(
        "span",
        "atlas-graph-metrics",
        `Centro: ${data.center.type_prefix} ${truncateText(data.center.label, 38)} · ${data.node_count} nó(s) · ${data.edge_count} conexão(ões) · ${data.depth} salto(s)`
      );
      stageBar.appendChild(metaSummary);

      const zoomControls = createEl("div", "atlas-graph-zoom-controls");
      const btnZoomIn = createEl("button", "btn btn-sm btn-ghost", "+ Zoom");
      btnZoomIn.id = "atlas-graph-zoom-in";
      btnZoomIn.type = "button";
      const btnZoomOut = createEl("button", "btn btn-sm btn-ghost", "− Zoom");
      btnZoomOut.id = "atlas-graph-zoom-out";
      btnZoomOut.type = "button";
      const btnZoomReset = createEl("button", "btn btn-sm btn-ghost", "Centralizar");
      btnZoomReset.id = "atlas-graph-zoom-reset";
      btnZoomReset.type = "button";

      zoomControls.appendChild(btnZoomOut);
      zoomControls.appendChild(btnZoomIn);
      zoomControls.appendChild(btnZoomReset);
      stageBar.appendChild(zoomControls);
      container.appendChild(stageBar);

      const width = 860;
      const height = 520;
      const svg = createSvgEl("svg", {
        id: "atlas-graph-svg",
        class: "atlas-graph-svg",
        viewBox: `0 0 ${width} ${height}`,
        role: "img",
        "aria-label": `Mapa local de conhecimento centrado em ${data.center.label}`,
      });

      // Defs for directional arrowhead markers
      const defs = createSvgEl("defs");
      const marker = createSvgEl("marker", {
        id: "atlas-graph-arrow",
        viewBox: "0 0 10 10",
        refX: "27",
        refY: "5",
        markerWidth: "6",
        markerHeight: "6",
        orient: "auto-start-reverse",
      });
      marker.appendChild(
        createSvgEl("path", {
          d: "M 0 1 L 10 5 L 0 9 z",
          class: "atlas-graph-arrow-path",
        })
      );
      defs.appendChild(marker);
      svg.appendChild(defs);

      const viewportGroup = createSvgEl("g", {
        id: "atlas-graph-viewport",
      });

      function applyViewportTransform() {
        const cx = width / 2;
        const cy = height / 2;
        const tx = cx * (1 - graphZoom) + graphPanX;
        const ty = cy * (1 - graphZoom) + graphPanY;
        viewportGroup.setAttribute("transform", `translate(${tx}, ${ty}) scale(${graphZoom})`);
      }

      btnZoomIn.addEventListener("click", () => {
        graphZoom = Math.min(2.2, Math.round((graphZoom + 0.25) * 100) / 100);
        applyViewportTransform();
      });
      btnZoomOut.addEventListener("click", () => {
        graphZoom = Math.max(0.55, Math.round((graphZoom - 0.25) * 100) / 100);
        applyViewportTransform();
      });
      btnZoomReset.addEventListener("click", () => {
        graphZoom = 1;
        graphPanX = 0;
        graphPanY = 0;
        applyViewportTransform();
      });

      // Drag-to-pan on SVG canvas background (zero continuous loop; only on pointer move while dragging)
      let isDragging = false;
      let dragStartX = 0;
      let dragStartY = 0;
      svg.addEventListener("pointerdown", (e) => {
        if (e.target !== svg) return;
        isDragging = true;
        dragStartX = e.clientX - graphPanX;
        dragStartY = e.clientY - graphPanY;
      });
      svg.addEventListener("pointermove", (e) => {
        if (!isDragging) return;
        graphPanX = e.clientX - dragStartX;
        graphPanY = e.clientY - dragStartY;
        applyViewportTransform();
      });
      svg.addEventListener("pointerup", () => {
        isDragging = false;
      });
      svg.addEventListener("pointerleave", () => {
        isDragging = false;
      });

      const nodes = Array.isArray(data.nodes) ? data.nodes : [];
      const edges = Array.isArray(data.edges) ? data.edges : [];
      const posMap = computeDeterministicRadialLayout(nodes, edges, width, height);

      // Optional faint guide rings for Hop 1 and Hop 2
      const hop1Exists = nodes.some((n) => n.hop === 1);
      const hop2Exists = nodes.some((n) => n.hop >= 2);
      if (hop1Exists) {
        const r1 = hop2Exists ? Math.min(width, height) * 0.25 : Math.min(width, height) * 0.34;
        viewportGroup.appendChild(
          createSvgEl("circle", {
            cx: width / 2,
            cy: height / 2,
            r: r1,
            class: "atlas-graph-orbit-ring",
          })
        );
      }
      if (hop2Exists) {
        const r2 = Math.min(width, height) * 0.43;
        viewportGroup.appendChild(
          createSvgEl("circle", {
            cx: width / 2,
            cy: height / 2,
            r: r2,
            class: "atlas-graph-orbit-ring atlas-graph-orbit-ring-hop2",
          })
        );
      }

      // Pair offset tracking for parallel edges between the same two endpoints
      const pairCounts = Object.create(null);
      const edgesLayer = createSvgEl("g", { class: "atlas-graph-edges-layer" });

      edges.forEach((edge) => {
        const p1 = posMap[edge.source_key];
        const p2 = posMap[edge.target_key];
        if (!p1 || !p2) return;

        const pairKey =
          edge.source_key < edge.target_key
            ? `${edge.source_key}|${edge.target_key}`
            : `${edge.target_key}|${edge.source_key}`;
        const idx = pairCounts[pairKey] || 0;
        pairCounts[pairKey] = idx + 1;

        const dx = p2.x - p1.x;
        const dy = p2.y - p1.y;
        const dist = Math.hypot(dx, dy) || 1;
        const nx = -dy / dist;
        const ny = dx / dist;
        const curveOffset = idx === 0 ? 0 : (idx % 2 === 1 ? 1 : -1) * Math.ceil(idx / 2) * 26;

        const midX = (p1.x + p2.x) / 2 + nx * curveOffset;
        const midY = (p1.y + p2.y) / 2 + ny * curveOffset;

        const isSelected = graphSelectedEdgeId === edge.id;
        const showEdgeLabel = edges.length <= 16 || isSelected;
        const edgeGroup = createSvgEl("g", {
          class: `atlas-graph-edge-group hop-${edge.hop} ${isSelected ? "selected" : ""}`,
          "data-edge-id": edge.id,
          tabindex: "0",
          role: "button",
          "aria-label": `Conexão: ${edge.source_label} ${edge.outgoing_label} ${edge.target_label}`,
        });

        const pathD =
          curveOffset === 0
            ? `M ${p1.x} ${p1.y} L ${p2.x} ${p2.y}`
            : `M ${p1.x} ${p1.y} Q ${midX} ${midY} ${p2.x} ${p2.y}`;

        const lineEl = createSvgEl("path", {
          d: pathD,
          class: `atlas-graph-edge-line ${edge.hop >= 2 ? "atlas-edge-dashed" : "atlas-edge-solid"}`,
          "marker-end": "url(#atlas-graph-arrow)",
        });
        edgeGroup.appendChild(lineEl);

        if (showEdgeLabel) {
          const labelEl = createSvgEl("text", {
            x: midX,
            y: midY - 6,
            "text-anchor": "middle",
            class: "atlas-graph-edge-label",
          });
          labelEl.textContent = edge.relation_label;
          edgeGroup.appendChild(labelEl);
        }

        const onSelectEdge = () => {
          graphSelectedEdgeId = edge.id;
          graphSelectedNodeKey = null;
          renderGraphPaneView();
        };
        edgeGroup.addEventListener("click", onSelectEdge);
        edgeGroup.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelectEdge();
          }
        });

        edgesLayer.appendChild(edgeGroup);
      });
      viewportGroup.appendChild(edgesLayer);

      // Nodes Layer (Distinct shapes + textual prefixes so color is never the sole differentiator)
      const nodesLayer = createSvgEl("g", { class: "atlas-graph-nodes-layer" });
      nodes.forEach((node) => {
        const pos = posMap[node.key];
        if (!pos) return;

        const isSelected = graphSelectedNodeKey === node.key && !graphSelectedEdgeId;
        const nodeGroup = createSvgEl("g", {
          class: [
            "atlas-graph-node",
            `type-${node.type}`,
            `hop-${node.hop}`,
            node.is_center ? "is-center" : "",
            node.available ? "is-available" : "is-unavailable",
            isSelected ? "selected" : "",
          ]
            .filter(Boolean)
            .join(" "),
          transform: `translate(${pos.x}, ${pos.y})`,
          "data-node-key": node.key,
          "data-node-type": node.type,
          "data-node-id": node.id,
          tabindex: "0",
          role: "button",
          "aria-label": `${node.type_prefix} ${node.label}`,
        });

        // Distinct geometric shapes by entity type:
        // concept = circle, reflection = rounded rect, book = rect, highlight = diamond, note = hexagon, unavailable = dashed octagon
        const r = node.is_center ? 24 : 18;
        let shapeEl;
        if (!node.available) {
          shapeEl = createSvgEl("circle", {
            r,
            class: "atlas-node-shape atlas-shape-unavailable",
          });
        } else if (node.type === "reflection") {
          shapeEl = createSvgEl("rect", {
            x: -r,
            y: -r * 0.78,
            width: r * 2,
            height: r * 1.56,
            rx: 8,
            ry: 8,
            class: "atlas-node-shape",
          });
        } else if (node.type === "book") {
          shapeEl = createSvgEl("rect", {
            x: -r * 0.85,
            y: -r,
            width: r * 1.7,
            height: r * 2,
            rx: 3,
            ry: 3,
            class: "atlas-node-shape",
          });
        } else if (node.type === "highlight") {
          shapeEl = createSvgEl("polygon", {
            points: `0,${-r} ${r},0 0,${r} ${-r},0`,
            class: "atlas-node-shape",
          });
        } else if (node.type === "note") {
          const hr = r * 0.86;
          shapeEl = createSvgEl("polygon", {
            points: `${-r},0 ${-hr * 0.6},${-hr} ${hr * 0.6},${-hr} ${r},0 ${hr * 0.6},${hr} ${-hr * 0.6},${hr}`,
            class: "atlas-node-shape",
          });
        } else {
          shapeEl = createSvgEl("circle", {
            r,
            class: "atlas-node-shape",
          });
        }

        if (node.available && node.color) {
          shapeEl.style.stroke = sanitizeHexColor(node.color);
        }
        nodeGroup.appendChild(shapeEl);

        // Prefix badge inside the shape ([C], [R], [L], [D], [N], [!])
        const innerText = createSvgEl("text", {
          x: 0,
          y: 4,
          "text-anchor": "middle",
          class: "atlas-node-inner-prefix",
        });
        innerText.textContent = node.available
          ? (node.type_prefix || "").replace(/[[\]]/g, "")
          : "!";
        nodeGroup.appendChild(innerText);

        // Short label below the node
        const captionMaxLen = nodes.length > 20 ? 18 : 28;
        const captionText = createSvgEl("text", {
          x: 0,
          y: r + 15,
          "text-anchor": "middle",
          class: "atlas-node-caption",
        });
        captionText.textContent = `${node.type_prefix} ${truncateText(node.short_label, captionMaxLen)}`;
        nodeGroup.appendChild(captionText);

        const onSelectNode = () => {
          graphSelectedNodeKey = node.key;
          graphSelectedEdgeId = null;
          renderGraphPaneView();
        };
        nodeGroup.addEventListener("click", onSelectNode);
        nodeGroup.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelectNode();
          }
        });

        nodesLayer.appendChild(nodeGroup);
      });
      viewportGroup.appendChild(nodesLayer);

      svg.appendChild(viewportGroup);
      applyViewportTransform();
      container.appendChild(svg);

      // Legend Bar (Shapes + Prefixes + Line Styles)
      const legend = createEl("div", "atlas-graph-legend");
      legend.id = "atlas-graph-legend";
      [
        "[C] Conceito (círculo)",
        "[R] Reflexão (retângulo arredondado)",
        "[L] Livro (retângulo)",
        "[D] Destaque (losango)",
        "[N] Nota (hexágono)",
        "[Indisponível] Traço tracejado",
        "Linha contínua: 1º salto · Linha tracejada: 2º salto",
      ].forEach((txt) => {
        legend.appendChild(createEl("span", "atlas-graph-legend-item", txt));
      });
      container.appendChild(legend);

      return container;
    }

    function buildAccessibleGraphTextView(data) {
      const box = createEl("div", "atlas-graph-text-view");
      box.id = "atlas-graph-text-view";
      box.setAttribute("role", "region");
      box.setAttribute("aria-label", "Alternativa textual acessível do mapa de conhecimento");

      const header = createEl("div", "rk-section-header");
      header.appendChild(
        createEl(
          "h3",
          "rk-section-title",
          `Conexões do subgrafo centrado em ${data.center.type_prefix} ${data.center.label} (${data.node_count} nós, ${data.edge_count} conexões)`
        )
      );
      box.appendChild(header);

      const listData = Array.isArray(data.accessible_text_list) ? data.accessible_text_list : [];
      if (listData.length === 0) {
        box.appendChild(
          createEl(
            "div",
            "rk-empty-state",
            "Nenhuma conexão encontrada para a entidade central com os filtros atuais."
          )
        );
        return box;
      }

      const ul = createEl("div", "rk-links-list atlas-graph-text-list");
      ul.id = "atlas-graph-text-list";

      listData.forEach((entry) => {
        const card = createEl("article", "rk-link-card atlas-graph-text-item");
        card.dataset.edgeId = entry.edge_id;

        const top = createEl("div", "rk-link-card-header");
        const badges = createEl("div", "rk-badges-row");
        badges.appendChild(createEl("span", "rk-badge", `${entry.hop}º salto`));
        badges.appendChild(createEl("span", "rk-badge rk-badge-relation", entry.relation_label));
        top.appendChild(badges);

        const actions = createEl("div", "rk-link-card-actions");
        const focusTarget =
          entry.counterpart_key === entry.target.key ? entry.target : entry.source;
        if (focusTarget && focusTarget.available) {
          appendNavigationButtonForEntity(actions, focusTarget);
          const btnRecenter = createEl("button", "btn btn-sm btn-ghost", "Centralizar neste nó");
          btnRecenter.type = "button";
          btnRecenter.addEventListener("click", () => {
            graphCenterType = focusTarget.type;
            graphCenterId = focusTarget.id;
            loadLocalKnowledgeGraph();
          });
          actions.appendChild(btnRecenter);
        }
        top.appendChild(actions);
        card.appendChild(top);

        const body = createEl("div", "rk-link-card-body");
        const srcText = entry.source.available
          ? `${entry.source.type_prefix} ${entry.source.label}`
          : "[Indisponível] Referência indisponível";
        const tgtText = entry.target.available
          ? `${entry.target.type_prefix} ${entry.target.label}`
          : "[Indisponível] Referência indisponível";
        body.appendChild(
          createEl("div", "rk-link-target-title", `${srcText} → ${entry.relation_label} → ${tgtText}`)
        );

        if (entry.rationale) {
          const rat = createEl("div", "rk-link-rationale");
          rat.appendChild(createEl("span", "rk-link-rationale-label", "Justificativa: "));
          rat.appendChild(createEl("span", "rk-link-rationale-text", entry.rationale));
          body.appendChild(rat);
        }
        card.appendChild(body);
        ul.appendChild(card);
      });

      box.appendChild(ul);
      return box;
    }

    function renderGraphDetailPanel() {
      if (!dom.graphDetailPanel || !graphData) return;
      clearNode(dom.graphDetailPanel);

      const nodes = Array.isArray(graphData.nodes) ? graphData.nodes : [];
      const edges = Array.isArray(graphData.edges) ? graphData.edges : [];

      if (graphSelectedEdgeId) {
        const edge = edges.find((e) => e.id === graphSelectedEdgeId);
        if (edge) {
          dom.graphDetailPanel.appendChild(createEl("div", "rk-subform-title", "Detalhes da conexão"));
          const badges = createEl("div", "rk-badges-row");
          badges.appendChild(createEl("span", "rk-badge rk-badge-relation", edge.outgoing_label));
          badges.appendChild(createEl("span", "rk-badge", `${edge.hop}º salto`));
          dom.graphDetailPanel.appendChild(badges);

          dom.graphDetailPanel.appendChild(
            createEl(
              "h4",
              "atlas-graph-detail-title",
              `${edge.source_label} → ${edge.outgoing_label} → ${edge.target_label}`
            )
          );
          dom.graphDetailPanel.appendChild(
            createEl(
              "p",
              "atlas-graph-detail-excerpt",
              edge.rationale || "Sem justificativa textual cadastrada para esta conexão."
            )
          );
          return;
        }
      }

      const selectedNode =
        nodes.find((n) => n.key === graphSelectedNodeKey) || graphData.center;
      if (!selectedNode) return;

      dom.graphDetailPanel.appendChild(
        createEl(
          "div",
          "rk-subform-title",
          selectedNode.is_center ? "Entidade central do mapa" : `Nó selecionado (${selectedNode.hop}º salto)`
        )
      );

      const badges = createEl("div", "rk-badges-row");
      badges.appendChild(
        createEl(
          "span",
          selectedNode.available ? "rk-badge rk-badge-type" : "rk-badge rk-badge-unavailable",
          `${selectedNode.type_prefix} ${selectedNode.type_label}`
        )
      );
      badges.appendChild(createEl("span", "rk-badge", `${selectedNode.degree || 0} conexão(ões) no mapa`));
      dom.graphDetailPanel.appendChild(badges);

      dom.graphDetailPanel.appendChild(
        createEl("h4", "atlas-graph-detail-title", selectedNode.label)
      );

      if (!selectedNode.available) {
        dom.graphDetailPanel.appendChild(
          createEl(
            "p",
            "atlas-graph-detail-excerpt",
            "Esta referência está indisponível (removida, tornada privada ou pertencente a uma fonte desativada). Seus metadados são protegidos e ela não pode ser expandida nem navegada."
          )
        );
        return;
      }

      if (selectedNode.subtitle) {
        dom.graphDetailPanel.appendChild(createEl("div", "rk-link-target-meta", selectedNode.subtitle));
      }
      if (selectedNode.excerpt) {
        dom.graphDetailPanel.appendChild(createEl("p", "atlas-graph-detail-excerpt", selectedNode.excerpt));
      }

      if (Array.isArray(selectedNode.tags) && selectedNode.tags.length > 0) {
        const tRow = createEl("div", "rk-tags-row");
        selectedNode.tags.forEach((t) => tRow.appendChild(createEl("span", "rk-tag-pill", `#${t}`)));
        dom.graphDetailPanel.appendChild(tRow);
      }

      const actions = createEl("div", "atlas-graph-detail-actions");
      appendNavigationButtonForEntity(actions, selectedNode);

      if (!selectedNode.is_center && selectedNode.expandable) {
        const btnRecenter = createEl("button", "btn btn-sm btn-primary", "Centralizar mapa neste nó");
        btnRecenter.id = "atlas-graph-btn-recenter";
        btnRecenter.type = "button";
        btnRecenter.addEventListener("click", () => {
          graphCenterType = selectedNode.type;
          graphCenterId = selectedNode.id;
          loadLocalKnowledgeGraph();
        });
        actions.appendChild(btnRecenter);
      }

      dom.graphDetailPanel.appendChild(actions);
    }

    // =========================================================================
    // CICLO DE VIDA PÚBLICO DO ATLAS
    // =========================================================================

    function initIfNeeded() {
      if (isInitialized) return;
      buildDom();
      isInitialized = true;
    }

    function activate(options) {
      initIfNeeded();
      const opts = options || {};
      if (opts.subtab) {
        requestSubtabSwitch(opts.subtab, opts);
      } else {
        requestSubtabSwitch(activeSubtab || "overview", opts);
      }
    }

    function openEntity(entityType, entityId) {
      initIfNeeded();
      if (typeof global.LectoriaOpenAtlasTab === "function") {
        global.LectoriaOpenAtlasTab();
      }
      if (entityType === "concept") {
        requestSubtabSwitch("concepts", { conceptId: entityId });
      } else if (entityType === "reflection") {
        requestSubtabSwitch("reflections", { reflectionId: entityId });
      }
    }

    function openGraph(entityType, entityId, depth) {
      initIfNeeded();
      if (typeof global.LectoriaOpenAtlasTab === "function") {
        global.LectoriaOpenAtlasTab();
      }
      requestSubtabSwitch("graph", {
        graphEntityType: entityType,
        graphEntityId: entityId,
        graphDepth: depth === 2 ? 2 : 1,
      });
    }

    function reset() {
      abortAllRequests();
      setEditorDirty(false);
      editorOpen = false;
      currentReflection = null;
      activeConceptDetail = null;
      overviewData = null;
      conceptsItems = [];
      reflectionsItems = [];
      linksItems = [];
      searchQuery = "";
      searchItems = [];
      searchTotal = 0;
      searchHasSearched = false;
      graphCenterType = "";
      graphCenterId = "";
      graphData = null;
      graphSelectedNodeKey = null;
      graphSelectedEdgeId = null;
      exportInProgress = false;
      closeExportModal();
    }

    return {
      activate,
      openEntity,
      openGraph,
      confirmLeaveIfDirty,
      reset,
    };
  }

  global.LectoriaAtlas = createAtlasController();
})(window);
