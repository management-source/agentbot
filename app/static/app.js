/* app/static/app.js
 * Clean, responsive UI + functional KPIs + no runtime undefined functions.
 * Update API paths below if your backend differs.
 */

const API = {
    login: "/user-auth/login",
    me: "/user-auth/me",
    users: "/user-auth/users",

    // tickets
    listTickets: "/tickets",
    setStatus: (threadId) => `/tickets/${encodeURIComponent(threadId)}/status`,
    setCategory: (threadId) => `/tickets/${encodeURIComponent(threadId)}/category`,
    setAssign: (threadId) => `/tickets/${encodeURIComponent(threadId)}/assign`,

    // autopilot
    autopilotStatus: "/autopilot/status",
    autopilotFetch: "/autopilot/fetch",
    autopilotStart: "/autopilot/start",
    autopilotStop: "/autopilot/stop",

    // threads (viewer)
    thread: (threadId) => `/threads/${encodeURIComponent(threadId)}`,
};

const state = {
    token: localStorage.getItem("agentbot_token") || null,
    me: null,
    users: [],
    tickets: [],
    filter: {
        status: "all",
        search: "",
        assignee: "",
        category: "",
    },
    autopilot: null,
};

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

function setStatusLine(text) {
    $("statusLine").textContent = text || "—";
}

function show(el, yes) {
    el.style.display = yes ? "" : "none";
}

function openModal(backdropEl) { backdropEl.classList.add("show"); }
function closeModal(backdropEl) { backdropEl.classList.remove("show"); }

function formatDateTime(iso) {
    if (!iso) return "—";
    try {
        const d = new Date(iso);
        return d.toLocaleString();
    } catch { return iso; }
}

function isOverdue(ticket) {
    if (!ticket?.sla_due_at) return false;
    try {
        return new Date(ticket.sla_due_at).getTime() < Date.now();
    } catch { return false; }
}

async function apiFetch(path, opts = {}) {
    const headers = new Headers(opts.headers || {});
    headers.set("Content-Type", "application/json");

    if (state.token) headers.set("Authorization", `Bearer ${state.token}`);

    const resp = await fetch(path, { ...opts, headers });

    if (resp.status === 401) {
        // Token invalid/expired
        state.token = null;
        localStorage.removeItem("agentbot_token");
        state.me = null;
        updateAuthUI();
        openModal($("loginBackdrop"));
        throw new Error("Unauthorized");
    }

    const ct = resp.headers.get("content-type") || "";
    const isJson = ct.includes("application/json");
    const data = isJson ? await resp.json().catch(() => null) : await resp.text();

    if (!resp.ok) {
        const msg = (data && data.detail) ? data.detail : `HTTP ${resp.status}`;
        throw new Error(msg);
    }
    return data;
}

function updateAuthUI() {
    const dot = $("authDot");
    const txt = $("authText");
    const btnLogout = $("btnLogout");
    const btnManageUsers = $("btnManageUsers");
    const mailboxLabel = $("mailboxLabel");

    if (state.me) {
        dot.className = "dot green";
        txt.textContent = `Signed in as: ${state.me.full_name || state.me.email} (${state.me.role || "USER"})`;
        show(btnLogout, true);

        const isAdmin = (state.me.role || "").toUpperCase() === "ADMIN";
        show(btnManageUsers, isAdmin);

        mailboxLabel.textContent = state.me.email || "me";
    } else {
        dot.className = "dot red";
        txt.textContent = "Not signed in";
        show(btnLogout, false);
        show(btnManageUsers, false);
        mailboxLabel.textContent = "—";
    }
}

function computeKpis(tickets) {
    const k = {
        notRepliedPriority: 0,
        pending: 0,
        inProgress: 0,
        responded: 0,
        replyNotNeeded: 0,
    };

    for (const t of tickets || []) {
        const st = (t.status || "").toLowerCase();

        if (st === "pending") k.pending++;
        else if (st === "in_progress") k.inProgress++;
        else if (st === "responded") k.responded++;
        else if (st === "reply_not_needed") k.replyNotNeeded++;

        const pri = (t.priority || "").toLowerCase();
        if (pri === "priority" && st !== "responded" && st !== "reply_not_needed") {
            k.notRepliedPriority++;
        }
    }

    return k;
}

