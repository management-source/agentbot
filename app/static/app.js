let currentTab = "all";
let currentAckThreadId = null;

// Local user auth (JWT)
let authToken = localStorage.getItem("agent_auth_token") || "";
let currentUser = null;
let usersCache = [];

async function apiFetch(url, options = {}) {
    const opts = { ...options, headers: { ...(options.headers || {}) } };
    if (authToken) {
        opts.headers["Authorization"] = `Bearer ${authToken}`;
    }
    return fetch(url, opts);
}

// Date filter applied to ticket list (set when you click Fetch Now).
let currentDateFilter = { start: "", end: "" };

let googleConnected = false;

async function refreshGoogleStatus() {
    const btn = document.getElementById("googleBtn");
    if (!btn) return;

    btn.disabled = false;
    try {
        const r = await fetch("/auth/status");
        if (!r.ok) {
            // Most likely: OAuth not configured on server.
            googleConnected = false;
            btn.textContent = "Google OAuth not configured";
            btn.className = "px-4 py-2 rounded-lg border text-slate-400 bg-slate-50 cursor-not-allowed";
            btn.disabled = true;
            return;
        }

        const j = await r.json();
        googleConnected = !!j.connected;

        const mb = document.getElementById("mailboxBadge");
        if (mb) {
            const target = (j.target_mailbox || j.delegated_mailbox || "me");
            mb.textContent = googleConnected ? (`Mailbox: ${target}`) : "";
        }

        if (googleConnected) {
            btn.textContent = "Google Connected";
            btn.className = "px-4 py-2 rounded-lg border bg-emerald-50 text-emerald-800 hover:bg-emerald-100";
        } else {
            btn.textContent = "Connect to Google";
            btn.className = "px-4 py-2 rounded-lg border text-slate-700 hover:bg-slate-50";
        }
    } catch {
        // If status check fails, keep button usable for login.
        googleConnected = false;
        btn.textContent = "Connect to Google";
        btn.className = "px-4 py-2 rounded-lg border text-slate-700 hover:bg-slate-50";
    }
}

async function googleConnectOrManage() {
    if (!googleConnected) {
        window.location.href = "/auth/google/login";
        return;
    }

    const ok = confirm("Google is currently connected. Do you want to disconnect this account?");
    if (!ok) return;

    try {
        const r = await fetch("/auth/google/disconnect", { method: "POST" });
        const t = await r.text();
        if (!r.ok) {
            alert(`Disconnect failed (${r.status}):\n\n${t}`);
            return;
        }
    } catch (e) {
        alert("Disconnect failed: " + e);
    } finally {
        await refreshGoogleStatus();
    }
}

// -------------------------
// Settings (persisted in localStorage)
// -------------------------
const SETTINGS_KEY = "agent_settings_v1";
let settings = {
    defaultHtmlView: false,
    proxyRemoteImages: true,
    compactTickets: false,
};

function loadSettings() {
    try {
        const raw = localStorage.getItem(SETTINGS_KEY);
        if (raw) {
            const parsed = JSON.parse(raw);
            // Backward compatibility: older versions used blockRemoteImages.
            if (typeof parsed.proxyRemoteImages === "undefined" && typeof parsed.blockRemoteImages !== "undefined") {
                parsed.proxyRemoteImages = !!parsed.blockRemoteImages;
            }
            settings = { ...settings, ...parsed };
        }
    } catch {
        // ignore
    }
}

function saveSettings() {
    try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch { }
}


function openUsersModal() {
    const modal = document.getElementById("usersModal");
    if (!modal) return;
    modal.classList.remove("hidden");
    renderUsersList();
}

function closeUsersModal() {
    const modal = document.getElementById("usersModal");
    if (!modal) return;
    modal.classList.add("hidden");
}

