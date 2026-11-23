/**
 * Lectoria v1.9.0 (Fase 4) — Núcleo compartilhado de cliente HTTP e utilitários
 * do domínio de Conhecimento Conectado (Atlas).
 *
 * Reutilizado por:
 * - Leitores PDF e EPUB (reader-knowledge.js)
 * - Caderno de Estudos (reader.js)
 * - Integração contextual da Biblioteca (book-knowledge.js)
 * - Futura interface principal do Atlas (Fase 5)
 *
 * Garantias:
 * - Autenticação por cookie de sessão (same-origin) e cache: "no-store".
 * - Envio estrito de CSRF via cabeçalho X-CSRF-Token em métodos mutáveis (nunca na URL).
 * - Zero persistência de dados privados em localStorage ou sessionStorage.
 * - Helpers seguros de DOM (textContent / createElement) e navegação validada.
 */
(function (global) {
  "use strict";

  const RELATION_TYPES = [
    { value: "relates_to", outgoing: "Relaciona-se com", incoming: "Relacionado a" },
    { value: "complements", outgoing: "Complementa", incoming: "Complementado por" },
    { value: "contradicts", outgoing: "Contradiz", incoming: "Contradito por" },
    { value: "exemplifies", outgoing: "Exemplifica", incoming: "Exemplificado por" },
    { value: "grounds", outgoing: "Fundamenta", incoming: "Fundamentado por" },
    { value: "develops", outgoing: "Desenvolve", incoming: "Desenvolvido por" },
    { value: "references", outgoing: "Referencia", incoming: "Referenciado por" },
    { value: "questions", outgoing: "Questiona", incoming: "Questionado por" },
  ];

  const RELATION_MAP = Object.create(null);
  RELATION_TYPES.forEach((r) => {
    RELATION_MAP[r.value] = r;
  });

  const ENTITY_TYPE_LABELS = {
    concept: "Conceito",
    reflection: "Reflexão",
    highlight: "Destaque",
    note: "Nota",
    link: "Conexão",
    book: "Livro",
  };

  const DEFAULT_CONCEPT_COLOR = "#d4a853";
  const HEX_COLOR_RE = /^#[0-9a-fA-F]{3,8}$/;
  const SAFE_BOOK_ID_RE = /^[A-Za-z0-9._:@-]{1,128}$/;
  const EPUB_CFI_RE = /^epubcfi\(.+\)$/i;
  const REQUEST_TIMEOUT_MS = 15000;

  function sanitizeHexColor(value) {
    if (typeof value === "string" && HEX_COLOR_RE.test(value.trim())) {
      return value.trim();
    }
    return DEFAULT_CONCEPT_COLOR;
  }

  function truncateText(text, maxLen) {
    const clean = String(text || "").replace(/\s+/g, " ").trim();
    if (clean.length <= maxLen) return clean;
    return clean.slice(0, Math.max(0, maxLen - 1)).trimEnd() + "…";
  }

  function clearNode(node) {
    if (!node) return;
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function createEl(tag, className, textContent) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (textContent !== undefined && textContent !== null) {
      el.textContent = String(textContent);
    }
    return el;
  }

  function getCsrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)lectoria_csrf=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function mapHttpErrorMessage(status, detail) {
    const cleanDetail = typeof detail === "string" && detail.trim() ? detail.trim() : "";
    if (status === 401) {
      return "Sua sessão expirou. Faça login novamente para gerenciar o conhecimento conectado.";
    }
    if (status === 403) {
      return cleanDetail || "Operação não autorizada ou token de segurança inválido. Recarregue a página.";
    }
    if (status === 404) {
      return cleanDetail || "Recurso não encontrado ou indisponível para sua conta.";
    }
    if (status === 409) {
      return cleanDetail || "Este registro ou conexão já existe no seu Atlas.";
    }
    if (status === 400 || status === 422) {
      return cleanDetail || "Os dados informados são inválidos. Verifique os campos e tente novamente.";
    }
    if (status >= 500) {
      return "O servidor não pôde concluir a operação agora. A navegação continua normalmente.";
    }
    return cleanDetail || "Não foi possível completar a solicitação.";
  }

  async function apiRequest(path, options) {
    const opts = options || {};
    const method = (opts.method || "GET").toUpperCase();
    const headers = new Headers(opts.headers || {});
    if (opts.body !== undefined && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
      const csrf = typeof opts.csrfToken === "string" ? opts.csrfToken : getCsrfToken();
      if (csrf) {
        headers.set("X-CSRF-Token", csrf);
      }
    }

    const timeoutCtrl = new AbortController();
    const externalSignal = opts.signal;
    let timedOut = false;

    const onExternalAbort = () => timeoutCtrl.abort();
    if (externalSignal) {
      if (externalSignal.aborted) {
        const abortErr = new Error("Aborted");
        abortErr.name = "AbortError";
        throw abortErr;
      }
      externalSignal.addEventListener("abort", onExternalAbort, { once: true });
    }

    const timerId = setTimeout(() => {
      timedOut = true;
      timeoutCtrl.abort();
    }, opts.timeoutMs || REQUEST_TIMEOUT_MS);

    try {
      const resp = await fetch(path, {
        method,
        headers,
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
        credentials: "same-origin",
        cache: "no-store",
        signal: timeoutCtrl.signal,
      });

      let data = null;
      const contentType = resp.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        try {
          data = await resp.json();
        } catch (_) {
          data = null;
        }
      }

      if (!resp.ok) {
        const detail = data && typeof data.detail === "string"
          ? data.detail
          : (Array.isArray(data && data.detail) && data.detail[0] && data.detail[0].msg)
            ? data.detail[0].msg
            : "";
        const err = new Error(mapHttpErrorMessage(resp.status, detail));
        err.status = resp.status;
        err.detail = detail;
        throw err;
      }

      return data;
    } catch (err) {
      if (err && err.name === "AbortError") {
        if (timedOut) {
          const timeoutErr = new Error("A requisição demorou demais para responder. Tente novamente.");
          timeoutErr.isTimeout = true;
          throw timeoutErr;
        }
        throw err;
      }
      if (err && typeof err.status === "number") {
        throw err;
      }
      const netErr = new Error("Falha de comunicação com o servidor. Verifique sua conexão e tente novamente.");
      netErr.isNetworkError = true;
      throw netErr;
    } finally {
      clearTimeout(timerId);
      if (externalSignal) {
        externalSignal.removeEventListener("abort", onExternalAbort);
      }
    }
  }

  function buildSafeNavigationSpec(counterpart, currentBookId) {
    if (!counterpart || counterpart.available !== true) return null;
    const type = counterpart.type;
    if (type !== "book" && type !== "highlight" && type !== "note") {
      return null;
    }

    const rawBookId = type === "book" ? (counterpart.book_id || counterpart.id) : counterpart.book_id;
    const targetBookId = typeof rawBookId === "string" ? rawBookId.trim() : "";
    if (!targetBookId || !SAFE_BOOK_ID_RE.test(targetBookId) || targetBookId.includes("..")) {
      return null;
    }

    const rawCfi = typeof counterpart.epub_cfi === "string" ? counterpart.epub_cfi.trim() : "";
    const validCfi = rawCfi && EPUB_CFI_RE.test(rawCfi) ? rawCfi : null;
    const rawPage = Number(counterpart.page);
    const validPage = Number.isInteger(rawPage) && rawPage >= 1 ? rawPage : null;
    const chapterLabel = typeof counterpart.chapter_label === "string" ? counterpart.chapter_label.trim() : "";
    const sameBook = Boolean(currentBookId) && String(targetBookId) === String(currentBookId);

    let url = `/reader/${encodeURIComponent(targetBookId)}`;
    if (validCfi) {
      url += `#cfi=${encodeURIComponent(validCfi)}`;
    } else if (validPage) {
      url += `#page=${validPage}`;
    }

    return {
      sameBook,
      bookId: targetBookId,
      bookFormat: counterpart.book_format || null,
      page: validPage,
      epubCfi: validCfi,
      chapterLabel,
      url,
    };
  }

  function formatLocationMeta(counterpart) {
    if (!counterpart || counterpart.available !== true) return "";
    const parts = [];
    if (counterpart.book_title) {
      parts.push(counterpart.book_title);
    }
    if (counterpart.chapter_label) {
      parts.push(counterpart.chapter_label);
    }
    if (counterpart.epub_cfi) {
      parts.push("Trecho EPUB");
    } else if (Number.isInteger(Number(counterpart.page)) && Number(counterpart.page) >= 1) {
      parts.push(`Página ${Number(counterpart.page)}`);
    }
    if (counterpart.is_own === false && counterpart.author_display_name) {
      parts.push(`Pública • ${counterpart.author_display_name}`);
    }
    return parts.join(" · ");
  }

  function getBookSummary(bookId, signal) {
    return apiRequest(`/api/knowledge/books/${encodeURIComponent(bookId)}/summary`, { signal });
  }

  function getEntitiesSummary(entities, signal) {
    return apiRequest("/api/knowledge/entities/summary", {
      method: "POST",
      body: { entities },
      signal,
    });
  }

  function getEntityConnections(entityType, entityId, params, signal) {
    const qs = new URLSearchParams();
    if (params && params.direction) qs.set("direction", params.direction);
    if (params && params.limit) qs.set("limit", String(params.limit));
    if (params && params.offset !== undefined) qs.set("offset", String(params.offset));
    if (params && params.relation_type) qs.set("relation_type", params.relation_type);
    return apiRequest(
      `/api/knowledge/entities/${encodeURIComponent(entityType)}/${encodeURIComponent(entityId)}/connections?${qs.toString()}`,
      { signal }
    );
  }

  function lookupEntities(params, signal) {
    const qs = new URLSearchParams();
    qs.set("q", String((params && params.q) || "").trim());
    if (params && params.entity_type) qs.set("entity_type", params.entity_type);
    if (params && params.book_id) qs.set("book_id", params.book_id);
    if (params && params.limit) qs.set("limit", String(params.limit));
    return apiRequest(`/api/knowledge/lookup?${qs.toString()}`, { signal });
  }

  function createConcept(payload) {
    return apiRequest("/api/knowledge/concepts", { method: "POST", body: payload });
  }

  function createLink(payload) {
    return apiRequest("/api/knowledge/links", { method: "POST", body: payload });
  }

  function updateLink(linkId, payload) {
    return apiRequest(`/api/knowledge/links/${encodeURIComponent(linkId)}`, {
      method: "PATCH",
      body: payload,
    });
  }

  function deleteLink(linkId) {
    return apiRequest(`/api/knowledge/links/${encodeURIComponent(linkId)}`, {
      method: "DELETE",
    });
  }

  function getAtlasSummary(recentLimit, signal) {
    const lim = Number(recentLimit) > 0 ? Number(recentLimit) : 6;
    return apiRequest(`/api/knowledge/summary?recent_limit=${lim}`, { signal });
  }

  function listConcepts(params, signal) {
    const qs = new URLSearchParams();
    if (params && params.search) qs.set("search", String(params.search).trim());
    if (params && params.tag) qs.set("tag", String(params.tag).trim());
    if (params && params.include_archived) qs.set("include_archived", "true");
    if (params && params.sort) qs.set("sort", params.sort);
    if (params && params.limit) qs.set("limit", String(params.limit));
    if (params && params.offset !== undefined) qs.set("offset", String(params.offset));
    return apiRequest(`/api/knowledge/concepts?${qs.toString()}`, { signal });
  }

  function getConcept(conceptId, signal) {
    return apiRequest(`/api/knowledge/concepts/${encodeURIComponent(conceptId)}`, { signal });
  }

  function updateConcept(conceptId, payload) {
    return apiRequest(`/api/knowledge/concepts/${encodeURIComponent(conceptId)}`, {
      method: "PATCH",
      body: payload,
    });
  }

  function deleteConcept(conceptId) {
    return apiRequest(`/api/knowledge/concepts/${encodeURIComponent(conceptId)}`, {
      method: "DELETE",
    });
  }

  function listReflections(params, signal) {
    const qs = new URLSearchParams();
    if (params && params.search) qs.set("search", String(params.search).trim());
    if (params && params.tag) qs.set("tag", String(params.tag).trim());
    if (params && params.primary_book_id) qs.set("primary_book_id", String(params.primary_book_id).trim());
    if (params && params.include_archived) qs.set("include_archived", "true");
    if (params && params.sort) qs.set("sort", params.sort);
    if (params && params.limit) qs.set("limit", String(params.limit));
    if (params && params.offset !== undefined) qs.set("offset", String(params.offset));
    return apiRequest(`/api/knowledge/reflections?${qs.toString()}`, { signal });
  }

  function getReflection(reflectionId, signal) {
    return apiRequest(`/api/knowledge/reflections/${encodeURIComponent(reflectionId)}`, { signal });
  }

  function createReflection(payload) {
    return apiRequest("/api/knowledge/reflections", {
      method: "POST",
      body: payload,
    });
  }

  function updateReflection(reflectionId, payload) {
    return apiRequest(`/api/knowledge/reflections/${encodeURIComponent(reflectionId)}`, {
      method: "PATCH",
      body: payload,
    });
  }

  function deleteReflection(reflectionId) {
    return apiRequest(`/api/knowledge/reflections/${encodeURIComponent(reflectionId)}`, {
      method: "DELETE",
    });
  }

  function listLinks(params, signal) {
    const qs = new URLSearchParams();
    if (params && params.entity_type) qs.set("entity_type", params.entity_type);
    if (params && params.entity_id) qs.set("entity_id", params.entity_id);
    if (params && params.relation_type) qs.set("relation_type", params.relation_type);
    if (params && params.direction) qs.set("direction", params.direction);
    if (params && params.sort) qs.set("sort", params.sort);
    if (params && params.limit) qs.set("limit", String(params.limit));
    if (params && params.offset !== undefined) qs.set("offset", String(params.offset));
    return apiRequest(`/api/knowledge/links?${qs.toString()}`, { signal });
  }

  function searchKnowledge(params, signal) {
    const qs = new URLSearchParams();
    qs.set("q", String((params && params.q) || "").trim());
    if (params && params.entity_types) qs.set("entity_types", String(params.entity_types).trim());
    if (params && params.book_id) qs.set("book_id", String(params.book_id).trim());
    if (params && params.include_archived) qs.set("include_archived", "true");
    if (params && params.limit) qs.set("limit", String(params.limit));
    if (params && params.offset !== undefined) qs.set("offset", String(params.offset));
    return apiRequest(`/api/knowledge/search?${qs.toString()}`, { signal });
  }

  function getKnowledgeGraph(params, signal) {
    const qs = new URLSearchParams();
    qs.set("entity_type", String((params && params.entity_type) || "").trim());
    qs.set("entity_id", String((params && params.entity_id) || "").trim());
    if (params && params.depth) qs.set("depth", String(params.depth));
    if (params && params.max_nodes) qs.set("max_nodes", String(params.max_nodes));
    if (params && params.max_edges) qs.set("max_edges", String(params.max_edges));
    if (params && params.entity_types) qs.set("entity_types", String(params.entity_types).trim());
    if (params && params.relation_types) qs.set("relation_types", String(params.relation_types).trim());
    return apiRequest(`/api/knowledge/graph?${qs.toString()}`, { signal });
  }

  function buildExportUrl(params) {
    const qs = new URLSearchParams();
    const fmt = (params && params.format) || "markdown_zip";
    qs.set("format", fmt);
    if (params && params.wikilinks) qs.set("wikilinks", "true");
    if (params && params.include_archived === false) {
      qs.set("include_archived", "false");
    } else {
      qs.set("include_archived", "true");
    }
    return `/api/knowledge/export?${qs.toString()}`;
  }

  async function downloadAtlasExport(params, signal) {
    const url = buildExportUrl(params);
    const timeoutCtrl = new AbortController();
    let timedOut = false;

    const onExternalAbort = () => timeoutCtrl.abort();
    if (signal) {
      if (signal.aborted) {
        const abortErr = new Error("Aborted");
        abortErr.name = "AbortError";
        throw abortErr;
      }
      signal.addEventListener("abort", onExternalAbort, { once: true });
    }

    const timerId = setTimeout(() => {
      timedOut = true;
      timeoutCtrl.abort();
    }, 60000);

    try {
      const resp = await fetch(url, {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        signal: timeoutCtrl.signal,
      });

      if (!resp.ok) {
        let detail = "";
        const ct = resp.headers.get("content-type") || "";
        if (ct.includes("application/json")) {
          try {
            const errJson = await resp.json();
            if (errJson && typeof errJson.detail === "string") detail = errJson.detail;
          } catch (_) {}
        }
        const err = new Error(mapHttpErrorMessage(resp.status, detail));
        err.status = resp.status;
        err.detail = detail;
        throw err;
      }

      const blob = await resp.blob();
      const cd = resp.headers.get("content-disposition") || "";
      const match = cd.match(/filename="([^"]+)"/i);
      const fallbackExt = (params && params.format === "json") ? "json" : "zip";
      const filename = match ? match[1] : `lectoria-atlas-export.${fallbackExt}`;
      return { blob, filename };
    } catch (err) {
      if (err && err.name === "AbortError") {
        if (timedOut) {
          const timeoutErr = new Error("A exportação demorou demais para responder. Tente novamente.");
          timeoutErr.isTimeout = true;
          throw timeoutErr;
        }
        throw err;
      }
      if (err && typeof err.status === "number") {
        throw err;
      }
      const netErr = new Error("Falha de comunicação ao exportar o Atlas. Verifique sua conexão.");
      netErr.isNetworkError = true;
      throw netErr;
    } finally {
      clearTimeout(timerId);
      if (signal) {
        signal.removeEventListener("abort", onExternalAbort);
      }
    }
  }

  function buildAtlasDeepLink(entityType, entityId) {
    const cleanId = typeof entityId === "string" ? entityId.trim() : "";
    if (!cleanId || !SAFE_BOOK_ID_RE.test(cleanId) || cleanId.includes("..")) {
      return null;
    }
    if (entityType === "concept") {
      return `/?tab=atlas&subtab=concepts&id=${encodeURIComponent(cleanId)}`;
    }
    if (entityType === "reflection") {
      return `/?tab=atlas&subtab=reflections&id=${encodeURIComponent(cleanId)}`;
    }
    return null;
  }

  function countWords(text) {
    const clean = String(text || "").trim();
    if (!clean) return 0;
    return clean.split(/\s+/).filter(Boolean).length;
  }

  function sanitizeMarkdownUrl(rawUrl) {
    if (typeof rawUrl !== "string") return null;
    // Remove caracteres de controle ASCII e Unicode invisíveis usados para ofuscar javascript:
    const stripped = rawUrl
      .replace(/[\u0000-\u0020\u007F-\u00A0\u1680\u2000-\u200F\u2028-\u202F\u205F-\u206F\uFEFF]/g, "")
      .trim();
    if (!stripped) return null;

    // Caminhos relativos internos seguros (ex.: /reader/..., /?tab=atlas...)
    if (stripped.startsWith("/") && !stripped.startsWith("//") && !stripped.startsWith("/\\")) {
      return stripped;
    }

    // Bloqueia explicitamente esquemas perigosos ou especiais
    const lower = stripped.toLowerCase();
    if (
      lower.startsWith("javascript:") ||
      lower.startsWith("vbscript:") ||
      lower.startsWith("data:") ||
      lower.startsWith("blob:") ||
      lower.startsWith("file:") ||
      lower.startsWith("about:") ||
      lower.startsWith("//")
    ) {
      return null;
    }

    try {
      const parsed = new URL(stripped);
      if (parsed.protocol === "http:" || parsed.protocol === "https:" || parsed.protocol === "mailto:") {
        return parsed.href;
      }
    } catch (_) {
      return null;
    }
    return null;
  }

  // Tokenizador inline seguro: constrói APENAS TextNodes e elementos strong/em/code/a/span (zero innerHTML)
  const INLINE_TOKEN_RE =
    /(!\[([^\]]*)\]\(([^)]*)\)|\[([^\]]+)\]\(([^)]*)\)|`([^`]+)`|\*\*([^*]+)\*\*|\*([^*]+)\*)/g;

  function appendInlineMarkdownNodes(parentEl, text) {
    const src = String(text || "");
    if (!src) return;
    let lastIndex = 0;
    INLINE_TOKEN_RE.lastIndex = 0;
    let match;

    while ((match = INLINE_TOKEN_RE.exec(src)) !== null) {
      if (match.index > lastIndex) {
        parentEl.appendChild(document.createTextNode(src.slice(lastIndex, match.index)));
      }

      if (match[1].startsWith("![")) {
        // Imagens externas são bloqueadas por privacidade (Seção 12.3)
        const altText = (match[2] || "").trim() || "imagem";
        parentEl.appendChild(
          createEl("span", "atlas-md-image-blocked", `[Imagem externa bloqueada por privacidade: ${altText}]`)
        );
      } else if (match[4] !== undefined) {
        // Link [label](url)
        const label = match[4];
        const rawHref = match[5] || "";
        const safeHref = sanitizeMarkdownUrl(rawHref);
        if (safeHref) {
          const a = createEl("a", "atlas-md-link", label);
          a.setAttribute("href", safeHref);
          a.setAttribute("rel", "noopener noreferrer");
          if (!safeHref.startsWith("/")) {
            a.setAttribute("target", "_blank");
          }
          parentEl.appendChild(a);
        } else {
          parentEl.appendChild(createEl("span", "atlas-md-link-blocked", label));
        }
      } else if (match[6] !== undefined) {
        parentEl.appendChild(createEl("code", "atlas-md-inline-code", match[6]));
      } else if (match[7] !== undefined) {
        parentEl.appendChild(createEl("strong", "", match[7]));
      } else if (match[8] !== undefined) {
        parentEl.appendChild(createEl("em", "", match[8]));
      }

      lastIndex = INLINE_TOKEN_RE.lastIndex;
    }

    if (lastIndex < src.length) {
      parentEl.appendChild(document.createTextNode(src.slice(lastIndex)));
    }
  }

  /**
   * Renderiza Markdown em `container` construindo exclusivamente nós DOM explícitos
   * (`createElement` + `createTextNode`). Nunca invoca `innerHTML`, `outerHTML` ou `insertAdjacentHTML`.
   * Qualquer HTML bruto no texto torna-se texto literal inerte.
   */
  function renderSafeMarkdownToDom(container, markdownText) {
    if (!container) return;
    clearNode(container);

    const raw = String(markdownText || "").replace(/\r\n?/g, "\n");
    if (!raw.trim()) {
      container.appendChild(createEl("p", "rk-muted-text", "Nenhum conteúdo para visualizar."));
      return;
    }

    const lines = raw.split("\n");
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();

      if (!trimmed) {
        i++;
        continue;
      }

      // Bloco de código cercado ```
      if (trimmed.startsWith("```")) {
        const codeLines = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith("```")) {
          codeLines.push(lines[i]);
          i++;
        }
        if (i < lines.length) i++; // consome ``` de fechamento
        const pre = createEl("pre", "atlas-md-pre");
        const code = createEl("code", "atlas-md-code-block", codeLines.join("\n"));
        pre.appendChild(code);
        container.appendChild(pre);
        continue;
      }

      // Linha horizontal --- ou ***
      if (/^(-{3,}|\*{3,})$/.test(trimmed)) {
        container.appendChild(createEl("hr", "atlas-md-hr"));
        i++;
        continue;
      }

      // Títulos # a ####
      const headingMatch = line.match(/^\s*(#{1,4})\s+(.+)$/);
      if (headingMatch) {
        const level = Math.min(4, Math.max(1, headingMatch[1].length));
        const hEl = createEl(`h${level}`, `atlas-md-h${level}`);
        appendInlineMarkdownNodes(hEl, headingMatch[2].trim());
        container.appendChild(hEl);
        i++;
        continue;
      }

      // Citação >
      if (/^\s*>\s?/.test(line)) {
        const quoteLines = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          quoteLines.push(lines[i].replace(/^\s*>\s?/, ""));
          i++;
        }
        const bq = createEl("blockquote", "atlas-md-blockquote");
        appendInlineMarkdownNodes(bq, quoteLines.join(" "));
        container.appendChild(bq);
        continue;
      }

      // Lista não ordenada (- ou *)
      if (/^\s*[-*]\s+/.test(line)) {
        const ul = createEl("ul", "atlas-md-list");
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
          const li = createEl("li", "atlas-md-li");
          appendInlineMarkdownNodes(li, lines[i].replace(/^\s*[-*]\s+/, ""));
          ul.appendChild(li);
          i++;
        }
        container.appendChild(ul);
        continue;
      }

      // Lista ordenada (1. )
      if (/^\s*\d+\.\s+/.test(line)) {
        const ol = createEl("ol", "atlas-md-list");
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
          const li = createEl("li", "atlas-md-li");
          appendInlineMarkdownNodes(li, lines[i].replace(/^\s*\d+\.\s+/, ""));
          ol.appendChild(li);
          i++;
        }
        container.appendChild(ol);
        continue;
      }

      // Parágrafo comum (agrupa linhas contínuas que não iniciam outro bloco)
      const paraLines = [];
      while (
        i < lines.length &&
        lines[i].trim() !== "" &&
        !lines[i].trim().startsWith("```") &&
        !/^(-{3,}|\*{3,})$/.test(lines[i].trim()) &&
        !/^\s*#{1,4}\s+/.test(lines[i]) &&
        !/^\s*>\s?/.test(lines[i]) &&
        !/^\s*[-*]\s+/.test(lines[i]) &&
        !/^\s*\d+\.\s+/.test(lines[i])
      ) {
        paraLines.push(lines[i].trim());
        i++;
      }
      const p = createEl("p", "atlas-md-p");
      appendInlineMarkdownNodes(p, paraLines.join(" "));
      container.appendChild(p);
    }
  }

  global.LectoriaKnowledgeApi = {
    RELATION_TYPES,
    RELATION_MAP,
    ENTITY_TYPE_LABELS,
    DEFAULT_CONCEPT_COLOR,
    sanitizeHexColor,
    truncateText,
    clearNode,
    createEl,
    getCsrfToken,
    mapHttpErrorMessage,
    apiRequest,
    buildSafeNavigationSpec,
    formatLocationMeta,
    getBookSummary,
    getEntitiesSummary,
    getEntityConnections,
    lookupEntities,
    createConcept,
    createLink,
    updateLink,
    deleteLink,
    getAtlasSummary,
    listConcepts,
    getConcept,
    updateConcept,
    deleteConcept,
    listReflections,
    getReflection,
    createReflection,
    updateReflection,
    deleteReflection,
    listLinks,
    searchKnowledge,
    getKnowledgeGraph,
    buildExportUrl,
    downloadAtlasExport,
    buildAtlasDeepLink,
    countWords,
    sanitizeMarkdownUrl,
    renderSafeMarkdownToDom,
  };
})(window);
