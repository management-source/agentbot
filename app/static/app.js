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

function escapeHtml(text) {
    if (typeof text !== "string") return "";
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
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
            btn.className = "btn";
            btn.disabled = true;

            const pill = document.getElementById("googlePill");
            if (pill) pill.style.display = "none";
            return;
        }

        const j = await r.json();
        googleConnected = !!j.connected;

        const target = (j.target_mailbox || j.delegated_mailbox || "me");

        const mb = document.getElementById("mailboxBadge");
        if (mb) mb.textContent = googleConnected ? (`Mailbox: ${target}`) : "";

        const mb2 = document.getElementById("mailboxLabel");
        if (mb2) mb2.textContent = googleConnected ? target : "—";

        const pill = document.getElementById("googlePill");
        if (pill) pill.style.display = googleConnected ? "inline-flex" : "none";

        if (googleConnected) {
            btn.textContent = "Google Connected";
            // Tailwind page expects tailwind classes, Good UI expects .btn
            if (btn.className.includes("px-")) {
                btn.className = "px-4 py-2 rounded-lg border bg-emerald-50 text-emerald-800 hover:bg-emerald-100";
            } else {
                btn.className = "btn";
            }
        } else {
            btn.textContent = "Connect to Google";
            if (btn.className.includes("px-")) {
                btn.className = "px-4 py-2 rounded-lg border text-slate-700 hover:bg-slate-50";
            } else {
                btn.className = "btn";
            }
        }
    } catch {
        // If status check fails, keep button usable for login.
        googleConnected = false;
        btn.textContent = "Connect to Google";
        if (btn.className.includes("px-")) {
            btn.className = "px-4 py-2 rounded-lg border text-slate-700 hover:bg-slate-50";
        } else {
            btn.className = "btn";
        }
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
                ${["ADMIN", "PM", "LEASING", "SALES", "ACCOUNTS", "READONLY"].map(r => `<option value="${r}" ${r === u.role ? "selected" : ""}>${r}</option>`).join("")}
              </select>
              <label class="text-sm text-slate-600 flex items-center gap-1">
                <input type="checkbox" ${u.is_active ? "checked" : ""} data-user-active="${u.id}" />
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
        headers: { "Content-Type": "application/json" },
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
        headers: { "Content-Type": "application/json" },
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

    // Tailwind tabs (legacy)
    document.querySelectorAll(".tabbtn").forEach(btn => {
        const isActive = (btn.dataset.tab === tab);
        btn.className = isActive
            ? "tabbtn px-4 py-2 rounded-lg border bg-indigo-600 text-white"
            : "tabbtn px-4 py-2 rounded-lg border bg-white";
    });

    // Segmented control (Good UI)
    const seg = document.getElementById("statusSeg");
    if (seg) {
        seg.querySelectorAll("button[data-tab], button[data-status]").forEach(btn => {
            const key = btn.dataset.tab || btn.dataset.status || "";
            const isActive = (key === tab);
            if (isActive) btn.classList.add("active");
            else btn.classList.remove("active");
        });
    }

    loadTickets();
}

async function fetchNow() {
    const btn = document.getElementById("fetchBtn") || document.getElementById("btnFetch");
    if (btn) {
        btn.disabled = true;
        btn.textContent = "Fetching...";
    }

    try {
        const startEl = document.getElementById("startDate") || document.getElementById("fromDate");
        const endEl = document.getElementById("endDate") || document.getElementById("toDate");
        const maxEl = document.getElementById("maxThreads") || document.getElementById("limit");

        const incEl = document.getElementById("incrementalSync") || document.getElementById("incremental");
        const allEl = document.getElementById("includeAnywhere") || document.getElementById("allMail");

        const start = startEl ? (startEl.value || "") : "";
        const end = endEl ? (endEl.value || "") : "";
        const maxThreads = parseInt((maxEl && maxEl.value) ? maxEl.value : "500", 10);
        const incremental = !!(incEl && incEl.checked);
        const includeAnywhere = !!(allEl && allEl.checked);

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
            const mb1 = document.getElementById("mailboxBadge");
            const mb2 = document.getElementById("mailboxLabel");
            if (mb1) mb1.textContent = `Mailbox: ${j.target_mailbox}`;
            if (mb2) mb2.textContent = j.target_mailbox;
        }

        const last1 = document.getElementById("lastSync");
        if (last1) last1.textContent = new Date().toLocaleString();

        await loadTickets();
        console.log(j);
    } catch (e) {
        alert("Fetch failed: " + e);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "Fetch Now";
        }
    }
}

