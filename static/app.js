const API = "";

const state = {
  mode: "records", // records | review | settings
  docs: [],
  selectedId: null,
  previewDocId: null,
  previewBlobUrl: null,
  detailSig: null,
  metaSig: null,
  panelSig: null,
  sidebarSnap: null,
  theme: localStorage.getItem("clearpost-theme") || "light",
};

const el = {
  sidebarList: document.getElementById("sidebar-list"),
  sidebarCount: document.getElementById("sidebar-count"),
  dropzone: document.getElementById("dropzone"),
  fileInput: document.getElementById("file-input"),
  uploadStatus: document.getElementById("upload-status"),
  contentBox: document.getElementById("content-box"),
  emptyMessage: document.getElementById("empty-message"),
  contentTitle: document.getElementById("content-title"),
  contentMeta: document.getElementById("content-meta"),
  previewFrame: document.getElementById("preview-frame"),
  previewImage: document.getElementById("preview-image"),
  previewFallback: document.getElementById("preview-fallback"),
  detailPane: document.getElementById("detail-pane"),
  panelWorkspace: document.getElementById("panel-workspace"),
  panelSettings: document.getElementById("panel-settings"),
  btnSettings: document.getElementById("btn-settings"),
  btnEditSelected: document.getElementById("btn-edit-selected"),
};

const ALLOWED_EXT = new Set([
  ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp",
  ".docx", ".txt", ".csv", ".xlsx",
]);

/* —— Theme —— */
function applyTheme(theme) {
  state.theme = theme === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", state.theme);
  localStorage.setItem("clearpost-theme", state.theme);
  document.getElementById("theme-light")?.classList.toggle("primary", state.theme === "light");
  document.getElementById("theme-light")?.classList.toggle("active", state.theme === "light");
  document.getElementById("theme-dark")?.classList.toggle("primary", state.theme === "dark");
  document.getElementById("theme-dark")?.classList.toggle("active", state.theme === "dark");
}

applyTheme(state.theme);

document.querySelectorAll("[data-theme-set]").forEach((btn) => {
  btn.addEventListener("click", () => applyTheme(btn.dataset.themeSet));
});

/* —— Mode —— */
function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".mode-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.mode === mode);
  });
  const settings = mode === "settings";
  el.panelWorkspace.classList.toggle("active", !settings);
  el.panelSettings.classList.toggle("active", settings);
  el.btnSettings.classList.toggle("active", settings);
  el.sidebarList.classList.toggle("review-only", mode === "review");

  if (settings) {
    loadSystemStatus();
    return;
  }

  // Force right-pane rebuild when switching Records <-> Review
  state.panelSig = null;

  if (mode === "review") {
    if (el.emptyMessage) {
      el.emptyMessage.textContent =
        "Pick a file that needs attention (highlighted in the log), then edit, approve, and commit.";
    }
    // Auto-pick first open review item if nothing selected
    const open = state.docs.find((d) => needsAttention(d) && d.record);
    if (!state.selectedId && open) {
      selectDocument(open.id, true);
      return;
    }
    if (state.selectedId) {
      selectDocument(state.selectedId, true);
      return;
    }
    setWorkspaceState("empty");
    renderSidebar();
    return;
  }

  if (el.emptyMessage) {
    el.emptyMessage.textContent =
      "Select a file from the log to preview and inspect extraction.";
  }
  renderSidebar();
  if (state.selectedId) selectDocument(state.selectedId, true);
  else setWorkspaceState("empty");
}

document.getElementById("btn-mode-records").addEventListener("click", () => setMode("records"));
document.getElementById("btn-mode-review").addEventListener("click", () => setMode("review"));
el.btnSettings.addEventListener("click", () => setMode("settings"));
document.getElementById("btn-back-workspace").addEventListener("click", () => setMode("records"));

document.getElementById("btn-export-technical").addEventListener("click", () => {
  window.location.href = `${API}/exports/technical`;
});
document.getElementById("btn-export-readable").addEventListener("click", () => {
  window.location.href = `${API}/exports/readable`;
});
document.getElementById("btn-refresh-status").addEventListener("click", () => loadSystemStatus());

