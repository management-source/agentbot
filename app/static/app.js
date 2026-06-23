let currentTab = "awaiting_reply";
let currentAckThreadId = null;
let currentAiThreadId = null;
let aiVoiceRecorder = null;
let aiVoiceStream = null;
let aiVoiceChunks = [];
let aiSpeechRecognition = null;
let aiSpeechTranscript = "";
let currentDashboardTab = "portal";

// Mailbox context (multi-inbox)
let currentMailbox = localStorage.getItem("agent_mailbox") || "";


// Local user auth (JWT)
const APP_CONFIG = window.AGENTBOT_CONFIG || {};
const recaptchaEnabled = !!APP_CONFIG.recaptcha_enabled;
let authToken = localStorage.getItem("agent_auth_token") || "";
let currentUser = null;
let usersCache = [];
let loginRecaptchaWidgetId = null;

window.onRecaptchaLoad = function () {
    ensureLoginRecaptcha().catch(() => {
        setLoginError("reCAPTCHA could not load. Please refresh and try again.");
    });
};

async function apiFetch(url, options = {}) {
    const opts = { ...options, headers: { ...(options.headers || {}) } };
    if (authToken) {
        opts.headers["Authorization"] = `Bearer ${authToken}`;
    }
    if (currentMailbox) {
        opts.headers["X-Mailbox"] = currentMailbox;
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

// Pagination state
let currentPage = 1;
let pageSize = 25;

// UI filters
let currentSearch = "";
// Category filtering removed (we avoid AI-based categorization and UI filters for now).
let currentRentPage = 1;
let rentLoadedOnce = false;
let rentViewMode = "tracker";
let currentPropertiesPage = 1;
let propertiesLoadedOnce = false;
let currentCompliancePage = 1;
let complianceLoadedOnce = false;
let currentCoveragePage = 1;
let coverageLoadedOnce = false;
let propertyOptionsCache = [];
let propertyOptionsByLabel = {};
let addressSuggestionsByLabel = {};
let addressSuggestionTimer = null;
let complianceRecordsCache = {};
let editingComplianceRecordId = null;

function isAllEmailsTab() {
    return String(currentTab || "").toLowerCase() === "all";
}

function updateSyncContextUI() {
    const info = document.getElementById("syncInfo");
    const viewBadge = document.getElementById("queueViewMode");
    const mailboxBadge = document.getElementById("queueMailboxMode");
    if (mailboxBadge) mailboxBadge.textContent = currentMailbox || "-";
    if (currentDashboardTab === "portal") {
        if (viewBadge) viewBadge.textContent = "Portal Hub";
        if (info) info.textContent = "Portal Hub: choose a workspace tile or use the menu to jump into a feature.";
        return;
    }
    if (currentDashboardTab === "rent") {
        if (viewBadge) viewBadge.textContent = "Rent Tracker";
        if (info) info.textContent = "Inbox sync controls are hidden while you are on the rent tracker tab.";
        return;
    }
    if (currentDashboardTab === "compliance") {
        if (viewBadge) viewBadge.textContent = "Compliance";
        if (info) info.textContent = "Inbox sync controls are hidden while you are on the compliance tab.";
        return;
    }
    if (currentDashboardTab === "coverage") {
        if (viewBadge) viewBadge.textContent = "Compliance Report";
        if (info) info.textContent = "Inbox sync controls are hidden while you are on the compliance report tab.";
        return;
    }
    if (currentDashboardTab === "properties") {
        if (viewBadge) viewBadge.textContent = "Properties";
        if (info) info.textContent = "Inbox sync controls are hidden while you are on the properties tab.";
        return;
    }
    if (!info) return;
    if (isAllEmailsTab()) {
        if (viewBadge) viewBadge.textContent = "All Emails";
        info.textContent = "All Emails mode: choose a From/To date range, then Fetch or Check updates for that range.";
    } else {
        if (viewBadge) viewBadge.textContent = "Awaiting Reply";
        info.textContent = "Awaiting Reply mode: incremental updates focus on active inbox threads that need a response.";
    }
}

let googleConnected = false;
async function initMailboxes() {
    const sel = document.getElementById("mailboxSelect");
    if (!sel) return;
    try {
        const r = await apiFetch("/settings/mailboxes");
        const j = await r.json();
        const mbs = Array.isArray(j.mailboxes) ? j.mailboxes : [];
        sel.innerHTML = "";
        for (const mb of mbs) {
            const opt = document.createElement("option");
            opt.value = mb;
            opt.textContent = mb;
            sel.appendChild(opt);
        }
        if (!currentMailbox && mbs.length) currentMailbox = mbs[0];
        if (currentMailbox) {
            localStorage.setItem("agent_mailbox", currentMailbox);
            sel.value = currentMailbox;
        }
        sel.addEventListener("change", () => {
            currentMailbox = sel.value;
            localStorage.setItem("agent_mailbox", currentMailbox);
            // refresh UI data under new mailbox
            currentPage = 1;
            rentLoadedOnce = false;
            propertiesLoadedOnce = false;
            complianceLoadedOnce = false;
            coverageLoadedOnce = false;
            updateSyncContextUI();
            loadTickets();
            if (currentDashboardTab === "rent") {
                currentRentPage = 1;
                loadActiveRentView();
            }
            if (currentDashboardTab === "properties") {
                currentPropertiesPage = 1;
                loadProperties();
            }
            if (currentDashboardTab === "compliance") {
                currentCompliancePage = 1;
                loadComplianceDashboard();
            }
            if (currentDashboardTab === "coverage") {
                currentCoveragePage = 1;
                loadComplianceCoverage();
            }
            refreshPropertyOptions();
            refreshGoogleStatus();
        });

        const lbl = document.getElementById("mailboxLabel");
        if (lbl) lbl.textContent = currentMailbox || "-";
        const mailboxBadge = document.getElementById("queueMailboxMode");
        if (mailboxBadge) mailboxBadge.textContent = currentMailbox || "-";
        updateSyncContextUI();
    } catch (e) {
        // If auth isn't ready yet, we'll retry after login.
    }
}


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

        const target = (currentMailbox || j.target_mailbox || j.delegated_mailbox || "me");

        const mb = document.getElementById("mailboxBadge");
        if (mb) mb.textContent = googleConnected ? (`Mailbox: ${target}`) : "";

        const mb2 = document.getElementById("mailboxLabel");
        if (mb2) mb2.textContent = googleConnected ? target : "-";

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
        row.className = "p-2 rounded-lg border bg-white";
        const avatar = u.avatar_url ? `<img src="${escapeHtml(u.avatar_url)}" style="width:40px;height:40px;border-radius:999px;object-fit:cover;border:1px solid #ddd" />` : `<div style="width:40px;height:40px;border-radius:999px;background:#eef2f7;border:1px solid #ddd"></div>`;
        row.innerHTML = `
            <div class="row space" style="align-items:flex-start">
              <div class="row" style="align-items:flex-start">
                ${avatar}
                <div class="min-w-0">
                  <div class="font-medium text-slate-900 truncate">${escapeHtml(u.name)}</div>
                  <div class="text-xs text-slate-500 truncate">${escapeHtml(u.email)} • ${escapeHtml(u.role)}${u.is_active ? "" : " • Inactive"}</div>
                </div>
              </div>
              <div class="row" style="align-items:center">
                <label class="btn" style="cursor:pointer">
                  Avatar
                  <input type="file" accept="image/*" style="display:none" onchange="uploadUserAvatar(${u.id}, this)" />
                </label>
              </div>
            </div>
            <div class="row" style="margin-top:8px;align-items:flex-end">
              <select class="px-2 py-1 rounded-md border bg-white text-sm" data-user-role="${u.id}">
                ${["ADMIN", "PM", "LEASING", "SALES", "ACCOUNTS", "READONLY"].map(r => `<option value="${r}" ${r === u.role ? "selected" : ""}>${r}</option>`).join("")}
              </select>
              <label class="text-sm text-slate-600 flex items-center gap-1 checkbox" style="padding:6px 10px">
                <input type="checkbox" ${u.is_active ? "checked" : ""} data-user-active="${u.id}" />
                Active
              </label>
              <input type="password" data-user-password="${u.id}" placeholder="Reset password" style="max-width:220px" />
              <button class="px-3 py-1.5 rounded-md border text-sm" onclick="adminResetPassword(${u.id})">Reset Password</button>
              <button class="px-3 py-1.5 rounded-md border text-sm" onclick="saveUserEdits(${u.id})">Save</button>
              <button class="px-3 py-1.5 rounded-md border text-sm" style="color:#b91c1c" onclick="deleteUser(${u.id})">Delete</button>
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
    const force = !!document.getElementById("newUserForcePassword")?.checked;
    if (!email || !name || !password) {
        alert("Email, name and password are required.");
        return;
    }
    const r = await apiFetch("/user-auth/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, name, role, password, is_active: true, must_change_password: force }),
    });
    if (!r.ok) {
        const msg = await r.text();
        alert("Failed to create user: " + msg);
        return;
    }
    document.getElementById("newUserEmail").value = "";
    document.getElementById("newUserName").value = "";
    document.getElementById("newUserPassword").value = "";
    if (document.getElementById("newUserForcePassword")) document.getElementById("newUserForcePassword").checked = true;
    await renderUsersList();
}

async function uploadUserAvatar(userId, input) {
    const file = input && input.files ? input.files[0] : null;
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    const r = await apiFetch(`/user-auth/users/${userId}/avatar`, { method: "POST", body: form });
    if (!r.ok) {
        alert("Failed to upload avatar.");
        return;
    }
    await renderUsersList();
}

async function adminResetPassword(userId) {
    const el = document.querySelector(`[data-user-password="${userId}"]`);
    const pw = el ? String(el.value || "") : "";
    if (!pw) {
        alert("Enter a new password first.");
        return;
    }
    const r = await apiFetch(`/user-auth/users/${userId}/password`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_password: pw, force_change_on_next_login: true }),
    });
    if (!r.ok) {
        const t = await r.text();
        alert("Password reset failed: " + t);
        return;
    }
    if (el) el.value = "";
    alert("Password reset successfully.");
}

async function deleteUser(userId) {
    if (!confirm("Delete this user permanently? This cannot be undone.")) return;
    const r = await apiFetch(`/user-auth/users/${userId}`, { method: "DELETE" });
    if (!r.ok) {
        const t = await r.text();
        alert("Delete failed: " + t);
        return;
    }
    await renderUsersList();
}

function toggleAccountMenu() {
    const dd = document.getElementById("accountMenuDropdown");
    if (!dd) return;
    dd.classList.toggle("show");
}

function closeAccountMenu() {
    const dd = document.getElementById("accountMenuDropdown");
    if (dd) dd.classList.remove("show");
}

function openPasswordModal() {
    const m = document.getElementById("passwordModal");
    if (!m) return;
    const e = document.getElementById("pwError");
    if (e) { e.style.display = "none"; e.textContent = ""; }
    ["pwCurrent", "pwNew", "pwConfirm"].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = "";
    });
    m.classList.remove("hidden");
}

function closePasswordModal() {
    const m = document.getElementById("passwordModal");
    if (m) m.classList.add("hidden");
}

async function submitPasswordChange() {
    const curr = document.getElementById("pwCurrent")?.value || "";
    const next = document.getElementById("pwNew")?.value || "";
    const confirm = document.getElementById("pwConfirm")?.value || "";
    const err = document.getElementById("pwError");
    if (next !== confirm) {
        if (err) { err.style.display = "block"; err.textContent = "New password confirmation does not match."; }
        return;
    }
    const r = await apiFetch("/user-auth/me/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: curr, new_password: next }),
    });
    if (!r.ok) {
        const t = await r.text();
        if (err) { err.style.display = "block"; err.textContent = t; }
        return;
    }
    closePasswordModal();
    alert("Password updated successfully.");
}

function openSettings() {
    const m = document.getElementById("settingsModal");
    if (!m) return;
    document.getElementById("setDefaultHtml").checked = !!settings.defaultHtmlView;
    document.getElementById("setBlockRemote").checked = !!settings.proxyRemoteImages;
    document.getElementById("setCompact").checked = !!settings.compactTickets;

    // Load signature from server (best-effort)
    const sigBox = document.getElementById("signatureText");
    if (sigBox) {
        sigBox.value = "Loading...";
        apiFetch("/settings/signature").then(async (r) => {
            const t = await r.text();
            if (!r.ok) {
                sigBox.value = "";
                return;
            }
            try {
                const j = JSON.parse(t);
                sigBox.value = j.signature || "";
            } catch {
                sigBox.value = "";
            }
        }).catch(() => { sigBox.value = ""; });
    }
    m.classList.remove("hidden");
    // HTML signature preview (best-effort)
    refreshSignaturePreview().catch(() => { /* ignore */ });
}


async function refreshSignaturePreview() {
    const iframe = document.getElementById("signaturePreview");
    if (!iframe) return;
    try {
        const r = await apiFetch("/settings/signature/html");
        if (!r.ok) return;
        const j = await r.json();
        const html = (j && j.html) ? j.html : "";
        const meta = document.getElementById("sigSourceMeta");
        if (meta) {
            const src = (j && j.source) ? j.source : "";
            const sa = (j && j.send_as) ? j.send_as : "";
            meta.textContent = src === "gmail" ? `Source: Gmail (${sa || ""})` : (src ? `Source: ${src}` : "");
        }
        iframe.srcdoc = html || "<div style='font-family:Arial; padding:10px; color:#666'>No HTML signature set yet. Click Fetch from Gmail.</div>";
    } catch {
        // ignore
    }
}


async function fetchGmailSignature() {
    const input = document.getElementById("sigSendAsEmail");
    const sendAsEmail = input ? (input.value || "").trim() : "";
    try {
        const r = await apiFetch("/settings/signature/fetch-gmail", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ send_as_email: sendAsEmail || null })
        });
        const t = await r.text();
        if (!r.ok) {
            alert(`Failed to fetch Gmail signature (${r.status}):\n\n${t}`);
            return;
        }
        await refreshSignaturePreview();
        alert("Fetched Gmail signature successfully.");
    } catch (e) {
        alert("Failed to fetch Gmail signature: " + e);
    }
}



async function uploadSignatureAsset(name, file) {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
        const r = await apiFetch(`/settings/signature/assets/${encodeURIComponent(name)}`, {
            method: "POST",
            body: form
        });
        const t = await r.text();
        if (!r.ok) {
            alert(`Upload failed (${r.status}):\n\n${t}`);
            return;
        }
        await refreshSignaturePreview();
    } catch (e) {
        alert("Upload failed: " + e);
    }
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
// Autopilot / query rules removed.

function formatDate(dt) {
    if (!dt) return "-";
    try { return new Date(dt).toLocaleString(); } catch { return dt; }
}
function formatDateShort(dt) {
    if (!dt) return "-";
    try { return new Date(dt).toLocaleDateString(); } catch { return dt; }
}

function switchDashboardTab(tab) {
    currentDashboardTab = ["portal", "rent", "compliance", "coverage", "properties", "inbox"].includes(tab) ? tab : "portal";
    const titles = {
        portal: ["Portal Hub", "Your workspace shortcuts for email, rent, compliance, and property setup."],
        inbox: ["Email Manager", "Unified inbox operations with clear action queues and fast follow-up tools."],
        rent: ["Rent Tracker", "Track rental due dates, payments, arrears, and yearly rent reporting."],
        compliance: ["Compliance", "Create and update compliance records with calculated due dates."],
        coverage: ["Compliance Report", "Review missing and incomplete MRS, Smoke, Gas, and Electrical checks."],
        properties: ["Properties", "Maintain the active Victorian managed property register."],
    };
    const title = document.getElementById("topbarTitle");
    const subtitle = document.getElementById("topbarSubtitle");
    if (title) title.textContent = titles[currentDashboardTab]?.[0] || "Portal Hub";
    if (subtitle) subtitle.textContent = titles[currentDashboardTab]?.[1] || "";
    
    const portalPanel = document.getElementById("portalPanel");
    const inboxPanel = document.getElementById("inboxPanel");
    const rentPanel = document.getElementById("rentPanel");
    const propertiesPanel = document.getElementById("propertiesPanel");
    const compliancePanel = document.getElementById("compliancePanel");
    const coveragePanel = document.getElementById("coveragePanel");
    const navInbox = document.getElementById("navInbox");
    const navPortal = document.getElementById("navPortal");
    const navRent = document.getElementById("navRentTracker");
    const navProperties = document.getElementById("navProperties");
    const navCompliance = document.getElementById("navCompliance");
    const navCoverage = document.getElementById("navComplianceCoverage");
    const shell = document.getElementById("dashboardShell");

    if (portalPanel) portalPanel.classList.toggle("hidden", currentDashboardTab !== "portal");
    if (inboxPanel) inboxPanel.classList.toggle("hidden", currentDashboardTab !== "inbox");
    if (rentPanel) rentPanel.classList.toggle("hidden", currentDashboardTab !== "rent");
    if (propertiesPanel) propertiesPanel.classList.toggle("hidden", currentDashboardTab !== "properties");
    if (compliancePanel) compliancePanel.classList.toggle("hidden", currentDashboardTab !== "compliance");
    if (coveragePanel) coveragePanel.classList.toggle("hidden", currentDashboardTab !== "coverage");
    if (navPortal) navPortal.classList.toggle("active", currentDashboardTab === "portal");
    if (navInbox) navInbox.classList.toggle("active", currentDashboardTab === "inbox");
    if (navRent) navRent.classList.toggle("active", currentDashboardTab === "rent");
    if (navProperties) navProperties.classList.toggle("active", currentDashboardTab === "properties");
    if (navCompliance) navCompliance.classList.toggle("active", currentDashboardTab === "compliance");
    if (navCoverage) navCoverage.classList.toggle("active", currentDashboardTab === "coverage");
    if (shell) {
        shell.classList.toggle("portal-mode", currentDashboardTab === "portal");
        shell.classList.toggle("rent-mode", currentDashboardTab === "rent");
        shell.classList.toggle("compliance-mode", currentDashboardTab === "compliance");
        shell.classList.toggle("coverage-mode", currentDashboardTab === "coverage");
        shell.classList.toggle("properties-mode", currentDashboardTab === "properties");
    }

    updateSyncContextUI();
    if (currentDashboardTab === "rent" && !rentLoadedOnce) {
        loadActiveRentView();
    }
    if (currentDashboardTab === "properties" && !propertiesLoadedOnce) {
        loadProperties();
        refreshPropertyOptions();
    }
    if (currentDashboardTab === "compliance" && !complianceLoadedOnce) {
        refreshPropertyOptions();
        loadComplianceDashboard();
    }
    if (currentDashboardTab === "coverage" && !coverageLoadedOnce) {
        loadComplianceCoverage();
    }
}

function applySidebarState() {
    const shell = document.getElementById("appShell");
    const btn = document.getElementById("sidebarToggle");
    const collapsed = localStorage.getItem("agent_sidebar_collapsed") === "1";
    if (shell) shell.classList.toggle("sidebar-collapsed", collapsed);
    if (btn) {
        const label = collapsed ? "Expand menu" : "Collapse menu";
        btn.setAttribute("aria-label", label);
        btn.setAttribute("title", label);
        btn.setAttribute("aria-expanded", String(!collapsed));
    }
}

function toggleSidebar() {
    const shell = document.getElementById("appShell");
    const collapsed = !(shell && shell.classList.contains("sidebar-collapsed"));
    localStorage.setItem("agent_sidebar_collapsed", collapsed ? "1" : "0");
    applySidebarState();
}

function rentStatusChip(status) {
    const key = String(status || "DUE").toUpperCase();
    if (key === "PAID") return `<span class="rent-status paid">Paid</span>`;
    if (key === "PARTIAL") return `<span class="rent-status partial">Partial</span>`;
    if (key === "VACANT") return `<span class="rent-status vacant">Vacant</span>`;
    if (key === "AWAITING_CLEARANCE") return `<span class="rent-status awaiting">Awaiting Clearance</span>`;
    return `<span class="rent-status due">Due</span>`;
}

function getRentFilters() {
    const status = (document.getElementById("rentStatusFilter")?.value || "").trim();
    const frequency = (document.getElementById("rentFrequencyFilter")?.value || "").trim();
    const query = (document.getElementById("rentSearchBox")?.value || "").trim();
    return { status, frequency, query };
}

function getRollingMonths() {
    const base = new Date();
    return [-1, 0, 1].map((offset) => {
        const d = new Date(base.getFullYear(), base.getMonth() + offset, 1);
        const y = d.getFullYear();
        const m = d.getMonth() + 1;
        return {
            key: `${y}-${String(m).padStart(2, "0")}`,
            label: d.toLocaleDateString(undefined, { month: "short", year: "numeric" }),
        };
    });
}

function getYearMonths() {
    const year = new Date().getFullYear();
    const labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return labels.map((label, idx) => ({
        key: `${year}-${String(idx + 1).padStart(2, "0")}`,
        label,
    }));
}

function switchRentViewMode(mode) {
    rentViewMode = (mode === "year") ? "year" : "tracker";
    const btnTracker = document.getElementById("rentViewTrackerBtn");
    const btnYear = document.getElementById("rentViewYearBtn");
    const trackerGrid = document.getElementById("rentTrackerGrid");
    const yearGrid = document.getElementById("rentYearReportGrid");
    if (btnTracker) btnTracker.classList.toggle("active", rentViewMode === "tracker");
    if (btnYear) btnYear.classList.toggle("active", rentViewMode === "year");
    if (trackerGrid) trackerGrid.classList.toggle("hidden", rentViewMode !== "tracker");
    if (yearGrid) yearGrid.classList.toggle("hidden", rentViewMode !== "year");
    currentRentPage = 1;
    loadActiveRentView();
}

function loadActiveRentView(page = null) {
    if (rentViewMode === "year") return loadRentYearReport(page);
    return loadRentTracker(page);
}

function monthCellSelect(item) {
    if (!item || !item.id) return `<span class="small muted">-</span>`;
    const status = String(item.status || "DUE").toUpperCase();
    const extra = Number(item.extra_items || 0);
    const due = item.due_date ? formatDateShort(item.due_date) : "";
    const partialAmount = (typeof item.partial_amount === "number" && item.partial_amount > 0) ? Number(item.partial_amount).toFixed(2) : "";
    return `
      <div>
        <select style="min-width:110px" onchange="updateRentMonthCell(${item.id}, this.value, ${item.partial_amount || 0})">
          <option value="DUE" ${status === "DUE" ? "selected" : ""}>Due</option>
          <option value="PAID" ${status === "PAID" ? "selected" : ""}>Paid</option>
          <option value="PARTIAL" ${status === "PARTIAL" ? "selected" : ""}>Partial</option>
          <option value="AWAITING_CLEARANCE" ${status === "AWAITING_CLEARANCE" ? "selected" : ""}>Awaiting</option>
          <option value="VACANT" ${status === "VACANT" ? "selected" : ""}>Vacant</option>
        </select>
        ${status === "PARTIAL" ? `
        <div style="margin-top:6px">
          <input type="number" min="0" step="0.01" placeholder="Amount" value="${partialAmount}" style="width:100px" onchange="updateRentPartialAmount(${item.id}, this.value)" />
        </div>` : ``}
        <div style="margin-top:4px">${rentStatusChip(status)}</div>
        <div class="small muted" style="margin-top:4px">${escapeHtml(due)}${partialAmount ? ` • $${partialAmount}` : ""}${extra > 0 ? ` • +${extra}` : ""}</div>
      </div>
    `;
}

async function loadRentTracker(page = null) {
    if (page !== null) currentRentPage = page;
    const p = currentRentPage || 1;
    const { status, frequency, query } = getRentFilters();

    const itemsUrl = new URL("/rent-tracker/properties", window.location.origin);
    const summaryUrl = new URL("/rent-tracker/summary", window.location.origin);
    itemsUrl.searchParams.set("page", String(p));
    itemsUrl.searchParams.set("page_size", "25");
    if (status) itemsUrl.searchParams.set("status", status);
    if (frequency) itemsUrl.searchParams.set("frequency", frequency);
    if (query) itemsUrl.searchParams.set("query", query);

    const body = document.getElementById("rentTableBody");
    if (body) body.innerHTML = `<tr><td colspan="6" class="muted">Loading...</td></tr>`;

    const rollingMonths = getRollingMonths();
    const h1 = document.getElementById("rentMonthHead1");
    const h2 = document.getElementById("rentMonthHead2");
    const h3 = document.getElementById("rentMonthHead3");
    if (h1) h1.textContent = rollingMonths[0].label;
    if (h2) h2.textContent = rollingMonths[1].label;
    if (h3) h3.textContent = rollingMonths[2].label;

    const [itemsResp, summaryResp] = await Promise.all([
        apiFetch(itemsUrl.toString()),
        apiFetch(summaryUrl.toString()),
    ]);

    if (!itemsResp.ok) {
        const t = await itemsResp.text();
        if (body) body.innerHTML = `<tr><td colspan="6" class="muted">Failed to load rent tracker: ${escapeHtml(t)}</td></tr>`;
        return;
    }
    if (!summaryResp.ok) {
        const t = await summaryResp.text();
        if (body) body.innerHTML = `<tr><td colspan="6" class="muted">Failed to load summary: ${escapeHtml(t)}</td></tr>`;
        return;
    }

    const data = await itemsResp.json();
    const summary = await summaryResp.json();
    rentLoadedOnce = true;

    const items = Array.isArray(data.items) ? data.items : [];
    const statusCounts = summary.status_counts || {};
    const setText = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = String(val || 0);
    };

    setText("rentKpiTotal", summary.total || 0);
    setText("rentKpiOverdue", summary.overdue || 0);
    setText("rentKpiDueSoon", summary.due_next_7_days || 0);
    setText("rentKpiPaid", statusCounts.PAID || 0);
    setText("rentKpiPending", (statusCounts.DUE || 0) + (statusCounts.PARTIAL || 0) + (statusCounts.AWAITING_CLEARANCE || 0));

    if (!body) return;
    if (items.length === 0) {
        body.innerHTML = `<tr><td colspan="6" class="muted">No properties found for this filter.</td></tr>`;
    } else {
        body.innerHTML = items.map((r) => `
            <tr>
                <td><div style="font-weight:700">${escapeHtml(r.property_address || "")}</div><div class="small muted">${escapeHtml(r.source_sheet || "-")}</div></td>
                <td>${escapeHtml(r.frequency || "-")}</td>
                <td>
                    <div class="small muted">Paid ${Number((r.counts || {}).PAID || 0)} / ${Number(r.total_items || 0)}</div>
                    <div class="small muted">Due ${Number((r.counts || {}).DUE || 0)} • Partial ${Number((r.counts || {}).PARTIAL || 0)}</div>
                </td>
                ${rollingMonths.map((mk) => `<td>${monthCellSelect((r.months || {})[mk.key])}</td>`).join("")}
            </tr>
        `).join("");
    }

    const pi = document.getElementById("rentPageInfo");
    if (pi) {
        const total = Number(data.total || 0);
        const pageNow = Number(data.page || 1);
        const sizeNow = Number(data.page_size || 25);
        const pages = sizeNow > 0 ? Math.max(1, Math.ceil(total / sizeNow)) : 1;
        pi.textContent = `Page ${pageNow} of ${pages} • ${total} properties`;
    }

    const btnPrev = document.getElementById("rentBtnPrev");
    const btnNext = document.getElementById("rentBtnNext");
    if (btnPrev) btnPrev.disabled = Number(data.page || 1) <= 1;
    if (btnNext) btnNext.disabled = !Boolean(data.has_more);
}

async function loadRentYearReport(page = null) {
    if (page !== null) currentRentPage = page;
    const p = currentRentPage || 1;
    const { status, frequency, query } = getRentFilters();

    const itemsUrl = new URL("/rent-tracker/properties", window.location.origin);
    const summaryUrl = new URL("/rent-tracker/summary", window.location.origin);
    itemsUrl.searchParams.set("page", String(p));
    itemsUrl.searchParams.set("page_size", "25");
    if (status) itemsUrl.searchParams.set("status", status);
    if (frequency) itemsUrl.searchParams.set("frequency", frequency);
    if (query) itemsUrl.searchParams.set("query", query);

    const body = document.getElementById("rentYearTableBody");
    if (body) body.innerHTML = `<tr><td colspan="15" class="muted">Loading...</td></tr>`;

    const [itemsResp, summaryResp] = await Promise.all([
        apiFetch(itemsUrl.toString()),
        apiFetch(summaryUrl.toString()),
    ]);

    if (!itemsResp.ok) {
        const t = await itemsResp.text();
        if (body) body.innerHTML = `<tr><td colspan="15" class="muted">Failed to load report: ${escapeHtml(t)}</td></tr>`;
        return;
    }
    if (!summaryResp.ok) {
        const t = await summaryResp.text();
        if (body) body.innerHTML = `<tr><td colspan="15" class="muted">Failed to load summary: ${escapeHtml(t)}</td></tr>`;
        return;
    }

    const data = await itemsResp.json();
    const summary = await summaryResp.json();
    rentLoadedOnce = true;

    const items = Array.isArray(data.items) ? data.items : [];
    const statusCounts = summary.status_counts || {};
    const setText = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = String(val || 0);
    };
    setText("rentKpiTotal", summary.total || 0);
    setText("rentKpiOverdue", summary.overdue || 0);
    setText("rentKpiDueSoon", summary.due_next_7_days || 0);
    setText("rentKpiPaid", statusCounts.PAID || 0);
    setText("rentKpiPending", (statusCounts.DUE || 0) + (statusCounts.PARTIAL || 0) + (statusCounts.AWAITING_CLEARANCE || 0));

    if (!body) return;
    const yearMonths = getYearMonths();
    if (items.length === 0) {
        body.innerHTML = `<tr><td colspan="15" class="muted">No properties found for this filter.</td></tr>`;
    } else {
        body.innerHTML = items.map((r) => `
            <tr>
                <td class="sticky-col"><div style="font-weight:700">${escapeHtml(r.property_address || "")}</div><div class="small muted">${escapeHtml(r.source_sheet || "-")}</div></td>
                <td>${escapeHtml(r.frequency || "-")}</td>
                <td>
                    <div class="small muted">Paid ${Number((r.counts || {}).PAID || 0)} / ${Number(r.total_items || 0)}</div>
                    <div class="small muted">Due ${Number((r.counts || {}).DUE || 0)} • Partial ${Number((r.counts || {}).PARTIAL || 0)}</div>
                </td>
                ${yearMonths.map((mk) => `<td>${monthCellSelect((r.months || {})[mk.key])}</td>`).join("")}
            </tr>
        `).join("");
    }

    const pi = document.getElementById("rentPageInfo");
    if (pi) {
        const total = Number(data.total || 0);
        const pageNow = Number(data.page || 1);
        const sizeNow = Number(data.page_size || 25);
        const pages = sizeNow > 0 ? Math.max(1, Math.ceil(total / sizeNow)) : 1;
        pi.textContent = `Page ${pageNow} of ${pages} • ${total} properties`;
    }

    const btnPrev = document.getElementById("rentBtnPrev");
    const btnNext = document.getElementById("rentBtnNext");
    if (btnPrev) btnPrev.disabled = Number(data.page || 1) <= 1;
    if (btnNext) btnNext.disabled = !Boolean(data.has_more);
}

function prevRentPage() {
    if (currentRentPage <= 1) return;
    currentRentPage -= 1;
    loadActiveRentView();
}

function nextRentPage() {
    currentRentPage += 1;
    loadActiveRentView();
}

async function updateRentMonthCell(itemId, status, existingPartialAmount = 0) {
    const payload = { status };
    if (status === "PAID") {
        const d = new Date();
        payload.paid_on = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}T00:00:00`;
    }
    if (status === "PARTIAL") {
        let amount = Number(existingPartialAmount || 0);
        if (!(amount > 0)) {
            const entered = prompt("Enter partially paid amount:");
            if (entered === null) return;
            amount = Number(entered);
        }
        if (!(amount > 0)) {
            alert("Please enter a valid partial amount greater than 0.");
            return;
        }
        payload.partial_amount = amount;
    } else {
        payload.partial_amount = null;
    }
    const r = await apiFetch(`/rent-tracker/items/${itemId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    if (!r.ok) {
        const t = await r.text();
        alert(`Failed to update month cell: ${t}`);
        return;
    }
    await loadActiveRentView();
}

async function updateRentPartialAmount(itemId, value) {
    const amount = Number(value || 0);
    if (!(amount > 0)) {
        alert("Partial amount must be greater than 0.");
        return;
    }
    const r = await apiFetch(`/rent-tracker/items/${itemId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "PARTIAL", partial_amount: amount }),
    });
    if (!r.ok) {
        const t = await r.text();
        alert(`Failed to update partial amount: ${t}`);
        return;
    }
    await loadActiveRentView();
}