function renderKpisFrom(tickets) {
    const k = computeKpis(tickets || []);
    $("kpiNotRepliedPriority").textContent = String(k.notRepliedPriority);
    $("kpiPending").textContent = String(k.pending);
    $("kpiInProgress").textContent = String(k.inProgress);
    $("kpiResponded").textContent = String(k.responded);
    $("kpiReplyNotNeeded").textContent = String(k.replyNotNeeded);
}

function applyFilters(tickets) {
    const f = state.filter;
    let out = [...(tickets || [])];

    if (f.status && f.status !== "all") {
        out = out.filter(t => (t.status || "").toLowerCase() === f.status);
    }

    if (f.assignee) {
        out = out.filter(t => String(t.assignee_user_id || "") === String(f.assignee));
    }

    if (f.category) {
        out = out.filter(t => String(t.category || "") === String(f.category));
    }

    if (f.search) {
        const q = f.search.toLowerCase();
        out = out.filter(t => {
            const subj = (t.subject || "").toLowerCase();
            const from = (t.from_name || "").toLowerCase();
            const addr = (t.from_email || "").toLowerCase();
            const snip = (t.snippet || "").toLowerCase();
            return subj.includes(q) || from.includes(q) || addr.includes(q) || snip.includes(q);
        });
    }

    return out;
}

function badge(html, extraClass = "") {
    return `<span class="badge ${extraClass}">${html}</span>`;
}

function userNameById(id) {
    const u = state.users.find(x => String(x.id) === String(id));
    return u ? (u.full_name || u.email) : "Unassigned";
}

function renderTickets() {
    const list = $("ticketList");
    const filtered = applyFilters(state.tickets);

    // KPIs must reflect current list displayed (your complaint)
    renderKpisFrom(filtered);

    if (!filtered.length) {
        list.innerHTML = `
      <div class="card" style="margin-top:12px">
        <div style="font-weight:800">No tickets found</div>
        <div class="small muted" style="margin-top:6px">Try adjusting filters or click “Fetch Now”.</div>
      </div>
    `;
        return;
    }

    list.innerHTML = filtered.map(t => {
        const pri = (t.priority || "").toLowerCase() === "priority";
        const unread = !!t.is_unread;
        const overdue = isOverdue(t);

        const assigneeText = t.assignee_user_id ? escapeHtml(userNameById(t.assignee_user_id)) : "Unassigned";
        const catText = escapeHtml(t.category || "GENERAL");

        const badges = [
            pri ? badge("Priority", "priority") : "",
            unread ? badge("Unread", "unread") : "",
            overdue ? badge("Overdue", "overdue") : "",
            badge(`Category: ${catText}`),
            badge(`Assignee: ${assigneeText}`),
            badge(`Status: ${escapeHtml(t.status || "pending")}`),
        ].filter(Boolean).join("");

        return `
      <div class="ticket" data-thread-id="${escapeHtml(t.thread_id)}">
        <div>
          <h4>${escapeHtml(t.subject || "(no subject)")}</h4>
          <div class="from">${escapeHtml(t.from_name || "")} ${t.from_email ? `&lt;${escapeHtml(t.from_email)}&gt;` : ""}</div>
          <div class="snippet">${escapeHtml(t.snippet || "")}</div>
          <div class="badge-row">${badges}</div>

          <div class="ticket-meta" style="margin-top:10px">
            Last: ${escapeHtml(formatDateTime(t.last_message_at))} &nbsp; • &nbsp;
            SLA Due: ${escapeHtml(formatDateTime(t.sla_due_at))} &nbsp; • &nbsp;
            Thread: ${escapeHtml(t.thread_id)}
          </div>
        </div>

        <div class="ticket-right">
          <div class="ticket-controls">
            <div class="field">
              <div class="label">Status</div>
              <select data-action="status">
                <option value="pending" ${t.status === "pending" ? "selected" : ""}>Pending</option>
                <option value="in_progress" ${t.status === "in_progress" ? "selected" : ""}>In Progress</option>
                <option value="responded" ${t.status === "responded" ? "selected" : ""}>Responded</option>
                <option value="reply_not_needed" ${t.status === "reply_not_needed" ? "selected" : ""}>Reply Not Needed</option>
              </select>
            </div>

            <div class="field">
              <div class="label">Category</div>
              <select data-action="category">
                ${["MAINTENANCE", "RENT_ARREARS", "LEASING", "COMPLIANCE", "SALES", "GENERAL"].map(c =>
            `<option value="${c}" ${String(t.category || "GENERAL") === c ? "selected" : ""}>${c}</option>`
        ).join("")}
              </select>
            </div>

            <div class="field">
              <div class="label">Assignee</div>
              <select data-action="assign">
                <option value="">Unassigned</option>
                ${state.users.map(u => {
            const sel = String(t.assignee_user_id || "") === String(u.id) ? "selected" : "";
            return `<option value="${escapeHtml(u.id)}" ${sel}>${escapeHtml(u.full_name || u.email)}</option>`;
        }).join("")}
              </select>
            </div>
          </div>

          <div class="ticket-actions">
            <button class="btn" data-action="open">Open</button>
            <button class="btn primary" data-action="quickReply">Quick Reply</button>
          </div>
        </div>
      </div>
    `;
    }).join("");
}