/* —— Helpers —— */
function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fileExt(name) {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i).toLowerCase() : "";
}

function confClass(score) {
  if (score == null) return "";
  if (score >= 0.95) return "green";
  if (score >= 0.75) return "yellow";
  return "red";
}

function toDateInputValue(value) {
  if (value == null || value === "") return "";
  const s = String(value).trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
  const m = s.match(/(\d{4})-(\d{2})-(\d{2})/);
  if (m) return `${m[1]}-${m[2]}-${m[3]}`;
  const d = new Date(s);
  if (!Number.isNaN(d.getTime())) {
    const y = d.getFullYear();
    const mo = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${mo}-${day}`;
  }
  return "";
}

function humanizeNote(note) {
  let s = String(note ?? "").trim();
  if (!s) return "";
  s = s.replace(/^```[\s\S]*?```$/g, "").replace(/`+/g, "").trim().replace(/_/g, " ");
  const map = {
    "missing vendor": "Vendor is missing.",
    "missing invoice_number": "Invoice number is missing.",
    "missing or invalid invoice_date": "Invoice date is missing or not a valid date.",
    "missing total_amount": "Total amount is missing.",
    "total_amount is negative": "Total amount cannot be negative.",
    "missing currency": "Currency is missing.",
    "source has line items but none extracted": "This file looks like it has line items, but none were extracted.",
  };
  const key = s.toLowerCase();
  if (map[key]) return map[key];
  if (s.length) return s.charAt(0).toUpperCase() + s.slice(1) + (s.endsWith(".") ? "" : ".");
  return s;
}

function parseErrorLines(raw) {
  if (raw == null || raw === "") return ["Something went wrong."];
  if (raw instanceof Error) return parseErrorLines(raw.message);
  if (Array.isArray(raw)) return raw.map(humanizeNote).filter(Boolean);
  if (typeof raw === "object") {
    if (raw.detail != null) return parseErrorLines(raw.detail);
    if (raw.message) return [humanizeNote(raw.message)];
    return ["Something went wrong."];
  }
  let text = String(raw).trim();
  try {
    return parseErrorLines(JSON.parse(text));
  } catch { /* plain */ }
  const inv = text.match(/Main fields incomplete:\s*(.+)/i) || text.match(/Main fields invalid:\s*(.+)/i);
  if (inv) return inv[1].split(";").map((p) => humanizeNote(p.trim())).filter(Boolean);
  if (text.includes(";") && text.length < 500) {
    const parts = text.split(";").map((p) => humanizeNote(p.trim())).filter(Boolean);
    if (parts.length > 1) return parts;
  }
  return [humanizeNote(text)];
}

function formatErrorHtml(raw) {
  const lines = parseErrorLines(raw);
  if (lines.length === 1) return `<p class="error-text">${escapeHtml(lines[0])}</p>`;
  return `<ul class="error-list">${lines.map((l) => `<li>${escapeHtml(l)}</li>`).join("")}</ul>`;
}

function formatNotesHtml(notes) {
  const list = Array.isArray(notes) ? notes : notes ? [notes] : [];
  if (!list.length) return `<p class="ok-text">No validation issues.</p>`;
  return `<ul class="error-list validation-notes">${list.map((n) => `<li>${escapeHtml(humanizeNote(n))}</li>`).join("")}</ul>`;
}

function setStatusHtml(node, html, isError) {
  if (!node) return;
  node.classList.toggle("is-error", !!isError);
  node.classList.toggle("is-ok", !isError);
  node.innerHTML = html;
}

function needsAttention(doc) {
  return ["ready", "needs_review", "approved", "done", "processing"].includes(doc.status)
    && !(doc.record && doc.record.committed_at);
}