async function importRentWorkbook() {
    const fileInput = document.getElementById("rentImportFile");
    const f = fileInput && fileInput.files ? fileInput.files[0] : null;
    if (!f) {
        alert("Choose an .xlsx workbook first.");
        return;
    }

    const form = new FormData();
    form.append("file", f, f.name);

    const meta = document.getElementById("rentImportMeta");
    if (meta) meta.textContent = "Importing workbook...";

    const r = await apiFetch("/rent-tracker/import-xlsx", {
        method: "POST",
        body: form,
    });
    const t = await r.text();
    if (!r.ok) {
        if (meta) meta.textContent = "Import failed.";
        alert(`Import failed (${r.status}):\n\n${t}`);
        return;
    }
    let j = null;
    try { j = JSON.parse(t); } catch { j = null; }
    if (meta) {
        const rows = j && j.imported_rows ? j.imported_rows : "-";
        const y = (j && j.year) ? j.year : new Date().getFullYear();
        meta.textContent = `Imported ${rows} rows for ${y} from ${escapeHtml(f.name)} at ${new Date().toLocaleString()}.`;
    }
    currentRentPage = 1;
    await loadActiveRentView();
}

function complianceStatusChip(status) {
    const key = String(status || "OPEN").toUpperCase();
    if (key === "CURRENT") return `<span class="compliance-status current">Current</span>`;
    if (key === "DUE_SOON") return `<span class="compliance-status due-soon">Due Soon</span>`;
    if (key === "OVERDUE") return `<span class="compliance-status overdue">Overdue</span>`;
    if (key === "ACTION_REQUIRED") return `<span class="compliance-status action">Action Required</span>`;
    if (key === "WAIVED") return `<span class="compliance-status waived">Waived</span>`;
    return `<span class="compliance-status open">Open</span>`;
}