async function renderUsersList() {
    await loadUsersCache();
    const list = document.getElementById("usersList");
    if (!list) return;
    list.innerHTML = "";
    for (const u of usersCache) {
        const row = document.createElement("div");
        row.className = "flex items-center justify-between gap-3 p-2 rounded-lg border bg-white";
        row.innerHTML = `
            <div class="min-w-0">
              <div class="font-medium text-slate-900 truncate">${escapeHtml(u.name)}</div>
              <div class="text-xs text-slate-500 truncate">${escapeHtml(u.email)} • ${escapeHtml(u.role)}${u.is_active ? "" : " • Inactive"}</div>
            </div>
            <div class="flex items-center gap-2">
              <select class="px-2 py-1 rounded-md border bg-white text-sm" data-user-role="${u.id}">
                ${["ADMIN","PM","LEASING","SALES","ACCOUNTS","READONLY"].map(r => `<option value="${r}" ${r===u.role?"selected":""}>${r}</option>`).join("")}
              </select>
              <label class="text-sm text-slate-600 flex items-center gap-1">
                <input type="checkbox" ${u.is_active ? "checked":""} data-user-active="${u.id}" />
                Active
              </label>
              <button class="px-3 py-1.5 rounded-md border text-sm" onclick="saveUserEdits(${u.id})">Save</button>
            </div>
        `;
        list.appendChild(row);
    }
}

async function saveUserEdits(userId) {
    const roleSel = document.querySelector(`[data-user-role="${userId}"]`);
    const activeChk = document.querySelector(`[data-user-active="${userId}"]`);
    const payload = { role: roleSel ? roleSel.value : undefined, is_active: activeChk ? !!activeChk.checked : undefined };
    const r = await apiFetch(`/user-auth/users/${userId}`, {
        method: "PATCH",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify(payload),
    });
    if (!r.ok) {
        alert("Failed to update user (Admin only).");
        return;
    }
    await renderUsersList();
}

async function createUserFromForm() {
    const email = document.getElementById("newUserEmail").value.trim();
    const name = document.getElementById("newUserName").value.trim();
    const role = document.getElementById("newUserRole").value;
    const password = document.getElementById("newUserPassword").value;
    if (!email || !name || !password) {
        alert("Email, name and password are required.");
        return;
    }
    const r = await apiFetch("/user-auth/users", {
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ email, name, role, password, is_active: true }),
    });
    if (!r.ok) {
        const msg = await r.text();
        alert("Failed to create user: " + msg);
        return;
    }
    document.getElementById("newUserEmail").value = "";
    document.getElementById("newUserName").value = "";
    document.getElementById("newUserPassword").value = "";
    await renderUsersList();
}

function openSettings() {
    const m = document.getElementById("settingsModal");
    if (!m) return;
    document.getElementById("setDefaultHtml").checked = !!settings.defaultHtmlView;
    document.getElementById("setBlockRemote").checked = !!settings.proxyRemoteImages;
    document.getElementById("setCompact").checked = !!settings.compactTickets;
    m.classList.remove("hidden");
}

function closeSettings() {
    const m = document.getElementById("settingsModal");
    if (!m) return;
    m.classList.add("hidden");
}

function applySettingsFromModal() {
    settings.defaultHtmlView = document.getElementById("setDefaultHtml").checked;
    settings.proxyRemoteImages = document.getElementById("setBlockRemote").checked;
    settings.compactTickets = document.getElementById("setCompact").checked;
    saveSettings();
    closeSettings();
    loadTickets();
}

async function flushDatabase() {
  const text = prompt("Type FLUSH to permanently delete all tickets and sync state:");
  if (!text) return;
  if (text.trim().toUpperCase() !== "FLUSH") {
    alert("Cancelled. Confirmation text did not match.");
    return;
  }
  try {
    const resp = await apiFetch("/tickets/admin/flush", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: "FLUSH" }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Flush failed");
    alert("Database flushed. Reloading tickets...");
    await loadTickets();
  } catch (e) {
    alert("Flush failed: " + (e.message || e));
  }
}
function addQuery() { alert("Add Query (MVP): not implemented yet."); }