function field(label, name, value, type = "text") {
  if (type === "textarea") {
    const v = value ?? "";
    return `<label class="field-block full">${escapeHtml(label)}
      <textarea data-field="${name}">${escapeHtml(typeof v === "string" ? v : JSON.stringify(v, null, 2))}</textarea>
    </label>`;
  }
  if (type === "date") {
    return `<label class="field-block">${escapeHtml(label)}
      <input data-field="${name}" type="date" value="${escapeHtml(toDateInputValue(value))}" />
    </label>`;
  }
  if (type === "money") {
    const v = value == null || value === "" ? "" : Number(value);
    const shown = v === "" || Number.isNaN(v) ? "" : String(v);
    return `<label class="field-block">${escapeHtml(label)}
      <input data-field="${name}" type="number" inputmode="decimal" step="0.01" min="0"
        value="${escapeHtml(shown)}" placeholder="0.00" />
    </label>`;
  }
  return `<label class="field-block">${escapeHtml(label)}
    <input data-field="${name}" type="text" value="${escapeHtml(value ?? "")}" />
  </label>`;
}

function renderReadonlyFields(record) {
  if (!record) return "<em class='muted'>No extraction yet</em>";
  const items = (record.line_items || [])
    .map((li) => `<li>${escapeHtml(li.description)} — ${li.amount}</li>`)
    .join("");
  return `
    <div class="detail-scroll">
      <ul class="fields-list">
        <li><strong>Vendor:</strong> ${escapeHtml(record.vendor)}</li>
        <li><strong>Invoice #:</strong> ${escapeHtml(record.invoice_number)}</li>
        <li><strong>Date:</strong> ${escapeHtml(record.invoice_date)}</li>
        <li><strong>Total:</strong> ${escapeHtml(record.currency)} ${record.total_amount ?? ""}</li>
        <li><strong>Due:</strong> ${escapeHtml(record.due_date) || "—"}</li>
        <li><strong>Subtotal / Tax:</strong> ${record.subtotal ?? "—"} / ${record.tax_amount ?? "—"}</li>
        <li><strong>PO / Terms:</strong> ${escapeHtml(record.po_number) || "—"} / ${escapeHtml(record.payment_terms) || "—"}</li>
        <li><strong>Line items:</strong><ul>${items || "<li>—</li>"}</ul></li>
      </ul>
      <div class="issues-block">
        <div class="issues-label">Issues</div>
        ${formatNotesHtml(record.validation_notes)}
      </div>
    </div>
  `;
}

/* —— Upload —— */
el.dropzone.addEventListener("click", () => el.fileInput.click());
el.dropzone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    el.fileInput.click();
  }
});
el.dropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  el.dropzone.classList.add("dragover");
});
el.dropzone.addEventListener("dragleave", (e) => {
  if (!el.dropzone.contains(e.relatedTarget)) {
    el.dropzone.classList.remove("dragover");
  }
});
el.dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  el.dropzone.classList.remove("dragover");
  if (e.dataTransfer.files.length) handleUpload(e.dataTransfer.files[0]);
});
el.fileInput.addEventListener("change", () => {
  if (el.fileInput.files.length) handleUpload(el.fileInput.files[0]);
  el.fileInput.value = "";
});

async function handleUpload(file) {
  if (!ALLOWED_EXT.has(fileExt(file.name))) {
    el.uploadStatus.textContent =
      "Unsupported type. Use PDF, PNG, JPG, WEBP, TIFF, BMP, DOCX, TXT, CSV, or XLSX.";
    return;
  }
  el.uploadStatus.textContent = `Uploading ${file.name}…`;
  const form = new FormData();
  form.append("file", file);
  try {
    const res = await fetch(`${API}/documents/upload`, { method: "POST", body: form });
    if (!res.ok) throw new Error(await res.text());
    const doc = await res.json();
    el.uploadStatus.textContent = `Uploaded #${doc.id} (${doc.file_type}). Reading / OCR…`;
    const ex = await fetch(`${API}/documents/${doc.id}/extract`, { method: "POST" });
    if (!ex.ok) {
      el.uploadStatus.textContent = `Extract failed: ${await ex.text()}`;
    } else {
      const rec = await ex.json();
      const notes = (rec.validation_notes || []).map(humanizeNote).join(" · ") || "OK";
      el.uploadStatus.textContent = `Done: ${file.name} → ${rec.document_status || "extracted"} · ${notes}`;
    }
    await loadDocuments({ force: true });
    selectDocument(doc.id, true);
  } catch (e) {
    el.uploadStatus.textContent = `Error: ${e.message}`;
  }
}