function complianceTypeLabel(type) {
    const labels = {
        GAS: "Gas",
        SMOKE: "Smoke",
        ELECTRICAL: "Electrical",
        MRS: "MRS",
        POOL: "Pool",
        POWERBAND: "PowerBand",
        DISCLOSURE: "Disclosure",
        OTHER: "Other",
    };
    return labels[String(type || "OTHER").toUpperCase()] || String(type || "-");
}

function complianceCycleHint(type) {
    const key = String(type || "").toUpperCase();
    if (key === "SMOKE") return "12 months";
    if (key === "GAS" || key === "ELECTRICAL") return "2 years";
    return "Tracked manually";
}

function dateInputValue(dt) {
    if (!dt) return "";
    const d = new Date(dt);
    if (Number.isNaN(d.getTime())) return String(dt).slice(0, 10);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function isoDateOrNull(dateValue) {
    const value = String(dateValue || "").trim();
    return value ? `${value}T00:00:00` : null;
}

function addYearsToDateInput(dateValue, years) {
    if (!dateValue || !years) return "";
    const [year, month, day] = dateValue.split("-").map((x) => Number(x));
    if (!year || !month || !day) return "";
    const d = new Date(year + years, month - 1, day);
    if (d.getMonth() !== month - 1) {
        d.setDate(0);
    }
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function calculatedNextDueInput(type, doneDate) {
    const key = String(type || "").toUpperCase();
    if (key === "SMOKE") return addYearsToDateInput(doneDate, 1);
    if (key === "GAS" || key === "ELECTRICAL") return addYearsToDateInput(doneDate, 2);
    return "";
}

function coverageStatusChip(state) {
    const key = String(state || "MISSING").toUpperCase();
    if (key === "MISSING") return `<span class="compliance-status missing">Missing</span>`;
    if (key === "OPEN") return `<span class="compliance-status incomplete">Open</span>`;
    return complianceStatusChip(key);
}

function getPropertyFilters() {
    return { query: (document.getElementById("propertySearchBox")?.value || "").trim() };
}

async function loadProperties(page = null) {
    if (page !== null) currentPropertiesPage = page;
    const p = currentPropertiesPage || 1;
    const { query } = getPropertyFilters();
    const url = new URL("/properties", window.location.origin);
    url.searchParams.set("page", String(p));
    url.searchParams.set("page_size", "25");
    if (query) url.searchParams.set("query", query);

    const body = document.getElementById("propertiesTableBody");
    if (body) body.innerHTML = `<tr><td colspan="6" class="muted">Loading...</td></tr>`;

    const r = await apiFetch(url.toString());
    const t = await r.text();
    if (!r.ok) {
        if (body) body.innerHTML = `<tr><td colspan="6" class="muted">Failed to load properties: ${escapeHtml(t)}</td></tr>`;
        return;
    }
    const data = JSON.parse(t);
    propertiesLoadedOnce = true;
    const items = Array.isArray(data.items) ? data.items : [];
    if (body) {
        if (!items.length) {
            body.innerHTML = `<tr><td colspan="6" class="muted">No properties found.</td></tr>`;
        } else {
            body.innerHTML = items.map((row) => `
                <tr>
                    <td><div style="font-weight:700">${escapeHtml(row.property_address || "")}</div><div class="small muted">${escapeHtml(row.address_line_2 || "")}</div></td>
                    <td>${escapeHtml(row.suburb || "-")}</td>
                    <td>${escapeHtml(row.state_code || "-")}</td>
                    <td>${escapeHtml(row.postcode || "-")}</td>
                    <td>${escapeHtml(row.source || "-")}</td>
                    <td><button class="btn danger" onclick="deleteProperty(${row.id})">Delete</button></td>
                </tr>
            `).join("");
        }
    }
    const pi = document.getElementById("propertiesPageInfo");
    if (pi) {
        const total = Number(data.total || 0);
        const pageNow = Number(data.page || 1);
        const sizeNow = Number(data.page_size || 25);
        const pages = sizeNow > 0 ? Math.max(1, Math.ceil(total / sizeNow)) : 1;
        pi.textContent = `Page ${pageNow} of ${pages} - ${total} properties`;
    }
    const btnPrev = document.getElementById("propertiesBtnPrev");
    const btnNext = document.getElementById("propertiesBtnNext");
    if (btnPrev) btnPrev.disabled = Number(data.page || 1) <= 1;
    if (btnNext) btnNext.disabled = !Boolean(data.has_more);
}

function splitAustralianAddress(value) {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    const parts = text.split(",").map((part) => part.trim()).filter(Boolean);
    if (parts.length < 2) return null;
    const states = new Set(["VIC", "VICTORIA", "NSW", "QLD", "SA", "WA", "TAS", "NT", "ACT"]);
    let postcode = "";
    let state = "";
    let suburb = "";
    let unsupportedState = "";
    const setState = (raw) => {
        const normalized = String(raw || "").trim().toUpperCase();
        if (!states.has(normalized)) return false;
        state = normalized === "VICTORIA" ? "VIC" : normalized;
        if (state !== "VIC") unsupportedState = state;
        return true;
    };
    const remaining = [...parts];
    const last = remaining[remaining.length - 1] || "";
    const postcodeMatch = last.match(/\b(\d{4})\b$/);
    if (postcodeMatch) {
        postcode = postcodeMatch[1];
        const prefix = last.slice(0, postcodeMatch.index).trim();
        remaining.pop();
        if (prefix) {
            const stateWithSuburb = prefix.match(/^(.+?)\s+(VIC|VICTORIA|NSW|QLD|SA|WA|TAS|NT|ACT)$/i);
            if (stateWithSuburb) {
                suburb = stateWithSuburb[1].trim();
                setState(stateWithSuburb[2]);
            } else if (!setState(prefix)) {
                suburb = prefix;
            }
        }
    }
    if (remaining.length) {
        const maybeState = remaining[remaining.length - 1].toUpperCase();
        if (!state && setState(maybeState)) {
            remaining.pop();
        }
    }
    if (remaining.length >= 2 && !suburb) suburb = remaining.pop();
    const propertyAddress = remaining.join(", ") || text;
    return { propertyAddress, suburb, state: state || "VIC", postcode, unsupportedState };
}

function fillPropertyFormFromOption(option) {
    if (!option) return;
    const address = document.getElementById("newPropertyAddress");
    const suburb = document.getElementById("newPropertySuburb");
    const state = document.getElementById("newPropertyState");
    const postcode = document.getElementById("newPropertyPostcode");
    if (address) address.value = option.property_address || option.label || "";
    if (suburb) suburb.value = option.suburb || "";
    if (state) state.value = "VIC";
    if (postcode) postcode.value = option.postcode || "";
}

function rememberAddressSuggestion(option) {
    if (!option) return;
    [option.label, option.property_address].forEach((value) => {
        const key = String(value || "").trim().toLowerCase();
        if (key) addressSuggestionsByLabel[key] = option;
    });
}

function renderAddressSuggestionOptions(remoteItems = [], filterValue = "") {
    const addressList = document.getElementById("propertyAddressSuggestions");
    if (!addressList) return;
    const needle = String(filterValue || "").trim().toLowerCase();
    addressSuggestionsByLabel = {};
    const merged = [];
    const seen = new Set();
    const addOption = (option) => {
        const label = String(option?.label || option?.property_address || "").trim();
        const key = label.toLowerCase();
        if (!label || seen.has(key)) return;
        seen.add(key);
        merged.push({ ...option, label });
        rememberAddressSuggestion({ ...option, label });
    };
    (Array.isArray(remoteItems) ? remoteItems : []).forEach(addOption);
    propertyOptionsCache
        .filter((p) => !needle || String(p.label || "").toLowerCase().includes(needle))
        .slice(0, 40)
        .forEach(addOption);
    addressList.innerHTML = merged
        .slice(0, 60)
        .map((p) => `<option value="${escapeHtml(p.label || "")}"></option>`)
        .join("");
}

async function loadVictorianAddressSuggestions(value) {
    const query = String(value || "").trim();
    if (query.length < 3) {
        renderAddressSuggestionOptions([], query);
        return;
    }
    const url = new URL("/properties/address-suggestions", window.location.origin);
    url.searchParams.set("q", query);
    try {
        const r = await apiFetch(url.pathname + url.search);
        if (!r.ok) {
            renderAddressSuggestionOptions([], query);
            return;
        }
        const data = await r.json();
        const currentValue = (document.getElementById("newPropertyAddress")?.value || "").trim();
        if (currentValue !== query) return;
        renderAddressSuggestionOptions(Array.isArray(data.items) ? data.items : [], query);
    } catch {
        renderAddressSuggestionOptions([], query);
    }
}

function scheduleAddressSuggestionSearch() {
    const address = document.getElementById("newPropertyAddress");
    const value = address ? address.value : "";
    if (addressSuggestionTimer) clearTimeout(addressSuggestionTimer);
    addressSuggestionTimer = setTimeout(() => loadVictorianAddressSuggestions(value), 300);
}

function autocompleteNewPropertyFields() {
    const address = document.getElementById("newPropertyAddress");
    if (!address) return;
    const raw = address.value || "";
    const exact = addressSuggestionsByLabel[raw.trim().toLowerCase()] || propertyOptionsByLabel[raw.trim().toLowerCase()];
    if (exact) {
        fillPropertyFormFromOption(exact);
        return;
    }
    const parsed = splitAustralianAddress(raw);
    if (!parsed) return;
    if (parsed.unsupportedState) {
        alert("Only Victorian properties can be added. This address looks like it is in " + parsed.unsupportedState + ".");
        address.focus();
        return false;
    }
    const suburb = document.getElementById("newPropertySuburb");
    const state = document.getElementById("newPropertyState");
    const postcode = document.getElementById("newPropertyPostcode");
    address.value = parsed.propertyAddress;
    if (suburb && parsed.suburb) suburb.value = parsed.suburb;
    if (state) state.value = "VIC";
    if (postcode && parsed.postcode) postcode.value = parsed.postcode;
    return true;
}

function prevPropertiesPage() {
    if (currentPropertiesPage <= 1) return;
    currentPropertiesPage -= 1;
    loadProperties();
}

function nextPropertiesPage() {
    currentPropertiesPage += 1;
    loadProperties();
}

async function createPropertyFromForm() {
    if (autocompleteNewPropertyFields() === false) return;
    const property_address = (document.getElementById("newPropertyAddress")?.value || "").trim();
    const suburb = (document.getElementById("newPropertySuburb")?.value || "").trim();
    const state_code = "VIC";
    const postcode = (document.getElementById("newPropertyPostcode")?.value || "").trim();
    if (!property_address) {
        alert("Property address is required.");
        return;
    }
    const r = await apiFetch("/properties", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ property_address, suburb, state_code, postcode }),
    });
    const t = await r.text();
    if (!r.ok) {
        alert(`Failed to add property (${r.status}):\n\n${t}`);
        return;
    }
    ["newPropertyAddress", "newPropertySuburb", "newPropertyPostcode"].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.value = "";
    });
    currentPropertiesPage = 1;
    propertiesLoadedOnce = false;
    await loadProperties();
    await refreshPropertyOptions();
}