async function loadMe() {
    state.me = await apiFetch(API.me, { method: "GET" });
    updateAuthUI();
}

async function loadUsers() {
    state.users = await apiFetch(API.users, { method: "GET" });
    // also populate filter dropdown
    const sel = $("assigneeFilter");
    const current = sel.value;
    sel.innerHTML = `<option value="">All</option>` + state.users.map(u =>
        `<option value="${escapeHtml(u.id)}">${escapeHtml(u.full_name || u.email)}</option>`
    ).join("");
    sel.value = current || "";
}

async function loadAutopilotStatus() {
    try {
        const s = await apiFetch(API.autopilotStatus, { method: "GET" });
        state.autopilot = s;
        const running = !!s?.running;
        const lastSync = s?.last_sync_at ? formatDateTime(s.last_sync_at) : "—";
        $("autopilotInfo").textContent = `${running ? "Running" : "Stopped"} • Checking every ${s?.interval_minutes || 5} minutes • Last sync: ${lastSync}`;
        show($("googlePill"), !!s?.google_connected);
    } catch (e) {
        $("autopilotInfo").textContent = "Status unavailable";
    }
}

async function loadTickets() {
    // IMPORTANT: always reset visible state before loading
    state.tickets = [];
    renderKpisFrom([]);    // hard reset KPIs
    $("ticketList").innerHTML = `
    <div class="card" style="margin-top:12px">
      <div style="font-weight:800">Loading…</div>
      <div class="small muted" style="margin-top:6px">Please wait.</div>
    </div>
  `;

    const data = await apiFetch(API.listTickets, { method: "GET" });
    state.tickets = Array.isArray(data) ? data : (data?.items || []);
    renderTickets();
}

async function doFetchNow() {
    // build query params
    const params = new URLSearchParams();
    const from = $("fromDate").value;
    const to = $("toDate").value;
    const limit = $("limit").value;

    if (from) params.set("from", from);
    if (to) params.set("to", to);
    if (limit) params.set("limit", String(limit));
    params.set("incremental", $("incremental").checked ? "1" : "0");
    params.set("all_mail", $("allMail").checked ? "1" : "0");

    setStatusLine("Fetching emails…");
    try {
        await apiFetch(`${API.autopilotFetch}?${params.toString()}`, { method: "POST" });
        setStatusLine("Fetch complete");
        await loadTickets();
    } catch (err) {
        console.error("Fetch Now failed:", err);
        setStatusLine("Fetch failed");
        alert(`Fetch failed: ${err?.message || err}`);
        // ensure KPIs don't show stale counts
        renderKpisFrom([]);
    }
}

async function doStart() {
    setStatusLine("Starting autopilot…");
    try {
        await apiFetch(API.autopilotStart, { method: "POST" });
        setStatusLine("Autopilot started");
        await loadAutopilotStatus();
    } catch (e) {
        setStatusLine("Start failed");
        alert(`Start failed: ${e?.message || e}`);
    }
}

async function doStop() {
    setStatusLine("Stopping autopilot…");
    try {
        await apiFetch(API.autopilotStop, { method: "POST" });
        setStatusLine("Autopilot stopped");
        await loadAutopilotStatus();
    } catch (e) {
        setStatusLine("Stop failed");
        alert(`Stop failed: ${e?.message || e}`);
    }
}

