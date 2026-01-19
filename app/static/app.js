let currentTab = "all";
let currentAckThreadId = null;

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

function openSettings() { alert("Settings (MVP): not implemented yet."); }
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

        const url = new URL("/autopilot/fetch-now", window.location.origin);
        if (start) url.searchParams.set("start", start);
        if (end) url.searchParams.set("end", end);

        const r = await fetch(url.toString(), { method: "POST" });
        const text = await r.text();
        if (!r.ok) {
            alert(`Fetch failed (${r.status}):\n\n${text}`);
            return;
        }
        const j = JSON.parse(text);

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
    card.className = "bg-white rounded-xl shadow border p-5 flex items-start justify-between gap-4";

    const due = t.due_at ? `Due: ${formatDate(t.due_at)}` : "Due: —";
    const last = t.last_message_at ? `Last: ${formatDate(t.last_message_at)}` : "Last: —";

    card.innerHTML = `
    <div class="min-w-0 flex-1">
      <div class="flex items-center gap-2">
        <div class="font-semibold text-slate-900 truncate">${t.from_name || t.from_email || "(unknown sender)"}</div>
        ${priorityBadge(t.priority)}
        ${t.is_not_replied ? `<span class="px-2 py-0.5 rounded-full text-xs bg-orange-100 text-orange-700 border">Not Replied</span>` : ``}
        ${t.is_unread ? `<span class="px-2 py-0.5 rounded-full text-xs bg-slate-100 text-slate-700 border">Unread</span>` : ``}
      </div>

      <div class="mt-1 text-slate-900 font-medium truncate">${t.subject || "(no subject)"}</div>
      <div class="mt-1 text-sm text-slate-500 truncate">${t.from_email || ""}</div>
      <div class="mt-2 text-sm text-slate-600">${t.snippet || ""}</div>

      <div class="mt-3 flex flex-wrap gap-3 text-xs text-slate-500">
        <div>${last}</div>
        <div class="text-orange-700">${due}</div>
      </div>

      <div class="mt-4 flex flex-wrap gap-2">
        <button class="px-3 py-2 rounded-lg border text-slate-700 hover:bg-slate-50" onclick="openThread('${t.thread_id}')">Open</button>
        <button class="px-3 py-2 rounded-lg border text-slate-700 hover:bg-slate-50" onclick="openAckModal('${t.thread_id}')">Draft Ack</button>
        ${t.from_email ? `<button class="px-3 py-2 rounded-lg border text-red-700 hover:bg-red-50" onclick="blacklistSender('${t.from_email}')">Blacklist Sender</button>` : ``}
      </div>
    </div>

    <div class="flex flex-col items-end gap-2">
      <select class="px-3 py-2 rounded-lg border bg-white"
        onchange="updateStatus('${t.thread_id}', this.value)">
        ${statusOptions(t.status)}
      </select>
    </div>
  `;

    return card;
}

async function loadTickets() {
    const url = `/tickets?tab=${encodeURIComponent(currentTab)}&limit=50`;
    const r = await fetch(url);
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

async function updateStatus(threadId, status) {
    await fetch(`/tickets/${threadId}/status`, {
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

    const r = await fetch(`/threads/${threadId}`);
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

    const renderMessage = (m, idx) => {
        const hasHtml = !!m.body_html;
        const msgId = m.id;
        const safeText = escapeHtml(m.body_text || m.snippet || "");
        const iframeId = `msg_iframe_${idx}`;
        const btnId = `msg_toggle_${idx}`;
        const html = hasHtml ? rewriteCid(m.body_html, msgId) : "";

        return `
        <div class="border rounded-xl p-4 bg-slate-50">
          <div class="flex items-start justify-between gap-3">
            <div>
              <div class="text-xs text-slate-500">${escapeHtml(m.date || "")}</div>
              <div class="text-sm"><span class="font-medium">From:</span> ${escapeHtml(m.from || "")}</div>
              <div class="text-sm"><span class="font-medium">To:</span> ${escapeHtml(m.to || "")}</div>
              <div class="text-sm"><span class="font-medium">Subject:</span> ${escapeHtml(m.subject || "")}</div>
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
                <iframe id="${iframeId}" class="w-full rounded-lg border bg-white" style="height: 520px;" sandbox="allow-popups allow-forms" referrerpolicy="no-referrer"></iframe>
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

        const html = rewriteCid(m.body_html, m.id);
        iframe.srcdoc = html;

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

function closeThreadModal() {
    document.getElementById("threadModal").classList.add("hidden");
}

async function openAckModal(threadId) {
    currentAckThreadId = threadId;
    document.getElementById("ackModal").classList.remove("hidden");
    document.getElementById("ackSubject").value = "";
    document.getElementById("ackBody").value = "Loading draft…";
    document.getElementById("sendAckBtn").disabled = true;

    const r = await fetch(`/tickets/${threadId}/draft-ack`, { method: "POST" });
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
        const r = await fetch(`/tickets/${currentAckThreadId}/send-ack`, {
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
    const r = await fetch(`/blacklist?email=${encodeURIComponent(email)}`, { method: "POST" });
    const t = await r.text();
    if (!r.ok) {
        alert(`Blacklist failed (${r.status}):\n\n${t}`);
        return;
    }
    await loadTickets();
}

window.addEventListener("load", async () => {
    document.getElementById("lastSync").textContent = new Date().toLocaleString();
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
