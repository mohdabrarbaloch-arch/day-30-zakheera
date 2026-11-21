/* ==========================================================================
   Zakheera — frontend
   Vanilla JS, no build step. Everything the API returns is treated as
   untrusted text and escaped before it reaches the DOM: document content and
   questions are user-supplied, and a knowledge base that renders a colleague's
   uploaded file as HTML is a stored-XSS hole.
   ========================================================================== */

(() => {
  "use strict";

  const TOKEN_KEY = "zakheera.token";
  const state = {
    token: localStorage.getItem(TOKEN_KEY) || "",
    user: null,
    workspaces: [],
    activeId: null,
    activeWorkspace: null,
    documents: [],
    lastAnswer: null,
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  /* ── Utilities ─────────────────────────────────────────────────────── */

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatBytes(bytes) {
    if (!bytes) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const value = bytes / Math.pow(1024, i);
    return `${value >= 10 || i === 0 ? Math.round(value) : value.toFixed(1)} ${units[i]}`;
  }

  function formatNumber(value) {
    return new Intl.NumberFormat("en-US").format(value ?? 0);
  }

  function formatDate(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  }

  function fileIcon(name) {
    const lower = (name || "").toLowerCase();
    if (lower.endsWith(".pdf")) return "📕";
    if (lower.endsWith(".docx")) return "📘";
    if (lower.endsWith(".md") || lower.endsWith(".markdown")) return "📝";
    return "📄";
  }

  function toast(message, kind = "info") {
    const root = $("#toast-root");
    const el = document.createElement("div");
    el.className = `toast is-${kind}`;
    el.textContent = message;
    root.appendChild(el);
    setTimeout(() => {
      el.style.transition = "opacity .3s";
      el.style.opacity = "0";
      setTimeout(() => el.remove(), 300);
    }, kind === "error" ? 5200 : 3200);
  }

  /* ── API ───────────────────────────────────────────────────────────── */

  async function api(path, { method = "GET", body, isForm = false } = {}) {
    const headers = {};
    if (state.token) headers.Authorization = `Bearer ${state.token}`;
    if (body && !isForm) headers["Content-Type"] = "application/json";

    const response = await fetch(path, {
      method,
      headers,
      body: isForm ? body : body ? JSON.stringify(body) : undefined,
    });

    if (response.status === 204) return null;

    let payload = null;
    const text = await response.text();
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        payload = { detail: text };
      }
    }

    if (!response.ok) {
      if (response.status === 401 && state.token) {
        signOut();
        throw new Error("Your session expired. Please sign in again.");
      }
      const detail = payload?.detail;
      let message = "Something went wrong.";
      if (typeof detail === "string") message = detail;
      else if (Array.isArray(payload?.errors) && payload.errors.length) {
        message = payload.errors.map((e) => `${e.field}: ${e.message}`).join("; ");
      } else if (Array.isArray(detail)) {
        message = detail.map((d) => d.msg || String(d)).join("; ");
      }
      throw new Error(message);
    }

    return payload;
  }

  /* ── Auth ──────────────────────────────────────────────────────────── */

  function showAuth() {
    $("#auth-view").classList.remove("is-hidden");
    $("#app-view").classList.add("is-hidden");
  }

  function showApp() {
    $("#auth-view").classList.add("is-hidden");
    $("#app-view").classList.remove("is-hidden");
    $("#user-chip").textContent = state.user?.display_name || state.user?.email || "";
    $("#admin-btn").classList.toggle("is-hidden", !state.user?.is_admin);
  }

  function setToken(token, user) {
    state.token = token;
    state.user = user;
    localStorage.setItem(TOKEN_KEY, token);
  }

  function signOut() {
    state.token = "";
    state.user = null;
    state.workspaces = [];
    state.activeId = null;
    state.activeWorkspace = null;
    state.documents = [];
    localStorage.removeItem(TOKEN_KEY);
    showAuth();
  }

  async function handleAuthSubmit(event, mode) {
    event.preventDefault();
    const form = event.target;
    const errorBox = $("#auth-error");
    errorBox.classList.add("is-hidden");

    const data = Object.fromEntries(new FormData(form).entries());
    const button = $("button[type=submit]", form);
    const original = button.textContent;
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span>';

    try {
      const payload = await api(`/api/auth/${mode}`, { method: "POST", body: data });
      setToken(payload.access_token, payload.user);
      form.reset();
      showApp();
      await loadWorkspaces();
    } catch (error) {
      errorBox.textContent = error.message;
      errorBox.classList.remove("is-hidden");
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  }

  /* ── Workspaces ────────────────────────────────────────────────────── */

  async function loadWorkspaces() {
    state.workspaces = await api("/api/workspaces");
    renderWorkspaceList();

    if (state.workspaces.length === 0) {
      state.activeId = null;
      renderEmptyState();
      return;
    }

    const stillExists = state.workspaces.some((w) => w.id === state.activeId);
    if (!stillExists) state.activeId = state.workspaces[0].id;
    await selectWorkspace(state.activeId);
  }

  function renderWorkspaceList() {
    const list = $("#workspace-list");
    if (state.workspaces.length === 0) {
      list.innerHTML = '<p class="sidebar-note" style="padding:8px 4px">No knowledge bases yet.</p>';
      return;
    }
    list.innerHTML = state.workspaces
      .map(
        (w) => `
        <button class="ws-item ${w.id === state.activeId ? "is-active" : ""}" data-workspace="${w.id}">
          <span class="ws-item-name">${escapeHtml(w.name)}</span>
          <span class="ws-item-meta">${escapeHtml(w.slug)}</span>
        </button>`
      )
      .join("");
  }

  function renderEmptyState() {
    $("#empty-state").classList.remove("is-hidden");
    $("#workspace-view").classList.add("is-hidden");
    $("#admin-view").classList.add("is-hidden");
  }

  async function selectWorkspace(id) {
    state.activeId = id;
    renderWorkspaceList();
    $("#empty-state").classList.add("is-hidden");
    $("#admin-view").classList.add("is-hidden");
    $("#workspace-view").classList.remove("is-hidden");

    const [stats, documents] = await Promise.all([
      api(`/api/workspaces/${id}/stats`),
      api(`/api/workspaces/${id}/documents`),
    ]);

    state.activeWorkspace = stats.workspace;
    state.documents = documents;

    $("#ws-name").textContent = stats.workspace.name;
    $("#ws-desc").textContent = stats.workspace.description || "";
    renderStats(stats);
    renderDocuments();
    renderSamples();
    $("#answer-area").innerHTML = "";
    $("#ask-meta").textContent = "";
  }

  function renderStats(stats) {
    const answerRate = stats.question_count ? Math.round(stats.answer_rate * 100) : null;
    const cards = [
      { label: "Documents", value: formatNumber(stats.document_count), sub: `${formatNumber(stats.chunk_count)} passages` },
      { label: "Corpus size", value: formatNumber(stats.total_chars), sub: "characters indexed" },
      { label: "Questions", value: formatNumber(stats.question_count), sub: `${formatNumber(stats.abstained_count)} abstained` },
      {
        label: "Answer rate",
        value: answerRate === null ? "—" : `${answerRate}%`,
        sub: answerRate === null ? "no questions yet" : `${formatNumber(stats.answered_count)} answered`,
        cls: answerRate === null ? "" : answerRate >= 60 ? "is-teal" : "is-rose",
      },
      { label: "Median latency", value: stats.question_count ? `${stats.median_latency_ms}ms` : "—", sub: "retrieval + synthesis" },
    ];

    $("#stat-row").innerHTML = cards
      .map(
        (c) => `
        <div class="stat">
          <div class="stat-label">${escapeHtml(c.label)}</div>
          <div class="stat-value ${c.cls || ""}">${escapeHtml(c.value)}</div>
          <div class="stat-sub">${escapeHtml(c.sub)}</div>
        </div>`
      )
      .join("");
  }

  function renderDocuments() {
    const list = $("#doc-list");
    $("#doc-count").textContent = state.documents.length
      ? `${state.documents.length} file${state.documents.length === 1 ? "" : "s"}`
      : "";

    if (state.documents.length === 0) {
      list.innerHTML =
        '<div class="empty-note">No documents yet. Add a policy, a handbook, a spec — anything you want to be able to ask questions about.</div>';
      return;
    }

    list.innerHTML = state.documents
      .map(
        (d) => `
        <div class="doc-item">
          <div class="doc-icon" aria-hidden="true">${fileIcon(d.filename)}</div>
          <div class="doc-body">
            <div class="doc-title" title="${escapeHtml(d.title)}">${escapeHtml(d.title)}</div>
            <div class="doc-meta">${formatNumber(d.char_count)} chars · ${d.chunk_count} passages · ${formatBytes(d.size_bytes)}</div>
          </div>
          <div class="doc-actions">
            <button class="icon-btn" data-view-doc="${d.id}" title="Preview" aria-label="Preview document">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>
            <button class="icon-btn" data-delete-doc="${d.id}" title="Delete" aria-label="Delete document">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>
            </button>
          </div>
        </div>`
      )
      .join("");
  }

  function renderSamples() {
    const samples = [
      "How many annual leave days do I get?",
      "What is the approval threshold for a 100,000 PKR expense?",
      "How quickly must I report a security incident?",
      "What is the hotel cap in Karachi?",
      "What is the company policy on cryptocurrency trading?",
    ];
    $("#ask-samples").innerHTML = samples
      .map((s) => `<button type="button" class="sample-chip" data-sample="${escapeHtml(s)}">${escapeHtml(s)}</button>`)
      .join("");
  }

  /* ── Ask ───────────────────────────────────────────────────────────── */

  async function askQuestion(question) {
    const area = $("#answer-area");
    const topK = parseInt($("#ask-topk").value, 10) || 8;

    area.innerHTML = `
      <div class="answer-card">
        <div class="skeleton" style="width:38%;margin-bottom:12px"></div>
        <div class="skeleton" style="width:100%;margin-bottom:7px"></div>
        <div class="skeleton" style="width:92%;margin-bottom:7px"></div>
        <div class="skeleton" style="width:64%"></div>
      </div>`;

    try {
      const result = await api(`/api/workspaces/${state.activeId}/query`, {
        method: "POST",
        body: { question, top_k: topK },
      });
      state.lastAnswer = result;
      renderAnswer(result);
      $("#ask-meta").textContent = `${result.latency_ms}ms · ${result.synthesis}`;
      // Refresh stats so the counters reflect the question just asked.
      const stats = await api(`/api/workspaces/${state.activeId}/stats`);
      renderStats(stats);
    } catch (error) {
      area.innerHTML = `<div class="answer-card is-abstain"><p class="abstain-text">${escapeHtml(error.message)}</p></div>`;
    }
  }

  function renderAnswer(result) {
    const area = $("#answer-area");
    const coveragePct = Math.round(result.coverage * 100);
    const thresholdPct = Math.round(result.coverage_threshold * 100);
    const passed = result.coverage >= result.coverage_threshold;

    const coverageBlock = `
      <div class="coverage-bar">
        <div class="coverage-head">
          <span>corpus coverage</span>
          <span>${coveragePct}% · threshold ${thresholdPct}%</span>
        </div>
        <div class="coverage-track">
          <div class="coverage-fill ${passed ? "is-pass" : "is-fail"}" style="width:${Math.min(coveragePct, 100)}%"></div>
          <div class="coverage-threshold" style="left:${Math.min(thresholdPct, 100)}%"></div>
        </div>
        ${
          result.missing_terms?.length
            ? `<div class="missing-terms">Not found in the corpus: ${result.missing_terms
                .map((t) => `<code>${escapeHtml(t)}</code>`)
                .join("")}</div>`
            : ""
        }
      </div>`;

    if (!result.answered) {
      area.innerHTML = `
        <div class="answer-card is-abstain">
          <span class="answer-badge badge-abstain">Abstained</span>
          <p class="abstain-text">${escapeHtml(result.abstain_reason || "No answer found.")}</p>
          ${coverageBlock}
        </div>
        ${renderRetrieved(result.retrieved)}`;
      return;
    }

    // Turn "[1]" markers into clickable references that scroll to the citation.
    const answerHtml = escapeHtml(result.answer).replace(
      /\[(\d+)\]/g,
      '<span class="cite-ref" data-cite="$1" role="button" tabindex="0">$1</span>'
    );

    const citationsHtml = result.citations
      .map(
        (c) => `
        <div class="citation" id="citation-${c.marker}">
          <div class="citation-head">
            <span class="citation-marker">[${c.marker}]</span>
            <span class="citation-doc">${escapeHtml(c.document_title)}</span>
            <span class="citation-offsets">chars ${formatNumber(c.start_char)}–${formatNumber(c.end_char)}</span>
          </div>
          <blockquote class="citation-quote">${escapeHtml(c.quote)}</blockquote>
        </div>`
      )
      .join("");

    area.innerHTML = `
      <div class="answer-card">
        <span class="answer-badge badge-answered">Answered from ${result.citations.length} source${result.citations.length === 1 ? "" : "s"}</span>
        <div class="answer-text">${answerHtml}</div>
        ${coverageBlock}
      </div>
      <div class="citations">
        <div class="citations-head">Sources — quoted verbatim, with exact offsets</div>
        ${citationsHtml}
      </div>
      ${renderRetrieved(result.retrieved)}`;
  }

  function renderRetrieved(retrieved) {
    if (!retrieved?.length) return "";
    const rows = retrieved
      .map(
        (p, index) => `
        <div class="passage">
          <div class="passage-head">
            <span class="passage-doc">${escapeHtml(p.document_title)}</span>
            <span class="citation-offsets">#${p.chunk_ordinal} · chars ${formatNumber(p.start_char)}–${formatNumber(p.end_char)}</span>
            <span class="passage-scores">
              <span class="score-pill is-bm25" title="BM25 lexical score">bm25 ${p.bm25_score.toFixed(2)}</span>
              <span class="score-pill is-vec" title="TF-IDF cosine similarity">vec ${p.vector_score.toFixed(3)}</span>
              <span class="score-pill is-fused" title="Reciprocal Rank Fusion score">rrf ${p.fused_score.toFixed(4)}</span>
            </span>
          </div>
          <p class="passage-text" data-passage="${index}">${escapeHtml(p.text)}</p>
          <button class="passage-toggle" data-toggle-passage="${index}">Show full passage</button>
        </div>`
      )
      .join("");

    return `
      <details class="retrieved">
        <summary>Retrieved passages (${retrieved.length}) — how the ranking was built</summary>
        <div class="retrieved-list">${rows}</div>
      </details>`;
  }

  /* ── Modals ────────────────────────────────────────────────────────── */

  function openModal(html) {
    $("#modal-content").innerHTML = html;
    $("#modal-root").classList.remove("is-hidden");
    const firstInput = $("#modal-content input, #modal-content textarea");
    if (firstInput) setTimeout(() => firstInput.focus(), 60);
  }

  function closeModal() {
    $("#modal-root").classList.add("is-hidden");
    $("#modal-content").innerHTML = "";
  }

  function newWorkspaceModal() {
    openModal(`
      <h3>New knowledge base</h3>
      <p class="modal-sub">A knowledge base is a set of documents you can ask questions about. Keep them focused — one topic per base retrieves better than one giant pile.</p>
      <form class="modal-form" id="new-workspace-form">
        <label class="field">
          <span>Name</span>
          <input name="name" required maxlength="160" placeholder="e.g. Engineering Handbook">
        </label>
        <label class="field">
          <span>Description <span style="font-weight:400;color:var(--paper-faint)">(optional)</span></span>
          <textarea name="description" rows="2" maxlength="2000" placeholder="What lives in here?"></textarea>
        </label>
        <div class="modal-actions">
          <button type="button" class="btn btn-ghost" data-close-modal>Cancel</button>
          <button type="submit" class="btn btn-primary">Create</button>
        </div>
      </form>`);
  }

  function addDocumentModal() {
    openModal(`
      <h3>Add a document</h3>
      <p class="modal-sub">Upload a file, or paste text directly. Supported: .txt, .md, .pdf, .docx</p>
      <div class="tabs" role="tablist">
        <button class="tab is-active" data-doc-tab="upload" role="tab">Upload file</button>
        <button class="tab" data-doc-tab="paste" role="tab">Paste text</button>
      </div>

      <form class="modal-form" id="upload-form">
        <div class="dropzone" id="dropzone">
          <div class="dropzone-icon" aria-hidden="true">📄</div>
          <div class="dropzone-text">Drop a file here, or click to choose</div>
          <div class="dropzone-hint">.txt · .md · .pdf · .docx — up to 5 MB</div>
          <div class="dropzone-file is-hidden" id="dropzone-file"></div>
          <input type="file" id="file-input" accept=".txt,.md,.markdown,.pdf,.docx" class="is-hidden">
        </div>
        <label class="field">
          <span>Title <span style="font-weight:400;color:var(--paper-faint)">(optional — defaults to the filename)</span></span>
          <input name="title" maxlength="255" placeholder="e.g. Leave Policy 2026">
        </label>
        <div class="modal-actions">
          <button type="button" class="btn btn-ghost" data-close-modal>Cancel</button>
          <button type="submit" class="btn btn-primary" id="upload-submit" disabled>Add document</button>
        </div>
      </form>

      <form class="modal-form is-hidden" id="paste-form">
        <label class="field">
          <span>Title</span>
          <input name="title" required maxlength="255" placeholder="e.g. Meeting notes — Q4 planning">
        </label>
        <label class="field">
          <span>Content</span>
          <textarea name="content" rows="9" required placeholder="Paste the text you want to be able to ask questions about…"></textarea>
        </label>
        <div class="modal-actions">
          <button type="button" class="btn btn-ghost" data-close-modal>Cancel</button>
          <button type="submit" class="btn btn-primary">Add document</button>
        </div>
      </form>`);
  }

  async function viewDocumentModal(id) {
    try {
      const doc = await api(`/api/workspaces/${state.activeId}/documents/${id}`);
      openModal(`
        <h3>${escapeHtml(doc.title)}</h3>
        <p class="modal-sub">${escapeHtml(doc.filename)} · ${formatNumber(doc.char_count)} characters · ${doc.chunk_count} passages · added ${formatDate(doc.created_at)}</p>
        <pre style="white-space:pre-wrap;font-family:var(--font-mono);font-size:12.5px;line-height:1.65;color:var(--paper-dim);background:var(--ink-800);border:1px solid var(--ink-700);border-radius:8px;padding:14px;max-height:52vh;overflow:auto;margin:0">${escapeHtml(doc.preview)}</pre>
        <div class="modal-actions" style="margin-top:16px">
          <button type="button" class="btn btn-ghost" data-close-modal>Close</button>
        </div>`);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function confirmModal(title, message, confirmLabel, onConfirm) {
    openModal(`
      <h3>${escapeHtml(title)}</h3>
      <p class="modal-sub">${escapeHtml(message)}</p>
      <div class="modal-actions">
        <button type="button" class="btn btn-ghost" data-close-modal>Cancel</button>
        <button type="button" class="btn btn-danger-ghost" id="confirm-action">${escapeHtml(confirmLabel)}</button>
      </div>`);
    $("#confirm-action").addEventListener("click", async () => {
      closeModal();
      await onConfirm();
    });
  }

  /* ── Admin ─────────────────────────────────────────────────────────── */

  async function showAdmin() {
    try {
      const stats = await api("/api/admin/stats");
      $("#empty-state").classList.add("is-hidden");
      $("#workspace-view").classList.add("is-hidden");
      $("#admin-view").classList.remove("is-hidden");

      const cards = [
        { label: "Users", value: formatNumber(stats.user_count) },
        { label: "Knowledge bases", value: formatNumber(stats.workspace_count) },
        { label: "Documents", value: formatNumber(stats.document_count) },
        { label: "Passages", value: formatNumber(stats.chunk_count) },
        { label: "Questions", value: formatNumber(stats.question_count) },
        {
          label: "Answer rate",
          value: stats.question_count ? `${Math.round(stats.answer_rate * 100)}%` : "—",
          cls: stats.answer_rate >= 0.6 ? "is-teal" : "is-rose",
        },
      ];
      $("#admin-stats").innerHTML = cards
        .map(
          (c) => `
          <div class="stat">
            <div class="stat-label">${escapeHtml(c.label)}</div>
            <div class="stat-value ${c.cls || ""}">${escapeHtml(c.value)}</div>
          </div>`
        )
        .join("");

      $("#admin-questions").innerHTML = stats.top_questions.length
        ? stats.top_questions
            .map(
              (q) => `
              <div class="question-row">
                <span class="question-text" title="${escapeHtml(q.question)}">${escapeHtml(q.question)}</span>
                <span class="question-count">×${q.count}</span>
              </div>`
            )
            .join("")
        : '<div class="empty-note">No questions asked yet.</div>';
    } catch (error) {
      toast(error.message, "error");
    }
  }

  /* ── Event wiring ──────────────────────────────────────────────────── */

  function wireEvents() {
    // Auth tabs
    $$("[data-auth-tab]").forEach((tab) => {
      tab.addEventListener("click", () => {
        const mode = tab.dataset.authTab;
        $$("[data-auth-tab]").forEach((t) => {
          const active = t === tab;
          t.classList.toggle("is-active", active);
          t.setAttribute("aria-selected", String(active));
        });
        $("#login-form").classList.toggle("is-hidden", mode !== "login");
        $("#register-form").classList.toggle("is-hidden", mode !== "register");
        $("#auth-error").classList.add("is-hidden");
      });
    });

    $("#login-form").addEventListener("submit", (e) => handleAuthSubmit(e, "login"));
    $("#register-form").addEventListener("submit", (e) => handleAuthSubmit(e, "register"));
    $("#logout-btn").addEventListener("click", signOut);

    // Mobile nav
    $("#nav-toggle").addEventListener("click", () => {
      const sidebar = $("#sidebar");
      const open = sidebar.classList.toggle("is-open");
      $("#nav-toggle").setAttribute("aria-expanded", String(open));
    });

    // Workspace selection
    $("#workspace-list").addEventListener("click", (e) => {
      const button = e.target.closest("[data-workspace]");
      if (!button) return;
      $("#sidebar").classList.remove("is-open");
      selectWorkspace(Number(button.dataset.workspace)).catch((err) => toast(err.message, "error"));
    });

    // New workspace
    const openNewWorkspace = () => newWorkspaceModal();
    $("#new-workspace-btn").addEventListener("click", openNewWorkspace);
    $$('[data-action="new-workspace"]').forEach((b) => b.addEventListener("click", openNewWorkspace));

    // Add document
    $("#add-doc-btn").addEventListener("click", addDocumentModal);

    // Delete workspace
    $("#delete-ws-btn").addEventListener("click", () => {
      const ws = state.activeWorkspace;
      if (!ws) return;
      confirmModal(
        "Delete this knowledge base?",
        `"${ws.name}" and all ${state.documents.length} of its documents will be permanently removed. This cannot be undone.`,
        "Delete knowledge base",
        async () => {
          try {
            await api(`/api/workspaces/${ws.id}`, { method: "DELETE" });
            toast("Knowledge base deleted", "success");
            state.activeId = null;
            await loadWorkspaces();
          } catch (error) {
            toast(error.message, "error");
          }
        }
      );
    });

    // Admin
    $("#admin-btn").addEventListener("click", showAdmin);
    $("#admin-back").addEventListener("click", () => {
      if (state.activeId) selectWorkspace(state.activeId).catch((e) => toast(e.message, "error"));
      else renderEmptyState();
    });

    // Ask
    $("#ask-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const question = $("#ask-input").value.trim();
      if (question) askQuestion(question);
    });

    $("#ask-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        $("#ask-form").requestSubmit();
      }
    });

    // Sample chips
    $("#ask-samples").addEventListener("click", (e) => {
      const chip = e.target.closest("[data-sample]");
      if (!chip) return;
      $("#ask-input").value = chip.dataset.sample;
      askQuestion(chip.dataset.sample);
    });

    // Answer area: citation refs, passage toggles
    $("#answer-area").addEventListener("click", (e) => {
      const ref = e.target.closest("[data-cite]");
      if (ref) {
        const target = $(`#citation-${ref.dataset.cite}`);
        if (target) {
          target.scrollIntoView({ behavior: "smooth", block: "center" });
          target.classList.add("is-flash");
          setTimeout(() => target.classList.remove("is-flash"), 1400);
        }
        return;
      }
      const toggle = e.target.closest("[data-toggle-passage]");
      if (toggle) {
        const passage = $(`[data-passage="${toggle.dataset.togglePassage}"]`);
        if (passage) {
          const open = passage.classList.toggle("is-open");
          toggle.textContent = open ? "Collapse" : "Show full passage";
        }
      }
    });

    // Document list actions
    $("#doc-list").addEventListener("click", (e) => {
      const view = e.target.closest("[data-view-doc]");
      if (view) {
        viewDocumentModal(Number(view.dataset.viewDoc));
        return;
      }
      const del = e.target.closest("[data-delete-doc]");
      if (del) {
        const id = Number(del.dataset.deleteDoc);
        const doc = state.documents.find((d) => d.id === id);
        confirmModal(
          "Delete this document?",
          `"${doc?.title || "This document"}" and its passages will be removed from the index.`,
          "Delete document",
          async () => {
            try {
              await api(`/api/workspaces/${state.activeId}/documents/${id}`, { method: "DELETE" });
              toast("Document deleted", "success");
              await selectWorkspace(state.activeId);
            } catch (error) {
              toast(error.message, "error");
            }
          }
        );
      }
    });

    // Modal: close on backdrop or [data-close-modal]
    $("#modal-root").addEventListener("click", (e) => {
      if (e.target.closest("[data-close-modal]") || e.target.classList.contains("modal-backdrop")) {
        closeModal();
      }
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("#modal-root").classList.contains("is-hidden")) closeModal();
    });

    // Modal content: delegated handlers for dynamically injected forms
    $("#modal-content").addEventListener("click", (e) => {
      const docTab = e.target.closest("[data-doc-tab]");
      if (docTab) {
        const mode = docTab.dataset.docTab;
        $$("[data-doc-tab]", $("#modal-content")).forEach((t) => t.classList.toggle("is-active", t === docTab));
        $("#upload-form").classList.toggle("is-hidden", mode !== "upload");
        $("#paste-form").classList.toggle("is-hidden", mode !== "paste");
        return;
      }
      if (e.target.closest("#dropzone")) $("#file-input").click();
    });

    $("#modal-content").addEventListener("change", (e) => {
      if (e.target.id === "file-input") {
        const file = e.target.files[0];
        const label = $("#dropzone-file");
        const submit = $("#upload-submit");
        if (file) {
          label.textContent = `${file.name} · ${formatBytes(file.size)}`;
          label.classList.remove("is-hidden");
          submit.disabled = false;
        } else {
          label.classList.add("is-hidden");
          submit.disabled = true;
        }
      }
    });

    // Drag and drop
    $("#modal-content").addEventListener("dragover", (e) => {
      const zone = e.target.closest("#dropzone");
      if (zone) {
        e.preventDefault();
        zone.classList.add("is-over");
      }
    });
    $("#modal-content").addEventListener("dragleave", (e) => {
      const zone = e.target.closest("#dropzone");
      if (zone) zone.classList.remove("is-over");
    });
    $("#modal-content").addEventListener("drop", (e) => {
      const zone = e.target.closest("#dropzone");
      if (!zone) return;
      e.preventDefault();
      zone.classList.remove("is-over");
      const file = e.dataTransfer.files[0];
      if (!file) return;
      const input = $("#file-input");
      const transfer = new DataTransfer();
      transfer.items.add(file);
      input.files = transfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });

    // Modal form submissions
    $("#modal-content").addEventListener("submit", async (e) => {
      e.preventDefault();
      const form = e.target;

      if (form.id === "new-workspace-form") {
        const data = Object.fromEntries(new FormData(form).entries());
        try {
          const created = await api("/api/workspaces", { method: "POST", body: data });
          closeModal();
          toast("Knowledge base created", "success");
          state.activeId = created.id;
          await loadWorkspaces();
        } catch (error) {
          toast(error.message, "error");
        }
        return;
      }

      if (form.id === "paste-form") {
        const data = Object.fromEntries(new FormData(form).entries());
        try {
          await api(`/api/workspaces/${state.activeId}/documents/text`, { method: "POST", body: data });
          closeModal();
          toast("Document added and indexed", "success");
          await selectWorkspace(state.activeId);
        } catch (error) {
          toast(error.message, "error");
        }
        return;
      }

      if (form.id === "upload-form") {
        const file = $("#file-input").files[0];
        if (!file) {
          toast("Choose a file first", "error");
          return;
        }
        const formData = new FormData();
        formData.append("file", file);
        const title = form.querySelector('[name="title"]').value.trim();
        if (title) formData.append("title", title);

        const submit = $("#upload-submit");
        submit.disabled = true;
        submit.innerHTML = '<span class="spinner"></span> Indexing…';
        try {
          await api(`/api/workspaces/${state.activeId}/documents/upload`, {
            method: "POST",
            body: formData,
            isForm: true,
          });
          closeModal();
          toast("Document uploaded and indexed", "success");
          await selectWorkspace(state.activeId);
        } catch (error) {
          toast(error.message, "error");
          submit.disabled = false;
          submit.textContent = "Add document";
        }
      }
    });
  }

  /* ── Boot ──────────────────────────────────────────────────────────── */

  async function boot() {
    wireEvents();

    if (!state.token) {
      showAuth();
      return;
    }

    try {
      state.user = await api("/api/auth/me");
      showApp();
      await loadWorkspaces();
    } catch {
      signOut();
    }
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