async function login(email, password) {
    $("loginError").style.display = "none";
    const payload = { email, password };
    const data = await apiFetch(API.login, {
        method: "POST",
        body: JSON.stringify(payload),
        headers: {},
    });

    // expected: { access_token: "..." }
    const token = data?.access_token;
    if (!token) throw new Error("Login failed (no token returned)");

    state.token = token;
    localStorage.setItem("agentbot_token", token);

    await loadMe();
    await loadUsers();
    await loadAutopilotStatus();
    await loadTickets();

    closeModal($("loginBackdrop"));
}

function logout() {
    state.token = null;
    localStorage.removeItem("agentbot_token");
    state.me = null;
    updateAuthUI();
    openModal($("loginBackdrop"));
}

/* Ticket actions */
async function updateTicketStatus(threadId, status) {
    await apiFetch(API.setStatus(threadId), { method: "PATCH", body: JSON.stringify({ status }) });
    // update local state to avoid full reload
    const t = state.tickets.find(x => x.thread_id === threadId);
    if (t) t.status = status;
    renderTickets();
}

async function updateTicketCategory(threadId, category) {
    await apiFetch(API.setCategory(threadId), { method: "PATCH", body: JSON.stringify({ category }) });
    const t = state.tickets.find(x => x.thread_id === threadId);
    if (t) t.category = category;
    renderTickets();
}

async function updateTicketAssignee(threadId, assignee_user_id) {
    await apiFetch(API.setAssign(threadId), { method: "PATCH", body: JSON.stringify({ assignee_user_id: assignee_user_id || null }) });
    const t = state.tickets.find(x => x.thread_id === threadId);
    if (t) t.assignee_user_id = assignee_user_id || null;
    renderTickets();
}

async function openThreadViewer(threadId) {
    $("viewerTitle").textContent = `Thread ${threadId}`;
    openModal($("viewerBackdrop"));

    // expect backend returns something like { messages: [...], html: "..."} or structured
    // We'll use: /threads/{id} should return "messages" with "body_html_sanitized" or similar
    const data = await apiFetch(API.thread(threadId), { method: "GET" });

    // Choose best html to show:
    // - If backend returns a prebuilt "thread_html" use it
    // - Else show the latest message body_html
    let html = data?.thread_html || data?.html || "";

    if (!html && Array.isArray(data?.messages) && data.messages.length) {
        const last = data.messages[data.messages.length - 1];
        html = last.body_html_sanitized || last.body_html || last.body || "";
    }

    // Fallback
    if (!html) {
        html = `<div style="font-family:Arial;padding:16px">No HTML available for this thread.</div>`;
    }

    // IMPORTANT: escapeHtml is for plain text, not HTML. We expect sanitized HTML from backend.
    // Put into iframe srcdoc.
    const frame = $("viewerFrame");
    frame.srcdoc = html;
}

/* Users modal */
function renderUsersTable() {
    const wrap = $("usersTable");
    if (!state.users.length) {
        wrap.innerHTML = `<div class="small muted">No users</div>`;
        return;
    }

    const rows = state.users.map(u => {
        return `
      <div class="card" style="margin-top:10px; padding:12px">
        <div class="row space">
          <div>
            <div style="font-weight:900">${escapeHtml(u.full_name || "—")}</div>
            <div class="small muted">${escapeHtml(u.email)}</div>
          </div>
          <div class="row">
            <span class="badge">Role: ${escapeHtml(u.role || "USER")}</span>
            <span class="badge">${u.is_active === false ? "Inactive" : "Active"}</span>
          </div>
        </div>
      </div>
    `;
    }).join("");

    wrap.innerHTML = rows;
}

async function openUsersModal() {
    $("usersError").style.display = "none";
    await loadUsers();
    renderUsersTable();
    openModal($("usersBackdrop"));
}

async function createUserFromModal() {
    const email = $("newUserEmail").value.trim();
    const full_name = $("newUserName").value.trim();
    const role = $("newUserRole").value;
    const password = $("newUserPassword").value;

    $("usersError").style.display = "none";

    try {
        await apiFetch(API.users, {
            method: "POST",
            body: JSON.stringify({ email, full_name, role, password }),
        });
        $("newUserEmail").value = "";
        $("newUserName").value = "";
        $("newUserPassword").value = "";
        await loadUsers();
        renderUsersTable();
    } catch (e) {
        $("usersError").textContent = `Create failed: ${e?.message || e}`;
        $("usersError").style.display = "";
    }
}