/* —— Sidebar —— */
function renderSidebar() {
  const docs = state.docs;
  el.sidebarCount.textContent = String(docs.length);
  el.sidebarList.innerHTML = "";
  if (!docs.length) {
    el.sidebarList.innerHTML = `<p class="muted" style="padding:0.75rem;font-size:0.85rem">No files yet. Drop one on the right.</p>`;
    return;
  }

  let shown = 0;
  for (const doc of docs) {
    if (state.mode === "review" && !needsAttention(doc)) {
      // still show but dimmed via CSS; keep all for navigation
    }
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "sidebar-item";
    if (doc.id === state.selectedId) btn.className += " active";
    if (needsAttention(doc)) btn.className += " needs-attention";
    const score = doc.confidence_score;
    const scoreText = score == null ? "—" : Number(score).toFixed(2);
    btn.innerHTML = `
      <span class="name">${escapeHtml(doc.filename)}</span>
      <span class="meta">
        <span><span class="dot ${escapeHtml(doc.status)}"></span>${escapeHtml(doc.status)}</span>
        <span class="tag">${escapeHtml(doc.file_type || "")}</span>
        <span class="confidence ${confClass(score)}">${scoreText}</span>
      </span>
    `;
    btn.addEventListener("click", () => selectDocument(doc.id, true));
    el.sidebarList.appendChild(btn);
    shown += 1;
  }
  if (!shown) {
    el.sidebarList.innerHTML = `<p class="muted" style="padding:0.75rem;font-size:0.85rem">No files.</p>`;
  }
}

function metaSignature(doc) {
  return `${doc.id}|${doc.status}|${doc.confidence_score ?? ""}|${doc.filename || ""}`;
}

function detailSignature(doc) {
  const r = doc.record;
  if (!r) return `${doc.id}|null`;
  return [
    doc.id,
    r.id,
    r.vendor,
    r.invoice_number,
    r.invoice_date,
    r.total_amount,
    r.currency,
    r.due_date,
    r.subtotal,
    r.tax_amount,
    r.po_number,
    r.payment_terms,
    r.confidence_score,
    JSON.stringify(r.line_items || []),
    JSON.stringify(r.validation_notes || []),
    r.committed_at || "",
    r.approved_at || "",
  ].join("|");
}

function panelSignature(doc) {
  // Includes mode so switching Records/Review always rebuilds the right pane
  return `${state.mode}|${detailSignature(doc)}`;
}

function sidebarSignature(docs) {
  return docs
    .map((d) => `${d.id}:${d.status}:${d.confidence_score ?? ""}:${d.filename || ""}`)
    .join("|");
}

function setWorkspaceState(mode) {
  if (el.contentBox) el.contentBox.dataset.state = mode;
}

function revokePreviewBlob() {
  if (state.previewBlobUrl) {
    try {
      URL.revokeObjectURL(state.previewBlobUrl);
    } catch { /* ignore */ }
    state.previewBlobUrl = null;
  }
}

function hideAllPreviewModes() {
  if (el.previewFrame) el.previewFrame.hidden = true;
  if (el.previewImage) el.previewImage.hidden = true;
  if (el.previewFallback) {
    el.previewFallback.hidden = true;
    el.previewFallback.innerHTML = "";
  }
}

function showEmptyWorkspace() {
  setWorkspaceState("empty");
  state.detailSig = null;
  state.metaSig = null;
  state.panelSig = null;
  state.previewDocId = null;
  revokePreviewBlob();
  if (el.previewFrame) {
    el.previewFrame.removeAttribute("src");
    el.previewFrame.hidden = true;
  }
  if (el.previewImage) {
    el.previewImage.removeAttribute("src");
    el.previewImage.hidden = true;
  }
  hideAllPreviewModes();
  if (el.detailPane) el.detailPane.innerHTML = "";
}