async function deleteProperty(propertyId, label = "this property") {
    if (!confirm(`Delete ${label} from the active property register? Compliance records will be kept in history.`)) return;
    const r = await apiFetch(`/properties/${propertyId}`, { method: "DELETE" });
    const t = await r.text();
    if (!r.ok) {
        alert(`Failed to delete property (${r.status}):\n\n${t}`);
        return;
    }
    currentPropertiesPage = 1;
    propertiesLoadedOnce = false;
    await loadProperties();
    await refreshPropertyOptions();
    if (complianceLoadedOnce) await loadComplianceDashboard(1);
    if (coverageLoadedOnce) await loadComplianceCoverage(1);
}

async function importPropertiesWorkbook() {
    const fileInput = document.getElementById("propertyImportFile");
    const f = fileInput && fileInput.files ? fileInput.files[0] : null;
    if (!f) {
        alert("Choose a property .xlsx workbook first.");
        return;
    }
    const form = new FormData();
    form.append("file", f, f.name);
    const meta = document.getElementById("propertyImportMeta");
    if (meta) meta.textContent = "Importing properties...";
    const r = await apiFetch("/properties/import-xlsx", { method: "POST", body: form });
    const t = await r.text();
    if (!r.ok) {
        if (meta) meta.textContent = "Import failed.";
        alert(`Property import failed (${r.status}):\n\n${t}`);
        return;
    }
    let j = null;
    try { j = JSON.parse(t); } catch { j = null; }
    if (meta) meta.textContent = `Imported ${j?.imported_rows || "-"} properties from ${escapeHtml(f.name)} at ${new Date().toLocaleString()}.`;
    currentPropertiesPage = 1;
    propertiesLoadedOnce = false;
    await loadProperties();
    await refreshPropertyOptions();
}

async function refreshPropertyOptions() {
    const search = document.getElementById("compliancePropertySearch");
    const hidden = document.getElementById("compliancePropertyId");
    const complianceList = document.getElementById("compliancePropertyOptions");
    try {
        const r = await apiFetch("/properties/options");
        if (!r.ok) {
            if (complianceList) complianceList.innerHTML = "";
            renderAddressSuggestionOptions();
            if (hidden) hidden.value = "";
            return;
        }
        const data = await r.json();
        propertyOptionsCache = Array.isArray(data.items) ? data.items : [];
        propertyOptionsByLabel = {};
        propertyOptionsCache.forEach((p) => {
            propertyOptionsByLabel[String(p.label || "").trim().toLowerCase()] = p;
            propertyOptionsByLabel[String(p.property_address || "").trim().toLowerCase()] = p;
        });
        const optionsHtml = propertyOptionsCache
            .map((p) => `<option value="${escapeHtml(p.label || "")}"></option>`)
            .join("");
        if (complianceList) complianceList.innerHTML = optionsHtml;
        renderAddressSuggestionOptions();
        if (search && hidden) {
            const match = resolvePropertySearchValue(search.value);
            hidden.value = match ? String(match.id) : "";
        }
    } catch {
        if (complianceList) complianceList.innerHTML = "";
        renderAddressSuggestionOptions();
        if (hidden) hidden.value = "";
    }
}

function resolvePropertySearchValue(value) {
    const needle = String(value || "").trim().toLowerCase();
    if (!needle) return null;
    if (propertyOptionsByLabel[needle]) return propertyOptionsByLabel[needle];
    const matches = propertyOptionsCache.filter((p) => String(p.label || "").toLowerCase().includes(needle));
    return matches.length === 1 ? matches[0] : null;
}

function updateCompliancePropertySelection() {
    const search = document.getElementById("compliancePropertySearch");
    const hidden = document.getElementById("compliancePropertyId");
    if (!search || !hidden) return null;
    const match = resolvePropertySearchValue(search.value);
    hidden.value = match ? String(match.id) : "";
    return match;
}

function getComplianceFilters() {
    return {
        state: (document.getElementById("complianceStateFilter")?.value || "").trim(),
        complianceType: (document.getElementById("complianceTypeFilter")?.value || "").trim(),
        query: (document.getElementById("complianceSearchBox")?.value || "").trim(),
    };
}

async function loadComplianceDashboard(page = null) {
    if (page !== null) currentCompliancePage = page;
    const p = currentCompliancePage || 1;
    const { state, complianceType, query } = getComplianceFilters();

    const itemsUrl = new URL("/compliance/records", window.location.origin);
    const summaryUrl = new URL("/compliance/summary", window.location.origin);
    itemsUrl.searchParams.set("page", String(p));
    itemsUrl.searchParams.set("page_size", "25");
    if (state) itemsUrl.searchParams.set("state", state);
    if (complianceType) itemsUrl.searchParams.set("compliance_type", complianceType);
    if (query) itemsUrl.searchParams.set("query", query);

    const body = document.getElementById("complianceTableBody");
    if (body) body.innerHTML = `<tr><td colspan="9" class="muted">Loading...</td></tr>`;

    const [itemsResp, summaryResp] = await Promise.all([
        apiFetch(itemsUrl.toString()),
        apiFetch(summaryUrl.toString()),
    ]);
    if (!itemsResp.ok) {
        const t = await itemsResp.text();
        if (body) body.innerHTML = `<tr><td colspan="9" class="muted">Failed to load compliance: ${escapeHtml(t)}</td></tr>`;
        return;
    }
    if (!summaryResp.ok) {
        const t = await summaryResp.text();
        if (body) body.innerHTML = `<tr><td colspan="9" class="muted">Failed to load summary: ${escapeHtml(t)}</td></tr>`;
        return;
    }

    const data = await itemsResp.json();
    const summary = await summaryResp.json();
    complianceLoadedOnce = true;

    const setText = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = String(val || 0);
    };
    setText("complianceKpiTotal", summary.total_properties || 0);
    setText("complianceKpiAction", summary.action_required_records || 0);
    setText("complianceKpiOverdue", summary.overdue_records || 0);
    setText("complianceKpiSoon", summary.due_soon_records || 0);
    setText("complianceKpiCurrent", summary.current_records || 0);

    const items = Array.isArray(data.items) ? data.items : [];
    complianceRecordsCache = {};
    items.forEach((row) => {
        complianceRecordsCache[row.id] = row;
    });
    if (body) {
        if (!items.length) {
            body.innerHTML = `<tr><td colspan="9" class="muted">No compliance records found for this filter.</td></tr>`;
        } else {
            body.innerHTML = items.map((row) => `
                <tr>
                    <td>
                        <div style="font-weight:700">${escapeHtml(row.property_address || "")}</div>
                        <div class="small muted">${escapeHtml([row.suburb, row.state_code, row.postcode].filter(Boolean).join(", ") || "-")}</div>
                    </td>
                    <td>${escapeHtml(complianceTypeLabel(row.compliance_type))}</td>
                    <td>
                        ${complianceStatusChip(row.state)}
                        <div class="small muted" style="margin-top:4px">${escapeHtml(complianceCycleHint(row.compliance_type))}</div>
                        <div style="margin-top:6px">
                          <select onchange="updateComplianceRecordStatus(${row.id}, this.value)">
                            ${["OPEN", "ACTION_REQUIRED", "COMPLETED", "WAIVED"].map((s) => `<option value="${s}" ${s === row.status ? "selected" : ""}>${s.replace("_", " ")}</option>`).join("")}
                          </select>
                        </div>
                    </td>
                    <td>${escapeHtml(formatDateShort(row.completed_at))}</td>
                    <td>${escapeHtml(formatDateShort(row.due_date))}</td>
                    <td>${escapeHtml(row.provider_name || "-")}</td>
                    <td>${escapeHtml(row.result_text || "-")}</td>
                    <td class="small">${escapeHtml(row.notes || "-")}</td>
                    <td>
                        <div class="row">
                            <button class="btn" onclick="openComplianceEditModal(${row.id})">Edit</button>
                            <button class="btn danger" onclick="deleteComplianceRecord(${row.id})">Delete</button>
                        </div>
                    </td>
                </tr>
            `).join("");
        }
    }

    const pi = document.getElementById("compliancePageInfo");
    if (pi) {
        const total = Number(data.total || 0);
        const pageNow = Number(data.page || 1);
        const sizeNow = Number(data.page_size || 25);
        const pages = sizeNow > 0 ? Math.max(1, Math.ceil(total / sizeNow)) : 1;
        pi.textContent = `Page ${pageNow} of ${pages} - ${total} records - Due soon window ${Number(summary.due_soon_window_days || 30)} days`;
    }

    const btnPrev = document.getElementById("complianceBtnPrev");
    const btnNext = document.getElementById("complianceBtnNext");
    if (btnPrev) btnPrev.disabled = Number(data.page || 1) <= 1;
    if (btnNext) btnNext.disabled = !Boolean(data.has_more);
}