function formatDate(dt) {
    if (!dt) return "—";
    try { return new Date(dt).toLocaleString(); } catch { return dt; }
}

function setTab(tab) {
    currentTab = tab;
    document.querySelectorAll(".tabbtn").forEach(btn => {
        const isActive = btn.dataset.tab === tab;
        btn.className = isActive
            ? "tabbtn px-4 py-2 rounded-lg border bg-indigo-600 text-white"
            : "tabbtn px-4 py-2 rounded-lg border bg-white";
    });
    loadTickets();
}

async function fetchNow() {
    const btn = document.getElementById("fetchBtn");
    btn.disabled = true;
    btn.textContent = "Fetching...";

    try {
        const start = document.getElementById("startDate").value;
        const end = document.getElementById("endDate").value;
        const maxThreads = parseInt(document.getElementById("maxThreads").value || "500", 10);
        const incremental = !!document.getElementById("incrementalSync").checked;
        const includeAnywhere = !!document.getElementById("includeAnywhere").checked;

        // Persist the selected date filter for the ticket list.
        currentDateFilter = { start: start || "", end: end || "" };

        const url = new URL("/autopilot/fetch-now", window.location.origin);
        if (start) url.searchParams.set("start", start);
        if (end) url.searchParams.set("end", end);
        if (!Number.isNaN(maxThreads) && maxThreads > 0) url.searchParams.set("max_threads", String(maxThreads));
        // incremental applies only when no date range
        if (!start && !end) url.searchParams.set("incremental", incremental ? "true" : "false");
        if (start || end) url.searchParams.set("include_anywhere", includeAnywhere ? "true" : "false");

        const r = await fetch(url.toString(), { method: "POST" });
        const text = await r.text();
        if (!r.ok) {
            alert(`Fetch failed (${r.status}):\n\n${text}`);
            return;
        }
        const j = JSON.parse(text);

        if (j && j.hit_limit) {
            alert("Fetch completed, but hit the configured limit. Increase Max and fetch again to capture more emails for the selected range.");
        }
        if (j && j.target_mailbox) {
            const mb = document.getElementById("mailboxBadge");
            if (mb) mb.textContent = `Mailbox: ${j.target_mailbox}`;
        }

        document.getElementById("lastSync").textContent = new Date().toLocaleString();
        await loadTickets();
        console.log(j);
    } catch (e) {
        alert("Fetch failed: " + e);
    } finally {
        btn.disabled = false;
        btn.textContent = "Fetch Now";
    }
}

function clearDateFilter() {
    const s = document.getElementById("startDate");
    const e = document.getElementById("endDate");
    if (s) s.value = "";
    if (e) e.value = "";
    currentDateFilter = { start: "", end: "" };
    loadTickets();
}

async function startAutopilot() {
    const r = await fetch("/autopilot/start", { method: "POST" });
    const t = await r.text();
    if (!r.ok) { alert(`Start failed (${r.status}):\n\n${t}`); return; }
    await refreshAutopilotStatus();
}

async function stopAutopilot() {
    const r = await fetch("/autopilot/stop", { method: "POST" });
    const t = await r.text();
    if (!r.ok) { alert(`Stop failed (${r.status}):\n\n${t}`); return; }
    await refreshAutopilotStatus();
}

async function refreshAutopilotStatus() {
    try {
        const r = await fetch("/autopilot/status");
        const j = await r.json();
        const running = !!j.running;

        document.getElementById("autopilotStatus").textContent = running ? "Active" : "Stopped";
        document.getElementById("statusDot").className = running
            ? "h-2 w-2 rounded-full bg-green-500"
            : "h-2 w-2 rounded-full bg-red-500";
        document.getElementById("nextRun").textContent = j.next_run_time || "—";
    } catch {
        // ignore
    }
}

