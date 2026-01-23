/* app/static/app.js
 * Compatible with the user-provided Tailwind index.html (onclick functions + IDs).
 * Includes:
 * - Auth (login modal + token storage)
 * - Google connect/manage (redirect fallback)
 * - Autopilot start/stop/fetch with date range and incremental
 * - Tickets list rendering + tab filters + actions (status/category/assignee)
 * - Thread modal open
 * - Quick Reply modal + send
 * - Settings modal (localStorage prefs)
 * - Flush DB (danger)
 * - Users modal (Admin)
 */

/* ----------------------------- API ROUTES ----------------------------- */
/** If any of your backend paths differ, adjust ONLY here. */
const API = {
    auth: {
        login: ["/user-auth/login", "/auth/login"],
        me: ["/user-auth/me", "/auth/me"],
        users: ["/user-auth/users", "/auth/users"],
    },
    google: {
        status: ["/google/status", "/auth/google/status", "/oauth/status"],
        start: ["/google/connect", "/auth/google/connect", "/oauth/google/start", "/auth/google"],
        disconnect: ["/google/disconnect", "/auth/google/disconnect"],
    },
    autopilot: {
        status: ["/autopilot/status", "/api/autopilot/status"],
        fetch: ["/autopilot/fetch", "/autopilot/fetch-now", "/autopilot/fetch_now", "/api/autopilot/fetch"],
        start: ["/autopilot/start", "/autopilot/run", "/autopilot/enable", "/api/autopilot/start"],
        stop: ["/autopilot/stop", "/autopilot/pause", "/autopilot/disable", "/api/autopilot/stop"],
    },
    tickets: {
        list: ["/tickets", "/api/tickets"],
        patch: (threadId) => [
            `/tickets/${encodeURIComponent(threadId)}`,
            `/api/tickets/${encodeURIComponent(threadId)}`,
        ],
        status: (threadId) => [
            `/tickets/${encodeURIComponent(threadId)}/status`,
            `/api/tickets/${encodeURIComponent(threadId)}/status`,
        ],
        category: (threadId) => [
            `/tickets/${encodeURIComponent(threadId)}/category`,
            `/api/tickets/${encodeURIComponent(threadId)}/category`,
        ],
        assign: (threadId) => [
            `/tickets/${encodeURIComponent(threadId)}/assign`,
            `/api/tickets/${encodeURIComponent(threadId)}/assign`,
        ],
        blacklist: (threadId) => [
            `/tickets/${encodeURIComponent(threadId)}/blacklist`,
            `/api/tickets/${encodeURIComponent(threadId)}/blacklist`,
        ],
    },
    threads: {
        get: (threadId) => [
            `/threads/${encodeURIComponent(threadId)}`,
            `/api/threads/${encodeURIComponent(threadId)}`,
        ],
        reply: (threadId) => [
            `/threads/${encodeURIComponent(threadId)}/reply`,
            `/api/threads/${encodeURIComponent(threadId)}/reply`,
            `/tickets/${encodeURIComponent(threadId)}/reply`,
            `/api/tickets/${encodeURIComponent(threadId)}/reply`,
        ],
    },
    admin: {
        flush: ["/admin/flush", "/admin/flush-db", "/settings/flush", "/danger/flush", "/api/admin/flush"],
    },
};

/* ----------------------------- STATE ----------------------------- */
const state = {
    token: localStorage.getItem("agentbot_token") || null,
    me: null,
    google: { connected: false, email: null },
    autopilot: null,
    users: [],
    tickets: [],
    tab: "all", // all | not_replied | pending | in_progress | responded | no_reply_needed
    settings: {
        defaultHtml: JSON.parse(localStorage.getItem("setDefaultHtml") || "true"),
        proxyRemote: JSON.parse(localStorage.getItem("setBlockRemote") || "true"),
        compact: JSON.parse(localStorage.getItem("setCompact") || "false"),
    },
    currentThreadId: null,
};

/* ----------------------------- HELPERS ----------------------------- */
function $(id) { return document.getElementById(id); }