function prevCompliancePage() {
    if (currentCompliancePage <= 1) return;
    currentCompliancePage -= 1;
    loadComplianceDashboard();
}

function nextCompliancePage() {
    currentCompliancePage += 1;
    loadComplianceDashboard();
}

async function createComplianceRecord() {
    const selectedProperty = updateCompliancePropertySelection();
    const propertyId = Number(selectedProperty?.id || document.getElementById("compliancePropertyId")?.value || 0);
    if (!propertyId) {
        alert("Search and select a property from the suggestions first.");
        return;
    }
    const compliance_type = (document.getElementById("complianceTypeInput")?.value || "OTHER").trim();
    const completedDate = (document.getElementById("complianceCompletedDateInput")?.value || "").trim();
    const provider_name = (document.getElementById("complianceProviderInput")?.value || "").trim();
    const notes = (document.getElementById("complianceNotesInput")?.value || "").trim();
    const payload = {
        property_id: propertyId,
        compliance_type,
        status: completedDate ? "COMPLETED" : "OPEN",
        completed_at: isoDateOrNull(completedDate),
        provider_name: provider_name || null,
        notes: notes || null,
    };
    const r = await apiFetch("/compliance/records", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    const t = await r.text();
    if (!r.ok) {
        alert(`Failed to add compliance record (${r.status}):\n\n${t}`);
        return;
    }
    ["complianceCompletedDateInput", "complianceProviderInput", "complianceNotesInput"].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.value = "";
    });
    currentCompliancePage = 1;
    await loadComplianceDashboard();
    if (coverageLoadedOnce) await loadComplianceCoverage();
}