async function mountPreview(doc) {
  if (state.previewDocId === doc.id) return;
  state.previewDocId = doc.id;

  const ft = (doc.file_type || "").toLowerCase();
  const url = `${API}/documents/${doc.id}/file`;

  hideAllPreviewModes();
  revokePreviewBlob();

  if (["png", "jpg", "jpeg", "webp", "tiff", "bmp"].includes(ft)) {
    el.previewImage.hidden = false;
    el.previewImage.alt = `Preview of ${doc.filename}`;
    el.previewImage.src = url;
    return;
  }

  if (ft === "pdf") {
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      if (state.selectedId !== doc.id) return;
      const blobUrl = URL.createObjectURL(blob);
      state.previewBlobUrl = blobUrl;
      el.previewFrame.hidden = false;
      el.previewFrame.src = blobUrl;
    } catch (e) {
      el.previewFallback.hidden = false;
      el.previewFallback.innerHTML = `
        <p>Could not load PDF preview.</p>
        <p><a class="btn" href="${url}" target="_blank" rel="noopener">Open file</a></p>
        <p class="muted">${escapeHtml(e.message || e)}</p>`;
    }
    return;
  }

  el.previewFallback.hidden = false;
  el.previewFallback.innerHTML = `
    <p>No inline preview for <strong>${escapeHtml(ft || "this type")}</strong>.</p>
    <p><a class="btn" href="${url}" target="_blank" rel="noopener">Open / download file</a></p>`;
}