function priorityBadge(p) {
    const val = (p || "medium").toLowerCase();
    if (val === "high") return `<span class="px-2 py-0.5 rounded-full text-xs bg-red-100 text-red-700 border">high</span>`;
    if (val === "low") return `<span class="px-2 py-0.5 rounded-full text-xs bg-emerald-100 text-emerald-700 border">low</span>`;
    return `<span class="px-2 py-0.5 rounded-full text-xs bg-amber-100 text-amber-700 border">medium</span>`;
}


function assigneeOptions(selectedId) {
    const opts = ['<option value="">Unassigned</option>'];
    for (const u of usersCache) {
        const sel = String(u.id) === String(selectedId) ? "selected" : "";
        opts.push(`<option value="${u.id}" ${sel}>${escapeHtml(u.name)} (${escapeHtml(u.role)})</option>`);
    }
    return opts.join("");
}

function categoryOptions(selected) {
    const cats = ["MAINTENANCE","RENT_ARREARS","LEASING","COMPLIANCE","SALES","GENERAL"];
    return cats.map(c => `<option value="${c}" ${c=== (selected||"GENERAL") ? "selected": ""}>${c.replace("_"," ")}</option>`).join("");
}

function statusOptions(selected) {
    const opts = [
        ["PENDING", "Pending"],
        ["IN_PROGRESS", "In Progress"],
        ["RESPONDED", "Responded"],
        ["NO_REPLY_NEEDED", "Reply Not Needed"]
    ];
    return opts.map(([v, label]) => `<option value="${v}" ${v === selected ? "selected" : ""}>${label}</option>`).join("");
}

function renderTicket(t) {
    const card = document.createElement("div");
    card.className = settings.compactTickets
        ? "bg-white rounded-xl shadow border p-4 flex items-start justify-between gap-4"
        : "bg-white rounded-xl shadow border p-5 flex items-start justify-between gap-4";

    const due = t.due_at ? `Due: ${formatDate(t.due_at)}` : "Due: —";
    const last = t.last_message_at ? `Last: ${formatDate(t.last_message_at)}` : "Last: —";

    const cat = (t.category || "GENERAL").replace("_", " ");
    const catBadge = `<span class="px-2 py-0.5 rounded-full text-xs bg-indigo-50 text-indigo-800 border">${cat}</span>`;
    const assignee = t.assignee_user_id ? (usersCache.find(u => u.id === t.assignee_user_id)?.name || `User#${t.assignee_user_id}`) : "Unassigned";
    const assigneeBadge = `<span class="px-2 py-0.5 rounded-full text-xs bg-slate-50 text-slate-700 border">${assignee}</span>`;

    let slaText = "SLA: —";
    let slaClass = "text-slate-500";
    if (t.sla_due_at) {
        const dueMs = Date.parse(t.sla_due_at);
        const nowMs = Date.now();
        const overdue = nowMs > dueMs;
        slaText = overdue ? `SLA overdue: ${formatDate(t.sla_due_at)}` : `SLA due: ${formatDate(t.sla_due_at)}`;
        slaClass = overdue ? "text-red-700" : "text-emerald-700";
    }

    card.innerHTML = `
    <div class="min-w-0 flex-1">
      <div class="flex items-center gap-2">
        <div class="font-semibold text-slate-900 truncate">${t.from_name || t.from_email || "(unknown sender)"}</div>
        ${priorityBadge(t.priority)}
        ${catBadge}
        ${assigneeBadge}
        ${t.is_not_replied ? `<span class="px-2 py-0.5 rounded-full text-xs bg-orange-100 text-orange-700 border">Not Replied</span>` : ``}
        ${t.is_unread ? `<span class="px-2 py-0.5 rounded-full text-xs bg-slate-100 text-slate-700 border">Unread</span>` : ``}
      </div>

      <div class="mt-1 text-slate-900 font-medium truncate">${t.subject || "(no subject)"}</div>
      <div class="mt-1 text-sm text-slate-500 truncate">${t.from_email || ""}</div>
      <div class="mt-2 text-sm text-slate-600">${t.snippet || ""}</div>

      <div class="mt-3 flex flex-wrap gap-3 text-xs text-slate-500">
        <div>${last}</div>
        <div class="text-orange-700">${due}</div>
        <div class="${slaClass}">${slaText}</div>
      </div>

      <div class="mt-4 flex flex-wrap gap-2">
        <button class="px-3 py-2 rounded-lg border text-slate-700 hover:bg-slate-50" onclick="openThread('${t.thread_id}')">Open</button>
        <button class="px-3 py-2 rounded-lg border text-slate-700 hover:bg-slate-50" onclick="openAckModal('${t.thread_id}')">Quick Reply</button>
        ${t.from_email ? `<button class="px-3 py-2 rounded-lg border text-red-700 hover:bg-red-50" onclick="blacklistSender('${t.from_email}')">Blacklist Sender</button>` : ``}
      </div>
    </div>

    <div class="flex flex-col items-end gap-2 w-56">
      <label class="w-full text-xs text-slate-500">Status</label>
      <select class="w-full px-3 py-2 rounded-lg border bg-white"
        onchange="updateStatus('${t.thread_id}', this.value)">
        ${statusOptions(t.status)}
      </select>
      <label class="w-full text-xs text-slate-500">Assignee</label>
      <select class="w-full px-3 py-2 rounded-lg border bg-white"
        onchange="updateAssignee('${t.thread_id}', this.value)">
        ${assigneeOptions(t.assignee_user_id)}
      </select>
      <label class="w-full text-xs text-slate-500">Category</label>
      <select class="w-full px-3 py-2 rounded-lg border bg-white"
        onchange="updateCategory('${t.thread_id}', this.value)">
        ${categoryOptions(t.category)}
      </select>
    </div>
  `;

    return card;
}