async function updateComplianceRecordStatus(recordId, status) {
    const payload = { status };
    if (status === "COMPLETED") {
        const d = new Date();
        payload.completed_at = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}T00:00:00`;
    }
    const r = await apiFetch(`/compliance/records/${recordId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    if (!r.ok) {
        const t = await r.text();
        alert(`Failed to update compliance record: ${t}`);
        return;
    }
    await loadComplianceDashboard();
    if (coverageLoadedOnce) await loadComplianceCoverage();
}

function updateComplianceEditNextDuePreview() {
    const record = complianceRecordsCache[editingComplianceRecordId] || {};
    const doneDate = document.getElementById("editComplianceCompletedAt")?.value || "";
    const nextDue = calculatedNextDueInput(record.compliance_type, doneDate);
    const el = document.getElementById("editComplianceNextDue");
    if (el) el.value = nextDue || "No automatic cycle";
}

function openComplianceEditModal(recordId) {
    const record = complianceRecordsCache[recordId];
    if (!record) {
        alert("This record is not loaded anymore. Please refresh the compliance dashboard.");
        return;
    }
    editingComplianceRecordId = recordId;
    const title = document.getElementById("complianceEditTitle");
    if (title) {
        title.textContent = `${record.property_address || "Property"} - ${complianceTypeLabel(record.compliance_type)} (${complianceCycleHint(record.compliance_type)})`;
    }
    const status = document.getElementById("editComplianceStatus");
    const completedAt = document.getElementById("editComplianceCompletedAt");
    const provider = document.getElementById("editComplianceProvider");
    const result = document.getElementById("editComplianceResult");
    const notes = document.getElementById("editComplianceNotes");
    if (status) status.value = record.status || "OPEN";
    if (completedAt) completedAt.value = dateInputValue(record.completed_at);
    if (provider) provider.value = record.provider_name || "";
    if (result) result.value = record.result_text || "";
    if (notes) notes.value = record.notes || "";
    updateComplianceEditNextDuePreview();
    const modal = document.getElementById("complianceEditModal");
    if (modal) modal.classList.remove("hidden");
}

function closeComplianceEditModal() {
    editingComplianceRecordId = null;
    const modal = document.getElementById("complianceEditModal");
    if (modal) modal.classList.add("hidden");
}

async function saveComplianceEdit() {
    if (!editingComplianceRecordId) return;
    const status = (document.getElementById("editComplianceStatus")?.value || "OPEN").trim();
    const completedDate = (document.getElementById("editComplianceCompletedAt")?.value || "").trim();
    const provider_name = (document.getElementById("editComplianceProvider")?.value || "").trim();
    const result_text = (document.getElementById("editComplianceResult")?.value || "").trim();
    const notes = (document.getElementById("editComplianceNotes")?.value || "").trim();
    const payload = {
        status,
        completed_at: isoDateOrNull(completedDate),
        provider_name: provider_name || null,
        result_text: result_text || null,
        notes: notes || null,
    };
    const r = await apiFetch(`/compliance/records/${editingComplianceRecordId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    const t = await r.text();
    if (!r.ok) {
        alert(`Failed to save compliance record (${r.status}):\n\n${t}`);
        return;
    }
    closeComplianceEditModal();
    await loadComplianceDashboard();
    if (coverageLoadedOnce) await loadComplianceCoverage();
}

async function deleteComplianceRecord(recordId) {
    const record = complianceRecordsCache[recordId] || {};
    const label = `${record.property_address || "this property"} - ${complianceTypeLabel(record.compliance_type)}`;
    if (!confirm(`Delete compliance record for ${label}?`)) return false;
    const r = await apiFetch(`/compliance/records/${recordId}`, { method: "DELETE" });
    const t = await r.text();
    if (!r.ok) {
        alert(`Failed to delete compliance record (${r.status}):\n\n${t}`);
        return false;
    }
    await loadComplianceDashboard();
    if (coverageLoadedOnce) await loadComplianceCoverage();
    return true;
}

async function deleteComplianceRecordFromModal() {
    if (!editingComplianceRecordId) return;
    const recordId = editingComplianceRecordId;
    const deleted = await deleteComplianceRecord(recordId);
    if (deleted) closeComplianceEditModal();
}

function getCoverageFilters() {
    return {
        query: (document.getElementById("coverageSearchBox")?.value || "").trim(),
        includeCurrent: !!document.getElementById("coverageIncludeCurrent")?.checked,
    };
}

function formatCheckList(items) {
    if (!Array.isArray(items) || !items.length) return `<span class="muted">-</span>`;
    return items.map((x) => `<span class="badge">${escapeHtml(x)}</span>`).join(" ");
}

function formatCoverageChecks(checks) {
    if (!Array.isArray(checks) || !checks.length) return `<span class="muted">-</span>`;
    return checks.map((check) => {
        const dates = [
            check.completed_at ? `Done ${formatDateShort(check.completed_at)}` : "",
            check.due_date ? `Due ${formatDateShort(check.due_date)}` : "",
        ].filter(Boolean).join(" - ");
        return `
            <div style="margin-bottom:8px">
                ${coverageStatusChip(check.state)}
                <b style="margin-left:6px">${escapeHtml(check.label || check.type || "")}</b>
                <div class="small muted" style="margin-top:3px">${escapeHtml(dates || "No completed record")}</div>
            </div>
        `;
    }).join("");
}

async function loadComplianceCoverage(page = null) {
    if (page !== null) currentCoveragePage = page;
    const p = currentCoveragePage || 1;
    const { query, includeCurrent } = getCoverageFilters();
    const url = new URL("/compliance/coverage", window.location.origin);
    url.searchParams.set("page", String(p));
    url.searchParams.set("page_size", "25");
    if (query) url.searchParams.set("query", query);
    if (includeCurrent) url.searchParams.set("include_current", "true");

    const body = document.getElementById("coverageTableBody");
    if (body) body.innerHTML = `<tr><td colspan="6" class="muted">Loading...</td></tr>`;
    const r = await apiFetch(url.toString());
    const t = await r.text();
    if (!r.ok) {
        if (body) body.innerHTML = `<tr><td colspan="6" class="muted">Failed to load compliance report: ${escapeHtml(t)}</td></tr>`;
        return;
    }
    const data = JSON.parse(t);
    coverageLoadedOnce = true;
    const summary = data.summary || {};
    const setText = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = String(val || 0);
    };
    setText("coverageKpiTotal", summary.total_properties || 0);
    setText("coverageKpiAttention", summary.needs_attention || 0);
    setText("coverageKpiMissing", summary.with_missing || 0);
    setText("coverageKpiIncomplete", summary.with_incomplete || 0);
    setText("coverageKpiCurrent", summary.fully_current || 0);

    const items = Array.isArray(data.items) ? data.items : [];
    if (body) {
        if (!items.length) {
            body.innerHTML = `<tr><td colspan="6" class="muted">No compliance report items found for this filter.</td></tr>`;
        } else {
            body.innerHTML = items.map((row) => `
                <tr>
                    <td>
                        <div style="font-weight:700">${escapeHtml(row.property_address || "")}</div>
                        <div class="small muted">${escapeHtml([row.suburb, row.state_code, row.postcode].filter(Boolean).join(", ") || "-")}</div>
                    </td>
                    <td>${formatCheckList(row.missing)}</td>
                    <td>${formatCheckList(row.incomplete)}</td>
                    <td>${formatCheckList(row.overdue)}</td>
                    <td>${formatCheckList(row.due_soon)}</td>
                    <td>${formatCoverageChecks(row.checks)}</td>
                </tr>
            `).join("");
        }
    }
    const pi = document.getElementById("coveragePageInfo");
    if (pi) {
        const total = Number(data.total || 0);
        const pageNow = Number(data.page || 1);
        const sizeNow = Number(data.page_size || 25);
        const pages = sizeNow > 0 ? Math.max(1, Math.ceil(total / sizeNow)) : 1;
        pi.textContent = `Page ${pageNow} of ${pages} - ${total} properties needing review`;
    }
    const btnPrev = document.getElementById("coverageBtnPrev");
    const btnNext = document.getElementById("coverageBtnNext");
    if (btnPrev) btnPrev.disabled = Number(data.page || 1) <= 1;
    if (btnNext) btnNext.disabled = !Boolean(data.has_more);
}

function prevCoveragePage() {
    if (currentCoveragePage <= 1) return;
    currentCoveragePage -= 1;
    loadComplianceCoverage();
}

function nextCoveragePage() {
    currentCoveragePage += 1;
    loadComplianceCoverage();
}

function setTab(tab) {
    currentTab = tab;
    currentPage = 1;
    updateSyncContextUI();

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

    // UX: when changing tabs, jump to top so the user sees the newest tickets first.
    try { window.scrollTo({ top: 0, behavior: "smooth" }); } catch { window.scrollTo(0, 0); }
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
        const allMode = isAllEmailsTab();

        if (allMode && (!start || !end)) {
            alert("All Emails mode requires both From and To dates.");
            return;
        }

        // Persist the selected date filter for the ticket list.
        currentDateFilter = { start: start || "", end: end || "" };

        const url = new URL("/sync/fetch-now", window.location.origin);
        if (currentMailbox) url.searchParams.set("mailbox", currentMailbox);
        if (start) url.searchParams.set("start", start);
        if (end) url.searchParams.set("end", end);
        if (!Number.isNaN(maxThreads) && maxThreads > 0) url.searchParams.set("max_threads", String(maxThreads));
        url.searchParams.set("awaiting_only", allMode ? "false" : "true");
        // incremental applies only when no date range
        if (!start && !end) url.searchParams.set("incremental", incremental ? "true" : "false");
        if (start || end) {
            const includeAny = allMode ? true : includeAnywhere;
            url.searchParams.set("include_anywhere", includeAny ? "true" : "false");
        }

        const r = await apiFetch(url.toString(), { method: "POST" });
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

async function checkUpdates() {
    const btn = document.getElementById("btnCheckUpdates");
    if (btn) {
        btn.disabled = true;
        btn.textContent = "Checking...";
    }

    try {
        const allMode = isAllEmailsTab();
        const startEl = document.getElementById("startDate") || document.getElementById("fromDate");
        const endEl = document.getElementById("endDate") || document.getElementById("toDate");
        const maxEl = document.getElementById("maxThreads") || document.getElementById("limit");
        const start = startEl ? (startEl.value || currentDateFilter.start || "") : (currentDateFilter.start || "");
        const end = endEl ? (endEl.value || currentDateFilter.end || "") : (currentDateFilter.end || "");
        const maxThreads = parseInt((maxEl && maxEl.value) ? maxEl.value : "200", 10);

        let url;
        if (allMode) {
            if (!start || !end) {
                alert("In All Emails mode, choose both From and To dates before checking updates.");
                return;
            }
            currentDateFilter = { start, end };
            url = new URL("/sync/fetch-now", window.location.origin);
            url.searchParams.set("start", start);
            url.searchParams.set("end", end);
            url.searchParams.set("incremental", "false");
            url.searchParams.set("include_anywhere", "true");
            url.searchParams.set("awaiting_only", "false");
            url.searchParams.set("max_threads", String(!Number.isNaN(maxThreads) && maxThreads > 0 ? maxThreads : 500));
        } else {
            url = new URL("/sync/check-updates", window.location.origin);
            // Safety cap; frequent use should stay light.
            url.searchParams.set("max_threads", String(!Number.isNaN(maxThreads) && maxThreads > 0 ? maxThreads : 200));
        }
        if (currentMailbox) url.searchParams.set("mailbox", currentMailbox);

        const r = await apiFetch(url.toString(), { method: "POST" });
        const text = await r.text();
        if (!r.ok) {
            alert(`Check Updates failed (${r.status}):\n\n${text}`);
            return;
        }

        const last1 = document.getElementById("lastSync");
        if (last1) last1.textContent = new Date().toLocaleString();

        await loadTickets();
    } catch (e) {
        alert("Check Updates failed: " + e);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "Check Updates";
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

// Autopilot removed.

function priorityBadge(p) {
    const val = String(p || "medium").toLowerCase();
    if (val === "high") return `<span class="px-2 py-0.5 rounded-full text-xs bg-red-100 text-red-700 border">high</span>`;
    if (val === "low") return `<span class="px-2 py-0.5 rounded-full text-xs bg-emerald-100 text-emerald-700 border">low</span>`;
    return `<span class="px-2 py-0.5 rounded-full text-xs bg-amber-100 text-amber-700 border">medium</span>`;
}

// AI-based categorization is disabled for now. Keep the UI lean and avoid
// background AI calls. (AI drafting remains available per-ticket via the
// "AI Draft" modal.)
function aiBadges(_t) {
    return "";
}


// Assignment and manual category selection removed.

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

    const due = t.due_at ? `Due: ${formatDate(t.due_at)}` : "Due: -";
    const last = t.last_message_at ? `Last: ${formatDate(t.last_message_at)}` : "Last: -";

    // Legacy manual category removed from UI; prefer AI category.
    const cat = "";
    // Assignment feature removed.
    const assignee = "";

    let slaText = "SLA: -";
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

        const priority = String(t.priority || "medium").toLowerCase();
        const priBadge = priority === "high"
            ? `<span class="badge priority">High</span>`
            : (priority === "low" ? `<span class="badge">Low</span>` : `<span class="badge">Medium</span>`);

        const unreadBadge = t.is_unread ? `<span class="badge unread">Unread</span>` : "";
        const nrBadge = t.is_not_replied ? `<span class="badge priority">Not Replied</span>` : "";
        const slaBadge = slaOverdue ? `<span class="badge overdue">Overdue</span>` : "";

        card.innerHTML = `
          <div>
            <h4>${escapeHtml(t.subject || "(no subject)")}</h4>
            <div class="from">${escapeHtml(t.from_name || t.from_email || "(unknown sender)")}  •  ${escapeHtml(t.from_email || "")}</div>
            <div class="snippet">${escapeHtml(t.snippet || "")}</div>

            <div class="badge-row">
              ${priBadge}
              ${aiBadges(t)}
              ${cat ? `<span class="badge">${escapeHtml(cat)}</span>` : ``}
              ${assignee ? `<span class="badge">${escapeHtml(assignee)}</span>` : ``}
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
              <button class="btn" onclick="openAiReplyModal('${t.thread_id}')">AI Draft</button>
              <button class="btn" onclick="openAckModal('${t.thread_id}')">Quick Reply</button>
            </div>

            <div class="ticket-controls">
              <div class="field">
                <div class="label">Status</div>
                <select onchange="updateStatus('${t.thread_id}', this.value)">
                  ${statusOptions(t.status)}
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

    const catBadge = ""; // legacy manual category removed; use AI category badge instead
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
        ${aiBadges(t)}
        ${catBadge}
        
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
        <button class="px-3 py-2 rounded-lg border text-slate-700 hover:bg-slate-50" onclick="openAiReplyModal('${t.thread_id}')">AI Draft</button>
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
      <!-- Manual category removed; AI category is computed automatically -->
    </div>
  `;

    return card;
}

async function loadTickets() {
    const url = new URL(`/tickets`, window.location.origin);
    url.searchParams.set("tab", currentTab);
    url.searchParams.set("page", String(currentPage));
    url.searchParams.set("page_size", String(pageSize));

    // Apply current filter (set by Fetch Now). If empty, do not filter.
    if (currentDateFilter.start) url.searchParams.set("start", currentDateFilter.start);
    if (currentDateFilter.end) url.searchParams.set("end", currentDateFilter.end);

    // Search / assignee / AI category filters
    const q = (currentSearch || "").trim();
    if (q) url.searchParams.set("query", q);
    // ai_category filter removed

    const r = await apiFetch(url);
    const data = await r.json();

    const items = Array.isArray(data.items) ? data.items : [];
    // If there are no items returned, force KPIs to zero to avoid displaying stale counts.
    if (items.length === 0) {
        data.counts = {
            awaiting_reply: 0,
            in_progress: 0,
            responded: 0,
            no_reply_needed: 0,
        };
    }

    const c = data.counts || {};
    const setText = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = String(val ?? 0);
    };
    setText("tabAllCount", c.all ?? 0);
    setText("tabAwaitingCount", c.awaiting_reply ?? 0);
    setText("tabInProgressCount", c.in_progress ?? 0);
    setText("tabRespondedCount", c.responded ?? 0);
    setText("tabNoReplyNeededCount", c.no_reply_needed ?? 0);

    const list = document.getElementById("ticketList");
    if (!list) return;
    list.innerHTML = "";

    items.forEach(t => list.appendChild(renderTicket(t)));

    if (items.length === 0) {
        list.innerHTML = `<div class="muted small" style="padding:10px">No tickets in this tab.</div>`;
    }

    renderPagination(data);
}

function renderPagination(data) {
    const wrap = document.getElementById("pagination");
    if (!wrap) return;

    const total = Number(data.total || 0);
    const page = Number(data.page || currentPage);
    const page_size = Number(data.page_size || pageSize);
    const has_more = Boolean(data.has_more);

    const btnPrev = document.getElementById("btnPrev");
    const btnNext = document.getElementById("btnNext");
    const info = document.getElementById("pageInfo");

    const totalPages = page_size > 0 ? Math.ceil(total / page_size) : 1;

    if (total <= page_size) {
        wrap.style.display = "none";
        return;
    }

    wrap.style.display = "flex";
    if (btnPrev) btnPrev.disabled = page <= 1;
    if (btnNext) btnNext.disabled = !has_more;
    if (info) info.textContent = `Page ${page} of ${totalPages}  •  ${total} tickets`;
}

function prevPage() {
    if (currentPage <= 1) return;
    currentPage -= 1;
    // UX: bring user to top of the list when paging.
    try { window.scrollTo({ top: 0, behavior: "smooth" }); } catch { window.scrollTo(0, 0); }
    loadTickets();
}

function nextPage() {
    currentPage += 1;
    // UX: bring user to top of the list when paging.
    try { window.scrollTo({ top: 0, behavior: "smooth" }); } catch { window.scrollTo(0, 0); }
    loadTickets();
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
        viewerFrame.srcdoc = `<div style="font-family:system-ui; padding:16px; color:#334155">Loading thread...</div>`;
    } else if (modal && content) {
        modal.classList.remove("hidden");
        content.innerHTML = `<div class="text-sm text-slate-600">Loading thread...</div>`;
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
            const url = `/assets/inline/${encodeURIComponent(messageId)}/${encodeURIComponent(cid)}`;
            return `src=${q}${url}${q}`;
        });
    };

    const rewriteRemoteImagesToProxy = (html) => {
        if (!html) return "";
        return html.replace(/(<img\b[^>]*\bsrc\s*=\s*)(["'])(https?:\/\/[^"'>\s]+)\2/gi, (m, pre, q, url) => {
            const proxied = `${window.location.origin}/assets/proxy-image?url=${encodeURIComponent(url)}`;
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
        const mimeQ = encodeURIComponent(a.mime_type || "");
        const url = `/assets/attachment/${encodeURIComponent(messageIdArg || "")}/${encodeURIComponent(a.attachment_id)}?filename=${encodeURIComponent(name)}&mime=${mimeQ}`;
        return `<a style="display:inline-flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid #e5e7eb;border-radius:999px;text-decoration:none;color:#334155;background:#fff" href="${url}" target="_blank" rel="noreferrer">
          <span style="font-size:12px;padding:2px 8px;border-radius:999px;background:#f1f5f9;color:#475569;border:1px solid #e5e7eb">${label}</span>
          <span style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtmlLocal(name)}</span>
        </a>`;
    };

    const renderMessage = (m, idx) => {
        // We render HTML only (backend always provides body_html, even for plain-text emails).
        const msgId = m.id;
        const iframeId = `msg_iframe_${idx}`;

        let html = rewriteCid(m.body_html || "", msgId);
        if (settings.proxyRemoteImages) {
            html = rewriteRemoteImagesToProxy(html);
        }

        const atts = (m.attachments || []).map(a => ({ ...a, message_id: msgId })).filter(a => !a.is_inline);
        const attachmentsHtml = atts.length ? `<div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:8px">${atts.map(a => attachmentBadge(a, threadId, msgId)).join("")}</div>` : "";

        const canHtml = (html || "").trim();
        const htmlBlock = `
          <div style="margin-top:12px" data-mode="html">
            <iframe id="${iframeId}" style="width:100%;height:520px;border:1px solid #e5e7eb;border-radius:12px;background:#fff"
              sandbox="allow-popups allow-forms allow-same-origin" referrerpolicy="no-referrer"></iframe>
          </div>
        `;

        return `
        <div data-msg-card="1" style="border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#f8fafc;margin-top:12px">
          <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:10px">
            <div>
              <div style="font-size:12px;color:#64748b">${escapeHtmlLocal(m.date || "")}</div>
              <div style="font-size:13px;color:#0f172a;margin-top:2px"><b>From:</b> ${escapeHtmlLocal(m.from || "")}</div>
              <div style="font-size:13px;color:#0f172a"><b>To:</b> ${escapeHtmlLocal(m.to || "")}</div>
              <div style="font-size:13px;color:#0f172a"><b>Subject:</b> ${escapeHtmlLocal(m.subject || "")}</div>
              ${attachmentsHtml}
            </div>

            
          </div>

          <div style="margin-top:12px">
            ${htmlBlock}
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
        // Populate message iframes after the viewer frame loads its srcdoc.
        viewerFrame.onload = () => {
            try { populateIframes(viewerFrame.contentDocument); } catch (e) { }
        };
        viewerFrame.srcdoc = threadHtml;
    } else {
        content.innerHTML = (j.messages || []).map((m, idx) => renderMessage(m, idx)).join("");
    }

    // Populate iframes AFTER insertion (in document for modal, in iframe for viewer)
    const populateIframes = (rootDoc) => {
        (j.messages || []).forEach((m, idx) => {
            const iframe = rootDoc.getElementById(`msg_iframe_${idx}`);
            if (!iframe) return;
            let html = rewriteCid(m.body_html || "", m.id);
            if (settings.proxyRemoteImages) {
                html = rewriteRemoteImagesToProxy(html);
            }
            iframe.srcdoc = html;
        });
    };

    if (!useViewer) populateIframes(document);
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
    const ccEl = document.getElementById("ackCc");
    const bccEl = document.getElementById("ackBcc");
    if (ccEl) ccEl.value = "";
    if (bccEl) bccEl.value = "";
    const fEl = document.getElementById("ackAttachments");
    const listEl = document.getElementById("ackAttachList");
    if (fEl) fEl.value = "";
    if (listEl) listEl.innerHTML = "";
    document.getElementById("ackBody").value = "Loading draft...";
    document.getElementById("sendAckBtn").disabled = true;

    // Quick Reply is deterministic (non-AI). AI is only invoked when you explicitly click AI Draft.
    const r = await apiFetch(`/tickets/${threadId}/draft-reply`, {
        method: "POST",
    });
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

    // Attachments list preview
    if (fEl) {
        fEl.onchange = () => {
            const files = Array.from(fEl.files || []);
            if (!listEl) return;
            if (files.length === 0) {
                listEl.innerHTML = "";
                return;
            }
            listEl.innerHTML = files.map(f => `${escapeHtml(f.name)} <span class="muted">(${Math.round(f.size / 1024)} KB)</span>`).join("<br/>");
        };
    }
}

async function openAiReplyModal(threadId) {
    currentAiThreadId = threadId;
    const modal = document.getElementById("aiReplyModal");
    if (modal) modal.classList.remove("hidden");
    document.getElementById("aiReplySubject").value = "";
    document.getElementById("aiReplyBody").value = "Loading draft...";
    const metaEl = document.getElementById("aiReplyMeta");
    if (metaEl) metaEl.textContent = "";
    const extraEl = getAiReplyExtraEl();
    if (extraEl) extraEl.value = "";
    const voiceStatus = document.getElementById("aiVoiceStatus");
    if (voiceStatus) voiceStatus.textContent = "Voice input idle.";
    const startBtn = document.getElementById("aiVoiceStartBtn");
    const stopBtn = document.getElementById("aiVoiceStopBtn");
    if (startBtn) startBtn.disabled = false;
    if (stopBtn) stopBtn.disabled = true;
    aiVoiceChunks = [];

    await generateAiDraft(threadId, "neutral", null);
}

function getAiReplyExtraEl() {
    return document.getElementById("aiReplyExtra") || document.getElementById("aiExtraContext");
}

function setAiVoiceStatus(text) {
    const el = document.getElementById("aiVoiceStatus");
    if (el) el.textContent = text || "";
}

function setAiRegenerateBusy(isBusy) {
    const btn = document.getElementById("aiReplyRegenerateBtn");
    if (!btn) return;
    btn.disabled = !!isBusy;
    btn.textContent = isBusy ? "Regenerating..." : "Regenerate";
}

async function startAiVoiceCapture() {
    if (!window.isSecureContext) {
        setAiVoiceStatus("Mic requires a secure context (HTTPS).");
        return;
    }
    if (aiVoiceRecorder && aiVoiceRecorder.state === "recording") {
        return;
    }
    if (aiSpeechRecognition) {
        return;
    }

    try {
        const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (SR) {
            aiSpeechTranscript = "";
            aiSpeechRecognition = new SR();
            aiSpeechRecognition.lang = "en-US";
            aiSpeechRecognition.interimResults = true;
            aiSpeechRecognition.continuous = true;
            aiSpeechRecognition.onresult = (event) => {
                let out = "";
                for (let i = 0; i < event.results.length; i++) {
                    out += (event.results[i][0]?.transcript || "") + " ";
                }
                aiSpeechTranscript = out.trim();
            };
            aiSpeechRecognition.onerror = (event) => {
                setAiVoiceStatus(`Speech recognition error: ${event.error || "unknown"}`);
            };
            aiSpeechRecognition.onend = async () => {
                const transcript = (aiSpeechTranscript || "").trim();
                aiSpeechRecognition = null;
                const startBtn = document.getElementById("aiVoiceStartBtn");
                const stopBtn = document.getElementById("aiVoiceStopBtn");
                if (startBtn) startBtn.disabled = false;
                if (stopBtn) stopBtn.disabled = true;

                const weakTranscript = (
                    !transcript ||
                    transcript.length < 6 ||
                    /^(you|yeah|yep|uh|um|hmm|thank you|thanks)[.!?\s]*$/i.test(transcript)
                );
                if (weakTranscript) {
                    setAiVoiceStatus("Low-confidence transcript. Please speak closer to mic and try again.");
                    return;
                }

                const extraEl = getAiReplyExtraEl();
                if (extraEl) {
                    const cur = String(extraEl.value || "").trim();
                    extraEl.value = cur ? `${cur}\n${transcript}` : transcript;
                }
                setAiVoiceStatus("Voice inserted. Regenerating draft...");
                if (currentAiThreadId) {
                    await regenerateAiDraftFromModal();
                }
            };

            aiSpeechRecognition.start();
            setAiVoiceStatus("Listening... click Stop & Insert when done.");
            const startBtn = document.getElementById("aiVoiceStartBtn");
            const stopBtn = document.getElementById("aiVoiceStopBtn");
            if (startBtn) startBtn.disabled = true;
            if (stopBtn) stopBtn.disabled = false;
            return;
        }

        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
            alert("Voice capture is not supported in this browser/device.");
            return;
        }

        // Fallback path: record audio and transcribe on backend.
        aiVoiceStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
                channelCount: 1,
            }
        });
        aiVoiceChunks = [];
        const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
            ? "audio/webm;codecs=opus"
            : (MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "");
        aiVoiceRecorder = mime ? new MediaRecorder(aiVoiceStream, { mimeType: mime }) : new MediaRecorder(aiVoiceStream);
        aiVoiceRecorder.ondataavailable = (e) => {
            if (e.data && e.data.size > 0) aiVoiceChunks.push(e.data);
        };
        aiVoiceRecorder.start();
        setAiVoiceStatus("Recording... click Stop & Insert when done.");
        const startBtn = document.getElementById("aiVoiceStartBtn");
        const stopBtn = document.getElementById("aiVoiceStopBtn");
        if (startBtn) startBtn.disabled = true;
        if (stopBtn) stopBtn.disabled = false;
    } catch (e) {
        const name = e && e.name ? e.name : "Error";
        const msg = e && e.message ? e.message : "";
        setAiVoiceStatus(`Mic failed: ${name}${msg ? ` - ${msg}` : ""}`);
    }
}

async function stopAiVoiceCaptureAndTranscribe() {
    const startBtn = document.getElementById("aiVoiceStartBtn");
    const stopBtn = document.getElementById("aiVoiceStopBtn");
    if (stopBtn) stopBtn.disabled = true;
    setAiVoiceStatus("Processing voice input...");

    if (aiSpeechRecognition) {
        try {
            aiSpeechRecognition.stop();
            return;
        } catch {
            // Continue to fallback/cleanup path below.
        }
    }

    if (!aiVoiceRecorder) {
        setAiVoiceStatus("No recording to process.");
        if (startBtn) startBtn.disabled = false;
        return;
    }

    if (aiVoiceRecorder.state === "recording") {
        await new Promise((resolve) => {
            aiVoiceRecorder.onstop = resolve;
            aiVoiceRecorder.stop();
        });
    }

    try {
        if (aiVoiceStream) {
            aiVoiceStream.getTracks().forEach(t => t.stop());
            aiVoiceStream = null;
        }
        const blob = new Blob(aiVoiceChunks, { type: (aiVoiceChunks[0] && aiVoiceChunks[0].type) || "audio/webm" });
        if (!blob || blob.size === 0) {
            setAiVoiceStatus("No audio captured.");
            if (startBtn) startBtn.disabled = false;
            return;
        }
        // Too-short clips often produce junk transcripts like "you".
        if (blob.size < 12000) {
            setAiVoiceStatus("Recording too short. Please speak for at least 2-3 seconds.");
            if (startBtn) startBtn.disabled = false;
            return;
        }

        const form = new FormData();
        form.append("file", blob, "voice-note.webm");
        const r = await apiFetch("/tickets/transcribe-audio", { method: "POST", body: form });
        const t = await r.text();
        if (!r.ok) {
            setAiVoiceStatus("Transcription failed.");
            alert(`Transcription failed (${r.status}):\n\n${t}`);
            if (startBtn) startBtn.disabled = false;
            return;
        }
        let j = null;
        try { j = JSON.parse(t); } catch { j = null; }
        const transcript = ((j && j.text) ? String(j.text) : "").trim();
        const weakTranscript = (
            !transcript ||
            transcript.length < 6 ||
            /^(you|yeah|yep|uh|um|hmm|thank you|thanks)[.!?\s]*$/i.test(transcript)
        );
        if (weakTranscript) {
            setAiVoiceStatus("Low-confidence transcript. Please speak closer to mic and try again.");
            if (startBtn) startBtn.disabled = false;
            return;
        }
        const extraEl = getAiReplyExtraEl();
        if (extraEl && transcript) {
            const cur = String(extraEl.value || "").trim();
            extraEl.value = cur ? `${cur}\n${transcript}` : transcript;
        }
        if (transcript) {
            setAiVoiceStatus("Voice inserted. Regenerating draft...");
            if (currentAiThreadId) {
                await regenerateAiDraftFromModal();
            }
        } else {
            setAiVoiceStatus("No speech detected.");
        }
    } finally {
        aiVoiceChunks = [];
        aiVoiceRecorder = null;
        if (startBtn) startBtn.disabled = false;
    }
}

async function generateAiDraft(threadId, tone, extraContext) {
    const metaEl = document.getElementById("aiReplyMeta");
    const r = await apiFetch(`/tickets/${threadId}/draft-ai-reply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tone: tone || "neutral", extra_context: extraContext || null }),
    });
    const text = await r.text();
    if (!r.ok) {
        document.getElementById("aiReplyBody").value = text;
        if (metaEl) metaEl.textContent = "";
        return;
    }
    const j = JSON.parse(text);
    document.getElementById("aiReplySubject").value = j.subject || "";
    document.getElementById("aiReplyBody").value = j.body || "";
    if (metaEl) {
        const role = j.meta?.role ? `Sender role: ${j.meta.role}` : "";
        const cat = j.meta?.ai_category ? `AI category: ${j.meta.ai_category}` : "";
        const urg = (typeof j.meta?.ai_urgency === "number") ? `Urgency: ${j.meta.ai_urgency}/5` : "";
        const conf = (typeof j.meta?.ai_confidence === "number") ? `Confidence: ${j.meta.ai_confidence}%` : "";
        metaEl.textContent = [role, cat, urg, conf].filter(Boolean).join("  •  ");
    }
}