function parseMoney(value) {
  if (value == null || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function readBody(root) {
  const body = {
    vendor: root.querySelector('[data-field="vendor"]').value,
    invoice_number: root.querySelector('[data-field="invoice_number"]').value,
    invoice_date: root.querySelector('[data-field="invoice_date"]').value || null,
    currency: (root.querySelector('[data-field="currency"]').value || "USD").trim().toUpperCase(),
    due_date: root.querySelector('[data-field="due_date"]').value || null,
    po_number: root.querySelector('[data-field="po_number"]').value.trim() || null,
    payment_terms: root.querySelector('[data-field="payment_terms"]').value.trim() || null,
  };
  body.total_amount = parseMoney(root.querySelector('[data-field="total_amount"]').value);
  body.subtotal = parseMoney(root.querySelector('[data-field="subtotal"]').value);
  body.tax_amount = parseMoney(root.querySelector('[data-field="tax_amount"]').value);
  const rawLines = root.querySelector('[data-field="line_items"]').value || "[]";
  try {
    body.line_items = JSON.parse(rawLines);
  } catch {
    throw new Error("Line items must be a valid list of description and amount.");
  }
  return body;
}

async function readErrorBody(res) {
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function wireNumberKeys(root) {
  root.querySelectorAll('input[type="number"]').forEach((input) => {
    input.addEventListener("keydown", (e) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const ok = ["Backspace", "Delete", "Tab", "Escape", "Enter", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"];
      if (ok.includes(e.key)) return;
      if (e.key === "." || e.key === ",") {
        if (input.value.includes(".") || input.value.includes(",")) e.preventDefault();
        return;
      }
      if (!/^\d$/.test(e.key)) e.preventDefault();
    });
  });
}

/** Right pane: read-only fields (Records) OR full edit form with Save/Approve/Commit (Review). */
function renderDetailPanel(doc) {
  const rec = doc.record;

  if (state.mode !== "review") {
    el.detailPane.innerHTML = renderReadonlyFields(rec);
    return;
  }

  // REVIEW MODE — form + sticky action buttons inside the right pane
  if (!rec) {
    el.detailPane.innerHTML = `
      <div class="detail-scroll">
        <p class="muted">No extraction yet for this file. Re-upload or wait for processing.</p>
      </div>`;
    return;
  }

  const hasIssues = (rec.validation_notes || []).length > 0;
  const card = document.createElement("div");
  card.className = "review-card";
  card.innerHTML = `
    <div class="detail-scroll">
      <p class="review-banner">Review mode — edit fields, then Save / Approve / Commit.</p>
      <div class="issues-block">
        <div class="issues-label">${hasIssues ? "Issues to fix" : "Checks"}</div>
        ${formatNotesHtml(rec.validation_notes)}
      </div>
      <div class="review-fields">
        ${field("Vendor", "vendor", rec.vendor)}
        ${field("Invoice #", "invoice_number", rec.invoice_number)}
        ${field("Invoice date", "invoice_date", rec.invoice_date, "date")}
        ${field("Due date", "due_date", rec.due_date, "date")}
        ${field("Currency", "currency", rec.currency || "USD")}
        ${field("Total amount", "total_amount", rec.total_amount, "money")}
        ${field("Subtotal", "subtotal", rec.subtotal, "money")}
        ${field("Tax", "tax_amount", rec.tax_amount, "money")}
        ${field("PO number", "po_number", rec.po_number)}
        ${field("Payment terms", "payment_terms", rec.payment_terms)}
        ${field("Line items (JSON)", "line_items", JSON.stringify(rec.line_items || [], null, 2), "textarea")}
      </div>
    </div>
    <div class="detail-actions">
      <button type="button" class="btn" data-action="save">Save</button>
      <button type="button" class="btn" data-action="approve">Approve</button>
      <button type="button" class="btn primary" data-action="commit">Approve &amp; Commit</button>
    </div>
    <div class="status-msg detail-status" data-msg></div>
  `;

  wireNumberKeys(card);
  const msg = () => card.querySelector("[data-msg]");

  card.querySelector('[data-action="save"]').addEventListener("click", async () => {
    setStatusHtml(msg(), "Saving…", false);
    try {
      const body = readBody(card);
      const patch = await fetch(`${API}/records/${rec.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!patch.ok) throw await readErrorBody(patch);
      setStatusHtml(msg(), "Saved and re-checked.", false);
      state.panelSig = null;
      await loadDocuments({ force: true });
      selectDocument(doc.id, true);
    } catch (e) {
      setStatusHtml(msg(), formatErrorHtml(e), true);
    }
  });

  card.querySelector('[data-action="approve"]').addEventListener("click", async () => {
    setStatusHtml(msg(), "Approving…", false);
    try {
      const body = readBody(card);
      const patch = await fetch(`${API}/records/${rec.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!patch.ok) throw await readErrorBody(patch);
      const ap = await fetch(`${API}/records/${rec.id}/approve`, { method: "POST" });
      if (!ap.ok) throw await readErrorBody(ap);
      setStatusHtml(msg(), "Approved.", false);
      state.panelSig = null;
      await loadDocuments({ force: true });
      selectDocument(doc.id, true);
    } catch (e) {
      setStatusHtml(msg(), formatErrorHtml(e), true);
    }
  });

  card.querySelector('[data-action="commit"]').addEventListener("click", async () => {
    setStatusHtml(msg(), "Committing…", false);
    try {
      const body = readBody(card);
      const patch = await fetch(`${API}/records/${rec.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!patch.ok) throw await readErrorBody(patch);
      const commit = await fetch(`${API}/records/${rec.id}/commit`, { method: "POST" });
      if (!commit.ok) throw await readErrorBody(commit);
      await commit.json();
      setStatusHtml(msg(), "Committed. Summaries written to output/.", false);
      state.panelSig = null;
      await loadDocuments({ force: true });
      selectDocument(doc.id, true);
    } catch (e) {
      setStatusHtml(msg(), formatErrorHtml(e), true);
    }
  });

  el.detailPane.innerHTML = "";
  el.detailPane.appendChild(card);
}

function updateToolbar(doc) {
  el.contentTitle.textContent = doc.filename;
  const score = doc.confidence_score;
  const scoreText = score == null ? "—" : Number(score).toFixed(2);
  el.contentMeta.innerHTML = `
    ${escapeHtml(doc.status)}
    · <span class="tag">${escapeHtml(doc.file_type || "")}</span>
    · confidence <span class="confidence ${confClass(score)}">${scoreText}</span>
  `;

  if (state.mode === "review") {
    el.btnEditSelected.textContent = "Refresh form";
    el.btnEditSelected.disabled = !doc.record;
    el.btnEditSelected.onclick = () => {
      state.panelSig = null;
      selectDocument(doc.id, true);
    };
  } else {
    el.btnEditSelected.textContent = "Edit in review";
    el.btnEditSelected.disabled = !doc.record;
    el.btnEditSelected.onclick = async () => {
      if (!doc.record) return;
      try {
        // Unlock committed records so they can be re-edited
        const res = await fetch(`${API}/documents/${doc.id}/edit`, { method: "POST" });
        if (!res.ok) throw new Error(await res.text());
        await loadDocuments({ force: true });
        setMode("review");
        selectDocument(doc.id, true);
      } catch (e) {
        el.uploadStatus.textContent = `Edit failed: ${e.message}`;
      }
    };
  }
}

function updateSelectedChrome(doc, { forcePanel = false } = {}) {
  setWorkspaceState("selected");
  updateToolbar(doc);

  if (state.previewDocId !== doc.id) {
    mountPreview(doc);
  }

  const panelSig = panelSignature(doc);
  const typing = !!document.activeElement?.closest?.("#detail-pane .review-card");
  if (forcePanel || state.panelSig !== panelSig) {
    if (typing && !forcePanel && state.mode === "review") {
      // Don't wipe the form while the user is typing on a soft poll
      return;
    }
    state.panelSig = panelSig;
    state.detailSig = detailSignature(doc);
    state.metaSig = metaSignature(doc);
    renderDetailPanel(doc);
  }
}

function selectDocument(id, forcePanel) {
  const switching = state.selectedId !== id;
  state.selectedId = id;
  renderSidebar();
  const doc = state.docs.find((d) => d.id === id);
  if (!doc) {
    showEmptyWorkspace();
    return;
  }
  if (switching) {
    state.previewDocId = null;
    state.panelSig = null;
    revokePreviewBlob();
    if (el.previewFrame) el.previewFrame.removeAttribute("src");
    if (el.previewImage) el.previewImage.removeAttribute("src");
  }
  updateSelectedChrome(doc, { forcePanel: forcePanel !== false });
}

async function loadDocuments(opts = {}) {
  const res = await fetch(`${API}/documents`);
  state.docs = await res.json();

  const sideSnap = sidebarSignature(state.docs);
  if (opts.force || sideSnap !== state.sidebarSnap) {
    state.sidebarSnap = sideSnap;
    renderSidebar();
  }

  if (state.selectedId == null) {
    setWorkspaceState("empty");
    return;
  }

  const doc = state.docs.find((d) => d.id === state.selectedId);
  if (!doc) {
    state.selectedId = null;
    showEmptyWorkspace();
    return;
  }

  if (opts.force) {
    updateSelectedChrome(doc, { forcePanel: true });
    return;
  }

  // Soft poll: update sidebar already done; only rebuild panel if data/mode changed
  const panelSig = panelSignature(doc);
  if (panelSig !== state.panelSig) {
    updateSelectedChrome(doc, { forcePanel: false });
  } else {
    setWorkspaceState("selected");
  }
}

/* —— Settings —— */
async function loadSystemStatus() {
  const online = document.getElementById("llm-online");
  const provider = document.getElementById("llm-provider");
  const model = document.getElementById("llm-model");
  const url = document.getElementById("llm-url");
  const err = document.getElementById("llm-error");
  try {
    const res = await fetch(`${API}/system/status`);
    const data = await res.json();
    const llm = data.llm || {};
    online.textContent = llm.status || (llm.online ? "online" : "offline");
    online.className = llm.online ? "online" : "offline";
    provider.textContent = llm.provider || "—";
    model.textContent = llm.model || "—";
    url.textContent = llm.url || "—";
    err.textContent = llm.error && !llm.online ? llm.error : "";
    document.getElementById("ocr-engine").textContent = data.ocr?.engine || "—";
    document.getElementById("ocr-license").textContent = data.ocr?.license || "—";
    document.getElementById("ocr-notes").textContent = data.ocr?.notes || "—";
    const chips = document.getElementById("allowed-types");
    chips.innerHTML = (data.allowed_extensions || [])
      .map((x) => `<span>${escapeHtml(x)}</span>`)
      .join("");
  } catch (e) {
    online.textContent = "offline";
    online.className = "offline";
    err.textContent = e.message;
  }
}

setWorkspaceState("empty");
loadDocuments({ force: true });
setInterval(() => loadDocuments(), 8000);