/* Wiring */
function wireEvents() {
    // Login modal
    $("btnLogin").addEventListener("click", async () => {
        try {
            await login($("loginEmail").value.trim(), $("loginPassword").value);
        } catch (e) {
            $("loginError").textContent = e?.message || String(e);
            $("loginError").style.display = "";
        }
    });
    $("btnLoginClose").addEventListener("click", () => closeModal($("loginBackdrop")));

    // Logout
    $("btnLogout").addEventListener("click", logout);

    // Autopilot actions
    $("btnFetch").addEventListener("click", doFetchNow);
    $("btnStart").addEventListener("click", doStart);
    $("btnStop").addEventListener("click", doStop);
    $("btnClearFilter").addEventListener("click", () => {
        $("fromDate").value = "";
        $("toDate").value = "";
        $("limit").value = "500";
        $("incremental").checked = true;
        $("allMail").checked = false;

        // filters
        state.filter = { status: "all", search: "", assignee: "", category: "" };
        $("searchBox").value = "";
        $("assigneeFilter").value = "";
        $("categoryFilter").value = "";

        // status seg UI
        [...$("statusSeg").querySelectorAll("button")].forEach(b => b.classList.remove("active"));
        $("statusSeg").querySelector('button[data-status="all"]').classList.add("active");

        renderTickets();
    });

    // Filters
    $("searchBox").addEventListener("input", (e) => {
        state.filter.search = e.target.value || "";
        renderTickets();
    });

    $("assigneeFilter").addEventListener("change", (e) => {
        state.filter.assignee = e.target.value || "";
        renderTickets();
    });

    $("categoryFilter").addEventListener("change", (e) => {
        state.filter.category = e.target.value || "";
        renderTickets();
    });

    $("statusSeg").addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-status]");
        if (!btn) return;
        [...$("statusSeg").querySelectorAll("button")].forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        state.filter.status = btn.getAttribute("data-status");
        renderTickets();
    });

    // Ticket list delegated events
    $("ticketList").addEventListener("change", async (e) => {
        const sel = e.target.closest("select[data-action]");
        if (!sel) return;
        const ticketEl = e.target.closest(".ticket");
        if (!ticketEl) return;
        const threadId = ticketEl.getAttribute("data-thread-id");

        const action = sel.getAttribute("data-action");
        const val = sel.value;

        try {
            if (action === "status") await updateTicketStatus(threadId, val);
            if (action === "category") await updateTicketCategory(threadId, val);
            if (action === "assign") await updateTicketAssignee(threadId, val);
        } catch (err) {
            alert(`${action} update failed: ${err?.message || err}`);
            // revert by re-rendering from state (state wasn’t updated on failure)
            renderTickets();
        }
    });

    $("ticketList").addEventListener("click", async (e) => {
        const btn = e.target.closest("button[data-action]");
        if (!btn) return;
        const ticketEl = e.target.closest(".ticket");
        if (!ticketEl) return;
        const threadId = ticketEl.getAttribute("data-thread-id");
        const action = btn.getAttribute("data-action");

        if (action === "open") {
            try { await openThreadViewer(threadId); }
            catch (err) { alert(`Open failed: ${err?.message || err}`); }
        }

        if (action === "quickReply") {
            alert("Quick Reply UI not included in this file. Wire your existing Quick Reply modal here.");
        }
    });

    // Users modal
    $("btnManageUsers").addEventListener("click", openUsersModal);
    $("btnUsersClose").addEventListener("click", () => closeModal($("usersBackdrop")));
    $("btnUsersRefresh").addEventListener("click", async () => {
        await loadUsers();
        renderUsersTable();
    });
    $("btnCreateUser").addEventListener("click", createUserFromModal);

    // Viewer
    $("btnViewerClose").addEventListener("click", () => closeModal($("viewerBackdrop")));

    // Placeholder buttons
    $("btnSettings").addEventListener("click", () => alert("Settings UI not included in this file. Wire your existing settings modal here."));
    $("btnAddQuery").addEventListener("click", () => alert("Add Query UI not included in this file. Wire your existing query modal here."));
}

async function bootstrap() {
    wireEvents();
    updateAuthUI();
    renderKpisFrom([]); // always start at 0

    if (!state.token) {
        openModal($("loginBackdrop"));
        return;
    }

    try {
        await loadMe();
        await loadUsers();
        await loadAutopilotStatus();
        await loadTickets();
    } catch (e) {
        console.error("Bootstrap error:", e);
        openModal($("loginBackdrop"));
    }
}

bootstrap();