async function loadTickets() {
    const url = new URL(`/tickets`, window.location.origin);
    url.searchParams.set("tab", currentTab);
    url.searchParams.set("limit", "50");

    // Apply current filter (set by Fetch Now). If empty, do not filter.
    if (currentDateFilter.start) url.searchParams.set("start", currentDateFilter.start);
    if (currentDateFilter.end) url.searchParams.set("end", currentDateFilter.end);

    const r = await apiFetch(url);
    const data = await r.json();

    const c = data.counts || {};
    document.getElementById("countNotReplied").textContent = c.not_replied ?? 0;
    document.getElementById("countPending").textContent = c.pending ?? 0;
    document.getElementById("countInProgress").textContent = c.in_progress ?? 0;
    document.getElementById("countResponded").textContent = c.responded ?? 0;
    document.getElementById("countNoReplyNeeded").textContent = c.no_reply_needed ?? 0;

    const list = document.getElementById("ticketList");
    list.innerHTML = "";

    (data.items || []).forEach(t => list.appendChild(renderTicket(t)));

    if ((data.items || []).length === 0) {
        list.innerHTML = `<div class="text-slate-600 text-sm">No tickets in this tab.</div>`;
    }
}


async function updateAssignee(threadId, userId) {
    const payload = { assignee_user_id: userId ? Number(userId) : null };
    const r = await apiFetch(`/tickets/${threadId}/assign`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    if (!r.ok) {
        alert("Failed to assign ticket");
        return;
    }
    await loadTickets();
}

async function updateCategory(threadId, category) {
    const r = await apiFetch(`/tickets/${threadId}/category`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category }),
    });
    if (!r.ok) {
        alert("Failed to update category");
        return;
    }
    await loadTickets();
}