function escapeHtml(input) {
    if (input === null || input === undefined) return "";
    return String(input)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function showModal(id) { const el = $(id); if (el) el.classList.remove("hidden"); }
function hideModal(id) { const el = $(id); if (el) el.classList.add("hidden"); }

function setText(id, text) { const el = $(id); if (el) el.textContent = text; }

function setDot(id, colorClass) {
    const el = $(id);
    if (!el) return;
    el.className = `h-2 w-2 rounded-full ${colorClass}`;
}

function fmtDateTime(iso) {
    if (!iso) return "—";
    try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

function isOverdue(slaDueAt) {
    if (!slaDueAt) return false;
    try { return new Date(slaDueAt).getTime() < Date.now(); } catch { return false; }
}

async function apiFetch(path, opts = {}) {
    const headers = new Headers(opts.headers || {});
    if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    if (state.token) headers.set("Authorization", `Bearer ${state.token}`);

    const resp = await fetch(path, { ...opts, headers });
    const ct = resp.headers.get("content-type") || "";
    const isJson = ct.includes("application/json");
    const data = isJson ? await resp.json().catch(() => null) : await resp.text();

    if (resp.status === 401) {
        // Kick to login
        state.token = null;
        localStorage.removeItem("agentbot_token");
        state.me = null;
        updateHeader();
        showModal("loginModal");
        throw new Error("Unauthorized");
    }

    if (!resp.ok) {
        const msg = data?.detail || data?.message || (typeof data === "string" ? data : `HTTP ${resp.status}`);
        // Normalize 404 detection for fallback loop
        const err = new Error(msg);
        err.httpStatus = resp.status;
        throw err;
    }

    return data;
}

async function apiFetchAny(paths, opts = {}) {
    const list = Array.isArray(paths) ? paths : [paths];
    let lastErr = null;

    for (const p of list) {
        try {
            return await apiFetch(p, opts);
        } catch (e) {
            lastErr = e;
            if (e?.httpStatus === 404) continue;
            // If method mismatch, try next
            const msg = String(e?.message || "");
            if (msg.toLowerCase().includes("method not allowed")) continue;
            break;
        }
    }
    throw lastErr || new Error("Request failed");
}

/* ----------------------------- AUTH ----------------------------- */
async function doLogin() {
    const email = ($("loginEmail")?.value || "").trim();
    const password = $("loginPassword")?.value || "";
    setText("loginError", "");

    try {
        const data = await apiFetchAny(API.auth.login, {
            method: "POST",
            body: JSON.stringify({ email, password }),
        });

        const token = data?.access_token;
        if (!token) throw new Error("Login failed (no token)");

        state.token = token;
        localStorage.setItem("agentbot_token", token);
        hideModal("loginModal");

        await bootstrapAfterAuth();
    } catch (e) {
        setText("loginError", e?.message || String(e));
    }
}

function logout() {
    state.token = null;
    localStorage.removeItem("agentbot_token");
    state.me = null;
    updateHeader();
    showModal("loginModal");
}

/* ----------------------------- GOOGLE ----------------------------- */
async function googleConnectOrManage() {
    // If connected, we can (optionally) disconnect. For now, just re-run status or redirect.
    try {
        const s = await apiFetchAny(API.google.status, { method: "GET" });
        const connected = !!(s?.connected || s?.google_connected);
        if (!connected) {
            // Start OAuth (some backends expect redirect rather than fetch)
            await startGoogleOAuth();
        } else {
            alert("Google is connected. If you need to re-authorize, disconnect in backend or revoke in Google and click Connect again.");
        }
    } catch {
        // If status endpoint doesn’t exist, just try redirect start.
        await startGoogleOAuth();
    }
}

async function startGoogleOAuth() {
    // Try calling a start endpoint; if it fails/404, redirect browser to a likely OAuth path.
    try {
        const r = await apiFetchAny(API.google.start, { method: "GET" });
        // If backend returns a URL, redirect there
        const url = r?.auth_url || r?.url;
        if (url) window.location.href = url;
        else window.location.href = API.google.start[API.google.start.length - 1];
    } catch {
        // redirect fallback
        window.location.href = "/auth/google";
    }
}

/* ----------------------------- AUTOPILOT ----------------------------- */
function clearDateFilter() {
    if ($("startDate")) $("startDate").value = "";
    if ($("endDate")) $("endDate").value = "";
    // Keep incremental checked by default
    if ($("incrementalSync")) $("incrementalSync").checked = true;
    if ($("includeAnywhere")) $("includeAnywhere").checked = false;
}

function buildFetchParams() {
    const params = new URLSearchParams();
    const from = $("startDate")?.value || "";
    const to = $("endDate")?.value || "";
    const maxThreads = $("maxThreads")?.value || "500";
    const incremental = $("incrementalSync")?.checked ? "1" : "0";
    const allMail = $("includeAnywhere")?.checked ? "1" : "0";

    // Support multiple common names
    if (from) { params.set("from", from); params.set("from_date", from); params.set("start_date", from); }
    if (to) { params.set("to", to); params.set("to_date", to); params.set("end_date", to); }

    params.set("limit", String(maxThreads));
    params.set("max_threads", String(maxThreads));
    params.set("maxResults", String(maxThreads));

    params.set("incremental", incremental);
    params.set("incremental_sync", incremental);
    params.set("all_mail", allMail);
    params.set("allMail", allMail);

    return params;
}

async function fetchNow() {
    $("fetchBtn")?.setAttribute("disabled", "true");
    setText("fetchBtn", "Fetching…");

    try {
        const params = buildFetchParams();

        // Many implementations expect POST with querystring; try that first.
        const urlVariants = API.autopilot.fetch.map(p => `${p}?${params.toString()}`);
        await apiFetchAny(urlVariants, { method: "POST" });

        await refreshAutopilotStatus();
        await loadTickets();
    } catch (e) {
        console.error("fetchNow error:", e);
        alert(`Fetch failed: ${e?.message || e}`);
    } finally {
        $("fetchBtn")?.removeAttribute("disabled");
        setText("fetchBtn", "Fetch Now");
    }
}

async function startAutopilot() {
    try {
        await apiFetchAny(API.autopilot.start, { method: "POST" });
        await refreshAutopilotStatus();
    } catch (e) {
        alert(`Start failed: ${e?.message || e}`);
    }
}

async function stopAutopilot() {
    try {
        await apiFetchAny(API.autopilot.stop, { method: "POST" });
        await refreshAutopilotStatus();
    } catch (e) {
        alert(`Stop failed: ${e?.message || e}`);
    }
}

async function refreshAutopilotStatus() {
    try {
        const s = await apiFetchAny(API.autopilot.status, { method: "GET" });
        state.autopilot = s;

        const running = !!(s?.running || s?.active);
        setDot("statusDot", running ? "bg-green-500" : "bg-red-500");
        setText("autopilotStatus", running ? "Active" : "Stopped");

        setText("pollEvery", String(s?.interval_minutes ?? s?.interval ?? 5));
        setText("lastSync", fmtDateTime(s?.last_sync_at || s?.last_sync));
        setText("nextRun", fmtDateTime(s?.next_run_at || s?.next_run));

        // mailbox badge if available
        const mailbox = s?.mailbox || s?.gmail_account || s?.email;
        if (mailbox) setText("mailboxBadge", `Mailbox: ${mailbox}`);

        // google connection badge button
        const connected = !!(s?.google_connected || s?.connected);
        state.google.connected = connected;
        if ($("googleBtn")) {
            $("googleBtn").textContent = connected ? "Google Connected" : "Connect to Google";
            $("googleBtn").className = connected
                ? "px-4 py-2 rounded-lg border text-emerald-700 bg-emerald-50 hover:bg-emerald-100"
                : "px-4 py-2 rounded-lg border text-slate-700 hover:bg-slate-50";
        }
    } catch (e) {
        // Don’t block UI
        console.warn("autopilot status unavailable:", e?.message || e);
    }
}

/* ----------------------------- TICKETS ----------------------------- */
function normalizeTicket(raw) {
    // Support different backend shapes
    return {
        thread_id: raw.thread_id || raw.threadId || raw.id,
        subject: raw.subject || "(no subject)",
        from_name: raw.from_name || raw.fromName || "",
        from_email: raw.from_email || raw.fromEmail || raw.sender_email || "",
        snippet: raw.snippet || raw.preview || "",
        status: raw.status || "pending",
        priority: raw.priority || raw.priority_label || "",
        category: raw.category || raw.intent || "GENERAL",
        is_unread: raw.is_unread ?? raw.unread ?? false,
        last_message_at: raw.last_message_at || raw.updated_at || raw.lastMessageAt,
        sla_due_at: raw.sla_due_at || raw.slaDueAt || null,
        assignee_user_id: raw.assignee_user_id || raw.assigneeUserId || raw.assignee_id || null,
        gmail_thread_url: raw.gmail_thread_url || raw.gmailUrl || null,
    };
}

function computeCounts(tickets) {
    // counts reflect currently selected tab filter
    const counts = {
        notReplied: 0,
        pending: 0,
        inProgress: 0,
        responded: 0,
        noReply: 0,
    };
    for (const t of tickets) {
        const st = String(t.status || "").toLowerCase();
        if (st === "pending") counts.pending++;
        if (st === "in_progress") counts.inProgress++;
        if (st === "responded") counts.responded++;
        if (st === "no_reply_needed" || st === "reply_not_needed") counts.noReply++;

        const pri = String(t.priority || "").toLowerCase();
        if (pri === "priority" && st !== "responded" && st !== "no_reply_needed" && st !== "reply_not_needed") {
            counts.notReplied++;
        }
    }
    return counts;
}

function applyTab(tickets) {
    const tab = state.tab;
    if (tab === "all") return tickets;

    const map = {
        not_replied: (t) => {
            const pri = String(t.priority || "").toLowerCase() === "priority";
            const st = String(t.status || "").toLowerCase();
            return pri && st !== "responded" && st !== "no_reply_needed" && st !== "reply_not_needed";
        },
        pending: (t) => String(t.status || "").toLowerCase() === "pending",
        in_progress: (t) => String(t.status || "").toLowerCase() === "in_progress",
        responded: (t) => String(t.status || "").toLowerCase() === "responded",
        no_reply_needed: (t) => {
            const st = String(t.status || "").toLowerCase();
            return st === "no_reply_needed" || st === "reply_not_needed";
        },
    };

    const fn = map[tab];
    return fn ? tickets.filter(fn) : tickets;
}

async function loadTickets() {
    const data = await apiFetchAny(API.tickets.list, { method: "GET" });
    const items = Array.isArray(data) ? data : (data?.items || data?.tickets || []);
    state.tickets = items.map(normalizeTicket);

    renderCountsAndList();
}

function renderCountsAndList() {
    // IMPORTANT: KPI should reflect actual loaded tickets (not stale)
    const all = state.tickets || [];
    const counts = computeCounts(all);

    setText("countNotReplied", String(counts.notReplied));
    setText("countPending", String(counts.pending));
    setText("countInProgress", String(counts.inProgress));
    setText("countResponded", String(counts.responded));
    setText("countNoReplyNeeded", String(counts.noReply));

    const filtered = applyTab(all);
    renderTicketList(filtered);
}

function roleBadge(role) {
    const r = String(role || "").toUpperCase();
    const cls = {
        ADMIN: "bg-indigo-50 text-indigo-700 border-indigo-200",
        PM: "bg-sky-50 text-sky-700 border-sky-200",
        LEASING: "bg-emerald-50 text-emerald-700 border-emerald-200",
        SALES: "bg-amber-50 text-amber-700 border-amber-200",
        ACCOUNTS: "bg-orange-50 text-orange-700 border-orange-200",
        READONLY: "bg-slate-50 text-slate-700 border-slate-200",
    }[r] || "bg-slate-50 text-slate-700 border-slate-200";

    return `<span class="px-2 py-1 rounded-full border text-xs ${cls}">${escapeHtml(r || "USER")}</span>`;
}

function ticketBadge(label, cls) {
    return `<span class="px-2 py-1 rounded-full border text-xs ${cls}">${escapeHtml(label)}</span>`;
}

function userName(id) {
    const u = state.users.find(x => String(x.id) === String(id));
    return u ? (u.full_name || u.email) : "Unassigned";
}

function renderTicketList(tickets) {
    const list = $("ticketList");
    if (!list) return;

    if (!tickets.length) {
        list.innerHTML = `
      <div class="bg-white rounded-xl shadow border p-5">
        <div class="font-semibold text-slate-900">No tickets</div>
        <div class="text-sm text-slate-500 mt-1">Click <b>Fetch Now</b> or change filters.</div>
      </div>
    `;
        return;
    }

    const compact = !!state.settings.compact;

    list.innerHTML = tickets.map(t => {
        const unread = t.is_unread;
        const overdue = isOverdue(t.sla_due_at);
        const pri = String(t.priority || "").toLowerCase() === "priority";

        const badges = [
            pri ? ticketBadge("Priority", "bg-amber-50 text-amber-700 border-amber-200") : "",
            unread ? ticketBadge("Unread", "bg-indigo-50 text-indigo-700 border-indigo-200") : "",
            overdue ? ticketBadge("Overdue", "bg-red-50 text-red-700 border-red-200") : "",
            ticketBadge(t.category || "GENERAL", "bg-slate-50 text-slate-700 border-slate-200"),
        ].filter(Boolean).join(" ");

        const assigneeLabel = t.assignee_user_id ? userName(t.assignee_user_id) : "Unassigned";

        return `
      <div class="bg-white rounded-xl shadow border p-${compact ? "4" : "6"}">
        <div class="flex items-start justify-between gap-4">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <div class="font-semibold text-slate-900 truncate">${escapeHtml(t.subject)}</div>
              ${badges}
            </div>
            <div class="text-sm text-slate-500 mt-1">
              ${escapeHtml(t.from_name)} ${t.from_email ? `&lt;${escapeHtml(t.from_email)}&gt;` : ""}
            </div>
            <div class="text-sm text-slate-700 mt-2">${escapeHtml(t.snippet || "")}</div>
            <div class="text-xs text-slate-500 mt-3">
              Last: ${escapeHtml(fmtDateTime(t.last_message_at))} • SLA: ${escapeHtml(fmtDateTime(t.sla_due_at))}
            </div>
          </div>

          <div class="w-full max-w-sm flex flex-col gap-2">
            <div class="grid grid-cols-1 md:grid-cols-3 gap-2">
              <select class="px-3 py-2 rounded-lg border bg-white text-sm"
                      onchange="updateTicketStatus('${escapeHtml(t.thread_id)}', this.value)">
                ${statusOptions(t.status)}
              </select>

              <select class="px-3 py-2 rounded-lg border bg-white text-sm"
                      onchange="updateTicketCategory('${escapeHtml(t.thread_id)}', this.value)">
                ${categoryOptions(t.category)}
              </select>

              <select class="px-3 py-2 rounded-lg border bg-white text-sm"
                      onchange="updateTicketAssignee('${escapeHtml(t.thread_id)}', this.value)">
                ${assigneeOptions(t.assignee_user_id)}
              </select>
            </div>

            <div class="flex gap-2 justify-end">
              <button class="px-4 py-2 rounded-lg border text-slate-700 hover:bg-slate-50"
                      onclick="openThreadModal('${escapeHtml(t.thread_id)}')">Open</button>

              <button class="px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700"
                      onclick="openAckModal('${escapeHtml(t.thread_id)}')">Quick Reply</button>
            </div>

            <div class="text-xs text-slate-500 text-right">
              Assignee: ${escapeHtml(assigneeLabel)}
            </div>
          </div>
        </div>
      </div>
    `;
    }).join("");
}

function statusOptions(current) {
    const cur = String(current || "pending").toLowerCase();
    const opts = [
        ["pending", "Pending"],
        ["in_progress", "In Progress"],
        ["responded", "Responded"],
        ["no_reply_needed", "Reply Not Needed"],
    ];
    return opts.map(([v, label]) => `<option value="${v}" ${cur === v ? "selected" : ""}>${label}</option>`).join("");
}

function categoryOptions(current) {
    const cur = String(current || "GENERAL").toUpperCase();
    const opts = ["MAINTENANCE", "RENT_ARREARS", "LEASING", "COMPLIANCE", "SALES", "GENERAL"];
    return opts.map(v => `<option value="${v}" ${cur === v ? "selected" : ""}>${v}</option>`).join("");
}

function assigneeOptions(currentId) {
    const cur = currentId ? String(currentId) : "";
    const base = `<option value="">Unassigned</option>`;
    const users = state.users.map(u => {
        const id = String(u.id);
        const label = u.full_name || u.email;
        return `<option value="${escapeHtml(id)}" ${cur === id ? "selected" : ""}>${escapeHtml(label)}</option>`;
    }).join("");
    return base + users;
}

/* ----------------------------- TICKET ACTIONS ----------------------------- */
async function updateTicketStatus(threadId, status) {
    try {
        // Try explicit endpoint first, then patch fallback
        try {
            await apiFetchAny(API.tickets.status(threadId), { method: "PATCH", body: JSON.stringify({ status }) });
        } catch {
            await apiFetchAny(API.tickets.patch(threadId), { method: "PATCH", body: JSON.stringify({ status }) });
        }
        // update local state
        const t = state.tickets.find(x => x.thread_id === threadId);
        if (t) t.status = status;
        renderCountsAndList();
    } catch (e) {
        alert(`Status update failed: ${e?.message || e}`);
    }
}

async function updateTicketCategory(threadId, category) {
    try {
        try {
            await apiFetchAny(API.tickets.category(threadId), { method: "PATCH", body: JSON.stringify({ category }) });
        } catch {
            await apiFetchAny(API.tickets.patch(threadId), { method: "PATCH", body: JSON.stringify({ category }) });
        }
        const t = state.tickets.find(x => x.thread_id === threadId);
        if (t) t.category = category;
        renderCountsAndList();
    } catch (e) {
        alert(`Category update failed: ${e?.message || e}`);
    }
}

async function updateTicketAssignee(threadId, assigneeUserId) {
    try {
        const payload = { assignee_user_id: assigneeUserId ? assigneeUserId : null };
        try {
            await apiFetchAny(API.tickets.assign(threadId), { method: "PATCH", body: JSON.stringify(payload) });
        } catch {
            await apiFetchAny(API.tickets.patch(threadId), { method: "PATCH", body: JSON.stringify(payload) });
        }
        const t = state.tickets.find(x => x.thread_id === threadId);
        if (t) t.assignee_user_id = assigneeUserId || null;
        renderCountsAndList();
    } catch (e) {
        alert(`Assignee update failed: ${e?.message || e}`);
    }
}

/* ----------------------------- THREAD MODAL ----------------------------- */
async function openThreadModal(threadId) {
    state.currentThreadId = threadId;
    showModal("threadModal");
    setText("threadContent", "Loading…");

    try {
        const data = await apiFetchAny(API.threads.get(threadId), { method: "GET" });

        // Gmail link
        const gmailUrl = data?.gmail_thread_url || data?.gmailUrl || data?.thread_url || null;
        const gmailLink = $("gmailLink");
        if (gmailLink) {
            gmailLink.href = gmailUrl || "#";
            gmailLink.style.display = gmailUrl ? "" : "none";
        }

        // Render messages
        const messages = Array.isArray(data?.messages) ? data.messages : (Array.isArray(data) ? data : []);
        if (!messages.length && (data?.html || data?.thread_html)) {
            // fallback single blob
            renderThreadHtmlBlob(data?.thread_html || data?.html);
            return;
        }

        const html = messages.map((m) => renderMessage(m)).join("");
        $("threadContent").innerHTML = html || `<div class="text-sm text-slate-500">No messages returned.</div>`;
    } catch (e) {
        $("threadContent").innerHTML = `<div class="text-sm text-red-600">Failed to load thread: ${escapeHtml(e?.message || e)}</div>`;
    }
}

function closeThreadModal() {
    hideModal("threadModal");
    state.currentThreadId = null;
}

function renderThreadHtmlBlob(html) {
    // Use iframe like your previous approach but safe-ish
    const safe = html || "<div style='font-family:Arial;padding:16px'>No content</div>";
    $("threadContent").innerHTML = `
    <div class="border rounded-lg overflow-hidden">
      <iframe sandbox="allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
              class="w-full h-[60vh]" srcdoc="${safe.replaceAll('"', '&quot;')}"></iframe>
    </div>
  `;
}

function renderMessage(m) {
    const from = m?.from_name || m?.from || "";
    const date = fmtDateTime(m?.date || m?.internal_date || m?.received_at);
    const subj = m?.subject || "";
    const bodyText = m?.body_text || m?.text || "";
    const bodyHtml = m?.body_html_sanitized || m?.body_html || m?.html || "";

    const preferHtml = !!state.settings.defaultHtml && !!bodyHtml;

    if (preferHtml) {
        // Note: backend should sanitize already
        return `
      <div class="rounded-xl border p-4">
        <div class="text-xs text-slate-500">${escapeHtml(from)} • ${escapeHtml(date)}</div>
        ${subj ? `<div class="text-sm font-semibold text-slate-900 mt-1">${escapeHtml(subj)}</div>` : ""}
        <div class="mt-3 border rounded-lg overflow-hidden">
          <iframe sandbox="allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
                  class="w-full h-[42vh]" srcdoc="${(bodyHtml || "").replaceAll('"', '&quot;')}"></iframe>
        </div>
      </div>
    `;
    }

    return `
    <div class="rounded-xl border p-4">
      <div class="text-xs text-slate-500">${escapeHtml(from)} • ${escapeHtml(date)}</div>
      ${subj ? `<div class="text-sm font-semibold text-slate-900 mt-1">${escapeHtml(subj)}</div>` : ""}
      <div class="text-sm text-slate-700 mt-2 whitespace-pre-wrap">${escapeHtml(bodyText || "(no text body)")}</div>
    </div>
  `;
}

/* ----------------------------- QUICK REPLY ----------------------------- */
function openAckModal(threadId) {
    state.currentThreadId = threadId;
    // Pre-fill subject/body if we can
    const t = state.tickets.find(x => x.thread_id === threadId);
    if (t) {
        $("ackSubject").value = `Re: ${t.subject || ""}`.trim();
        $("ackBody").value = "";
    } else {
        $("ackSubject").value = "";
        $("ackBody").value = "";
    }
    showModal("ackModal");
}

function closeAckModal() {
    hideModal("ackModal");
}

async function sendAckFromModal() {
    const threadId = state.currentThreadId;
    if (!threadId) return;

    const subject = $("ackSubject")?.value || "";
    const body = $("ackBody")?.value || "";

    const btn = $("sendAckBtn");
    if (btn) btn.setAttribute("disabled", "true");

    try {
        await apiFetchAny(API.threads.reply(threadId), {
            method: "POST",
            body: JSON.stringify({ subject, body }),
        });

        // Mark ticket responded as a convenience
        await updateTicketStatus(threadId, "responded");
        closeAckModal();
    } catch (e) {
        alert(`Send failed: ${e?.message || e}`);
    } finally {
        if (btn) btn.removeAttribute("disabled");
    }
}

/* ----------------------------- SETTINGS MODAL ----------------------------- */
function openSettings() {
    // Load settings into checkboxes
    if ($("setDefaultHtml")) $("setDefaultHtml").checked = !!state.settings.defaultHtml;
    if ($("setBlockRemote")) $("setBlockRemote").checked = !!state.settings.proxyRemote;
    if ($("setCompact")) $("setCompact").checked = !!state.settings.compact;
    showModal("settingsModal");
}

function closeSettings() {
    hideModal("settingsModal");
}

function applySettingsFromModal() {
    state.settings.defaultHtml = !!$("setDefaultHtml")?.checked;
    state.settings.proxyRemote = !!$("setBlockRemote")?.checked;
    state.settings.compact = !!$("setCompact")?.checked;

    localStorage.setItem("setDefaultHtml", JSON.stringify(state.settings.defaultHtml));
    localStorage.setItem("setBlockRemote", JSON.stringify(state.settings.proxyRemote));
    localStorage.setItem("setCompact", JSON.stringify(state.settings.compact));

    closeSettings();
    renderCountsAndList();
}

async function flushDatabase() {
    const confirmText = prompt('Type FLUSH to confirm deleting all tickets and sync state:');
    if (confirmText !== "FLUSH") return;

    try {
        await apiFetchAny(API.admin.flush, { method: "POST" });
        alert("Database flushed.");
        await loadTickets();
    } catch (e) {
        alert(`Flush failed: ${e?.message || e}`);
    }
}

/* ----------------------------- USERS (ADMIN) ----------------------------- */
function openUsersModal() {
    showModal("usersModal");
    renderUsersList();
}

function closeUsersModal() {
    hideModal("usersModal");
}

async function loadUsers() {
    try {
        state.users = await apiFetchAny(API.auth.users, { method: "GET" });
        // normalize expected shape
        if (!Array.isArray(state.users)) state.users = state.users?.items || state.users?.users || [];
    } catch (e) {
        // Non-fatal: tickets can still work without users
        console.warn("users load failed:", e?.message || e);
        state.users = [];
    }
}

function renderUsersList() {
    const wrap = $("usersList");
    if (!wrap) return;

    if (!state.users.length) {
        wrap.innerHTML = `<div class="text-sm text-slate-500">No users loaded.</div>`;
        return;
    }

    wrap.innerHTML = state.users.map(u => {
        return `
      <div class="flex items-center justify-between gap-3 border rounded-lg p-3 bg-white">
        <div class="min-w-0">
          <div class="font-medium text-slate-900 truncate">${escapeHtml(u.full_name || u.name || "—")}</div>
          <div class="text-sm text-slate-500 truncate">${escapeHtml(u.email || "")}</div>
        </div>
        <div class="flex items-center gap-2">
          ${roleBadge(u.role)}
        </div>
      </div>
    `;
    }).join("");
}

async function createUserFromForm() {
    const email = ($("newUserEmail")?.value || "").trim();
    const full_name = ($("newUserName")?.value || "").trim();
    const role = $("newUserRole")?.value || "PM";
    const password = $("newUserPassword")?.value || "";

    if (!email || !password) {
        alert("Email + Temp password are required.");
        return;
    }

    try {
        await apiFetchAny(API.auth.users, {
            method: "POST",
            body: JSON.stringify({ email, full_name, role, password }),
        });

        $("newUserEmail").value = "";
        $("newUserName").value = "";
        $("newUserPassword").value = "";

        await loadUsers();
        renderUsersList();
        alert("User created.");
    } catch (e) {
        alert(`Create user failed: ${e?.message || e}`);
    }
}

/* ----------------------------- TABS ----------------------------- */
function setTab(tab) {
    state.tab = tab;

    // Update tab button styling
    document.querySelectorAll(".tabbtn").forEach(btn => {
        const isActive = btn.getAttribute("data-tab") === tab;
        btn.className = isActive
            ? "tabbtn px-4 py-2 rounded-lg border bg-indigo-600 text-white"
            : "tabbtn px-4 py-2 rounded-lg border bg-white";
    });

    renderCountsAndList();
}

/* ----------------------------- ADD QUERY (placeholder) ----------------------------- */
/* Your HTML calls addQuery(), but you did not include an Add Query modal in the snippet.
 * If you have one elsewhere, hook it here. For now we keep a safe placeholder.
 */
function addQuery() {
    alert("Add Query UI is not included in this template snippet. If you have query rules in backend, tell me the endpoint/fields and I will wire it.");
}

/* ----------------------------- HEADER / BOOTSTRAP ----------------------------- */
async function loadMe() {
    state.me = await apiFetchAny(API.auth.me, { method: "GET" });
}

function updateHeader() {
    const userBadge = $("userBadge");
    const logoutBtn = $("logoutBtn");

    if (state.me) {
        const name = state.me.full_name || state.me.name || state.me.email || "User";
        userBadge.textContent = `Signed in as: ${name} (${state.me.role || "USER"})`;
        logoutBtn?.classList.remove("hidden");
    } else {
        if (userBadge) userBadge.textContent = "";
        logoutBtn?.classList.add("hidden");
    }
}

async function bootstrapAfterAuth() {
    await loadMe().catch(() => null);
    updateHeader();

    await loadUsers(); // may fail if non-admin; not fatal
    await refreshAutopilotStatus();
    await loadTickets();

    // If admin, allow users modal opening (optional: you can add a button later)
    renderUsersList();
}

async function bootstrap() {
    // Load settings persisted
    // (already loaded into state.settings above)

    if (!state.token) {
        showModal("loginModal");
        return;
    }

    try {
        await bootstrapAfterAuth();
    } catch (e) {
        console.error("bootstrap failed:", e);
        showModal("loginModal");
    }
}

bootstrap();

/* Expose required functions globally (onclick in HTML relies on these) */
window.doLogin = doLogin;
window.logout = logout;

window.googleConnectOrManage = googleConnectOrManage;

window.fetchNow = fetchNow;
window.clearDateFilter = clearDateFilter;
window.startAutopilot = startAutopilot;
window.stopAutopilot = stopAutopilot;

window.setTab = setTab;

window.openThreadModal = openThreadModal;
window.closeThreadModal = closeThreadModal;

window.openAckModal = openAckModal;
window.closeAckModal = closeAckModal;
window.sendAckFromModal = sendAckFromModal;

window.openSettings = openSettings;
window.closeSettings = closeSettings;
window.applySettingsFromModal = applySettingsFromModal;
window.flushDatabase = flushDatabase;

window.openUsersModal = openUsersModal;
window.closeUsersModal = closeUsersModal;
window.createUserFromForm = createUserFromForm;

window.updateTicketStatus = updateTicketStatus;
window.updateTicketCategory = updateTicketCategory;
window.updateTicketAssignee = updateTicketAssignee;

window.addQuery = addQuery;