async function regenerateAiDraftFromModal() {
    if (!currentAiThreadId) {
        alert("No active thread selected for regeneration.");
        return;
    }
    const extraEl = getAiReplyExtraEl();
    const extra = extraEl ? (extraEl.value || "").trim() : "";
    document.getElementById("aiReplyBody").value = "Regenerating...";
    setAiRegenerateBusy(true);
    try {
        await generateAiDraft(currentAiThreadId, "neutral", extra || null);
    } finally {
        setAiRegenerateBusy(false);
    }
}

async function regenerateAiDraft() {
    await regenerateAiDraftFromModal();
}

function closeAiReplyModal() {
    const modal = document.getElementById("aiReplyModal");
    if (modal) modal.classList.add("hidden");
    if (aiSpeechRecognition) {
        try { aiSpeechRecognition.stop(); } catch { }
        aiSpeechRecognition = null;
    }
    aiSpeechTranscript = "";
    if (aiVoiceRecorder && aiVoiceRecorder.state === "recording") {
        try { aiVoiceRecorder.stop(); } catch { }
    }
    if (aiVoiceStream) {
        try { aiVoiceStream.getTracks().forEach(t => t.stop()); } catch { }
        aiVoiceStream = null;
    }
    aiVoiceRecorder = null;
    aiVoiceChunks = [];
    currentAiThreadId = null;
}

function useAiDraftInQuickReply() {
    const subj = (document.getElementById("aiReplySubject").value || "").trim();
    const body = (document.getElementById("aiReplyBody").value || "").trim();
    const tid = currentAiThreadId;
    closeAiReplyModal();
    if (!tid) return;
    // Open Quick Reply and inject the draft.
    openAckModal(tid).then(() => {
        document.getElementById("ackSubject").value = subj;
        document.getElementById("ackBody").value = body;
    });
}

function closeAckModal() {
    document.getElementById("ackModal").classList.add("hidden");
    currentAckThreadId = null;
}