async function updateStatus(threadId, status) {
    await apiFetch(`/tickets/${threadId}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status })
    });
    await loadTickets();
}

async function openThread(threadId) {
    const modal = document.getElementById("threadModal");
    const content = document.getElementById("threadContent");
    const gmailLink = document.getElementById("gmailLink");

    modal.classList.remove("hidden");
    content.innerHTML = `<div class="text-sm text-slate-600">Loading thread…</div>`;

    const r = await apiFetch(`/threads/${threadId}`);
    const t = await r.text();
    if (!r.ok) {
        content.innerHTML = `<pre class="text-xs text-red-700 whitespace-pre-wrap">${t}</pre>`;
        return;
    }

    const j = JSON.parse(t);
    gmailLink.href = j.gmail_url || j.gmail_thread_url || "#";

    const escapeHtml = (s) => (s || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");

    const rewriteCid = (html, messageId) => {
        if (!html) return "";
        // Replace src="cid:..." or src='cid:...'
        return html.replace(/src\s*=\s*(["'])cid:([^"'>\s]+)\1/gi, (m, q, cid) => {
            const url = `/threads/${encodeURIComponent(threadId)}/messages/${encodeURIComponent(messageId)}/inline/${encodeURIComponent(cid)}`;
            return `src=${q}${url}${q}`;
        });
    };

    const rewriteRemoteImagesToProxy = (html) => {
        if (!html) return "";
        // Replace remote <img src="https://..."> with a privacy-preserving proxy endpoint.
        return html.replace(/(<img\b[^>]*\bsrc\s*=\s*)(["'])(https?:\/\/[^"'>\s]+)\2/gi, (m, pre, q, url) => {
            const proxied = `${window.location.origin}/threads/proxy-image?url=${encodeURIComponent(url)}`;
            return `${pre}${q}${proxied}${q}`;
        });
    };


    const attachmentBadge = (a) => {
        const name = a.filename || "attachment";
        const mime = (a.mime_type || "").toLowerCase();
        let label = "FILE";
        if (mime.startsWith("image/")) label = "IMAGE";
        else if (mime == "application/pdf") label = "PDF";
        else if (mime.startsWith("text/")) label = "TEXT";
        else if (mime.startsWith("application/vnd")) label = "DOC";
        const url = `/threads/${encodeURIComponent(threadId)}/messages/${encodeURIComponent(a.message_id || "")}/attachments/${encodeURIComponent(a.attachment_id)}?filename=${encodeURIComponent(name)}`;
        // message_id may be absent from API; we set it in-line below when building.
        return `<a class="inline-flex items-center gap-2 px-3 py-1 rounded-full border bg-white text-sm text-slate-700 hover:bg-slate-50" href="${url}" target="_blank" rel="noreferrer">
          <span class="text-xs px-2 py-0.5 rounded-full bg-slate-100 text-slate-600">${label}</span>
          <span class="truncate max-w-[220px]">${escapeHtml(name)}</span>
        </a>`;
    };

    const renderMessage = (m, idx) => {
        const hasHtml = !!m.body_html;
        const msgId = m.id;
        const safeText = escapeHtml(m.body_text || m.snippet || "");
        const iframeId = `msg_iframe_${idx}`;
        const btnId = `msg_toggle_${idx}`;
        let html = hasHtml ? rewriteCid(m.body_html, msgId) : "";
        if (hasHtml && settings.proxyRemoteImages) {
            html = rewriteRemoteImagesToProxy(html);
        }

        const atts = (m.attachments || []).map(a => ({...a, message_id: msgId})).filter(a => !a.is_inline);
        const attachmentsHtml = atts.length ? `<div class="mt-2 flex flex-wrap gap-2">${atts.map(attachmentBadge).join("")}</div>` : "";

        return `
        <div class="border rounded-xl p-4 bg-slate-50">
          <div class="flex items-start justify-between gap-3">
            <div>
              <div class="text-xs text-slate-500">${escapeHtml(m.date || "")}</div>
              <div class="text-sm"><span class="font-medium">From:</span> ${escapeHtml(m.from || "")}</div>
              <div class="text-sm"><span class="font-medium">To:</span> ${escapeHtml(m.to || "")}</div>
              <div class="text-sm"><span class="font-medium">Subject:</span> ${escapeHtml(m.subject || "")}</div>
              ${attachmentsHtml}
            </div>

            ${hasHtml ? `
              <button id="${btnId}" class="px-3 py-2 rounded-lg border text-slate-700 hover:bg-white" data-mode="html">
                View HTML
              </button>
            ` : ``}
          </div>

          <div class="mt-3">
            <div class="text-sm text-slate-700 whitespace-pre-wrap" data-mode="text">${safeText}</div>
            ${hasHtml ? `
              <div class="mt-3 hidden" data-mode="html">
                <iframe id="${iframeId}" class="w-full rounded-lg border bg-white" style="height: 520px;" sandbox="allow-popups allow-forms allow-same-origin" referrerpolicy="no-referrer"></iframe>
              </div>
            ` : ``}
          </div>
        </div>
      `;
    };

    content.innerHTML = (j.messages || []).map((m, idx) => renderMessage(m, idx)).join("");

    // Attach toggles and populate iframes AFTER insertion
    (j.messages || []).forEach((m, idx) => {
        if (!m.body_html) return;
        const btn = document.getElementById(`msg_toggle_${idx}`);
        const iframe = document.getElementById(`msg_iframe_${idx}`);
        if (!btn || !iframe) return;

        let html = rewriteCid(m.body_html, m.id);
        if (settings.proxyRemoteImages) {
            html = rewriteRemoteImagesToProxy(html);
        }
        iframe.srcdoc = html;

        // Default view preference
        if (settings.defaultHtmlView) {
            const card = btn.closest(".border");
            const textEl = card ? card.querySelector('[data-mode="text"]') : null;
            const htmlWrap = card ? card.querySelector('[data-mode="html"]') : null;
            if (textEl) textEl.classList.add("hidden");
            if (htmlWrap) htmlWrap.classList.remove("hidden");
            btn.textContent = "View Text";
        }

        btn.addEventListener("click", () => {
            const card = btn.closest(".border");
            if (!card) return;
            const textEl = card.querySelector('[data-mode="text"]');
            const htmlWrap = card.querySelector('[data-mode="html"]');
            const showing = htmlWrap && !htmlWrap.classList.contains("hidden");
            if (showing) {
                htmlWrap.classList.add("hidden");
                if (textEl) textEl.classList.remove("hidden");
                btn.textContent = "View HTML";
            } else {
                if (textEl) textEl.classList.add("hidden");
                if (htmlWrap) htmlWrap.classList.remove("hidden");
                btn.textContent = "View Text";
            }
        });
    });
}

function clearDateFilter() {
    document.getElementById("startDate").value = "";
    document.getElementById("endDate").value = "";
    currentDateFilter = { start: "", end: "" };
    loadTickets();
}

function closeThreadModal() {
    document.getElementById("threadModal").classList.add("hidden");
}

async function openAckModal(threadId) {
    currentAckThreadId = threadId;
    document.getElementById("ackModal").classList.remove("hidden");
    document.getElementById("ackSubject").value = "";
    document.getElementById("ackBody").value = "Loading draft…";
    document.getElementById("sendAckBtn").disabled = true;

    const r = await apiFetch(`/tickets/${threadId}/draft-ack`, { method: "POST" });
    const t = await r.text();
    if (!r.ok) {
        document.getElementById("ackBody").value = t;
        document.getElementById("sendAckBtn").disabled = true;
        return;
    }
    const j = JSON.parse(t);
    document.getElementById("ackSubject").value = j.subject || "";
    document.getElementById("ackBody").value = j.body || "";
    document.getElementById("sendAckBtn").disabled = false;
}

function closeAckModal() {
    document.getElementById("ackModal").classList.add("hidden");
    currentAckThreadId = null;
}

async function sendAckFromModal() {
    if (!currentAckThreadId) return;
    const subject = document.getElementById("ackSubject").value;
    const body = document.getElementById("ackBody").value;

    const btn = document.getElementById("sendAckBtn");
    btn.disabled = true;
    btn.textContent = "Sending...";

    try {
        const r = await apiFetch(`/tickets/${currentAckThreadId}/send-ack`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ subject, body, mark_as_responded: true })
        });
        const t = await r.text();
        if (!r.ok) {
            alert(`Send failed (${r.status}):\n\n${t}`);
            return;
        }
        closeAckModal();
        await loadTickets();
        alert("Acknowledgment sent.");
    } finally {
        btn.disabled = false;
        btn.textContent = "Send";
    }
}

async function blacklistSender(email) {
    if (!email) return;
    if (!confirm(`Blacklist sender ${email}? Future tickets from this sender will be hidden.`)) return;

    // Requires /blacklist endpoint. If you haven't added it yet, this will 404.
    const r = await apiFetch(`/blacklist?email=${encodeURIComponent(email)}`, { method: "POST" });
    const t = await r.text();
    if (!r.ok) {
        alert(`Blacklist failed (${r.status}):\n\n${t}`);
        return;
    }
    await loadTickets();
}

function showLoginModal() {
    const m = document.getElementById("loginModal");
    if (m) m.classList.remove("hidden");
}

function hideLoginModal() {
    const m = document.getElementById("loginModal");
    if (m) m.classList.add("hidden");
}

async function ensureAuthenticated() {
    if (!authToken) {
        showLoginModal();
        return false;
    }
    const r = await apiFetch("/user-auth/me");
    if (!r.ok) {
        authToken = "";
        localStorage.removeItem("agent_auth_token");
        showLoginModal();
        return false;
    }
    currentUser = await r.json();
    await loadUsersCache();
    const badge = document.getElementById("userBadge");
    if (badge) badge.textContent = `Signed in as: ${currentUser.name} (${currentUser.role})`;
    const logoutBtn = document.getElementById("logoutBtn");
    if (logoutBtn) logoutBtn.classList.remove("hidden");
    return true;
}

async function loadUsersCache() {
    try {
        const r = await apiFetch("/user-auth/users");
        if (!r.ok) return;
        usersCache = await r.json();
    } catch {
        // ignore
    }
}

async function doLogin() {
    const email = (document.getElementById("loginEmail").value || "").trim();
    const password = document.getElementById("loginPassword").value || "";
    const err = document.getElementById("loginError");
    if (err) err.textContent = "";

    const r = await fetch("/user-auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
    });
    if (!r.ok) {
        const t = await r.text();
        if (err) err.textContent = t || "Login failed";
        return;
    }
    const j = await r.json();
    authToken = j.access_token;
    localStorage.setItem("agent_auth_token", authToken);
    hideLoginModal();
    await ensureAuthenticated();
    await refreshGoogleStatus();
    await refreshAutopilotStatus();
    await loadTickets();
}

function logout() {
    authToken = "";
    currentUser = null;
    localStorage.removeItem("agent_auth_token");
    const badge = document.getElementById("userBadge");
    if (badge) badge.textContent = "";
    const logoutBtn = document.getElementById("logoutBtn");
    if (logoutBtn) logoutBtn.classList.add("hidden");
    showLoginModal();
}

window.addEventListener("load", async () => {
    loadSettings();
    document.getElementById("lastSync").textContent = new Date().toLocaleString();
    const ok = await ensureAuthenticated();
    if (!ok) return;

    await refreshGoogleStatus();

    // Small UX: show a one-time confirmation after OAuth callback.
    try {
        const params = new URLSearchParams(window.location.search);
        if (params.get("connected") === "1") {
            // Remove the parameter so the alert does not repeat on refresh.
            params.delete("connected");
            const newUrl = window.location.pathname + (params.toString() ? `?${params.toString()}` : "");
            window.history.replaceState({}, "", newUrl);
            alert("Google account connected successfully.");
        }
    } catch {
        // ignore
    }

    await refreshAutopilotStatus();
    await loadTickets();
});