function clearDateFilter() {
    const s1 = document.getElementById("startDate") || document.getElementById("fromDate");
    const e1 = document.getElementById("endDate") || document.getElementById("toDate");
    if (s1) s1.value = "";
    if (e1) e1.value = "";
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

        const s1 = document.getElementById("autopilotStatus");
        if (s1) s1.textContent = running ? "Active" : "Stopped";

        const dot = document.getElementById("statusDot");
        if (dot) {
            // Tailwind variant
            dot.className = running ? "h-2 w-2 rounded-full bg-green-500" : "h-2 w-2 rounded-full bg-red-500";
        }
        const dot2 = document.getElementById("autopilotDot");
        if (dot2) {
            dot2.classList.toggle("green", running);
            dot2.classList.toggle("red", !running);
        }

        const next = document.getElementById("nextRun");
        if (next) next.textContent = j.next_run_time || "—";

        const line = document.getElementById("statusLine");
        if (line) line.textContent = running ? "Active" : "Stopped";

        const info = document.getElementById("autopilotInfo");
        if (info) {
            const every = (j.poll_every_minutes || j.poll_every || 5);
            info.textContent = `Checking every ${every} minutes • Last sync: ${(document.getElementById("lastSync")?.textContent || "—")} • Next run: ${j.next_run_time || "—"}`;
            const pe = document.getElementById("pollEvery");
            if (pe) pe.textContent = String(every);
        }
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
    const cats = ["MAINTENANCE", "RENT_ARREARS", "LEASING", "COMPLIANCE", "SALES", "GENERAL"];
    return cats.map(c => `<option value="${c}" ${c === (selected || "GENERAL") ? "selected" : ""}>${c.replace("_", " ")}</option>`).join("");
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
    const useGoodUi = !!document.querySelector(".page") && !document.querySelector(".tabbtn");

    const due = t.due_at ? `Due: ${formatDate(t.due_at)}` : "Due: —";
    const last = t.last_message_at ? `Last: ${formatDate(t.last_message_at)}` : "Last: —";

    const cat = (t.category || "GENERAL").replace("_", " ");
    const assignee = t.assignee_user_id ? (usersCache.find(u => u.id === t.assignee_user_id)?.name || `User#${t.assignee_user_id}`) : "Unassigned";

    let slaText = "SLA: —";
    let slaOverdue = false;
    if (t.sla_due_at) {
        const dueMs = Date.parse(t.sla_due_at);
        const nowMs = Date.now();
        slaOverdue = nowMs > dueMs;
        slaText = slaOverdue ? `SLA overdue: ${formatDate(t.sla_due_at)}` : `SLA due: ${formatDate(t.sla_due_at)}`;
    }

    if (useGoodUi) {
        const card = document.createElement("div");
        card.className = "ticket";

        const priority = (t.priority || "medium").toLowerCase();
        const priBadge = priority === "high"
            ? `<span class="badge priority">High</span>`
            : (priority === "low" ? `<span class="badge">Low</span>` : `<span class="badge">Medium</span>`);

        const unreadBadge = t.is_unread ? `<span class="badge unread">Unread</span>` : "";
        const nrBadge = t.is_not_replied ? `<span class="badge priority">Not Replied</span>` : "";
        const slaBadge = slaOverdue ? `<span class="badge overdue">Overdue</span>` : "";

        card.innerHTML = `
          <div>
            <h4>${escapeHtml(t.subject || "(no subject)")}</h4>
            <div class="from">${escapeHtml(t.from_name || t.from_email || "(unknown sender)")} • ${escapeHtml(t.from_email || "")}</div>
            <div class="snippet">${escapeHtml(t.snippet || "")}</div>

            <div class="badge-row">
              ${priBadge}
              <span class="badge">${escapeHtml(cat)}</span>
              <span class="badge">${escapeHtml(assignee)}</span>
              ${nrBadge}
              ${unreadBadge}
              ${slaBadge}
            </div>

            <div class="ticket-meta" style="margin-top:10px">
              <div>${escapeHtml(last)}</div>
              <div>${escapeHtml(due)}</div>
              <div>${escapeHtml(slaText)}</div>
            </div>
          </div>

          <div class="ticket-right">
            <div class="ticket-actions">
              <button class="btn" onclick="openThread('${t.thread_id}')">Open</button>
              <button class="btn" onclick="openAckModal('${t.thread_id}')">Quick Reply</button>
            </div>

            <div class="ticket-controls">
              <div class="field">
                <div class="label">Status</div>
                <select onchange="updateStatus('${t.thread_id}', this.value)">
                  ${statusOptions(t.status)}
                </select>
              </div>

              <div class="field">
                <div class="label">Assignee</div>
                <select onchange="updateAssignee('${t.thread_id}', this.value)">
                  ${assigneeOptions(t.assignee_user_id)}
                </select>
              </div>

              <div class="field">
                <div class="label">Category</div>
                <select onchange="updateCategory('${t.thread_id}', this.value)">
                  ${categoryOptions(t.category)}
                </select>
              </div>

              ${t.from_email ? `<button class="btn danger" onclick="blacklistSender('${t.from_email}')">Blacklist Sender</button>` : ``}
            </div>
          </div>
        `;
        return card;
    }

    // Tailwind card (legacy)
    const card = document.createElement("div");
    card.className = settings.compactTickets
        ? "bg-white rounded-xl shadow border p-4 flex items-start justify-between gap-4"
        : "bg-white rounded-xl shadow border p-5 flex items-start justify-between gap-4";

    const catBadge = `<span class="px-2 py-0.5 rounded-full text-xs bg-indigo-50 text-indigo-800 border">${cat}</span>`;
    const assigneeBadge = `<span class="px-2 py-0.5 rounded-full text-xs bg-slate-50 text-slate-700 border">${assignee}</span>`;

    let slaClass = "text-slate-500";
    if (t.sla_due_at) {
        slaClass = slaOverdue ? "text-red-700" : "text-emerald-700";
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

    const items = Array.isArray(data.items) ? data.items : [];
    // If there are no items returned, force KPIs to zero to avoid displaying stale counts.
    if (items.length === 0) {
        data.counts = {
            not_replied: 0,
            pending: 0,
            in_progress: 0,
            responded: 0,
            no_reply_needed: 0,
        };
    }

    const c = data.counts || {};

    // Legacy KPI tiles (tailwind)
    const map = [
        ["countNotReplied", c.not_replied ?? 0],
        ["countPending", c.pending ?? 0],
        ["countInProgress", c.in_progress ?? 0],
        ["countResponded", c.responded ?? 0],
        ["countNoReplyNeeded", c.no_reply_needed ?? 0],
    ];
    for (const [id, val] of map) {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    }

    // Good UI KPIs
    const map2 = [
        ["kpiNotRepliedPriority", c.not_replied ?? 0],
        ["kpiPending", c.pending ?? 0],
        ["kpiInProgress", c.in_progress ?? 0],
        ["kpiResponded", c.responded ?? 0],
        ["kpiReplyNotNeeded", c.no_reply_needed ?? 0],
    ];
    for (const [id, val] of map2) {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    }

    const list = document.getElementById("ticketList");
    if (!list) return;
    list.innerHTML = "";

    items.forEach(t => list.appendChild(renderTicket(t)));

    if (items.length === 0) {
        list.innerHTML = `<div class="muted small" style="padding:10px">No tickets in this tab.</div>`;
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

    const viewerBackdrop = document.getElementById("viewerBackdrop");
    const viewerFrame = document.getElementById("viewerFrame");
    const viewerTitle = document.getElementById("viewerTitle");

    const useViewer = (!modal || !content) && viewerBackdrop && viewerFrame;

    if (useViewer) {
        viewerBackdrop.classList.add("show");
        if (viewerTitle) viewerTitle.textContent = "Thread";
        viewerFrame.srcdoc = `<div style="font-family:system-ui; padding:16px; color:#334155">Loading thread…</div>`;
    } else if (modal && content) {
        modal.classList.remove("hidden");
        content.innerHTML = `<div class="text-sm text-slate-600">Loading thread…</div>`;
    } else {
        alert("Thread viewer UI is missing from the page (threadModal/threadContent).");
        return;
    }

    const r = await apiFetch(`/threads/${threadId}`);
    const t = await r.text();
    if (!r.ok) {
        if (useViewer) viewerFrame.srcdoc = `<pre style="white-space:pre-wrap; color:#b91c1c; padding:16px">${escapeHtml(t)}</pre>`;
        else content.innerHTML = `<pre class="text-xs text-red-700 whitespace-pre-wrap">${t}</pre>`;
        return;
    }

    const j = JSON.parse(t);
    if (gmailLink) gmailLink.href = j.gmail_url || j.gmail_thread_url || "#";

    const escapeHtmlLocal = (s) => (s || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");

    const rewriteCid = (html, messageId) => {
        if (!html) return "";
        return html.replace(/src\s*=\s*(["'])cid:([^"'>\s]+)\1/gi, (m, q, cid) => {
            const url = `/threads/${encodeURIComponent(threadId)}/messages/${encodeURIComponent(messageId)}/inline/${encodeURIComponent(cid)}`;
            return `src=${q}${url}${q}`;
        });
    };

    const rewriteRemoteImagesToProxy = (html) => {
        if (!html) return "";
        return html.replace(/(<img\b[^>]*\bsrc\s*=\s*)(["'])(https?:\/\/[^"'>\s]+)\2/gi, (m, pre, q, url) => {
            const proxied = `${window.location.origin}/threads/proxy-image?url=${encodeURIComponent(url)}`;
            return `${pre}${q}${proxied}${q}`;
        });
    };

    const attachmentBadge = (a, threadIdArg, messageIdArg) => {
        const name = a.filename || "attachment";
        const mime = (a.mime_type || "").toLowerCase();
        let label = "FILE";
        if (mime.startsWith("image/")) label = "IMAGE";
        else if (mime == "application/pdf") label = "PDF";
        else if (mime.startsWith("text/")) label = "TEXT";
        else if (mime.startsWith("application/vnd")) label = "DOC";
        const url = `/threads/${encodeURIComponent(threadIdArg)}/messages/${encodeURIComponent(messageIdArg || "")}/attachments/${encodeURIComponent(a.attachment_id)}?filename=${encodeURIComponent(name)}`;
        return `<a style="display:inline-flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid #e5e7eb;border-radius:999px;text-decoration:none;color:#334155;background:#fff" href="${url}" target="_blank" rel="noreferrer">
          <span style="font-size:12px;padding:2px 8px;border-radius:999px;background:#f1f5f9;color:#475569;border:1px solid #e5e7eb">${label}</span>
          <span style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtmlLocal(name)}</span>
        </a>`;
    };

    const renderMessage = (m, idx) => {
        const hasHtml = !!m.body_html;
        const msgId = m.id;
        const safeText = escapeHtmlLocal(m.body_text || m.snippet || "");
        const iframeId = `msg_iframe_${idx}`;
        const btnId = `msg_toggle_${idx}`;

        let html = hasHtml ? rewriteCid(m.body_html, msgId) : "";
        if (hasHtml && settings.proxyRemoteImages) {
            html = rewriteRemoteImagesToProxy(html);
        }

        const atts = (m.attachments || []).map(a => ({ ...a, message_id: msgId })).filter(a => !a.is_inline);
        const attachmentsHtml = atts.length ? `<div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:8px">${atts.map(a => attachmentBadge(a, threadId, msgId)).join("")}</div>` : "";

        return `
        <div style="border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#f8fafc;margin-top:12px">
          <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:10px">
            <div>
              <div style="font-size:12px;color:#64748b">${escapeHtmlLocal(m.date || "")}</div>
              <div style="font-size:13px;color:#0f172a;margin-top:2px"><b>From:</b> ${escapeHtmlLocal(m.from || "")}</div>
              <div style="font-size:13px;color:#0f172a"><b>To:</b> ${escapeHtmlLocal(m.to || "")}</div>
              <div style="font-size:13px;color:#0f172a"><b>Subject:</b> ${escapeHtmlLocal(m.subject || "")}</div>
              ${attachmentsHtml}
            </div>

            ${hasHtml ? `
              <button id="${btnId}" class="btn" data-mode="html">View HTML</button>
            ` : ``}
          </div>

          <div style="margin-top:12px">
            <div style="font-size:13px;color:#334155;white-space:pre-wrap" data-mode="text">${safeText}</div>
            ${hasHtml ? `
              <div style="margin-top:12px;display:none" data-mode="html">
                <iframe id="${iframeId}" style="width:100%;height:520px;border:1px solid #e5e7eb;border-radius:12px;background:#fff"
                  sandbox="allow-popups allow-forms allow-same-origin" referrerpolicy="no-referrer"></iframe>
              </div>
            ` : ``}
          </div>
        </div>
      `;
    };

    const threadHtml = `
      <div style="font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; padding:16px; color:#0f172a">
        <div style="font-weight:800;font-size:14px">Thread</div>
        ${(j.messages || []).map((m, idx) => renderMessage(m, idx)).join("")}
      </div>
    `;

    if (useViewer) {
        viewerFrame.srcdoc = threadHtml;
    } else {
        content.innerHTML = (j.messages || []).map((m, idx) => renderMessage(m, idx)).join("");
    }

    // Attach toggles and populate iframes AFTER insertion (in document for modal, in iframe for viewer)
    const attachTogglesInDocument = (rootDoc) => {
        (j.messages || []).forEach((m, idx) => {
            if (!m.body_html) return;
            const btn = rootDoc.getElementById(`msg_toggle_${idx}`);
            const iframe = rootDoc.getElementById(`msg_iframe_${idx}`);
            if (!btn || !iframe) return;

            let html = rewriteCid(m.body_html, m.id);
            if (settings.proxyRemoteImages) {
                html = rewriteRemoteImagesToProxy(html);
            }
            iframe.srcdoc = html;

            // Default view preference
            if (settings.defaultHtmlView) {
                const card = btn.closest("div");
                const textEl = card ? card.querySelector('[data-mode="text"]') : null;
                const htmlWrap = card ? card.querySelector('[data-mode="html"]') : null;
                if (textEl) textEl.style.display = "none";
                if (htmlWrap) htmlWrap.style.display = "block";
                btn.textContent = "View Text";
            }

            btn.addEventListener("click", () => {
                const card = btn.closest("div");
                if (!card) return;
                const textEl = card.querySelector('[data-mode="text"]');
                const htmlWrap = card.querySelector('[data-mode="html"]');
                const showing = htmlWrap && htmlWrap.style.display !== "none";
                if (showing) {
                    if (htmlWrap) htmlWrap.style.display = "none";
                    if (textEl) textEl.style.display = "block";
                    btn.textContent = "View HTML";
                } else {
                    if (textEl) textEl.style.display = "none";
                    if (htmlWrap) htmlWrap.style.display = "block";
                    btn.textContent = "View Text";
                }
            });
        });
    };

    if (useViewer) {
        const iframeDoc = viewerFrame.contentDocument;
        // Wait a tick for srcdoc to load
        setTimeout(() => {
            if (viewerFrame.contentDocument) attachTogglesInDocument(viewerFrame.contentDocument);
        }, 0);
    } else {
        attachTogglesInDocument(document);
    }
}

function clearDateFilter() {
    const s1 = document.getElementById("startDate") || document.getElementById("fromDate");
    const e1 = document.getElementById("endDate") || document.getElementById("toDate");
    if (s1) s1.value = "";
    if (e1) e1.value = "";
    currentDateFilter = { start: "", end: "" };
    loadTickets();
}

function closeThreadModal() {
    const m = document.getElementById("threadModal");
    if (m) m.classList.add("hidden");
    const v = document.getElementById("viewerBackdrop");
    if (v) v.classList.remove("show");
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
    const m1 = document.getElementById("loginModal");
    if (m1) m1.classList.remove("hidden");
    const m2 = document.getElementById("loginBackdrop");
    if (m2) m2.classList.add("show");
}

function hideLoginModal() {
    const m1 = document.getElementById("loginModal");
    if (m1) m1.classList.add("hidden");
    const m2 = document.getElementById("loginBackdrop");
    if (m2) m2.classList.remove("show");
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

    // Legacy badge
    const badge = document.getElementById("userBadge");
    if (badge) badge.textContent = `Signed in as: ${currentUser.name} (${currentUser.role})`;

    // Good UI pill
    const authText = document.getElementById("authText");
    if (authText) authText.textContent = `Signed in as ${currentUser.name} (${currentUser.role})`;
    const authDot = document.getElementById("authDot");
    if (authDot) {
        authDot.classList.add("green");
    }

    // Logout buttons
    const logoutBtn = document.getElementById("logoutBtn");
    if (logoutBtn) logoutBtn.classList.remove("hidden");
    const btnLogout2 = document.getElementById("btnLogout");
    if (btnLogout2) btnLogout2.style.display = "inline-flex";

    // Admin-only UI controls (Good UI)
    const manageUsersBtn = document.getElementById("btnManageUsers");
    if (manageUsersBtn) {
        manageUsersBtn.style.display = (String(currentUser.role || "").toUpperCase() === "ADMIN") ? "inline-flex" : "none";
    }

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

    const authText = document.getElementById("authText");
    if (authText) authText.textContent = "Not signed in";
    const authDot = document.getElementById("authDot");
    if (authDot) {
        authDot.classList.remove("green");
        authDot.classList.remove("red");
        authDot.classList.remove("yellow");
    }

    const logoutBtn = document.getElementById("logoutBtn");
    if (logoutBtn) logoutBtn.classList.add("hidden");
    const btnLogout2 = document.getElementById("btnLogout");
    if (btnLogout2) btnLogout2.style.display = "none";

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