async function sendAckFromModal() {
    if (!currentAckThreadId) return;
    const subject = document.getElementById("ackSubject").value;
    const body = document.getElementById("ackBody").value;
    const cc = (document.getElementById("ackCc")?.value || "").trim();
    const bcc = (document.getElementById("ackBcc")?.value || "").trim();
    const filesEl = document.getElementById("ackAttachments");

    const btn = document.getElementById("sendAckBtn");
    btn.disabled = true;
    btn.textContent = "Sending...";

    try {
        const form = new FormData();
        form.append("subject", subject || "");
        form.append("body", body || "");
        form.append("cc", cc);
        form.append("bcc", bcc);
        form.append("mark_as_responded", "true");
        if (filesEl && filesEl.files) {
            for (const f of Array.from(filesEl.files)) {
                form.append("attachments", f, f.name);
            }
        }

        const r = await apiFetch(`/tickets/${currentAckThreadId}/send-reply`, {
            method: "POST",
            body: form
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

function openBlacklistModal() {
    const m = document.getElementById("blacklistModal");
    if (!m) return;
    m.classList.remove("hidden");
    refreshBlacklist();
}

function closeBlacklistModal() {
    const m = document.getElementById("blacklistModal");
    if (!m) return;
    m.classList.add("hidden");
}

async function refreshBlacklist() {
    const list = document.getElementById("blacklistList");
    if (!list) return;
    list.innerHTML = `<div class="muted small">Loading...</div>`;
    const r = await apiFetch("/blacklist", { method: "GET" });
    const t = await r.text();
    if (!r.ok) {
        list.innerHTML = `<div class="muted small">Failed to load: ${escapeHtml(t)}</div>`;
        return;
    }
    let items = [];
    try { items = JSON.parse(t); } catch { items = []; }
    if (!Array.isArray(items) || items.length === 0) {
        list.innerHTML = `<div class="muted small">No blacklisted senders.</div>`;
        return;
    }
    list.innerHTML = "";
    for (const b of items) {
        const email = (b.email || "").trim();
        const row = document.createElement("div");
        row.className = "row space";
        row.style.padding = "10px 0";
        row.style.borderBottom = "1px solid var(--border)";
        row.innerHTML = `
            <div class="small"><b>${escapeHtml(email)}</b></div>
            <button class="btn" onclick="unblacklistSender('${escapeHtml(email)}')">Remove</button>
        `;
        list.appendChild(row);
    }
}

async function unblacklistSender(email) {
    if (!email) return;
    const r = await apiFetch(`/blacklist?email=${encodeURIComponent(email)}`, { method: "DELETE" });
    const t = await r.text();
    if (!r.ok) {
        alert(`Unblacklist failed (${r.status}):\n\n${t}`);
        return;
    }
    await refreshBlacklist();
    await loadTickets();
}

function setAuthLayout(isAuthenticated) {
    const loginScreen = document.getElementById("loginScreen");
    const appShell = document.getElementById("appShell");
    if (loginScreen) loginScreen.classList.toggle("hidden", !!isAuthenticated);
    if (appShell) {
        if (isAuthenticated) appShell.removeAttribute("hidden");
        else appShell.setAttribute("hidden", "hidden");
    }
    document.body.classList.toggle("auth-locked", !isAuthenticated);
}

function setLoginError(message) {
    const err = document.getElementById("loginError");
    if (!err) return;
    const text = String(message || "").trim();
    err.textContent = text;
    err.style.display = text ? "block" : "none";
}

function resetLoginRecaptcha() {
    if (!recaptchaEnabled || loginRecaptchaWidgetId === null || !window.grecaptcha) return;
    window.grecaptcha.reset(loginRecaptchaWidgetId);
}

async function waitForRecaptchaLibrary(timeoutMs = 10000) {
    if (!recaptchaEnabled) return true;
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
        if (window.grecaptcha && typeof window.grecaptcha.render === "function") {
            return true;
        }
        await new Promise((resolve) => setTimeout(resolve, 120));
    }
    return false;
}

async function ensureLoginRecaptcha() {
    const slot = document.getElementById("loginRecaptcha");
    if (!recaptchaEnabled || !slot) return true;
    if (loginRecaptchaWidgetId !== null) return true;

    const ready = await waitForRecaptchaLibrary();
    if (!ready) {
        setLoginError("reCAPTCHA could not load. Please refresh and try again.");
        return false;
    }

    loginRecaptchaWidgetId = window.grecaptcha.render("loginRecaptcha", {
        sitekey: APP_CONFIG.recaptcha_site_key,
        theme: "light",
    });
    return true;
}

async function getLoginRecaptchaToken() {
    if (!recaptchaEnabled) return null;
    const ready = await ensureLoginRecaptcha();
    if (!ready || loginRecaptchaWidgetId === null || !window.grecaptcha) return null;
    const token = String(window.grecaptcha.getResponse(loginRecaptchaWidgetId) || "").trim();
    if (!token) {
        setLoginError("Please complete the reCAPTCHA check.");
        return null;
    }
    return token;
}

async function extractErrorMessage(response) {
    const raw = await response.text();
    if (!raw) return response.statusText || "Request failed";
    try {
        const parsed = JSON.parse(raw);
        if (typeof parsed.detail === "string" && parsed.detail.trim()) return parsed.detail.trim();
    } catch {
        // Fall through to raw response text.
    }
    return raw;
}

function showLoginModal() {
    setAuthLayout(false);
    const password = document.getElementById("loginPassword");
    if (password) password.value = "";
    setLoginError("");
    setTimeout(() => {
        const email = document.getElementById("loginEmail");
        if (email) email.focus();
    }, 50);
    ensureLoginRecaptcha().catch(() => {
        setLoginError("reCAPTCHA could not load. Please refresh and try again.");
    });
}

function hideLoginModal() {
    setAuthLayout(true);
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
    hideLoginModal();
    await loadUsersCache();

    // Legacy badge
    const badge = document.getElementById("userBadge");
    if (badge) badge.textContent = `Signed in as: ${currentUser.name} (${currentUser.role})`;

    // Good UI pill
    const authText = document.getElementById("authText");
    if (authText) authText.textContent = `Signed in as ${currentUser.name} (${currentUser.role})`;
    const accountName = document.getElementById("accountBarUserName");
    if (accountName) accountName.textContent = `${currentUser.name} (${currentUser.role})`;
    const accountAvatar = document.getElementById("accountBarAvatar");
    if (accountAvatar) {
        accountAvatar.src = currentUser.avatar_url || "/static/logo.png";
    }
    const authDot = document.getElementById("authDot");
    if (authDot) {
        authDot.classList.add("green");
    }
    const systemBtn = document.getElementById("btnSystemUsers");
    if (systemBtn) systemBtn.style.display = (String(currentUser.role || "").toUpperCase() === "ADMIN") ? "flex" : "none";
    const portalSystemTile = document.getElementById("portalSystemTile");
    if (portalSystemTile) portalSystemTile.classList.toggle("hidden", String(currentUser.role || "").toUpperCase() !== "ADMIN");

    if (currentUser.must_change_password) {
        setTimeout(() => openPasswordModal(), 100);
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
    const btn = document.getElementById("btnLogin");
    const btnText = btn ? btn.textContent : "";
    setLoginError("");

    const recaptchaToken = await getLoginRecaptchaToken();
    if (recaptchaEnabled && !recaptchaToken) return;

    if (btn) {
        btn.disabled = true;
        btn.textContent = "Signing in...";
    }

    try {
        const r = await fetch("/user-auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email, password, recaptcha_token: recaptchaToken }),
        });
        if (!r.ok) {
            setLoginError(await extractErrorMessage(r));
            resetLoginRecaptcha();
            return;
        }
        const j = await r.json();
        authToken = j.access_token;
        localStorage.setItem("agent_auth_token", authToken);
        hideLoginModal();
        await ensureAuthenticated();
        await refreshGoogleStatus();
        // Autopilot feature removed.
        await loadTickets();
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = btnText || "Sign in";
        }
    }
}

function logout() {
    authToken = "";
    currentUser = null;
    localStorage.removeItem("agent_auth_token");

    const badge = document.getElementById("userBadge");
    if (badge) badge.textContent = "";

    const authText = document.getElementById("authText");
    if (authText) authText.textContent = "Not signed in";
    const accountName = document.getElementById("accountBarUserName");
    if (accountName) accountName.textContent = "Not signed in";
    const accountAvatar = document.getElementById("accountBarAvatar");
    if (accountAvatar) accountAvatar.src = "/static/logo.png";
    closeAccountMenu();
    const systemBtn = document.getElementById("btnSystemUsers");
    if (systemBtn) systemBtn.style.display = "none";
    const portalSystemTile = document.getElementById("portalSystemTile");
    if (portalSystemTile) portalSystemTile.classList.add("hidden");
    const authDot = document.getElementById("authDot");
    if (authDot) {
        authDot.classList.remove("green");
        authDot.classList.remove("red");
        authDot.classList.remove("yellow");
    }

    resetLoginRecaptcha();
    showLoginModal();
}

window.addEventListener("load", async () => {
    // Footer year
    const yearEl = document.getElementById("year");
    if (yearEl) yearEl.textContent = String(new Date().getFullYear());

    loadSettings();
    document.getElementById("lastSync").textContent = new Date().toLocaleString();
    const ok = await ensureAuthenticated();
    if (!ok) return;

    document.addEventListener("click", (ev) => {
        const trigger = document.getElementById("accountMenuTrigger");
        const dd = document.getElementById("accountMenuDropdown");
        if (!dd || !trigger) return;
        if (dd.contains(ev.target) || trigger.contains(ev.target)) return;
        dd.classList.remove("show");
    });

    await refreshGoogleStatus();
    await initMailboxes();

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

    // Autopilot feature removed.

    // Wire filters
    const seg = document.getElementById("statusSeg");
    if (seg) {
        seg.querySelectorAll("button[data-tab]").forEach(btn => {
            btn.addEventListener("click", () => {
                const tab = btn.dataset.tab || "awaiting_reply";
                setTab(tab);
            });
        });
    }

    const searchEl = document.getElementById("searchBox");
    if (searchEl) {
        let tmr = null;
        searchEl.addEventListener("input", () => {
            currentSearch = searchEl.value || "";
            if (tmr) clearTimeout(tmr);
            tmr = setTimeout(() => loadTickets(), 250);
        });
    }
    const rentSearch = document.getElementById("rentSearchBox");
    if (rentSearch) {
        let tmr = null;
        rentSearch.addEventListener("input", () => {
            if (tmr) clearTimeout(tmr);
            tmr = setTimeout(() => {
                if (currentDashboardTab === "rent") {
                    currentRentPage = 1;
                    loadActiveRentView();
                }
            }, 250);
        });
    }
    ["rentStatusFilter", "rentFrequencyFilter"].forEach((id) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.addEventListener("change", () => {
            if (currentDashboardTab === "rent") {
                currentRentPage = 1;
                loadActiveRentView();
            }
        });
    });
    const propertySearch = document.getElementById("propertySearchBox");
    if (propertySearch) {
        let tmr = null;
        propertySearch.addEventListener("input", () => {
            if (tmr) clearTimeout(tmr);
            tmr = setTimeout(() => {
                if (currentDashboardTab === "properties") {
                    currentPropertiesPage = 1;
                    loadProperties();
                }
            }, 250);
        });
    }
    const newPropertyAddress = document.getElementById("newPropertyAddress");
    if (newPropertyAddress) {
        newPropertyAddress.addEventListener("input", scheduleAddressSuggestionSearch);
        newPropertyAddress.addEventListener("change", autocompleteNewPropertyFields);
        newPropertyAddress.addEventListener("blur", autocompleteNewPropertyFields);
    }
    const compliancePropertySearch = document.getElementById("compliancePropertySearch");
    if (compliancePropertySearch) {
        compliancePropertySearch.addEventListener("input", updateCompliancePropertySelection);
        compliancePropertySearch.addEventListener("change", updateCompliancePropertySelection);
    }
    ["complianceStateFilter", "complianceTypeFilter"].forEach((id) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.addEventListener("change", () => {
            if (currentDashboardTab === "compliance") {
                currentCompliancePage = 1;
                loadComplianceDashboard();
            }
        });
    });
    const complianceSearch = document.getElementById("complianceSearchBox");
    if (complianceSearch) {
        let tmr = null;
        complianceSearch.addEventListener("input", () => {
            if (tmr) clearTimeout(tmr);
            tmr = setTimeout(() => {
                if (currentDashboardTab === "compliance") {
                    currentCompliancePage = 1;
                    loadComplianceDashboard();
                }
            }, 250);
        });
    }
    const coverageSearch = document.getElementById("coverageSearchBox");
    if (coverageSearch) {
        let tmr = null;
        coverageSearch.addEventListener("input", () => {
            if (tmr) clearTimeout(tmr);
            tmr = setTimeout(() => {
                if (currentDashboardTab === "coverage") {
                    currentCoveragePage = 1;
                    loadComplianceCoverage();
                }
            }, 250);
        });
    }
    const coverageIncludeCurrent = document.getElementById("coverageIncludeCurrent");
    if (coverageIncludeCurrent) {
        coverageIncludeCurrent.addEventListener("change", () => {
            if (currentDashboardTab === "coverage") {
                currentCoveragePage = 1;
                loadComplianceCoverage();
            }
        });
    }
    const editCompleted = document.getElementById("editComplianceCompletedAt");
    if (editCompleted) {
        editCompleted.addEventListener("change", updateComplianceEditNextDuePreview);
    }
    const editStatus = document.getElementById("editComplianceStatus");
    if (editStatus) {
        editStatus.addEventListener("change", () => {
            if (editStatus.value === "COMPLETED" && !document.getElementById("editComplianceCompletedAt")?.value) {
                const d = new Date();
                const done = document.getElementById("editComplianceCompletedAt");
                if (done) done.value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
            }
            updateComplianceEditNextDuePreview();
        });
    }

    // Assignment / category filters removed.

    applySidebarState();
    await refreshPropertyOptions();
    switchDashboardTab("portal");
    updateSyncContextUI();

    // Set default tab (will load tickets).
    setTab(currentTab);
});
