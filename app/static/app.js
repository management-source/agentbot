let currentTab = "awaiting_reply";
let currentAckThreadId = null;
let currentAiThreadId = null;
let currentViewerThreadId = null;
let currentViewerTicket = null;
let aiVoiceRecorder = null;
let aiVoiceStream = null;
let aiVoiceChunks = [];
let aiSpeechRecognition = null;
let aiSpeechTranscript = "";
let currentDashboardTab = "portal";
let currentChecklistRun = null;
let checklistView = "start";

function checklistEscape(value) { return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function openChecklistView(view) {
    checklistView = view === "reports" ? "reports" : "start"; switchDashboardTab("checklist");
    document.getElementById("checklistStartView")?.classList.toggle("hidden", checklistView !== "start");
    document.getElementById("checklistReportsView")?.classList.toggle("hidden", checklistView !== "reports");
    document.getElementById("checklistEditorView")?.classList.add("hidden");
    document.querySelectorAll("[data-checklist-view]").forEach(x => x.classList.toggle("active", x.dataset.checklistView === checklistView)); loadChecklistRuns();
}
async function checklistJson(url, options = {}) {
    const response = await apiFetch(url, {...options, headers: {"Content-Type":"application/json", ...(options.headers || {})}}), data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Checklist request failed."); return data;
}
async function createChecklistRun() {
    const error=document.getElementById("checklistError");
    try { const applicant_name=document.getElementById("checklistApplicant").value.trim(), property_address=document.getElementById("checklistProperty").value.trim(), received=document.getElementById("checklistReceived").value;
        if(!applicant_name||!property_address) throw new Error("Applicant and property address are required.");
        const run=await checklistJson("/checklists/runs",{method:"POST",body:JSON.stringify({process_key:"application_screening",applicant_name,property_address,application_received:received?`${received}T00:00:00`:null})}); if(error)error.style.display="none"; openChecklistRun(run.id);
    } catch(e){if(error){error.textContent=e.message;error.style.display="block";}}
}
async function loadChecklistRuns() {
    if(currentDashboardTab!=="checklist")return;
    try { const [active,reports]=await Promise.all([checklistJson("/checklists/runs?status=IN_PROGRESS"),checklistJson("/checklists/runs?status=COMPLETED")]); const a=document.getElementById("checklistActiveList"),b=document.getElementById("checklistReportList");
        if(a)a.innerHTML=active.length?active.map(r=>`<div class="card" style="margin-top:10px"><strong>${checklistEscape(r.applicant_name)}</strong><div class="small muted">${checklistEscape(r.property_address)} · ${r.progress_percent}% complete</div><progress value="${r.progress_percent}" max="100" style="width:100%"></progress><button class="btn" onclick="openChecklistRun(${r.id})">Continue</button></div>`).join(""):"No tasks in progress.";
        if(b)b.innerHTML=reports.length?reports.map(r=>`<div class="card" style="margin-top:10px"><strong>${checklistEscape(r.applicant_name)}</strong><div class="small muted">${checklistEscape(r.property_address)} · Completed ${r.completed_at?new Date(r.completed_at).toLocaleString():""}</div><button class="btn" onclick="openChecklistRun(${r.id})">Display full report</button></div>`).join(""):"No completed reports yet.";
        active.forEach((r,index)=>{const card=a?.children[index];if(!card)return;const actions=document.createElement("div");actions.className="checklist-list-actions";actions.innerHTML=`<button class="btn" onclick="openChecklistRun(${r.id})">Continue</button><button class="btn danger" onclick="deleteChecklistRun(${r.id},false)">Discard</button>`;card.querySelector("button")?.remove();card.append(actions);});
        reports.forEach((r,index)=>{const card=b?.children[index];if(!card)return;const signed=r.approval_status==="APPROVED",badge=document.createElement("span");badge.className=`checklist-sign-state ${signed?"signed":"unsigned"}`;badge.textContent=signed?"Signed":"Not signed";card.querySelector("strong")?.after(document.createTextNode(" "),badge);const actions=document.createElement("div");actions.className="checklist-list-actions";const open=card.querySelector("button");if(open)actions.append(open);const del=document.createElement("button");del.className="btn danger";del.textContent="Delete report";del.onclick=()=>deleteChecklistRun(r.id,true);actions.append(del);card.append(actions);});
    }catch(e){const el=document.getElementById(checklistView==="reports"?"checklistReportList":"checklistActiveList");if(el)el.textContent=e.message;}
}
async function deleteChecklistRun(id,isReport){if(!confirm(isReport?"Permanently delete this completed report? This cannot be undone.":"Discard this application in progress? This cannot be undone."))return;try{const editorOpen=currentChecklistRun?.id===id&&!document.getElementById("checklistEditorView")?.classList.contains("hidden");await checklistJson(`/checklists/runs/${id}`,{method:"DELETE"});if(editorOpen){currentChecklistRun=null;openChecklistView(isReport?"reports":"start");}else await loadChecklistRuns();}catch(e){alert(e.message);}}
async function openChecklistRun(id){try{if(!assignableUsers.length)await loadAssignableUsers();currentChecklistRun=await checklistJson(`/checklists/runs/${id}`);renderChecklistEditor();}catch(e){alert(e.message);}}
function checklistStatusClass(status){return status==="Verified / Positive"?"status-positive":status==="Concern"?"status-concern":status==="Not Applicable"?"status-na":"status-pending";}
function checklistStaffOptions(selected){
    const staff=[...assignableUsers];
    if(selected&&!staff.some(u=>(u.name||u.email)===selected))staff.unshift({name:selected,email:""});
    return `<option value="">Select staff member</option>`+staff.map(u=>{const value=u.name||u.email,label=u.name&&u.email?`${u.name} (${u.email})`:value;return `<option value="${checklistEscape(value)}" ${value===selected?"selected":""}>${checklistEscape(label)}</option>`;}).join("");
}
function updateChecklistProgressPreview(){
    const statuses=[...document.querySelectorAll('#checklistEditorView [data-cf="status"]')].map(x=>x.value),percent=Math.round(statuses.filter(x=>x!=="Pending").length*100/statuses.length)||0;
    const fill=document.getElementById("checklistProgressFill"),label=document.getElementById("checklistProgressLabel");if(fill)fill.style.width=`${percent}%`;if(label)label.textContent=`${percent}%`;
}
function updateChecklistStatusStyle(select){const cls=checklistStatusClass(select.value),card=select.closest(".checklist-check");[select,card].forEach(el=>{if(!el)return;el.classList.remove("status-positive","status-pending","status-concern","status-na");el.classList.add(cls);});}
async function downloadChecklistPdf(){
    try{const response=await apiFetch(`/checklists/runs/${currentChecklistRun.id}/pdf`);if(!response.ok)throw new Error(await extractErrorMessage(response));const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;link.download=`application-screening-${currentChecklistRun.applicant_name}.pdf`;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);}catch(e){alert(`PDF export failed: ${e.message}`);}
}
async function requestChecklistApproval(){try{currentChecklistRun=await checklistJson(`/checklists/runs/${currentChecklistRun.id}/request-approval`,{method:"POST"});renderChecklistEditor();alert("Approval request emailed to admin@donspremier.com.au for Jessica.");}catch(e){alert(e.message);}}
async function approveChecklistWithSignature(){try{currentChecklistRun=await checklistJson(`/checklists/runs/${currentChecklistRun.id}/approve`,{method:"POST",body:JSON.stringify({signature_data:null})});renderChecklistEditor();alert(currentChecklistRun.confirmation_email_sent===false?"Report approved with Jessica's saved signature, but the confirmation email could not be sent.":"Report approved with Jessica's saved signature. Confirmation emailed to Jessica.");}catch(e){alert(e.message);}}
function renderChecklistEditor(){
    const r=currentChecklistRun,p=r.payload,readonly=r.status==="COMPLETED",editor=document.getElementById("checklistEditorView"),opts=(v,s)=>v.map(x=>`<option ${x===s?"selected":""}>${x}</option>`).join("");
    document.getElementById("checklistStartView")?.classList.add("hidden");document.getElementById("checklistReportsView")?.classList.add("hidden");editor.classList.remove("hidden");
    editor.innerHTML=`<div class="checklist-report-card"><button class="btn" onclick="openChecklistView('${readonly?"reports":"start"}')">← Back</button>${readonly?` <button class="btn primary" onclick="downloadChecklistPdf()">Export PDF</button>`:""}<h2>Property Application Screening Checklist</h2><div class="checklist-summary"><div><b>Applicant</b><br>${checklistEscape(r.applicant_name)}</div><div><b>Property</b><br>${checklistEscape(r.property_address)}</div><div><b>All checks completed by</b><br>Jessica Gale — Property Manager</div><div><b>Template</b><br>Application Screening</div></div><div class="checklist-progress-wrap"><div class="checklist-progress-head"><span>Application progress</span><span id="checklistProgressLabel">${r.progress_percent}%</span></div><div class="checklist-progress"><div class="checklist-progress-fill" id="checklistProgressFill"></div></div></div><div class="checklist-meta-grid"><div class="checklist-field"><label>Overall status</label><select data-checklist-root="overall_status" class="checklist-status-select ${checklistStatusClass(p.overall_status==='Recommended'?'Verified / Positive':p.overall_status==='Not Recommended'?'Concern':'Pending')}" ${readonly?"disabled":""}>${opts(["Recommended","Pending","Not Recommended"],p.overall_status)}</select></div></div><div class="checklist-checks">${p.checks.map((c,i)=>`<section class="checklist-check ${checklistStatusClass(c.status)}"><div class="checklist-check-head"><h4>${i+1}. ${checklistEscape(c.name)}</h4></div><div class="checklist-check-grid"><div class="checklist-field"><label>Status</label><select class="checklist-status-select ${checklistStatusClass(c.status)}" data-ci="${i}" data-cf="status" ${readonly?"disabled":""}>${opts(["Verified / Positive","Pending","Concern","Not Applicable"],c.status)}</select></div><div class="checklist-field"><label>Date checked</label><input type="date" data-ci="${i}" data-cf="date_checked" value="${checklistEscape(c.date_checked)}" ${readonly?"disabled":""}></div><div class="checklist-field description"><label>Additional notes</label><textarea placeholder="Add any context for this check…" data-ci="${i}" data-cf="notes" ${readonly?"disabled":""}>${checklistEscape(c.notes)}</textarea></div></div></section>`).join("")}</div><div class="checklist-assessment-grid"><div class="checklist-field"><label>Result / finding</label><textarea placeholder="Record the overall screening outcome…" data-checklist-root="default_result" ${readonly?"disabled":""}>${checklistEscape(p.default_result)}</textarea></div><div class="checklist-field"><label>Evidence / reference</label><textarea placeholder="Add the supporting documents or references…" data-checklist-root="default_evidence" ${readonly?"disabled":""}>${checklistEscape(p.default_evidence)}</textarea></div><div class="checklist-field"><label>Key positive points</label><textarea placeholder="Summarise the strongest findings…" data-checklist-root="key_positive_points" ${readonly?"disabled":""}>${checklistEscape(p.key_positive_points)}</textarea></div><div class="checklist-field"><label>Outstanding items</label><textarea placeholder="List anything still requiring action…" data-checklist-root="outstanding_items" ${readonly?"disabled":""}>${checklistEscape(p.outstanding_items)}</textarea></div><div class="checklist-field wide"><label>Property owner update / comment</label><textarea placeholder="Record the owner's assessment: proceed, hold, decline, or request more information…" data-checklist-root="owner_comment" ${readonly?"disabled":""}>${checklistEscape(p.owner_comment)}</textarea></div></div>${readonly?"":`<div class="checklist-actions"><button class="btn" onclick="saveChecklist(false)">Save progress</button><button class="btn primary" onclick="saveChecklist(true)">Complete &amp; file report</button></div>`}<div class="error" id="checklistEditorError" style="display:none"></div></div>`;
    requestAnimationFrame(()=>{document.getElementById("checklistProgressFill").style.width=`${r.progress_percent}%`;});
    editor.querySelectorAll('[data-cf="date_checked"]').forEach(input=>input.closest(".checklist-field")?.remove());
    editor.querySelector('[data-checklist-root="default_result"]')?.closest(".checklist-field")?.remove();
    editor.querySelectorAll(".checklist-check").forEach((card,index)=>{const notes=card.querySelector('[data-cf="notes"]')?.closest(".checklist-field"),field=document.createElement("div");field.className="checklist-field description";field.innerHTML=`<label>Result / finding</label><textarea placeholder="Record the outcome of this check…" data-ci="${index}" data-cf="result" ${readonly?"disabled":""}>${checklistEscape(p.checks[index]?.result||"")}</textarea>`;notes?.before(field);});
    const meta=editor.querySelector(".checklist-meta-grid"),mainStaff=document.createElement("div");mainStaff.className="checklist-field";mainStaff.innerHTML=`<label>Main screened by</label><select data-checklist-root="screened_by" ${readonly?"disabled":""}>${checklistStaffOptions(p.screened_by)}</select>`;meta?.prepend(mainStaff);
    editor.querySelectorAll(".checklist-check").forEach((card,index)=>{const result=card.querySelector('[data-cf="result"]')?.closest(".checklist-field"),field=document.createElement("div");field.className="checklist-field";field.innerHTML=`<label>Checked by</label><select data-ci="${index}" data-cf="checked_by" ${readonly?"disabled":""}>${checklistStaffOptions(p.checks[index]?.checked_by)}</select>`;result?.before(field);});
    const summary=[...editor.querySelectorAll(".checklist-summary > div")].find(x=>x.textContent.includes("All checks completed by"));if(summary)summary.innerHTML="<b>All checks supervised by</b><br>Jessica Gale — Property Manager";
    const approval=document.createElement("section"),approved=p.approval_status==="APPROVED";approval.className="checklist-approval";approval.innerHTML=`<h3>Jessica's approval</h3><p class="small muted">${approved?`Approved ${checklistEscape(p.approved_at||"")}`:p.approval_status==="REQUESTED"?`Approval requested ${checklistEscape(p.approval_requested_at||"")}. Upload Jessica's signature to approve.`:"Send an email to admin@donspremier.com.au requesting Jessica's signature and approval."}</p>${p.signature_data?`<img class="checklist-signature-preview" src="${checklistEscape(p.signature_data)}" alt="Jessica Gale signature">`:""}${approved?"":`${p.approval_status!=="REQUESTED"?`<button class="btn" onclick="requestChecklistApproval()">Request approval by email</button>`:""}<div class="checklist-field" style="margin-top:12px"><label>Jessica's signature image</label><input id="checklistSignatureFile" type="file" accept="image/png,image/jpeg,image/webp"></div><button class="btn primary" onclick="approveChecklistWithSignature()">Sign and approve</button>`}`;editor.querySelector(".checklist-actions")?.after(approval)||editor.querySelector(".checklist-report-card")?.append(approval);if(!readonly)approval.remove();
    approval.querySelector("#checklistSignatureFile")?.closest(".checklist-field")?.remove();
    const approveButton=[...approval.querySelectorAll("button")].find(x=>x.textContent.includes("Sign and approve"));if(approveButton)approveButton.textContent="Sign and approve with Jessica's saved signature";
    if(p.approval_status==="REQUESTED"){const note=approval.querySelector("p");if(note)note.textContent=`Approval requested ${p.approval_requested_at||""}. Approve using Jessica's saved signature.`;}
    if(approved&&!approval.querySelector("img")){const image=document.createElement("img");image.className="checklist-signature-preview";image.src="/static/jessica-gale-signature.jpeg";image.alt="Jessica Gale signature";approval.querySelector("p")?.after(image);}
    if(!readonly){const discard=document.createElement("button");discard.className="btn danger";discard.textContent="Discard application";discard.onclick=()=>deleteChecklistRun(r.id,false);editor.querySelector(".checklist-actions")?.append(discard);}
    editor.querySelectorAll('[data-cf="status"]').forEach(select=>select.addEventListener("change",()=>{updateChecklistStatusStyle(select);updateChecklistProgressPreview();}));
    const overall=editor.querySelector('[data-checklist-root="overall_status"]');if(overall)overall.addEventListener("change",()=>{overall.classList.remove("status-positive","status-pending","status-concern","status-na");overall.classList.add(checklistStatusClass(overall.value==="Recommended"?"Verified / Positive":overall.value==="Not Recommended"?"Concern":"Pending"));});
}
async function saveChecklist(complete){const editor=document.getElementById("checklistEditorView"),payload=JSON.parse(JSON.stringify(currentChecklistRun.payload));editor.querySelectorAll("[data-checklist-root]").forEach(x=>payload[x.dataset.checklistRoot]=x.value);editor.querySelectorAll("[data-ci]").forEach(x=>payload.checks[Number(x.dataset.ci)][x.dataset.cf]=x.value);try{currentChecklistRun=await checklistJson(`/checklists/runs/${currentChecklistRun.id}`,{method:"PUT",body:JSON.stringify({payload,complete})});renderChecklistEditor();if(complete)openChecklistView("reports");}catch(e){const el=document.getElementById("checklistEditorError");el.textContent=e.message;el.style.display="block";}}

// Mailbox context (multi-inbox)
const DEFAULT_MAILBOX = "admin@donspremier.com.au";
let currentMailbox = localStorage.getItem("agent_mailbox") || "";
const delegatedGmailMailboxes = new Set([DEFAULT_MAILBOX]);


// Local user auth (JWT)
const APP_CONFIG = window.AGENTBOT_CONFIG || {};
const recaptchaEnabled = !!APP_CONFIG.recaptcha_enabled;
let authToken = localStorage.getItem("agent_auth_token") || "";
let currentUser = null;
let usersCache = [];
let assignableUsers = [];
let notificationItems = [];
let latestNotificationData = {};
let loginRecaptchaWidgetId = null;
const STAFF_INACTIVITY_LIMIT_MS = 30 * 60 * 1000;
const STAFF_ACTIVITY_KEY = "agent_last_activity_at";
let inactivityCheckTimer = null;
let lastActivityWriteAt = 0;

window.onRecaptchaLoad = function () {
    ensureLoginRecaptcha().catch(() => {
        setLoginError("reCAPTCHA could not load. Please refresh and try again.");
    });
};

function setupLoginMotion() {
    const screen = document.getElementById("loginScreen");
    if (!screen || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    screen.addEventListener("pointermove", (event) => {
        const rect = screen.getBoundingClientRect();
        const x = Math.max(0, Math.min(100, ((event.clientX - rect.left) / rect.width) * 100));
        const y = Math.max(0, Math.min(100, ((event.clientY - rect.top) / rect.height) * 100));
        screen.style.setProperty("--pointer-x", `${x}%`);
        screen.style.setProperty("--pointer-y", `${y}%`);
        screen.style.setProperty("--orb-x", `${(x - 50) * 0.35}px`);
        screen.style.setProperty("--orb-y", `${(y - 50) * 0.35}px`);
    }, { passive: true });
}

let binduBusy = false;
let binduCurrentConversationId = null;
let binduHistoryLoaded = false;

function toggleBindu(forceOpen = null) {
    const panel = document.getElementById("binduPanel");
    const launcher = document.getElementById("binduLauncher");
    if (!panel || !launcher) return;
    const shouldOpen = forceOpen === null ? panel.classList.contains("hidden") : !!forceOpen;
    panel.classList.toggle("hidden", !shouldOpen);
    panel.setAttribute("aria-hidden", String(!shouldOpen));
    launcher.setAttribute("aria-expanded", String(shouldOpen));
    if (shouldOpen) {
        loadBinduHistory().catch(() => {});
        setTimeout(() => document.getElementById("binduInput")?.focus(), 80);
    }
}

function closeBindu() {
    toggleBindu(false);
}

function appendBinduMessage(role, text, sources = []) {
    const messages = document.getElementById("binduMessages");
    if (!messages) return;
    const row = document.createElement("div");
    row.className = `bindu-message ${role === "user" ? "user" : "assistant"}`;
    const bubble = document.createElement("div");
    bubble.className = "bindu-bubble";
    bubble.textContent = String(text || "");
    row.appendChild(bubble);
    messages.appendChild(row);
    if (Array.isArray(sources) && sources.length) {
        const sourceList = document.createElement("div");
        sourceList.className = "bindu-sources";
        sources.slice(0, 6).forEach((source) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "bindu-source";
            button.innerHTML = `<strong>${escapeHtml(source.title || "Portal record")}</strong><span>${escapeHtml(source.kind || source.page || "Source")}</span><span class="bindu-source-detail">${escapeHtml(source.detail || "Open source")}</span>`;
            button.addEventListener("click", () => openBinduSource(source));
            sourceList.appendChild(button);
        });
        messages.appendChild(sourceList);
    }
    messages.scrollTop = messages.scrollHeight;
}

function showBinduTyping(show) {
    document.getElementById("binduTyping")?.remove();
    if (!show) return;
    const messages = document.getElementById("binduMessages");
    if (!messages) return;
    const row = document.createElement("div");
    row.id = "binduTyping";
    row.className = "bindu-message assistant";
    row.innerHTML = '<div class="bindu-bubble"><span class="bindu-typing"><i></i><i></i><i></i></span></div>';
    messages.appendChild(row);
    messages.scrollTop = messages.scrollHeight;
}

function openBinduSource(source) {
    const page = String(source?.page || "portal");
    closeBindu();
    if (!canAccessPage(page)) return;
    switchDashboardTab(page);
    const recordId = source?.record_id;
    if (page === "inbox" && recordId) setTimeout(() => openThread(String(recordId)), 250);
    else if (page === "maintenance" && recordId) setTimeout(() => openMaintenanceOrder(Number(recordId)), 250);
    else if (page === "lease_renewals" && recordId) setTimeout(() => openLeaseRenewalRecord(Number(recordId)), 250);
}

function binduWelcome() {
    return '<div class="bindu-message assistant"><div class="bindu-bubble">Hi, I’m BINDU. I can find and summarise records you have permission to view. What can I help you find?</div></div>';
}

function newBinduConversation() {
    binduCurrentConversationId = null;
    const messages = document.getElementById("binduMessages");
    if (messages) messages.innerHTML = binduWelcome();
    toggleBinduHistory(false);
    document.getElementById("binduInput")?.focus();
    renderBinduHistorySelection();
}

function toggleBinduHistory(forceOpen = null) {
    const history = document.getElementById("binduHistory");
    if (!history) return;
    const open = forceOpen === null ? history.classList.contains("hidden") : !!forceOpen;
    history.classList.toggle("hidden", !open);
    if (open) loadBinduHistory(true).catch(() => {});
}

function binduDateLabel(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(date);
}

function renderBinduHistorySelection() {
    document.querySelectorAll(".bindu-history-item").forEach((item) => item.classList.toggle("active", Number(item.dataset.id) === Number(binduCurrentConversationId)));
}

async function loadBinduHistory(force = false) {
    if (!authToken || (binduHistoryLoaded && !force)) return;
    const list = document.getElementById("binduHistoryList");
    if (list) list.innerHTML = '<div class="bindu-empty">Loading conversations…</div>';
    const response = await apiFetch("/bindu/conversations");
    if (!response.ok) throw new Error(await extractErrorMessage(response));
    const conversations = await response.json();
    binduHistoryLoaded = true;
    if (!list) return;
    if (!conversations.length) {
        list.innerHTML = '<div class="bindu-empty">No saved conversations yet.<br>Start a new chat with BINDU.</div>';
        return;
    }
    list.innerHTML = "";
    conversations.forEach((conversation) => {
        const row = document.createElement("div");
        row.className = "bindu-history-item";
        row.dataset.id = String(conversation.id);
        const open = document.createElement("button");
        open.type = "button";
        open.className = "bindu-history-open";
        open.innerHTML = `<strong>${escapeHtml(conversation.title || "Conversation")}</strong><span>${escapeHtml(binduDateLabel(conversation.updated_at))}</span>`;
        open.addEventListener("click", () => openBinduConversation(conversation.id));
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "bindu-history-delete";
        remove.title = "Delete conversation";
        remove.setAttribute("aria-label", `Delete ${conversation.title || "conversation"}`);
        remove.textContent = "×";
        remove.addEventListener("click", () => deleteBinduConversation(conversation.id));
        row.append(open, remove);
        list.appendChild(row);
    });
    renderBinduHistorySelection();
}

async function openBinduConversation(conversationId) {
    const response = await apiFetch(`/bindu/conversations/${Number(conversationId)}/messages`);
    if (!response.ok) throw new Error(await extractErrorMessage(response));
    const history = await response.json();
    binduCurrentConversationId = Number(conversationId);
    const messages = document.getElementById("binduMessages");
    if (messages) messages.innerHTML = "";
    history.forEach((message) => appendBinduMessage(message.role, message.content, message.sources || []));
    if (!history.length && messages) messages.innerHTML = binduWelcome();
    toggleBinduHistory(false);
    renderBinduHistorySelection();
}

async function deleteBinduConversation(conversationId) {
    if (!window.confirm("Delete this BINDU conversation? This cannot be undone.")) return;
    const response = await apiFetch(`/bindu/conversations/${Number(conversationId)}`, { method: "DELETE" });
    if (!response.ok) throw new Error(await extractErrorMessage(response));
    if (Number(binduCurrentConversationId) === Number(conversationId)) newBinduConversation();
    binduHistoryLoaded = false;
    await loadBinduHistory(true);
}

function askBinduStarter(question) {
    const input = document.getElementById("binduInput");
    if (input) input.value = question;
    askBindu();
}

async function askBindu() {
    if (binduBusy || !authToken) return;
    const input = document.getElementById("binduInput");
    const send = document.getElementById("binduSend");
    const message = String(input?.value || "").trim();
    if (message.length < 2) return;
    binduBusy = true;
    if (input) input.value = "";
    if (send) send.disabled = true;
    appendBinduMessage("user", message);
    showBinduTyping(true);
    try {
        const response = await apiFetch("/bindu/ask", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message, current_page: currentDashboardTab, conversation_id: binduCurrentConversationId }),
        });
        if (!response.ok) throw new Error(await extractErrorMessage(response));
        const data = await response.json();
        binduCurrentConversationId = Number(data.conversation_id);
        binduHistoryLoaded = false;
        showBinduTyping(false);
        appendBinduMessage("assistant", data.answer || "I couldn't find an answer.", data.sources || []);
    } catch (error) {
        showBinduTyping(false);
        appendBinduMessage("assistant", error?.message || "I couldn't search the portal just now. Please try again.");
    } finally {
        binduBusy = false;
        if (send) send.disabled = false;
        input?.focus();
    }
}

function setupBindu() {
    const input = document.getElementById("binduInput");
    input?.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            askBindu();
        }
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !document.getElementById("binduPanel")?.classList.contains("hidden")) closeBindu();
    });
}

function recordStaffActivity(force = false) {
    if (!authToken) return;
    const now = Date.now();
    if (!force && now - lastActivityWriteAt < 15000) return;
    lastActivityWriteAt = now;
    localStorage.setItem(STAFF_ACTIVITY_KEY, String(now));
}

function stopInactivityGuard() {
    if (inactivityCheckTimer) window.clearInterval(inactivityCheckTimer);
    inactivityCheckTimer = null;
}

function checkStaffInactivity() {
    if (!authToken) return;
    const lastActivity = Number(localStorage.getItem(STAFF_ACTIVITY_KEY)) || Date.now();
    if (Date.now() - lastActivity >= STAFF_INACTIVITY_LIMIT_MS) {
        logout("Your session ended after 30 minutes of inactivity. Please sign in again.");
    }
}

function startInactivityGuard(resetActivity = false) {
    stopInactivityGuard();
    if (resetActivity || !localStorage.getItem(STAFF_ACTIVITY_KEY)) recordStaffActivity(true);
    inactivityCheckTimer = window.setInterval(checkStaffInactivity, 15000);
    checkStaffInactivity();
}

["pointerdown", "keydown", "scroll", "touchstart"].forEach((eventName) => {
    window.addEventListener(eventName, () => recordStaffActivity(), { passive: true });
});
window.addEventListener("storage", (event) => {
    if (event.key === STAFF_ACTIVITY_KEY) checkStaffInactivity();
    if (event.key === "agent_auth_token" && !event.newValue && authToken) {
        logout("You were signed out in another browser tab.");
    }
});
document.addEventListener("visibilitychange", () => {
    if (!document.hidden) checkStaffInactivity();
});

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

function gmailDelegateStorageKey(mailbox) {
    return `agent_gmail_delegate_base:${String(mailbox || "default").trim().toLowerCase()}`;
}

function needsDelegatedGmailUrl(mailbox) {
    return delegatedGmailMailboxes.has(String(mailbox || "").trim().toLowerCase());
}

function normalizeMailbox(value) {
    return String(value || "").trim().toLowerCase();
}

function chooseMailbox(mailboxes) {
    const available = Array.isArray(mailboxes)
        ? mailboxes.map(normalizeMailbox).filter(Boolean)
        : [];
    const saved = normalizeMailbox(currentMailbox);
    if (saved && available.includes(saved)) return saved;
    if (available.includes(DEFAULT_MAILBOX)) return DEFAULT_MAILBOX;
    return available[0] || DEFAULT_MAILBOX;
}

function normalizeGmailBaseUrl(value) {
    const raw = String(value || "").trim();
    if (!raw) return "";
    const url = new URL(raw);
    if (url.hostname !== "mail.google.com") {
        throw new Error("Use a mail.google.com Gmail URL.");
    }
    let path = url.pathname || "/mail/u/0/";
    if (!path.startsWith("/mail/")) path = "/mail/u/0/";
    if (!path.endsWith("/")) path += "/";
    return `${url.origin}${path}${url.search}`;
}

function buildGmailThreadUrl(baseUrl, gmailThreadId) {
    const base = normalizeGmailBaseUrl(baseUrl);
    const tid = String(gmailThreadId || "").trim();
    return base && tid ? `${base}#inbox/${encodeURIComponent(tid)}` : "";
}

function getStoredDelegatedGmailBase(mailbox) {
    return localStorage.getItem(gmailDelegateStorageKey(mailbox)) || "";
}

function configureDelegatedGmailBase(mailbox) {
    const key = gmailDelegateStorageKey(mailbox);
    const existing = localStorage.getItem(key) || "";
    const raw = prompt(
        "Open Gmail as management@donspremier.com, select the delegated admin@donspremier.com.au inbox, then paste that delegated Gmail URL here.\n\nExample format:\nhttps://mail.google.com/mail/b/126/u/0/#inbox\n\nThis is stored only in this browser.",
        existing
    );
    if (raw === null) return "";
    if (!raw.trim()) {
        localStorage.removeItem(key);
        return "";
    }
    try {
        const base = normalizeGmailBaseUrl(raw);
        localStorage.setItem(key, base);
        return base;
    } catch (e) {
        alert("That does not look like a valid Gmail URL. Please open the delegated admin inbox, copy the URL from the address bar, and try again.");
        return "";
    }
}

function getViewerGmailData() {
    const link = document.getElementById("gmailLink");
    return {
        link,
        mailbox: link?.dataset.mailbox || currentMailbox || "",
        gmailThreadId: link?.dataset.gmailThreadId || "",
        fallbackUrl: link?.dataset.gmailUrl || link?.href || "",
    };
}

function getGmailUrlForThread(mailbox, gmailThreadId, fallbackUrl) {
    const storedBase = getStoredDelegatedGmailBase(mailbox);
    if (storedBase && gmailThreadId) {
        return buildGmailThreadUrl(storedBase, gmailThreadId);
    }
    if (needsDelegatedGmailUrl(mailbox)) return "";
    return fallbackUrl || "";
}

function configureDelegatedGmailFromViewer() {
    const { mailbox, gmailThreadId, link } = getViewerGmailData();
    const base = configureDelegatedGmailBase(mailbox);
    if (base && link && gmailThreadId) {
        const url = buildGmailThreadUrl(base, gmailThreadId);
        link.href = url || "#";
    }
    return false;
}

function openGmailThreadFromViewer() {
    const { mailbox, gmailThreadId, fallbackUrl } = getViewerGmailData();
    let url = getGmailUrlForThread(mailbox, gmailThreadId, fallbackUrl);
    if (!url && needsDelegatedGmailUrl(mailbox)) {
        const base = configureDelegatedGmailBase(mailbox);
        if (!base) return false;
        url = buildGmailThreadUrl(base, gmailThreadId);
    }
    if (!url || url === "#") {
        alert("Could not build a Gmail link for this thread.");
        return false;
    }
    window.open(url, "_blank", "noopener,noreferrer");
    return false;
}

// Date filter applied to ticket list (set when you click Fetch Now).
let currentDateFilter = { start: "", end: "" };

// Pagination state
let currentPage = 1;
let pageSize = 25;
let ticketsLoadedOnce = false;
let ticketLoadController = null;
const ticketTabCache = new Map();
const TICKET_TAB_CACHE_MS = 60 * 1000;

// UI filters
let currentSearch = "";
// Category filtering removed (we avoid AI-based categorization and UI filters for now).
let currentRentPage = 1;
let rentLoadedOnce = false;
let rentViewMode = "tracker";
let currentLeaseRenewalPage = 1;
let leaseRenewalTotalPages = 1;
let leaseRenewalsLoadedOnce = false;
let leaseRenewalViewMode = "dashboard";
let selectedLeaseRenewalId = null;
let leaseRenewalRecordsCache = {};
let landlordReportLoadedOnce = false;
let landlordReportContext = null;
let landlordReportPropertyKey = "";
let landlordReportSectionOrder = [];
let landlordReportSelectedSections = new Set();
let landlordReportSectionNotes = {};
let landlordReportActivities = [];
let landlordReportEditingActivityId = "";
let landlordReportSelectedPhotoIds = new Set();
let landlordReportOnlyPhotos = [];
let landlordReportPendingPhotoIds = [];
let landlordReportOnlyPdfs = [];
let landlordReportPendingPdfIds = [];
let landlordReportPendingFilesChanged = false;
let landlordReportDetailValues = {};
let landlordReportViewMode = "builder";
let landlordReportDataLoaded = false;
let savedLandlordReportsLoaded = false;
let savedLandlordReportSearchTimer = null;
let savedLandlordReportsCache = {};
const LANDLORD_REPORT_DETAIL_FIELDS = [
    "Property address", "Tenancy", "Rent", "Maintenance", "Compliance", "Lease",
    "Tenant names", "Lease type", "Lease commencement", "Lease expiry", "Current weekly rent",
    "Bond amount", "Rent paid-to date", "Occupancy status", "Property type",
    "Rent received during period", "Recorded partial payments", "Owner disbursements",
    "Management fees", "Maintenance expenses", "Other expenses", "Current rent balance",
    "Outstanding invoices", "Net owner summary"
];
let landlordReportPreviewTimer = null;
let landlordReportContextTimer = null;
let landlordReportContextRequest = 0;
let landlordReportPreviewRequest = 0;
let landlordReportEventsBound = false;
let currentMaintenancePage = 1;
let maintenanceLoadedOnce = false;
let selectedMaintenanceOrderId = null;
let maintenanceOrdersCache = {};
let maintenanceViewMode = "dashboard";
let tenantRegistrationsCache = [];
let maintenanceTradiesCache = [];
let currentPropertiesPage = 1;
let propertiesLoadedOnce = false;
let propertyListingActiveTab = "overview";
let newListingCollectionsReady = false;
let currentCompliancePage = 1;
let complianceLoadedOnce = false;
let currentCoveragePage = 1;
let coverageLoadedOnce = false;
let usersLoadedOnce = false;
let teamLoadedOnce = false;
let inspectionsLoadedOnce = false;
let inspectionEventsBound = false;
let inspectionAgents = [];
let inspectionAvailableAgentIds = new Set();
let inspectionAvailabilityInitialized = false;
let inspectionRows = [];
let inspectionRowSequence = 0;
let inspectionPlans = [];
let inspectionCurrentPlanId = null;
let inspectionLastOptimization = null;
let inspectionMap = null;
let inspectionMapRouteLayer = null;
let inspectionMapMarkerLayer = null;
let inspectionAddressSuggestionTimer = null;
let inspectionAddressSuggestionController = null;
let inspectionAddressSuggestionRequest = 0;
let inspectionAddressSuggestionItems = [];
let inspectionAddressSuggestionsByLabel = new Map();
let inspectionAddressSuggestionQuery = "";
let activityLoadedOnce = false;
let currentActivityPage = 1;
let activityTotalPages = 1;
let activityAreasCache = [];
let mySpaceLoadedOnce = false;
let mySpaceSaveTimer = null;
let mySpaceQuickLinksCache = [];
let mySpaceSnippetsCache = [];
let mySpaceGuidesCache = [];
let mySpaceViewMode = "workspace";
let mySpaceTodosCache = [];
let mySpaceGoogleEvents = [];
let mySpaceGoogleCalendarError = "";
let mySpaceCalendarMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
let mySpaceSelectedDate = null;
let mySpaceEditingTodoId = null;
let timesheetDayCache = null;
let timesheetLoadedDate = "";
let timesheetCanReview = false;
let timesheetCanDeleteReports = false;
let timesheetReportsLoadedOnce = false;
let timesheetStaffCache = [];
let timesheetStaffLoadedOnce = false;
let pageRegistry = [];
let rolePagePermissions = {};
let allowedPages = new Set(["portal"]);
let propertyOptionsCache = [];
let propertyOptionsByLabel = {};
let propertyResultsCache = {};
let editingPropertyId = null;
let addressSuggestionsByLabel = {};
let addressSuggestionTimer = null;
let complianceRecordsCache = {};
let editingComplianceRecordId = null;
let complianceProvidersLoadedOnce = false;
let complianceProvidersCache = [];
let editingComplianceProviderId = null;

function isAllEmailsTab() {
    return String(currentTab || "").toLowerCase() === "all";
}

function updateSyncContextUI() {
    const info = document.getElementById("syncInfo");
    const viewBadge = document.getElementById("queueViewMode");
    setMailboxSummary(currentMailbox || "-");
    if (currentDashboardTab === "portal") {
        if (viewBadge) viewBadge.textContent = "Portal Hub";
        if (info) info.textContent = "Portal Hub: choose a workspace tile or use the menu to jump into a feature.";
        return;
    }
    if (currentDashboardTab === "myspace") {
        if (viewBadge) viewBadge.textContent = "My Space";
        if (info) info.textContent = "Inbox sync controls are hidden while you are in your private workspace.";
        return;
    }
    if (currentDashboardTab === "team") {
        if (viewBadge) viewBadge.textContent = "Our Team";
        if (info) info.textContent = "Inbox sync controls are hidden while you are viewing the staff directory.";
        return;
    }
    if (currentDashboardTab === "activity") {
        if (viewBadge) viewBadge.textContent = "Activity Log";
        if (info) info.textContent = "Inbox sync controls are hidden while you are reviewing platform activity.";
        return;
    }
    if (currentDashboardTab === "rent") {
        if (viewBadge) viewBadge.textContent = "Rent Tracker";
        if (info) info.textContent = "Inbox sync controls are hidden while you are on the rent tracker tab.";
        return;
    }
    if (currentDashboardTab === "lease_renewals") {
        if (viewBadge) viewBadge.textContent = "Lease Renewals";
        if (info) info.textContent = "Inbox sync controls are hidden while you are managing lease renewal tracking.";
        return;
    }
    if (currentDashboardTab === "landlord_reports") {
        if (viewBadge) viewBadge.textContent = "Landlord Reports";
        if (info) info.textContent = "Inbox sync controls are hidden while you are preparing a monthly landlord report.";
        return;
    }
    if (currentDashboardTab === "maintenance") {
        if (viewBadge) viewBadge.textContent = "Maintenance";
        if (info) info.textContent = "Inbox sync controls are hidden while you are managing maintenance orders.";
        return;
    }
    if (currentDashboardTab === "inspections") {
        if (viewBadge) viewBadge.textContent = "Inspections";
        if (info) info.textContent = "Inbox sync controls are hidden while you are planning inspection routes.";
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
    if (currentDashboardTab === "compliance_providers") {
        if (viewBadge) viewBadge.textContent = "Compliance Providers";
        if (info) info.textContent = "Inbox sync controls are hidden while you are managing compliance providers.";
        return;
    }
    if (currentDashboardTab === "properties") {
        if (viewBadge) viewBadge.textContent = "Properties";
        if (info) info.textContent = "Inbox sync controls are hidden while you are on the properties tab.";
        return;
    }
    if (currentDashboardTab === "system") {
        if (viewBadge) viewBadge.textContent = "System";
        if (info) info.textContent = "Inbox sync controls are hidden while you are managing users and access.";
        return;
    }
    if (!info) return;
    if (isAllEmailsTab()) {
        if (viewBadge) viewBadge.textContent = "All Emails";
        info.textContent = "All Emails mode: Fetch uses the date range; Check Updates only checks forward from the last Check Updates checkpoint.";
    } else if (String(currentTab || "").toLowerCase() === "assigned_to_me") {
        if (viewBadge) viewBadge.textContent = "Assigned to Me";
        info.textContent = "Assigned to Me shows tickets currently assigned to your staff account.";
    } else {
        if (viewBadge) viewBadge.textContent = "Awaiting Reply";
        info.textContent = "Awaiting Reply mode: Check Updates only checks forward from the last Check Updates checkpoint.";
    }
}

function setMailboxSummary(value) {
    const text = value || currentMailbox || "-";
    const mailboxBadge = document.getElementById("mailboxBadge");
    const queueMailbox = document.getElementById("queueMailboxMode");
    const mailboxLabel = document.getElementById("mailboxLabel");
    if (mailboxBadge) mailboxBadge.textContent = text;
    if (queueMailbox) queueMailbox.textContent = text;
    if (mailboxLabel) mailboxLabel.textContent = text;
}

function setLastSyncSummary(value = null) {
    const text = value || new Date().toLocaleString();
    const last = document.getElementById("lastSync");
    const mirror = document.getElementById("mailLastSyncMirror");
    if (last) last.textContent = text;
    if (mirror) mirror.textContent = text;
}

function syncResultItems(payload) {
    if (!payload || typeof payload !== "object") return [];
    if (Array.isArray(payload.results)) {
        return payload.results.filter(item => item && typeof item === "object");
    }
    return [payload];
}

function warnIfSyncHitLimit(payload) {
    const capped = syncResultItems(payload).filter(item => item.hit_limit);
    if (!capped.length) return;
    alert("Sync completed, but Gmail reported more changed emails than the current Limit. Increase the Limit and run Check Updates or Fetch Now again before relying on the queue as complete.");
}

function notificationKindLabel(kind) {
    const key = String(kind || "").toLowerCase();
    if (key === "email") return "Email";
    if (key === "maintenance") return "Maintenance";
    if (key === "rent") return "Rent";
    if (key === "lease") return "Lease";
    if (key === "compliance") return "Compliance";
    if (key === "myspace") return "My Space";
    return "Portal";
}

function notificationSeverityLabel(severity) {
    const key = String(severity || "").toLowerCase();
    if (key === "critical") return "Critical";
    if (key === "overdue") return "Overdue";
    if (key === "action") return "Action Required";
    if (key === "assigned") return "Assigned";
    if (key === "new") return "New";
    if (key === "soon") return "Due Soon";
    return "Info";
}

function notificationDateLabel(item) {
    if (item.due_at) return `Due ${formatDateShort(item.due_at)}`;
    if (item.created_at) return `Updated ${formatDateShort(item.created_at)}`;
    return "Needs attention";
}

function notificationActionLabel(item) {
    return item.action || "Open";
}

function notificationIndexByItem(item) {
    return notificationItems.indexOf(item);
}

function notificationCardHtml(item, idx) {
    return `
      <button class="notification-centre-card" type="button" onclick="openNotificationTarget(${idx})">
        <span class="notification-kind ${escapeHtml(String(item.severity || "").toLowerCase())}">${escapeHtml(notificationKindLabel(item.kind))} - ${escapeHtml(notificationSeverityLabel(item.severity))}</span>
        <strong>${escapeHtml(item.title || "Notification")}</strong>
        <span>${escapeHtml(item.detail || "")}</span>
        <span>${escapeHtml(notificationDateLabel(item))}</span>
        <em>${escapeHtml(notificationActionLabel(item))}</em>
      </button>
    `;
}

function notificationCategoryCards(data = {}) {
    const c = data.categories || {};
    const cards = [
        ["email", "Emails Assigned to Me", c.email || 0],
        ["lease", "Lease Renewals", c.lease || 0],
        ["myspace", "My Space", c.myspace || 0],
        ["maintenance", "Maintenance", c.maintenance || 0],
        ["rent", "Rent", c.rent || 0],
        ["compliance", "Compliance", c.compliance || 0],
    ];
    return cards.map(([kind, label, count]) => `
      <div class="notification-stat">
        <span>${escapeHtml(label)}</span>
        <strong>${Number(count || 0)}</strong>
        <small>${escapeHtml(notificationKindLabel(kind))}</small>
      </div>
    `).join("");
}

function notificationCategoryChartHtml(data = {}) {
    const c = data.categories || {};
    const rows = [
        ["email", "Emails Assigned to Me", Number(c.email || 0)],
        ["lease", "Lease Renewals", Number(c.lease || 0)],
        ["myspace", "My Space", Number(c.myspace || 0)],
        ["maintenance", "Maintenance", Number(c.maintenance || 0)],
        ["rent", "Rent", Number(c.rent || 0)],
        ["compliance", "Compliance", Number(c.compliance || 0)],
    ];
    const max = Math.max(...rows.map(([, , count]) => count), 1);
    return `
      <div class="notification-bar-chart">
        ${rows.map(([kind, label, count]) => {
            const width = Math.max(0, Math.round((count / max) * 100));
            return `
              <div class="notification-bar-row">
                <span>${escapeHtml(label)}</span>
                <div class="notification-bar-track" aria-label="${escapeHtml(label)} ${count}">
                  <div class="notification-bar-fill" style="width:${width}%"></div>
                </div>
                <span>${count}</span>
              </div>
            `;
        }).join("")}
      </div>
    `;
}

function notificationSeverityColor(severity) {
    const key = String(severity || "").toLowerCase();
    if (key === "critical") return "#991b1b";
    if (key === "overdue") return "#dc2626";
    if (key === "action") return "#b45309";
    if (key === "assigned") return "#2563eb";
    if (key === "new") return "#059669";
    if (key === "soon") return "#64748b";
    return "#94a3b8";
}

function notificationSeverityChartHtml() {
    const rows = ["critical", "overdue", "action", "assigned", "new", "soon", "info"].map((severity) => ({
        severity,
        label: notificationSeverityLabel(severity),
        count: notificationItems.filter((item) => String(item.severity || "info").toLowerCase() === severity).length,
        color: notificationSeverityColor(severity),
    })).filter((row) => row.count > 0);
    const total = rows.reduce((sum, row) => sum + row.count, 0);
    let cursor = 0;
    const segments = rows.map((row) => {
        const start = cursor;
        const end = cursor + (row.count / Math.max(total, 1)) * 100;
        cursor = end;
        return `${row.color} ${start.toFixed(2)}% ${end.toFixed(2)}%`;
    });
    const ring = total > 0
        ? `conic-gradient(${segments.join(", ")})`
        : "conic-gradient(#e5e7eb 0% 100%)";
    return `
      <div class="notification-severity-layout">
        <div class="notification-ring" style="background:${ring}">
          <div>
            <strong>${total}</strong>
            <small>Items</small>
          </div>
        </div>
        <div class="notification-severity-list">
          ${(rows.length ? rows : [{ severity: "info", label: "No active alerts", count: 0, color: "#94a3b8" }]).map((row) => `
            <div class="notification-severity-item">
              <span><i class="notification-severity-dot" style="background:${row.color}"></i>${escapeHtml(row.label)}</span>
              <strong>${row.count}</strong>
            </div>
          `).join("")}
        </div>
      </div>
    `;
}

function renderNotificationCenter(data = latestNotificationData) {
    const stats = document.getElementById("notificationCenterStats");
    const categoryChart = document.getElementById("notificationCategoryChart");
    const severityChart = document.getElementById("notificationSeverityChart");
    const list = document.getElementById("notificationCenterList");
    const generated = document.getElementById("notificationCenterGenerated");
    if (stats) stats.innerHTML = notificationCategoryCards(data || {});
    if (categoryChart) categoryChart.innerHTML = notificationCategoryChartHtml(data || {});
    if (severityChart) severityChart.innerHTML = notificationSeverityChartHtml();
    if (generated) generated.textContent = data.generated_at ? `Updated ${formatDate(data.generated_at)}` : "Ready";
    if (!list) return;
    if (!notificationItems.length) {
        list.innerHTML = `<div class="ticket-empty"><strong>No notifications right now</strong><div class="small muted" style="margin-top:6px">Assigned tickets, maintenance action, lease renewal alerts, compliance risk, rent alerts, and personal follow-ups will appear here.</div></div>`;
        return;
    }
    const groups = ["email", "lease", "myspace", "maintenance", "rent", "compliance"];
    list.innerHTML = groups.map((kind) => {
        const groupItems = notificationItems.filter((item) => String(item.kind || "").toLowerCase() === kind);
        if (!groupItems.length) return "";
        return `
          <section class="notification-centre-section">
            <div class="row space">
              <div>
                <h3>${escapeHtml(notificationKindLabel(kind))}</h3>
                <p class="small muted">${groupItems.length} active notification${groupItems.length === 1 ? "" : "s"}</p>
              </div>
            </div>
            <div class="notification-centre-grid">
              ${groupItems.map((item) => notificationCardHtml(item, notificationIndexByItem(item))).join("")}
            </div>
          </section>
        `;
    }).join("");
}

function renderNotifications(data = {}) {
    const bell = document.getElementById("notificationBell");
    const countEl = document.getElementById("notificationCount");
    const total = Number(data.total || 0);
    latestNotificationData = data || {};
    notificationItems = Array.isArray(data.items) ? data.items : [];

    if (bell) bell.classList.toggle("has-alerts", total > 0);
    if (countEl) countEl.textContent = total > 99 ? "99+" : String(total);
    renderNotificationCenter(data);
}

async function loadNotifications() {
    if (!authToken) return;
    try {
        const r = await apiFetch("/notifications");
        if (!r.ok) return;
        const data = await r.json();
        renderNotifications(data);
    } catch {
        // Notification health should never interrupt daily workflow.
    }
}

function openNotificationCenter() {
    switchDashboardTab("notifications");
}

async function openNotificationTarget(index) {
    const item = notificationItems[index];
    if (!item) return;
    const page = item.page || "portal";
    if (page === "inbox") {
        switchDashboardTab("inbox");
        setTab(item.tab || "awaiting_reply");
        if (item.thread_id) {
            setTimeout(() => openThread(item.thread_id), 400);
        }
        return;
    }
    if (page === "maintenance") {
        switchDashboardTab("maintenance");
        switchMaintenanceView(item.view || "active");
        if (item.order_id) {
            setTimeout(() => openMaintenanceOrder(item.order_id), 450);
        }
        return;
    }
    if (page === "lease_renewals") {
        switchDashboardTab("lease_renewals");
        switchLeaseRenewalView("report");
        if (item.record_id) {
            setTimeout(() => openLeaseRenewalRecord(item.record_id), 450);
        }
        return;
    }
    if (page === "compliance") {
        switchDashboardTab("compliance");
        setTimeout(() => loadComplianceDashboard(1), 100);
        return;
    }
    if (page === "coverage") {
        switchDashboardTab("coverage");
        setTimeout(() => loadComplianceCoverage(), 100);
        return;
    }
    switchDashboardTab(page);
}

let googleConnected = false;
async function initMailboxes() {
    const sel = document.getElementById("mailboxSelect");
    if (!sel) return;
    try {
        const r = await apiFetch("/settings/mailboxes");
        const j = await r.json();
        const mbs = Array.isArray(j.mailboxes)
            ? j.mailboxes.map(normalizeMailbox).filter(Boolean)
            : [];
        sel.innerHTML = "";
        for (const mb of mbs) {
            const opt = document.createElement("option");
            opt.value = mb;
            opt.textContent = mb;
            sel.appendChild(opt);
        }
        currentMailbox = chooseMailbox(mbs);
        if (currentMailbox) {
            localStorage.setItem("agent_mailbox", currentMailbox);
            sel.value = currentMailbox;
        }
        sel.addEventListener("change", () => {
            currentMailbox = sel.value;
            localStorage.setItem("agent_mailbox", currentMailbox);
            clearPropertyOptionsState();
            // refresh UI data under new mailbox
            currentPage = 1;
            rentLoadedOnce = false;
            leaseRenewalsLoadedOnce = false;
            currentLeaseRenewalPage = 1;
            leaseRenewalTotalPages = 1;
            selectedLeaseRenewalId = null;
            leaseRenewalRecordsCache = {};
            resetLandlordReportBuilder();
            maintenanceLoadedOnce = false;
            inspectionsLoadedOnce = false;
            inspectionPlans = [];
            resetInspectionWorkspace({ preserveDate: true, preserveAgents: true });
            renderInspectionPlans();
            propertiesLoadedOnce = false;
            complianceLoadedOnce = false;
            coverageLoadedOnce = false;
            complianceProvidersLoadedOnce = false;
            activityLoadedOnce = false;
            ticketsLoadedOnce = false;
            invalidateTicketCache();
            updateSyncContextUI();
            if (currentDashboardTab === "inbox") loadTickets();
            if (currentDashboardTab === "rent") {
                currentRentPage = 1;
                loadActiveRentView();
            }
            if (currentDashboardTab === "lease_renewals") {
                resetLeaseRenewalForm();
                loadLeaseRenewals();
            }
            if (currentDashboardTab === "maintenance") {
                currentMaintenancePage = 1;
                loadMaintenanceDashboard();
            }
            if (currentDashboardTab === "inspections") {
                initInspectionsWorkspace(true);
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
            if (currentDashboardTab === "compliance_providers") {
                loadComplianceProviders(true);
            }
            refreshPropertyOptions().then(() => {
                if (currentDashboardTab === "landlord_reports") initLandlordReportBuilder();
            });
            refreshGoogleStatus();
            loadNotifications();
        });

        setMailboxSummary(currentMailbox || "-");
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

        setMailboxSummary(googleConnected ? target : "-");

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
                  <div class="text-xs text-slate-500 truncate">${escapeHtml(u.email)} • ${escapeHtml(roleTitle(u.role))}${u.is_active ? "" : " • Inactive"}</div>
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
                ${roleOptions(u.role)}
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

const TIMESHEET_TASK_STATUSES = [
    { value: "COMPLETED", label: "Completed" },
    { value: "IN_PROGRESS", label: "In Progress" },
    { value: "FOLLOW_UP_REQUIRED", label: "Follow-up Required" },
];

function switchMySpaceView(view) {
    mySpaceViewMode = view === "timesheet" ? "timesheet" : "workspace";
    if (mySpaceViewMode === "timesheet") {
        localStorage.setItem(sideSubnavStorageKey("myspace"), "0");
    }
    switchDashboardTab("myspace");
    applySideSubnavState();
}

function applyMySpaceView() {
    const isTimesheet = mySpaceViewMode === "timesheet";
    document.getElementById("mySpaceWorkspaceView")?.classList.toggle("hidden", isTimesheet);
    document.getElementById("mySpaceTimesheetView")?.classList.toggle("hidden", !isTimesheet);
    document.querySelectorAll("[data-myspace-view]").forEach((button) => {
        button.classList.toggle("active", currentDashboardTab === "myspace" && button.dataset.myspaceView === mySpaceViewMode);
    });
    if (currentDashboardTab !== "myspace") return;
    const title = document.getElementById("topbarTitle");
    const subtitle = document.getElementById("topbarSubtitle");
    if (isTimesheet) {
        if (title) title.textContent = "Timesheet";
        if (subtitle) subtitle.textContent = "Log daily work and manage staff timesheet approvals.";
    } else {
        if (title) title.textContent = "My Space";
        if (subtitle) subtitle.textContent = "Your private workspace for planning, follow-ups, snippets, notes, and staff guides.";
    }
}

function timesheetDateToInput(value) {
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function timesheetWorkDateLabel(value) {
    const parts = String(value || "").split("-").map(Number);
    if (parts.length !== 3 || parts.some((part) => !Number.isFinite(part))) return String(value || "—");
    try {
        return new Date(parts[0], parts[1] - 1, parts[2]).toLocaleDateString(undefined, {
            weekday: "short",
            day: "numeric",
            month: "short",
            year: "numeric",
        });
    } catch {
        return String(value || "—");
    }
}

function timesheetTimeValue(value) {
    return String(value || "").slice(0, 5);
}

function timesheetPickerParts(value) {
    const match = /^(\d{2}):(\d{2})$/.exec(timesheetTimeValue(value));
    let hours24;
    let minutes;
    if (match) {
        hours24 = Math.min(23, Math.max(0, Number(match[1])));
        minutes = Math.min(50, Math.floor(Math.max(0, Number(match[2])) / 10) * 10);
    } else {
        const now = new Date();
        hours24 = now.getHours();
        minutes = Math.floor(now.getMinutes() / 10) * 10;
    }
    return {
        hour: String(hours24 % 12 || 12).padStart(2, "0"),
        minute: String(minutes).padStart(2, "0"),
        period: hours24 >= 12 ? "PM" : "AM",
    };
}

function timesheetPickerValue(parts) {
    let hours = Number(parts.hour) % 12;
    if (parts.period === "PM") hours += 12;
    return `${String(hours).padStart(2, "0")}:${parts.minute}`;
}

function formatTimesheetClock(value, placeholder = "Select time") {
    const match = /^(\d{2}):(\d{2})$/.exec(timesheetTimeValue(value));
    if (!match) return placeholder;
    const hours24 = Number(match[1]);
    const period = hours24 >= 12 ? "PM" : "AM";
    return `${String(hours24 % 12 || 12).padStart(2, "0")}:${match[2]} ${period}`;
}

function syncTimesheetTimePicker(input) {
    if (!input) return;
    const picker = input.closest(".timesheet-time-picker");
    if (!picker) return;
    const label = picker.querySelector(".timesheet-time-trigger-value");
    if (label) label.textContent = formatTimesheetClock(input.value, input.dataset.placeholder || "Select time");
    const selected = timesheetPickerParts(input.value);
    picker.querySelectorAll(".timesheet-time-option").forEach((option) => {
        option.classList.toggle("active", option.dataset.value === selected[option.dataset.part]);
    });
}

function closeTimesheetTimePickers(except = null) {
    document.querySelectorAll(".timesheet-time-picker.open").forEach((picker) => {
        if (picker === except) return;
        picker.classList.remove("open");
        picker.querySelector(".timesheet-time-popup")?.setAttribute("hidden", "");
        picker.querySelector(".timesheet-time-trigger")?.setAttribute("aria-expanded", "false");
    });
}

function initialiseTimesheetTimePickers(root = document) {
    root.querySelectorAll("input[data-timesheet-ten-minute]").forEach((input) => {
        if (input.dataset.timesheetPickerReady === "true") {
            syncTimesheetTimePicker(input);
            return;
        }
        input.dataset.timesheetPickerReady = "true";
        const placeholder = input.dataset.placeholder || "Select time";
        const picker = document.createElement("div");
        picker.className = "timesheet-time-picker";
        input.parentNode.insertBefore(picker, input);
        picker.appendChild(input);
        input.type = "hidden";

        const trigger = document.createElement("button");
        trigger.type = "button";
        trigger.className = "timesheet-time-trigger";
        trigger.setAttribute("aria-haspopup", "dialog");
        trigger.setAttribute("aria-expanded", "false");
        trigger.innerHTML = `<span class="timesheet-time-trigger-value"></span><span class="timesheet-time-chevron" aria-hidden="true">&#9662;</span>`;

        const popup = document.createElement("div");
        popup.className = "timesheet-time-popup";
        popup.setAttribute("hidden", "");
        popup.setAttribute("role", "dialog");
        popup.setAttribute("aria-label", placeholder);
        const groups = [
            ["hour", ["12", "01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11"]],
            ["minute", ["00", "10", "20", "30", "40", "50"]],
            ["period", ["AM", "PM"]],
        ];
        popup.innerHTML = groups.map(([part, values]) => (
            `<div class="timesheet-time-column" data-time-column="${part}">${values.map((value) => (
                `<button class="timesheet-time-option" type="button" data-part="${part}" data-value="${value}">${value}</button>`
            )).join("")}</div>`
        )).join("");

        trigger.addEventListener("click", () => {
            if (trigger.disabled) return;
            const opening = !picker.classList.contains("open");
            closeTimesheetTimePickers(opening ? picker : null);
            picker.classList.toggle("open", opening);
            popup.toggleAttribute("hidden", !opening);
            trigger.setAttribute("aria-expanded", opening ? "true" : "false");
            if (opening) {
                syncTimesheetTimePicker(input);
                requestAnimationFrame(() => {
                    popup.querySelectorAll(".timesheet-time-option.active").forEach((option) => (
                        option.scrollIntoView({ block: "nearest" })
                    ));
                });
            }
        });
        popup.addEventListener("click", (event) => {
            const option = event.target.closest(".timesheet-time-option");
            if (!option) return;
            const parts = timesheetPickerParts(input.value);
            parts[option.dataset.part] = option.dataset.value;
            input.value = timesheetPickerValue(parts);
            syncTimesheetTimePicker(input);
            if (input.dataset.timesheetEntryId) {
                updateTimesheetRowDuration(Number(input.dataset.timesheetEntryId));
            } else {
                updateTimesheetDurationPreview();
            }
        });
        picker.appendChild(trigger);
        picker.appendChild(popup);
        syncTimesheetTimePicker(input);
    });
}

document.addEventListener("click", (event) => {
    if (!event.target.closest(".timesheet-time-picker")) closeTimesheetTimePickers();
});
document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeTimesheetTimePickers();
});

function timesheetMinutesBetween(start, end) {
    const parse = (value) => {
        const match = /^(\d{2}):(\d{2})$/.exec(String(value || ""));
        if (!match) return null;
        const hours = Number(match[1]);
        const minutes = Number(match[2]);
        if (hours > 23 || minutes > 59) return null;
        return hours * 60 + minutes;
    };
    const from = parse(start);
    const to = parse(end);
    if (from === null || to === null || to <= from) return null;
    return to - from;
}

function formatTimesheetDuration(minutes) {
    const total = Math.max(0, Number(minutes) || 0);
    const hours = Math.floor(total / 60);
    const remainder = total % 60;
    if (hours && remainder) return `${hours}h ${remainder}m`;
    if (hours) return `${hours}h`;
    return `${remainder}m`;
}

function timesheetTaskStatusLabel(status) {
    return TIMESHEET_TASK_STATUSES.find((item) => item.value === status)?.label || String(status || "Completed");
}

function timesheetTaskStatusOptions(selected) {
    return TIMESHEET_TASK_STATUSES.map((item) => (
        `<option value="${item.value}" ${item.value === selected ? "selected" : ""}>${escapeHtml(item.label)}</option>`
    )).join("");
}

function timesheetApprovalLabel(status) {
    return {
        DRAFT: "Draft",
        SUBMITTED: "Awaiting Approval",
        CHANGES_REQUESTED: "Changes Requested",
        APPROVED: "Approved",
    }[String(status || "DRAFT").toUpperCase()] || String(status || "Draft");
}

function timesheetApprovalClass(status) {
    return {
        DRAFT: "draft",
        SUBMITTED: "submitted",
        CHANGES_REQUESTED: "changes-requested",
        APPROVED: "approved",
    }[String(status || "DRAFT").toUpperCase()] || "draft";
}

function timesheetCanEdit(report) {
    if (!report) return true;
    if (typeof report.can_edit === "boolean") return report.can_edit;
    return ["DRAFT", "CHANGES_REQUESTED"].includes(String(report.status || "DRAFT").toUpperCase());
}

function setTimesheetMessage(message = "", type = "") {
    const element = document.getElementById("timesheetMessage");
    if (!element) return;
    element.textContent = message;
    element.classList.toggle("hidden", !message);
    element.classList.toggle("error", type === "error");
    element.classList.toggle("success", type === "success");
}

function initialiseTimesheetView() {
    const dateInput = document.getElementById("timesheetWorkDate");
    if (dateInput && !dateInput.value) dateInput.value = timesheetDateToInput(new Date());

    const today = new Date();
    const from = new Date(today);
    from.setDate(from.getDate() - 13);
    const fromInput = document.getElementById("timesheetReportFrom");
    const toInput = document.getElementById("timesheetReportTo");
    if (fromInput && !fromInput.value) fromInput.value = timesheetDateToInput(from);
    if (toInput && !toInput.value) toInput.value = timesheetDateToInput(today);
    initialiseTimesheetTimePickers();
    populateTimesheetStaffFilter();
    updateTimesheetDurationPreview();
}

function populateTimesheetStaffFilter() {
    const select = document.getElementById("timesheetReportStaff");
    if (!select) return;
    const selected = select.value;
    const source = timesheetStaffCache.length ? timesheetStaffCache : (Array.isArray(usersCache) ? usersCache : []);
    const staff = source
        .filter((user) => user && user.is_active !== false)
        .sort((a, b) => String(a.name || a.email || "").localeCompare(String(b.name || b.email || "")));
    select.innerHTML = `<option value="">All Staff</option>` + staff.map((user) => (
        `<option value="${Number(user.id)}">${escapeHtml(user.name || user.email || (`Staff ${user.id}`))}</option>`
    )).join("");
    if (staff.some((user) => String(user.id) === selected)) select.value = selected;
}

async function loadTimesheetStaffDirectory() {
    if (timesheetStaffLoadedOnce) return;
    if (Array.isArray(usersCache) && usersCache.length) {
        timesheetStaffCache = usersCache.filter((user) => user && user.is_active !== false);
        timesheetStaffLoadedOnce = true;
        populateTimesheetStaffFilter();
        return;
    }
    try {
        const response = await apiFetch("/user-auth/team");
        if (!response.ok) return;
        const data = await response.json();
        timesheetStaffCache = Array.isArray(data) ? data : [];
        timesheetStaffLoadedOnce = true;
        populateTimesheetStaffFilter();
    } catch {
        // The report remains usable with the All Staff filter.
    }
}

function updateTimesheetDurationPreview() {
    const start = document.getElementById("timesheetStartTime")?.value || "";
    const end = document.getElementById("timesheetEndTime")?.value || "";
    const output = document.getElementById("timesheetDuration");
    if (!output) return;
    if (!start || !end) {
        output.value = "—";
        return;
    }
    const minutes = timesheetMinutesBetween(start, end);
    output.value = minutes === null ? "End must be later" : formatTimesheetDuration(minutes);
}

function updateTimesheetRowDuration(entryId) {
    const start = document.querySelector(`[data-timesheet-start="${entryId}"]`)?.value || "";
    const end = document.querySelector(`[data-timesheet-end="${entryId}"]`)?.value || "";
    const output = document.querySelector(`[data-timesheet-duration="${entryId}"]`);
    if (!output) return;
    const minutes = timesheetMinutesBetween(start, end);
    output.textContent = minutes === null ? "Check times" : formatTimesheetDuration(minutes);
}

function renderTimesheetEntries(report, workDate) {
    const list = document.getElementById("timesheetEntryList");
    if (!list) return;
    const entries = Array.isArray(report?.entries) ? report.entries : [];
    const editable = timesheetCanEdit(report);
    if (!entries.length) {
        list.innerHTML = `<div class="myspace-empty">No tasks recorded for ${escapeHtml(timesheetWorkDateLabel(workDate))}.</div>`;
        return;
    }

    list.innerHTML = entries.map((entry) => {
        const status = String(entry.status || entry.task_status || "COMPLETED").toUpperCase();
        const start = timesheetTimeValue(entry.start_time);
        const end = timesheetTimeValue(entry.end_time);
        if (!editable) {
            return `
                <article class="timesheet-entry-row locked">
                    <div class="timesheet-entry-cell"><span class="timesheet-mobile-label">Date</span>${escapeHtml(timesheetWorkDateLabel(workDate))}</div>
                    <div class="timesheet-entry-cell"><span class="timesheet-mobile-label">Start</span>${escapeHtml(start)}</div>
                    <div class="timesheet-entry-cell"><span class="timesheet-mobile-label">End</span>${escapeHtml(end)}</div>
                    <div class="timesheet-entry-cell"><span class="timesheet-mobile-label">Duration</span><strong>${escapeHtml(formatTimesheetDuration(entry.duration_minutes))}</strong></div>
                    <div class="timesheet-entry-cell task"><span class="timesheet-mobile-label">Task</span>${escapeHtml(entry.task || "")}</div>
                    <div class="timesheet-entry-cell"><span class="timesheet-mobile-label">Status</span><span class="user-chip">${escapeHtml(timesheetTaskStatusLabel(status))}</span></div>
                    <div class="timesheet-entry-cell"><span class="timesheet-mobile-label">Actions</span><span class="small muted">Locked</span></div>
                </article>
            `;
        }
        return `
            <article class="timesheet-entry-row">
                <div class="timesheet-entry-cell"><span class="timesheet-mobile-label">Date</span>${escapeHtml(timesheetWorkDateLabel(workDate))}</div>
                <div class="timesheet-entry-cell">
                    <span class="timesheet-mobile-label">Start</span>
                    <input type="time" step="600" data-timesheet-ten-minute data-timesheet-entry-id="${entry.id}" data-placeholder="Start time" data-timesheet-start="${entry.id}" value="${escapeHtml(start)}" />
                </div>
                <div class="timesheet-entry-cell">
                    <span class="timesheet-mobile-label">End</span>
                    <input type="time" step="600" data-timesheet-ten-minute data-timesheet-entry-id="${entry.id}" data-placeholder="End time" data-timesheet-end="${entry.id}" value="${escapeHtml(end)}" />
                </div>
                <div class="timesheet-entry-cell">
                    <span class="timesheet-mobile-label">Duration</span>
                    <strong data-timesheet-duration="${entry.id}">${escapeHtml(formatTimesheetDuration(entry.duration_minutes))}</strong>
                </div>
                <div class="timesheet-entry-cell task">
                    <span class="timesheet-mobile-label">Task</span>
                    <input type="text" data-timesheet-task="${entry.id}" value="${escapeHtml(entry.task || "")}" />
                </div>
                <div class="timesheet-entry-cell">
                    <span class="timesheet-mobile-label">Status</span>
                    <select data-timesheet-status="${entry.id}">${timesheetTaskStatusOptions(status)}</select>
                </div>
                <div class="timesheet-entry-actions">
                    <button class="btn" type="button" onclick="saveTimesheetEntry(${entry.id})">Save</button>
                    <button class="btn danger" type="button" onclick="deleteTimesheetEntry(${entry.id})">Delete</button>
                </div>
            </article>
        `;
    }).join("");
    initialiseTimesheetTimePickers(list);
}

function renderTimesheetDay(report, workDate) {
    const status = String(report?.status || "DRAFT").toUpperCase();
    const entries = Array.isArray(report?.entries) ? report.entries : [];
    const editable = timesheetCanEdit(report);
    const totalMinutes = Number(report?.total_duration_minutes ?? report?.total_minutes ?? entries.reduce((sum, entry) => sum + (Number(entry.duration_minutes) || 0), 0));
    const badge = document.getElementById("timesheetApprovalBadge");
    if (badge) {
        badge.textContent = timesheetApprovalLabel(status);
        badge.className = `timesheet-approval-badge ${timesheetApprovalClass(status)}`;
    }
    const count = document.getElementById("timesheetTaskCount");
    const total = document.getElementById("timesheetTotalDuration");
    const reportDate = document.getElementById("timesheetReportDate");
    if (count) count.textContent = String(entries.length);
    if (total) total.textContent = formatTimesheetDuration(totalMinutes);
    if (reportDate) reportDate.textContent = timesheetWorkDateLabel(workDate);

    const comment = String(report?.director_comment || "").trim();
    const note = document.getElementById("timesheetReturnNote");
    const noteTitle = note?.querySelector("strong");
    const noteBody = document.getElementById("timesheetReturnComment");
    if (note) note.classList.toggle("hidden", !comment);
    if (noteTitle) noteTitle.textContent = status === "APPROVED" ? "Director note" : (status === "CHANGES_REQUESTED" ? "Changes requested by the Director" : "Previous director comment");
    if (noteBody) noteBody.textContent = comment;

    ["timesheetStartTime", "timesheetEndTime", "timesheetTask", "timesheetTaskStatus"].forEach((id) => {
        const element = document.getElementById(id);
        if (element) element.disabled = !editable;
        const pickerTrigger = element?.closest(".timesheet-time-picker")?.querySelector(".timesheet-time-trigger");
        if (pickerTrigger) pickerTrigger.disabled = !editable;
    });
    const addButton = document.getElementById("timesheetAddButton");
    if (addButton) addButton.disabled = !editable;
    const submitButton = document.getElementById("timesheetSubmitButton");
    const submitHint = document.getElementById("timesheetSubmitHint");
    if (submitButton) {
        submitButton.textContent = status === "CHANGES_REQUESTED" ? "Resend for Approval" : "Send for Approval";
        submitButton.disabled = !editable || entries.length === 0;
    }
    if (submitHint) {
        if (status === "SUBMITTED") submitHint.textContent = "This report is with the Director and is locked while awaiting review.";
        else if (status === "APPROVED") submitHint.textContent = "This daily report has been approved and is now read-only.";
        else if (status === "CHANGES_REQUESTED") submitHint.textContent = "Update the requested items, then resend the report for approval.";
        else submitHint.textContent = entries.length ? "Check the entries, then send the complete day for approval." : "Add at least one task before sending the report.";
    }
    renderTimesheetEntries(report, workDate);
}

async function loadTimesheetDay() {
    initialiseTimesheetView();
    const dateInput = document.getElementById("timesheetWorkDate");
    const workDate = String(dateInput?.value || "");
    if (!workDate) return;
    const requestedDate = workDate;
    const list = document.getElementById("timesheetEntryList");
    if (list) list.innerHTML = `<div class="myspace-empty">Loading daily report...</div>`;
    try {
        const response = await apiFetch(`/my-space/timesheets/day?work_date=${encodeURIComponent(workDate)}`);
        if (!response.ok) throw new Error(await extractErrorMessage(response));
        const data = await response.json();
        if (document.getElementById("timesheetWorkDate")?.value !== requestedDate) return;
        timesheetDayCache = data.report || null;
        timesheetLoadedDate = workDate;
        timesheetCanReview = data.can_review === true;
        timesheetCanDeleteReports = data.can_delete_reports === true;
        renderTimesheetDay(timesheetDayCache, workDate);
        const reviewPanel = document.getElementById("timesheetReviewPanel");
        if (reviewPanel) reviewPanel.classList.toggle("hidden", !timesheetCanReview);
        if (timesheetCanReview) {
            await loadTimesheetStaffDirectory();
            if (!timesheetReportsLoadedOnce) await loadTimesheetReports();
        }
    } catch (error) {
        timesheetDayCache = null;
        if (list) list.innerHTML = `<div class="myspace-empty">Could not load this daily report.</div>`;
        setTimesheetMessage(String(error?.message || error || "Could not load the timesheet."), "error");
    }
}

async function addTimesheetEntry() {
    const workDate = document.getElementById("timesheetWorkDate")?.value || "";
    const startTime = document.getElementById("timesheetStartTime")?.value || "";
    const endTime = document.getElementById("timesheetEndTime")?.value || "";
    const task = String(document.getElementById("timesheetTask")?.value || "").trim();
    const status = document.getElementById("timesheetTaskStatus")?.value || "COMPLETED";
    if (!workDate || !startTime || !endTime || !task) {
        setTimesheetMessage("Date, start time, end time, and task are required.", "error");
        return;
    }
    if (timesheetMinutesBetween(startTime, endTime) === null) {
        setTimesheetMessage("End time must be later than start time.", "error");
        return;
    }
    const button = document.getElementById("timesheetAddButton");
    if (button) button.disabled = true;
    try {
        const response = await apiFetch("/my-space/timesheets/entries", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                work_date: workDate,
                start_time: startTime,
                end_time: endTime,
                task,
                status,
            }),
        });
        if (!response.ok) throw new Error(await extractErrorMessage(response));
        const taskInput = document.getElementById("timesheetTask");
        const startInput = document.getElementById("timesheetStartTime");
        const endInput = document.getElementById("timesheetEndTime");
        if (taskInput) taskInput.value = "";
        if (startInput) startInput.value = "";
        if (endInput) endInput.value = "";
        syncTimesheetTimePicker(startInput);
        syncTimesheetTimePicker(endInput);
        updateTimesheetDurationPreview();
        setTimesheetMessage("Task added to the daily report.", "success");
        await loadTimesheetDay();
    } catch (error) {
        setTimesheetMessage(String(error?.message || error || "Could not add the task."), "error");
    } finally {
        if (button && timesheetCanEdit(timesheetDayCache)) button.disabled = false;
    }
}

async function saveTimesheetEntry(entryId) {
    const startTime = document.querySelector(`[data-timesheet-start="${entryId}"]`)?.value || "";
    const endTime = document.querySelector(`[data-timesheet-end="${entryId}"]`)?.value || "";
    const task = String(document.querySelector(`[data-timesheet-task="${entryId}"]`)?.value || "").trim();
    const status = document.querySelector(`[data-timesheet-status="${entryId}"]`)?.value || "COMPLETED";
    if (!task || timesheetMinutesBetween(startTime, endTime) === null) {
        setTimesheetMessage("Enter a task and make sure the end time is later than the start time.", "error");
        return;
    }
    try {
        const response = await apiFetch(`/my-space/timesheets/entries/${entryId}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ start_time: startTime, end_time: endTime, task, status }),
        });
        if (!response.ok) throw new Error(await extractErrorMessage(response));
        setTimesheetMessage("Timesheet task saved.", "success");
        await loadTimesheetDay();
    } catch (error) {
        setTimesheetMessage(String(error?.message || error || "Could not save the task."), "error");
    }
}

async function deleteTimesheetEntry(entryId) {
    if (!confirm("Delete this timesheet task?")) return;
    try {
        const response = await apiFetch(`/my-space/timesheets/entries/${entryId}`, { method: "DELETE" });
        if (!response.ok) throw new Error(await extractErrorMessage(response));
        setTimesheetMessage("Timesheet task deleted.", "success");
        await loadTimesheetDay();
    } catch (error) {
        setTimesheetMessage(String(error?.message || error || "Could not delete the task."), "error");
    }
}

async function submitTimesheetForApproval() {
    const workDate = document.getElementById("timesheetWorkDate")?.value || "";
    if (!workDate || !confirm(`Send the complete report for ${timesheetWorkDateLabel(workDate)} to the Director?`)) return;
    const button = document.getElementById("timesheetSubmitButton");
    if (button) button.disabled = true;
    try {
        const response = await apiFetch(`/my-space/timesheets/day/${encodeURIComponent(workDate)}/submit`, { method: "POST" });
        if (!response.ok) throw new Error(await extractErrorMessage(response));
        setTimesheetMessage("Daily report sent for approval.", "success");
        await loadTimesheetDay();
        if (timesheetCanReview) await loadTimesheetReports();
    } catch (error) {
        setTimesheetMessage(String(error?.message || error || "Could not submit the report."), "error");
        if (button) button.disabled = false;
    }
}

function selectTodayTimesheet() {
    const input = document.getElementById("timesheetWorkDate");
    if (!input) return;
    input.value = timesheetDateToInput(new Date());
    loadTimesheetDay();
}

function shiftTimesheetDay(offset) {
    const input = document.getElementById("timesheetWorkDate");
    if (!input) return;
    const current = input.value ? new Date(`${input.value}T12:00:00`) : new Date();
    current.setDate(current.getDate() + Number(offset || 0));
    input.value = timesheetDateToInput(current);
    loadTimesheetDay();
}

function timesheetDateTimeLabel(value) {
    if (!value) return "";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
}

function renderTimesheetReports(reports) {
    const list = document.getElementById("timesheetReportList");
    const pending = document.getElementById("timesheetPendingCount");
    const rows = Array.isArray(reports) ? reports : [];
    if (pending) {
        const count = rows.filter((report) => report.status === "SUBMITTED").length;
        pending.textContent = `${count} awaiting approval`;
    }
    if (!list) return;
    if (!rows.length) {
        list.innerHTML = `<div class="myspace-empty">No submitted timesheet reports match these filters.</div>`;
        return;
    }
    list.innerHTML = rows.map((report) => {
        const staff = report.staff || {
            id: report.staff_user_id,
            name: report.staff_name,
            email: report.staff_email,
            avatar_url: report.staff_avatar_url,
        };
        const entries = Array.isArray(report.entries) ? report.entries : [];
        const status = String(report.status || "SUBMITTED").toUpperCase();
        const totalMinutes = Number(report.total_duration_minutes ?? report.total_minutes ?? entries.reduce((sum, entry) => sum + (Number(entry.duration_minutes) || 0), 0));
        const reviewer = report.reviewer?.name || report.reviewer?.email || report.reviewed_by_name || "";
        const comment = String(report.director_comment || "").trim();
        const submitted = report.submitted_at ? `Submitted ${timesheetDateTimeLabel(report.submitted_at)}` : "Submitted";
        const entryRows = entries.map((entry) => {
            const taskStatus = String(entry.status || entry.task_status || "COMPLETED").toUpperCase();
            return `
                <div class="timesheet-report-entry">
                    <span><span class="timesheet-mobile-label">Start</span>${escapeHtml(timesheetTimeValue(entry.start_time))}</span>
                    <span><span class="timesheet-mobile-label">End</span>${escapeHtml(timesheetTimeValue(entry.end_time))}</span>
                    <strong><span class="timesheet-mobile-label">Duration</span>${escapeHtml(formatTimesheetDuration(entry.duration_minutes))}</strong>
                    <span class="task"><span class="timesheet-mobile-label">Task</span>${escapeHtml(entry.task || "")}</span>
                    <span><span class="timesheet-mobile-label">Status</span><span class="user-chip">${escapeHtml(timesheetTaskStatusLabel(taskStatus))}</span></span>
                </div>
            `;
        }).join("");
        const completedMeta = reviewer && report.reviewed_at
            ? `Reviewed by ${escapeHtml(reviewer)} · ${escapeHtml(timesheetDateTimeLabel(report.reviewed_at))}`
            : escapeHtml(submitted);
        return `
            <article class="timesheet-report-card">
                <div class="timesheet-report-head">
                    <div class="timesheet-report-person">
                        <img class="timesheet-report-avatar" src="${escapeHtml(staff.avatar_url || "/static/logo.png")}" alt="" />
                        <div>
                            <strong>${escapeHtml(staff.name || staff.email || "Staff member")}</strong>
                            <span>${escapeHtml(staff.email || "")}${staff.email ? " · " : ""}${escapeHtml(timesheetWorkDateLabel(report.work_date))}</span>
                        </div>
                    </div>
                    <div class="timesheet-report-meta">
                        <span class="user-chip">${entries.length} task${entries.length === 1 ? "" : "s"}</span>
                        <span class="user-chip">${escapeHtml(formatTimesheetDuration(totalMinutes))}</span>
                        <span class="timesheet-approval-badge ${timesheetApprovalClass(status)}">${escapeHtml(timesheetApprovalLabel(status))}</span>
                        ${timesheetCanDeleteReports ? `<button class="btn danger" type="button" onclick="deleteTimesheetReport(${report.id})">Delete Report</button>` : ""}
                    </div>
                </div>
                <div class="timesheet-report-entries">${entryRows}</div>
                ${comment ? `<div class="timesheet-review-comment"><strong>Director comment</strong><br>${escapeHtml(comment)}</div>` : ""}
                ${status === "SUBMITTED" ? `
                    <div class="timesheet-review-actions">
                        <div class="field">
                            <div class="label">Director Comment</div>
                            <textarea id="timesheetReviewComment${report.id}" placeholder="Add an optional approval note, or explain what needs to change..."></textarea>
                        </div>
                        <button class="btn danger" type="button" onclick="reviewTimesheetReport(${report.id}, 'send_back')">Send Back to Staff</button>
                        <button class="btn primary" type="button" onclick="reviewTimesheetReport(${report.id}, 'approve')">Approve</button>
                    </div>
                ` : `<div class="timesheet-review-comment">${completedMeta}</div>`}
            </article>
        `;
    }).join("");
}

async function loadTimesheetReports() {
    if (!timesheetCanReview) return;
    initialiseTimesheetView();
    const list = document.getElementById("timesheetReportList");
    if (list) list.innerHTML = `<div class="myspace-empty">Loading submitted staff reports...</div>`;
    const params = new URLSearchParams();
    const from = document.getElementById("timesheetReportFrom")?.value || "";
    const to = document.getElementById("timesheetReportTo")?.value || "";
    const staffId = document.getElementById("timesheetReportStaff")?.value || "";
    const status = document.getElementById("timesheetReportStatus")?.value || "";
    if (from) params.set("date_from", from);
    if (to) params.set("date_to", to);
    if (staffId) params.set("staff_id", staffId);
    if (status) params.set("status", status);
    try {
        const response = await apiFetch(`/my-space/timesheets/reports?${params.toString()}`);
        if (!response.ok) throw new Error(await extractErrorMessage(response));
        const data = await response.json();
        const reports = Array.isArray(data) ? data : (Array.isArray(data.reports) ? data.reports : []);
        timesheetReportsLoadedOnce = true;
        renderTimesheetReports(reports);
    } catch (error) {
        if (list) list.innerHTML = `<div class="myspace-empty">${escapeHtml(String(error?.message || error || "Could not load staff reports."))}</div>`;
    }
}

async function exportTimesheetDailyReport() {
    const workDate = document.getElementById("timesheetReportTo")?.value || "";
    if (!workDate) {
        setTimesheetMessage("Choose the daily export date in the To field.", "error");
        return;
    }
    try {
        const response = await apiFetch(`/my-space/timesheets/reports/export?work_date=${encodeURIComponent(workDate)}`);
        if (!response.ok) throw new Error(await extractErrorMessage(response));
        const blob = await response.blob();
        const disposition = response.headers.get("Content-Disposition") || "";
        const filenameMatch = /filename="?([^";]+)"?/i.exec(disposition);
        const filename = filenameMatch?.[1] || `timesheet-report-${workDate}.pdf`;
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        setTimesheetMessage(`Daily report for ${timesheetWorkDateLabel(workDate)} exported.`, "success");
    } catch (error) {
        setTimesheetMessage(String(error?.message || error || "Could not export the daily report."), "error");
    }
}

async function deleteTimesheetReport(reportId) {
    const confirmed = window.confirm("Delete this full timesheet report and all of its tasks? This cannot be undone.");
    if (!confirmed) return;
    try {
        const response = await apiFetch(`/my-space/timesheets/reports/${reportId}`, { method: "DELETE" });
        if (!response.ok) throw new Error(await extractErrorMessage(response));
        const deleted = await response.json();
        setTimesheetMessage("Timesheet report deleted.", "success");
        await loadTimesheetReports();
        if (
            String(deleted.work_date || "") === timesheetLoadedDate
            && Number(deleted.staff_user_id) === Number(currentUser?.id)
        ) {
            await loadTimesheetDay();
        }
    } catch (error) {
        setTimesheetMessage(String(error?.message || error || "Could not delete the report."), "error");
    }
}

async function reviewTimesheetReport(reportId, action) {
    const comment = String(document.getElementById(`timesheetReviewComment${reportId}`)?.value || "").trim();
    if (action === "send_back" && !comment) {
        setTimesheetMessage("Add a comment explaining what the staff member needs to change.", "error");
        document.getElementById(`timesheetReviewComment${reportId}`)?.focus();
        return;
    }
    try {
        const response = await apiFetch(`/my-space/timesheets/reports/${reportId}/review`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action, comment }),
        });
        if (!response.ok) throw new Error(await extractErrorMessage(response));
        setTimesheetMessage(action === "approve" ? "Timesheet report approved." : "Report sent back to the staff member.", "success");
        await loadTimesheetReports();
        if (timesheetLoadedDate) await loadTimesheetDay();
    } catch (error) {
        setTimesheetMessage(String(error?.message || error || "Could not review the report."), "error");
    }
}

function mySpaceDuePayload(value) {
    const raw = String(value || "").trim();
    if (!raw) return null;
    return raw.length === 10 ? `${raw}T00:00:00` : raw.length === 16 ? `${raw}:00` : raw;
}

const MY_SPACE_BUCKETS = [
    { id: "today", label: "Today", empty: "Nothing planned for today." },
    { id: "week", label: "This Week", empty: "No weekly follow-ups yet." },
    { id: "later", label: "Later", empty: "No parked items." },
];

function mySpaceCleanBucket(value) {
    const bucket = String(value || "today").toLowerCase();
    return MY_SPACE_BUCKETS.some((item) => item.id === bucket) ? bucket : "today";
}

function mySpaceCleanType(value) {
    const type = String(value || "task").toLowerCase();
    return type === "follow_up" ? "follow_up" : "task";
}

function mySpaceTypeLabel(value) {
    return mySpaceCleanType(value) === "follow_up" ? "Follow-up" : "Task";
}

function mySpaceBucketLabel(value) {
    const bucket = MY_SPACE_BUCKETS.find((item) => item.id === mySpaceCleanBucket(value));
    return bucket ? bucket.label : "Today";
}

function mySpaceTodayInput() {
    const today = new Date();
    return `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
}

function mySpaceCanManageGuides() {
    return String(currentUser?.role || "").toUpperCase() === "ADMIN";
}

function mySpaceStats(items) {
    const total = items.length;
    const done = items.filter((item) => item.is_done).length;
    const open = total - done;
    const followUps = items.filter((item) => !item.is_done && mySpaceCleanType(item.item_type) === "follow_up").length;
    const dueToday = items.filter((item) => !item.is_done && item.due_at && dateInputValue(item.due_at) === mySpaceTodayInput()).length;
    const overdue = items.filter((item) => {
        if (item.is_done || !item.due_at) return false;
        const due = new Date(item.due_at);
        const today = new Date();
        due.setHours(23, 59, 59, 999);
        return due < today;
    }).length;
    return { total, done, open, overdue, followUps, dueToday };
}

function renderMySpaceStats(items) {
    const el = document.getElementById("mySpaceStats");
    if (!el) return;
    const stats = mySpaceStats(items);
    el.innerHTML = `
        <div class="system-stat"><span>Open Items</span><strong>${stats.open}</strong></div>
        <div class="system-stat"><span>Follow-Ups</span><strong>${stats.followUps}</strong></div>
        <div class="system-stat"><span>Due Today</span><strong>${stats.dueToday}</strong></div>
        <div class="system-stat"><span>Overdue</span><strong>${stats.overdue}</strong></div>
    `;
}

function renderMySpaceTodoItem(item) {
    const due = item.due_at ? dateInputValue(item.due_at) : "";
    const dueLabel = item.due_at ? formatDateShort(item.due_at) : "No due date";
    const priority = String(item.priority || "normal").toLowerCase();
    const itemType = mySpaceCleanType(item.item_type);
    const bucket = mySpaceCleanBucket(item.bucket);
    return `
        <article class="myspace-todo ${item.is_done ? "done" : ""}">
            <label class="myspace-check" title="Mark complete">
                <input type="checkbox" ${item.is_done ? "checked" : ""} onchange="toggleMySpaceTodo(${item.id}, this.checked)" />
                <span></span>
            </label>
            <div class="myspace-todo-main">
                <input class="myspace-title-input" data-myspace-title="${item.id}" value="${escapeHtml(item.title || "")}" />
                <textarea data-myspace-notes="${item.id}" placeholder="Private task notes...">${escapeHtml(item.notes || "")}</textarea>
                <div class="myspace-todo-meta">
                    <span class="user-chip ${priority === "high" ? "warn" : ""}">${escapeHtml(priority)}</span>
                    <span class="user-chip">${escapeHtml(mySpaceTypeLabel(itemType))}</span>
                    ${item.follow_up_with ? `<span class="user-chip">Chase: ${escapeHtml(item.follow_up_with)}</span>` : ""}
                    <span class="user-chip">${escapeHtml(dueLabel)}</span>
                    ${item.completed_at ? `<span class="user-chip ok">Done ${escapeHtml(formatDateShort(item.completed_at))}</span>` : ""}
                </div>
            </div>
            <div class="myspace-todo-actions">
                <select data-myspace-bucket="${item.id}">
                    <option value="today" ${bucket === "today" ? "selected" : ""}>Today</option>
                    <option value="week" ${bucket === "week" ? "selected" : ""}>This Week</option>
                    <option value="later" ${bucket === "later" ? "selected" : ""}>Later</option>
                </select>
                <select data-myspace-type="${item.id}" onchange="toggleMySpaceFollowField(${item.id}, this.value)">
                    <option value="task" ${itemType === "task" ? "selected" : ""}>Task</option>
                    <option value="follow_up" ${itemType === "follow_up" ? "selected" : ""}>Follow-up</option>
                </select>
                <input type="text" data-myspace-follow="${item.id}" class="${itemType === "follow_up" ? "" : "hidden"}" placeholder="Who or what to chase?" value="${escapeHtml(item.follow_up_with || "")}" />
                <select data-myspace-priority="${item.id}">
                    <option value="low" ${priority === "low" ? "selected" : ""}>Low</option>
                    <option value="normal" ${priority === "normal" ? "selected" : ""}>Normal</option>
                    <option value="high" ${priority === "high" ? "selected" : ""}>High</option>
                </select>
                <input type="date" data-myspace-due="${item.id}" value="${escapeHtml(due)}" />
                <div class="myspace-action-row">
                    <button class="btn" onclick="saveMySpaceTodo(${item.id})">Save</button>
                    <button class="btn danger" onclick="deleteMySpaceTodo(${item.id})">Delete</button>
                </div>
            </div>
        </article>
    `;
}

function renderMySpaceTodos(items) {
    mySpaceTodosCache = Array.isArray(items) ? items : [];
    renderMySpaceCalendar();
}

function mySpaceDateKey(value, googleAllDay = false) {
    if (!value) return "";
    const raw = String(value);
    if (googleAllDay || /^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw.slice(0, 10);
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return raw.slice(0, 10);
    return `${parsed.getFullYear()}-${String(parsed.getMonth() + 1).padStart(2, "0")}-${String(parsed.getDate()).padStart(2, "0")}`;
}

function mySpaceDateFromKey(key) {
    const [year, month, day] = String(key).split("-").map(Number);
    return new Date(year, month - 1, day);
}

function mySpaceDisplayTime(value, allDay = false) {
    if (!value || allDay) return allDay ? "All day" : "No time set";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? "" : parsed.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function mySpaceCalendarEntries() {
    const local = mySpaceTodosCache.map((item) => ({
        source: "local", id: item.id, title: item.title, start: item.due_at,
        dateKey: item.due_at ? mySpaceDateKey(item.due_at) : "9999-12-31", all_day: false, item,
    }));
    const google = mySpaceGoogleEvents.filter((item) => item.start).map((item) => ({
        ...item, dateKey: mySpaceDateKey(item.start, item.all_day), source: "google",
    }));
    return [...local, ...google];
}

function renderMySpaceCalendar() {
    const grid = document.getElementById("mySpaceCalendarGrid");
    const title = document.getElementById("mySpaceCalendarTitle");
    if (!grid || !title) return;
    title.textContent = mySpaceCalendarMonth.toLocaleDateString([], { month: "long", year: "numeric" });
    const first = new Date(mySpaceCalendarMonth.getFullYear(), mySpaceCalendarMonth.getMonth(), 1);
    const offset = (first.getDay() + 6) % 7;
    const gridStart = new Date(first);
    gridStart.setDate(first.getDate() - offset);
    const entries = mySpaceCalendarEntries();
    const todayKey = mySpaceTodayInput();
    const cells = [];
    for (let index = 0; index < 42; index += 1) {
        const day = new Date(gridStart);
        day.setDate(gridStart.getDate() + index);
        const key = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
        const dayEntries = entries.filter((entry) => entry.dateKey === key).slice(0, 3);
        const total = entries.filter((entry) => entry.dateKey === key).length;
        cells.push(`<button type="button" class="myspace-calendar-day ${day.getMonth() !== first.getMonth() ? "outside" : ""} ${key === todayKey ? "today" : ""} ${key === mySpaceSelectedDate ? "selected" : ""}" onclick="selectMySpaceCalendarDate('${key}')">
            <span class="day-number">${day.getDate()}</span>
            <span class="myspace-calendar-events">${dayEntries.map((entry) => `<span class="myspace-calendar-chip ${entry.source === "google" ? "google" : ""}">${escapeHtml(entry.title || "Untitled")}</span>`).join("")}${total > 3 ? `<span class="myspace-calendar-more">+${total - 3} more</span>` : ""}</span>
        </button>`);
    }
    grid.innerHTML = cells.join("");
    renderMySpaceUpcoming();
}

function renderMySpaceUpcoming() {
    const list = document.getElementById("mySpaceUpcomingList");
    const heading = document.getElementById("mySpaceAgendaTitle");
    if (!list || !heading) return;
    const today = mySpaceTodayInput();
    let entries = mySpaceCalendarEntries().filter((entry) => {
        if (mySpaceSelectedDate) return entry.dateKey === mySpaceSelectedDate;
        if (entry.source === "local" && !entry.item?.is_done) return true;
        return entry.dateKey >= today;
    });
    entries.sort((a, b) => String(a.start || "9999-12-31").localeCompare(String(b.start || "9999-12-31")));
    entries = entries.slice(0, 60);
    heading.textContent = mySpaceSelectedDate
        ? mySpaceDateFromKey(mySpaceSelectedDate).toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" })
        : "Upcoming";
    const calendarError = mySpaceGoogleCalendarError
        ? `<div class="myspace-empty">${escapeHtml(mySpaceGoogleCalendarError)}</div>`
        : "";
    if (!entries.length) {
        list.innerHTML = `${calendarError}<div class="myspace-empty">${mySpaceSelectedDate ? "Nothing scheduled for this day." : "No upcoming tasks or events."}</div>`;
        return;
    }
    list.innerHTML = calendarError + entries.map((entry) => {
        if (entry.source === "google") {
            return `<article class="myspace-agenda-item google"><span class="user-chip">G</span><div class="myspace-agenda-item-main"><strong>${escapeHtml(entry.title || "Busy")}</strong><p>${escapeHtml(entry.all_day ? `${entry.dateKey} · All day` : `${entry.dateKey} · ${mySpaceDisplayTime(entry.start)}`)}${entry.location ? ` · ${escapeHtml(entry.location)}` : ""}</p>${entry.html_link ? `<div class="myspace-agenda-actions"><a class="btn" href="${escapeHtml(entry.html_link)}" target="_blank" rel="noopener">Open in Google</a></div>` : ""}</div></article>`;
        }
        const item = entry.item;
        return `<article class="myspace-agenda-item ${item.is_done ? "done" : ""}"><label class="myspace-check" title="Mark complete"><input type="checkbox" ${item.is_done ? "checked" : ""} onchange="toggleMySpaceTodo(${item.id}, this.checked)"/><span></span></label><div class="myspace-agenda-item-main"><strong>${escapeHtml(item.title || "Task")}</strong><p>${escapeHtml(item.due_at ? `${entry.dateKey} · ${mySpaceDisplayTime(item.due_at)}` : "No due date")} · ${escapeHtml(mySpaceTypeLabel(item.item_type))} · ${escapeHtml(item.priority || "normal")}</p>${item.follow_up_with ? `<p>Follow up with ${escapeHtml(item.follow_up_with)}</p>` : ""}<div class="myspace-agenda-actions"><button class="btn" onclick="editMySpaceTodo(${item.id})">Edit</button><button class="btn danger" onclick="deleteMySpaceTodo(${item.id})">Delete</button></div></div></article>`;
    }).join("");
}

function selectMySpaceCalendarDate(key) {
    mySpaceSelectedDate = key;
    const due = document.getElementById("mySpaceNewDue");
    if (due && !due.value && !mySpaceEditingTodoId) due.value = `${key}T09:00`;
    renderMySpaceCalendar();
}

function clearMySpaceSelectedDate() { mySpaceSelectedDate = null; renderMySpaceCalendar(); }
function moveMySpaceCalendar(offset) {
    mySpaceCalendarMonth = new Date(mySpaceCalendarMonth.getFullYear(), mySpaceCalendarMonth.getMonth() + Number(offset || 0), 1);
    mySpaceSelectedDate = null;
    loadMySpaceGoogleEvents();
}
function goToMySpaceCalendarToday() {
    const now = new Date();
    mySpaceCalendarMonth = new Date(now.getFullYear(), now.getMonth(), 1);
    mySpaceSelectedDate = mySpaceTodayInput();
    loadMySpaceGoogleEvents();
}

function renderMySpaceQuickLinks(items) {
    mySpaceQuickLinksCache = items;
    const list = document.getElementById("mySpaceQuickLinks");
    if (!list) return;
    if (!items.length) {
        list.innerHTML = `<div class="myspace-empty">Add reusable links for portals, calendars, reports, or supplier systems.</div>`;
        return;
    }
    list.innerHTML = items.map((link) => `
        <article class="myspace-tool-item">
            <div class="myspace-tool-main">
                <input data-myspace-link-title="${link.id}" value="${escapeHtml(link.title || "")}" placeholder="Link title" />
                <input data-myspace-link-url="${link.id}" value="${escapeHtml(link.url || "")}" placeholder="https://..." />
                <textarea data-myspace-link-notes="${link.id}" placeholder="Optional notes...">${escapeHtml(link.notes || "")}</textarea>
            </div>
            <div class="myspace-tool-actions">
                <a class="btn" href="${escapeHtml(link.url || "#")}" target="_blank" rel="noopener noreferrer">Open</a>
                <button class="btn" onclick="saveMySpaceQuickLink(${link.id})">Save</button>
                <button class="btn danger" onclick="deleteMySpaceQuickLink(${link.id})">Delete</button>
            </div>
        </article>
    `).join("");
}

function renderMySpaceSnippets(items) {
    mySpaceSnippetsCache = items;
    const list = document.getElementById("mySpaceSnippets");
    if (!list) return;
    if (!items.length) {
        list.innerHTML = `<div class="myspace-empty">Save repeat email wording here so you can copy it in seconds.</div>`;
        return;
    }
    list.innerHTML = items.map((snippet) => `
        <article class="myspace-tool-item">
            <div class="myspace-tool-main">
                <div class="myspace-inline-fields">
                    <input data-myspace-snippet-title="${snippet.id}" value="${escapeHtml(snippet.title || "")}" placeholder="Snippet title" />
                    <input data-myspace-snippet-category="${snippet.id}" value="${escapeHtml(snippet.category || "")}" placeholder="Category" />
                </div>
                <textarea class="myspace-snippet-body" data-myspace-snippet-body="${snippet.id}" placeholder="Snippet body...">${escapeHtml(snippet.body || "")}</textarea>
            </div>
            <div class="myspace-tool-actions">
                <button class="btn primary" onclick="copyMySpaceSnippet(${snippet.id})">Copy</button>
                <button class="btn" onclick="saveMySpaceSnippet(${snippet.id})">Save</button>
                <button class="btn danger" onclick="deleteMySpaceSnippet(${snippet.id})">Delete</button>
            </div>
        </article>
    `).join("");
}

function renderMySpaceGuides(items) {
    mySpaceGuidesCache = items;
    const list = document.getElementById("mySpaceStaffGuides");
    const upload = document.getElementById("mySpaceGuideUpload");
    const canManage = mySpaceCanManageGuides();
    if (upload) upload.classList.toggle("hidden", !canManage);
    if (!list) return;
    if (!items.length) {
        list.innerHTML = `<div class="myspace-empty">No staff guides uploaded yet.</div>`;
        return;
    }
    list.innerHTML = items.map((guide) => `
        <article class="myspace-guide-item">
            <div>
                <strong>${escapeHtml(guide.title || "Staff guide")}</strong>
                <p>${escapeHtml(guide.description || guide.filename || "PDF guide")}</p>
                <span class="small muted">Uploaded ${escapeHtml(formatDateShort(guide.created_at))}</span>
            </div>
            <div class="myspace-guide-actions">
                <button class="btn primary" onclick="openMySpaceGuide(${guide.id})">View PDF</button>
                ${canManage ? `<button class="btn danger" onclick="deleteMySpaceGuide(${guide.id})">Delete</button>` : ""}
            </div>
        </article>
    `).join("");
}

function toggleMySpaceFollowField(todoId, value) {
    const followEl = document.querySelector(`[data-myspace-follow="${todoId}"]`);
    if (!followEl) return;
    const show = mySpaceCleanType(value) === "follow_up";
    followEl.classList.toggle("hidden", !show);
    if (!show) followEl.value = "";
}

function toggleMySpaceNewFollowField(value) {
    const field = document.getElementById("mySpaceNewFollowField");
    const followEl = document.getElementById("mySpaceNewFollowUpWith");
    if (!followEl || !field) return;
    const show = mySpaceCleanType(value) === "follow_up";
    field.classList.toggle("hidden", !show);
    if (!show) followEl.value = "";
}

function renderMySpaceGoogleStatus(status) {
    const el = document.getElementById("mySpaceGoogleConnect");
    if (!el) return;
    if (status?.connected) {
        el.classList.add("connected");
        el.innerHTML = `<span class="small"><strong>Google Calendar</strong><br>${escapeHtml(status.email || "Connected")}</span><button class="btn" onclick="loadMySpaceGoogleEvents()">Refresh</button><button class="btn danger" onclick="disconnectMySpaceGoogleCalendar()">Disconnect</button>`;
    } else {
        el.classList.remove("connected");
        el.innerHTML = `<span class="small muted">Google Calendar not connected</span><button class="btn" onclick="connectMySpaceGoogleCalendar()">Connect Google Calendar</button>`;
    }
}

async function loadMySpaceGoogleEvents() {
    const statusResponse = await apiFetch("/my-space/calendar/status");
    if (!statusResponse.ok) return;
    const status = await statusResponse.json();
    renderMySpaceGoogleStatus(status);
    if (!status.connected) {
        mySpaceGoogleEvents = [];
        mySpaceGoogleCalendarError = "";
        renderMySpaceCalendar();
        return;
    }
    const start = new Date(mySpaceCalendarMonth.getFullYear(), mySpaceCalendarMonth.getMonth() - 1, 1);
    const end = new Date(mySpaceCalendarMonth.getFullYear(), mySpaceCalendarMonth.getMonth() + 4, 1);
    const params = new URLSearchParams({ time_min: start.toISOString(), time_max: end.toISOString() });
    const response = await apiFetch(`/my-space/calendar/events?${params.toString()}`);
    if (!response.ok) {
        mySpaceGoogleCalendarError = await extractErrorMessage(response);
        renderMySpaceCalendar();
        return;
    }
    const data = await response.json();
    mySpaceGoogleCalendarError = "";
    mySpaceGoogleEvents = Array.isArray(data.events) ? data.events : [];
    renderMySpaceCalendar();
}

async function connectMySpaceGoogleCalendar() {
    const response = await apiFetch("/my-space/calendar/google/connect", { method: "POST" });
    if (!response.ok) {
        alert(`Could not start Google Calendar connection: ${await extractErrorMessage(response)}`);
        return;
    }
    const data = await response.json();
    window.location.assign(data.authorization_url);
}

async function disconnectMySpaceGoogleCalendar() {
    if (!confirm("Disconnect your Google Calendar from My Space?")) return;
    const response = await apiFetch("/my-space/calendar/google/disconnect", { method: "POST" });
    if (!response.ok) {
        alert(`Could not disconnect Google Calendar: ${await extractErrorMessage(response)}`);
        return;
    }
    mySpaceGoogleEvents = [];
    mySpaceGoogleCalendarError = "";
    renderMySpaceGoogleStatus({ connected: false });
    renderMySpaceCalendar();
}

async function loadMySpace() {
    const r = await apiFetch("/my-space");
    if (!r.ok) {
        alert(`Failed to load My Space: ${await extractErrorMessage(r)}`);
        return;
    }
    const data = await r.json();
    const items = Array.isArray(data.todos) ? data.todos : [];
    const note = document.getElementById("mySpaceNote");
    if (note) note.value = data.note || "";
    renderMySpaceStats(items);
    renderMySpaceTodos(items);
    renderMySpaceQuickLinks(Array.isArray(data.quick_links) ? data.quick_links : []);
    renderMySpaceSnippets(Array.isArray(data.snippets) ? data.snippets : []);
    renderMySpaceGuides(Array.isArray(data.staff_guides) ? data.staff_guides : []);
    mySpaceLoadedOnce = true;
    await loadMySpaceGoogleEvents();
    if (new URLSearchParams(window.location.search).has("calendar_connected")) {
        const cleanUrl = `${window.location.pathname}${window.location.hash || ""}`;
        window.history.replaceState({}, "", cleanUrl);
    }
    loadNotifications();
}

async function addMySpaceTodo() {
    const titleEl = document.getElementById("mySpaceNewTitle");
    const dueEl = document.getElementById("mySpaceNewDue");
    const priorityEl = document.getElementById("mySpaceNewPriority");
    const typeEl = document.getElementById("mySpaceNewType");
    const followEl = document.getElementById("mySpaceNewFollowUpWith");
    const title = String(titleEl?.value || "").trim();
    const itemType = mySpaceCleanType(typeEl?.value);
    if (!title) {
        alert("Add a task title first.");
        return;
    }
    const editingId = mySpaceEditingTodoId;
    const r = await apiFetch(editingId ? `/my-space/todos/${editingId}` : "/my-space/todos", {
        method: editingId ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            title,
            priority: priorityEl?.value || "normal",
            bucket: "today",
            item_type: itemType,
            follow_up_with: itemType === "follow_up" ? (followEl?.value || "") : "",
            due_at: mySpaceDuePayload(dueEl?.value),
        }),
    });
    if (!r.ok) {
        alert(`Failed to ${editingId ? "update" : "add"} task: ${await extractErrorMessage(r)}`);
        return;
    }
    if (titleEl) titleEl.value = "";
    if (dueEl) dueEl.value = "";
    if (priorityEl) priorityEl.value = "normal";
    if (typeEl) typeEl.value = "task";
    if (followEl) {
        followEl.value = "";
    }
    toggleMySpaceNewFollowField("task");
    mySpaceEditingTodoId = null;
    document.getElementById("mySpaceAddTaskButton").textContent = "Add Task";
    document.getElementById("mySpaceCancelEdit")?.classList.add("hidden");
    await loadMySpace();
}

function editMySpaceTodo(todoId) {
    const item = mySpaceTodosCache.find((todo) => Number(todo.id) === Number(todoId));
    if (!item) return;
    mySpaceEditingTodoId = item.id;
    document.getElementById("mySpaceNewTitle").value = item.title || "";
    document.getElementById("mySpaceNewDue").value = item.due_at ? String(item.due_at).slice(0, 16) : "";
    document.getElementById("mySpaceNewPriority").value = item.priority || "normal";
    document.getElementById("mySpaceNewType").value = mySpaceCleanType(item.item_type);
    document.getElementById("mySpaceNewFollowUpWith").value = item.follow_up_with || "";
    toggleMySpaceNewFollowField(item.item_type);
    document.getElementById("mySpaceAddTaskButton").textContent = "Update Task";
    document.getElementById("mySpaceCancelEdit")?.classList.remove("hidden");
    document.getElementById("mySpaceNewTitle")?.focus();
}

function cancelMySpaceTodoEdit() {
    mySpaceEditingTodoId = null;
    ["mySpaceNewTitle", "mySpaceNewDue", "mySpaceNewFollowUpWith"].forEach((id) => { const el = document.getElementById(id); if (el) el.value = ""; });
    document.getElementById("mySpaceNewPriority").value = "normal";
    document.getElementById("mySpaceNewType").value = "task";
    toggleMySpaceNewFollowField("task");
    document.getElementById("mySpaceAddTaskButton").textContent = "Add Task";
    document.getElementById("mySpaceCancelEdit")?.classList.add("hidden");
}

async function saveMySpaceTodo(todoId) {
    const titleEl = document.querySelector(`[data-myspace-title="${todoId}"]`);
    const notesEl = document.querySelector(`[data-myspace-notes="${todoId}"]`);
    const priorityEl = document.querySelector(`[data-myspace-priority="${todoId}"]`);
    const dueEl = document.querySelector(`[data-myspace-due="${todoId}"]`);
    const bucketEl = document.querySelector(`[data-myspace-bucket="${todoId}"]`);
    const typeEl = document.querySelector(`[data-myspace-type="${todoId}"]`);
    const followEl = document.querySelector(`[data-myspace-follow="${todoId}"]`);
    const title = String(titleEl?.value || "").trim();
    const itemType = mySpaceCleanType(typeEl?.value);
    if (!title) {
        alert("Task title cannot be empty.");
        return;
    }
    const r = await apiFetch(`/my-space/todos/${todoId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            title,
            notes: notesEl?.value || "",
            priority: priorityEl?.value || "normal",
            bucket: mySpaceCleanBucket(bucketEl?.value),
            item_type: itemType,
            follow_up_with: itemType === "follow_up" ? (followEl?.value || "") : "",
            due_at: mySpaceDuePayload(dueEl?.value),
        }),
    });
    if (!r.ok) {
        alert(`Failed to save task: ${await extractErrorMessage(r)}`);
        return;
    }
    await loadMySpace();
}

async function toggleMySpaceTodo(todoId, isDone) {
    const r = await apiFetch(`/my-space/todos/${todoId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_done: !!isDone }),
    });
    if (!r.ok) {
        alert(`Failed to update task: ${await extractErrorMessage(r)}`);
        return;
    }
    await loadMySpace();
}

async function deleteMySpaceTodo(todoId) {
    if (!confirm("Delete this private task?")) return;
    const r = await apiFetch(`/my-space/todos/${todoId}`, { method: "DELETE" });
    if (!r.ok) {
        alert(`Failed to delete task: ${await extractErrorMessage(r)}`);
        return;
    }
    await loadMySpace();
}

function scheduleMySpaceNoteSave() {
    const status = document.getElementById("mySpaceNoteStatus");
    if (status) status.textContent = "Saving...";
    if (mySpaceSaveTimer) clearTimeout(mySpaceSaveTimer);
    mySpaceSaveTimer = setTimeout(saveMySpaceNote, 700);
}

async function saveMySpaceNote() {
    const note = document.getElementById("mySpaceNote");
    const status = document.getElementById("mySpaceNoteStatus");
    const r = await apiFetch("/my-space/note", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body: note?.value || "" }),
    });
    if (!r.ok) {
        if (status) status.textContent = "Save failed";
        return;
    }
    if (status) status.textContent = `Saved ${new Date().toLocaleTimeString()}`;
}

async function addMySpaceQuickLink() {
    const titleEl = document.getElementById("mySpaceLinkTitle");
    const urlEl = document.getElementById("mySpaceLinkUrl");
    const notesEl = document.getElementById("mySpaceLinkNotes");
    const title = String(titleEl?.value || "").trim();
    const url = String(urlEl?.value || "").trim();
    if (!title || !url) {
        alert("Add both a link title and full URL.");
        return;
    }
    const r = await apiFetch("/my-space/quick-links", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, url, notes: notesEl?.value || "" }),
    });
    if (!r.ok) {
        alert(`Failed to add quick link: ${await extractErrorMessage(r)}`);
        return;
    }
    if (titleEl) titleEl.value = "";
    if (urlEl) urlEl.value = "";
    if (notesEl) notesEl.value = "";
    await loadMySpace();
}

async function saveMySpaceQuickLink(linkId) {
    const titleEl = document.querySelector(`[data-myspace-link-title="${linkId}"]`);
    const urlEl = document.querySelector(`[data-myspace-link-url="${linkId}"]`);
    const notesEl = document.querySelector(`[data-myspace-link-notes="${linkId}"]`);
    const title = String(titleEl?.value || "").trim();
    const url = String(urlEl?.value || "").trim();
    if (!title || !url) {
        alert("Quick links need a title and full URL.");
        return;
    }
    const r = await apiFetch(`/my-space/quick-links/${linkId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, url, notes: notesEl?.value || "" }),
    });
    if (!r.ok) {
        alert(`Failed to save quick link: ${await extractErrorMessage(r)}`);
        return;
    }
    await loadMySpace();
}

async function deleteMySpaceQuickLink(linkId) {
    if (!confirm("Delete this quick link?")) return;
    const r = await apiFetch(`/my-space/quick-links/${linkId}`, { method: "DELETE" });
    if (!r.ok) {
        alert(`Failed to delete quick link: ${await extractErrorMessage(r)}`);
        return;
    }
    await loadMySpace();
}

async function addMySpaceSnippet() {
    const titleEl = document.getElementById("mySpaceSnippetTitle");
    const categoryEl = document.getElementById("mySpaceSnippetCategory");
    const bodyEl = document.getElementById("mySpaceSnippetBody");
    const title = String(titleEl?.value || "").trim();
    const body = String(bodyEl?.value || "").trim();
    if (!title || !body) {
        alert("Add a snippet title and body.");
        return;
    }
    const r = await apiFetch("/my-space/snippets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, body, category: categoryEl?.value || "" }),
    });
    if (!r.ok) {
        alert(`Failed to add snippet: ${await extractErrorMessage(r)}`);
        return;
    }
    if (titleEl) titleEl.value = "";
    if (categoryEl) categoryEl.value = "";
    if (bodyEl) bodyEl.value = "";
    await loadMySpace();
}

async function saveMySpaceSnippet(snippetId) {
    const titleEl = document.querySelector(`[data-myspace-snippet-title="${snippetId}"]`);
    const categoryEl = document.querySelector(`[data-myspace-snippet-category="${snippetId}"]`);
    const bodyEl = document.querySelector(`[data-myspace-snippet-body="${snippetId}"]`);
    const title = String(titleEl?.value || "").trim();
    const body = String(bodyEl?.value || "").trim();
    if (!title || !body) {
        alert("Snippets need a title and body.");
        return;
    }
    const r = await apiFetch(`/my-space/snippets/${snippetId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, body, category: categoryEl?.value || "" }),
    });
    if (!r.ok) {
        alert(`Failed to save snippet: ${await extractErrorMessage(r)}`);
        return;
    }
    await loadMySpace();
}

async function deleteMySpaceSnippet(snippetId) {
    if (!confirm("Delete this email snippet?")) return;
    const r = await apiFetch(`/my-space/snippets/${snippetId}`, { method: "DELETE" });
    if (!r.ok) {
        alert(`Failed to delete snippet: ${await extractErrorMessage(r)}`);
        return;
    }
    await loadMySpace();
}

async function copyMySpaceSnippet(snippetId) {
    const bodyEl = document.querySelector(`[data-myspace-snippet-body="${snippetId}"]`);
    const cached = mySpaceSnippetsCache.find((snippet) => Number(snippet.id) === Number(snippetId));
    const body = String(bodyEl?.value || cached?.body || "");
    if (!body.trim()) {
        alert("This snippet is empty.");
        return;
    }
    try {
        await navigator.clipboard.writeText(body);
        alert("Snippet copied.");
    } catch {
        bodyEl?.focus();
        bodyEl?.select();
        alert("Copy shortcut ready. Press Ctrl+C to copy the selected snippet.");
    }
}

async function uploadMySpaceGuide() {
    const titleEl = document.getElementById("mySpaceGuideTitle");
    const descriptionEl = document.getElementById("mySpaceGuideDescription");
    const fileEl = document.getElementById("mySpaceGuideFile");
    const title = String(titleEl?.value || "").trim();
    const file = fileEl?.files?.[0] || null;
    if (!title || !file) {
        alert("Add a guide title and select a PDF file.");
        return;
    }
    const form = new FormData();
    form.append("title", title);
    form.append("description", descriptionEl?.value || "");
    form.append("file", file);
    const r = await apiFetch("/my-space/staff-guides", { method: "POST", body: form });
    if (!r.ok) {
        alert(`Failed to upload staff guide: ${await extractErrorMessage(r)}`);
        return;
    }
    if (titleEl) titleEl.value = "";
    if (descriptionEl) descriptionEl.value = "";
    if (fileEl) fileEl.value = "";
    await loadMySpace();
}

async function openMySpaceGuide(guideId) {
    const viewer = window.open("", "_blank");
    if (viewer) {
        viewer.document.write("<title>Loading staff guide...</title><body style='font-family:Arial,sans-serif;padding:24px'>Loading staff guide...</body>");
    }
    const r = await apiFetch(`/my-space/staff-guides/${guideId}/view`);
    if (!r.ok) {
        if (viewer) viewer.close();
        alert(`Failed to open staff guide: ${await extractErrorMessage(r)}`);
        return;
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    if (viewer) {
        viewer.location.href = url;
    } else {
        window.open(url, "_blank");
    }
    setTimeout(() => URL.revokeObjectURL(url), 60000);
}

async function deleteMySpaceGuide(guideId) {
    if (!confirm("Delete this staff guide?")) return;
    const r = await apiFetch(`/my-space/staff-guides/${guideId}`, { method: "DELETE" });
    if (!r.ok) {
        alert(`Failed to delete staff guide: ${await extractErrorMessage(r)}`);
        return;
    }
    await loadMySpace();
}

const USER_ROLE_ACCESS = {
    ADMIN: {
        label: "Administrator",
        optionLabel: "Administrator",
        defaultAdminAccess: true,
        summary: "Full control over the portal, system users, settings, and all operational workspaces.",
        access: ["Portal Hub", "My Space", "Email Manager", "Maintenance", "Inspections", "Rent Tracker", "Lease Renewals", "Monthly Landlord Report", "Compliance", "Compliance Report", "Compliance Providers", "Properties", "Our Team", "Activity Log", "System"],
    },
    PM: {
        label: "Property Manager",
        optionLabel: "Property Manager",
        defaultAdminAccess: true,
        summary: "Property management access for inbox triage, maintenance, compliance, rent, and property work.",
        access: ["Portal Hub", "My Space", "Email Manager", "Maintenance", "Inspections", "Rent Tracker", "Lease Renewals", "Monthly Landlord Report", "Compliance", "Compliance Report", "Compliance Providers", "Properties", "Our Team", "Activity Log", "System"],
    },
    LEASING: {
        label: "Marketing Advisor",
        optionLabel: "Marketing Advisor",
        summary: "Marketing and leasing-focused access for email work, property information, and team contacts.",
        access: ["Portal Hub", "My Space", "Email Manager", "Inspections", "Lease Renewals", "Properties", "Our Team"],
    },
    SALES: {
        label: "Director",
        optionLabel: "Director",
        defaultAdminAccess: true,
        summary: "Director-level access across the core portal workspaces.",
        access: ["Portal Hub", "My Space", "Email Manager", "Maintenance", "Inspections", "Rent Tracker", "Lease Renewals", "Monthly Landlord Report", "Compliance", "Compliance Report", "Compliance Providers", "Properties", "Our Team", "Activity Log", "System"],
    },
    ACCOUNTS: {
        label: "Administrative Assistant",
        optionLabel: "Administrative Assistant",
        summary: "Administrative support access for inbox follow-up, rent operations, team contacts, and daily workspace tools.",
        access: ["Portal Hub", "My Space", "Email Manager", "Rent Tracker", "Our Team"],
    },
    READONLY: {
        label: "Read Only",
        optionLabel: "Read Only",
        summary: "View-only team member role. Use for staff who should not manage system access.",
        access: ["Portal Hub", "My Space", "Our Team"],
    },
};

const STAFF_ROLE_KEYS = ["SALES", "PM", "LEASING", "ACCOUNTS", "ADMIN"];
const TEAM_ROLE_PRIORITY = { SALES: 0, PM: 1, LEASING: 2, ACCOUNTS: 3, ADMIN: 4, READONLY: 5 };

const FALLBACK_PAGE_REGISTRY = [
    { id: "portal", label: "Portal Hub", description: "Landing dashboard and shortcuts.", section: "Core", locked: true },
    { id: "notifications", label: "Notification Center", description: "Assigned tickets and portal alerts.", section: "Core", locked: true },
    { id: "myspace", label: "My Space", description: "Private planner, follow-ups, links, snippets, notes, and guides.", section: "Core" },
    { id: "inbox", label: "Email Manager", description: "Email tickets and inbox operations.", section: "Operations" },
    { id: "maintenance", label: "Maintenance", description: "Maintenance orders, owner approvals, quotes, tradie arrangements, and completion tracking.", section: "Operations" },
    { id: "inspections", label: "Inspections", description: "Multi-agent inspection planning, route optimisation, timings, buffers, and conflict checks.", section: "Operations" },
    { id: "checklist", label: "Checklist", description: "Start operational checklists, track work in progress, and review completed reports.", section: "Operations" },
    { id: "rent", label: "Rent Tracker", description: "Rent tracking and reports.", section: "Operations" },
    { id: "lease_renewals", label: "Lease Renewals", description: "Lease renewal dates, signatures, rent review tracking, follow-ups, and reporting.", section: "Operations" },
    { id: "landlord_reports", label: "Monthly Landlord Report", description: "Owner-facing monthly property reports and branded PDF generation.", section: "Operations" },
    { id: "compliance", label: "Compliance", description: "Compliance records and due dates.", section: "Compliance" },
    { id: "coverage", label: "Compliance Report", description: "Missing and incomplete compliance checks.", section: "Compliance" },
    { id: "compliance_providers", label: "Compliance Providers", description: "Reusable provider contacts for compliance records.", section: "Compliance" },
    { id: "properties", label: "Properties", description: "Managed property register.", section: "Setup" },
    { id: "team", label: "Our Team", description: "Registered staff profiles and contact details.", section: "Setup" },
    { id: "activity", label: "Activity Log", description: "Staff actions, platform changes, and audit trail.", section: "Setup" },
    { id: "system", label: "System", description: "Admin user and access controls.", section: "Setup", admin_only: true },
];

function userRoleKeys(includeLegacy = false) {
    if (!includeLegacy) return STAFF_ROLE_KEYS.slice();
    return [...STAFF_ROLE_KEYS, ...Object.keys(USER_ROLE_ACCESS).filter((role) => !STAFF_ROLE_KEYS.includes(role))];
}

function roleMeta(role) {
    const key = String(role || "READONLY").toUpperCase();
    return USER_ROLE_ACCESS[key] || USER_ROLE_ACCESS.READONLY;
}

function roleTitle(role, options = {}) {
    const meta = roleMeta(role);
    return options.option ? (meta.optionLabel || meta.label || String(role || "")) : (meta.label || String(role || ""));
}

function roleHasSystemAccess(role) {
    const roleKey = String(role || "READONLY").toUpperCase();
    const pages = rolePagePermissions[roleKey];
    if (Array.isArray(pages)) return pages.includes("system");
    return !!roleMeta(roleKey).defaultAdminAccess;
}

function staffOptionLabel(user) {
    const name = user?.name || user?.email || "Staff";
    const title = roleTitle(user?.role);
    return title ? `${name} (${title})` : name;
}

function allPageDefinitions() {
    return Array.isArray(pageRegistry) && pageRegistry.length ? pageRegistry : FALLBACK_PAGE_REGISTRY;
}

function pageLabel(pageId) {
    const page = allPageDefinitions().find((p) => p.id === pageId);
    return page ? page.label : pageId;
}

function normalizePageList(values) {
    const selected = new Set(Array.isArray(values) ? values : []);
    return allPageDefinitions().map((p) => p.id).filter((id) => selected.has(id));
}

function setUsersError(message = "", type = "error") {
    const el = document.getElementById("usersError");
    if (!el) return;
    el.textContent = message;
    el.style.display = message ? "block" : "none";
    el.classList.toggle("success", type === "success");
}

function canAccessPage(pageId) {
    if (pageId === "portal") return true;
    return allowedPages.has(pageId);
}

function firstAccessiblePage() {
    const pages = allPageDefinitions().map((p) => p.id);
    return pages.find((pageId) => canAccessPage(pageId)) || "portal";
}

function sideSubnavStorageKey(group) {
    return `agent_side_subnav_${group}_collapsed`;
}

function isSideSubnavCollapsed(group) {
    const stored = localStorage.getItem(sideSubnavStorageKey(group));
    return stored === null ? true : stored === "1";
}

function applySideSubnavState() {
    ["myspace", "maintenance", "lease", "landlord_reports", "compliance", "checklist"].forEach((group) => {
        const navGroup = document.querySelector(`[data-side-subnav-group="${group}"]`);
        const toggle = document.getElementById(`${group}SubnavToggle`);
        if (!navGroup) return;
        const collapsed = isSideSubnavCollapsed(group);
        navGroup.classList.toggle("subnav-collapsed", collapsed);
        if (toggle) toggle.setAttribute("aria-expanded", String(!collapsed));
    });
}

function toggleSideSubnav(group) {
    const collapsed = !isSideSubnavCollapsed(group);
    localStorage.setItem(sideSubnavStorageKey(group), collapsed ? "1" : "0");
    applySideSubnavState();
}

function openLeaseRenewalPrimary() {
    if (canAccessPage("lease_renewals")) {
        switchDashboardTab("lease_renewals");
        switchLeaseRenewalView("dashboard");
    } else {
        alert("This page is not assigned to your role.");
    }
}

function openCompliancePrimary() {
    if (canAccessPage("compliance")) {
        switchDashboardTab("compliance");
    } else if (canAccessPage("coverage")) {
        switchDashboardTab("coverage");
    } else if (canAccessPage("compliance_providers")) {
        switchDashboardTab("compliance_providers");
    } else {
        alert("This page is not assigned to your role.");
    }
}

function applyPageVisibility() {
    const navMap = {
        portal: "navPortal",
        myspace: "navMySpace",
        inbox: "navInbox",
        maintenance: "navMaintenance",
        inspections: "navInspections",
        checklist: "navChecklist",
        rent: "navRentTracker",
        lease_renewals: "navLeaseRenewals",
        landlord_reports: "navLandlordReports",
        compliance: "navCompliance",
        coverage: "navComplianceCoverage",
        compliance_providers: "navComplianceProviders",
        properties: "navProperties",
        team: "navTeam",
        activity: "navActivity",
        system: "btnSystemUsers",
    };
    for (const [pageId, elementId] of Object.entries(navMap)) {
        const el = document.getElementById(elementId);
        if (!el) continue;
        const visible = canAccessPage(pageId);
        el.classList.toggle("hidden", !visible);
        if (elementId === "btnSystemUsers") {
            el.style.display = visible ? "flex" : "none";
        }
    }
    const maintenanceSideSubnav = document.getElementById("maintenanceSideSubnav");
    if (maintenanceSideSubnav) maintenanceSideSubnav.classList.toggle("hidden", !canAccessPage("maintenance"));
    const maintenanceNavGroup = document.getElementById("maintenanceNavGroup");
    if (maintenanceNavGroup) maintenanceNavGroup.classList.toggle("hidden", !canAccessPage("maintenance"));
    const mySpaceSideSubnav = document.getElementById("mySpaceSideSubnav");
    if (mySpaceSideSubnav) mySpaceSideSubnav.classList.toggle("hidden", !canAccessPage("myspace"));
    const mySpaceNavGroup = document.getElementById("mySpaceNavGroup");
    if (mySpaceNavGroup) mySpaceNavGroup.classList.toggle("hidden", !canAccessPage("myspace"));
    const leaseSideSubnav = document.getElementById("leaseSideSubnav");
    if (leaseSideSubnav) leaseSideSubnav.classList.toggle("hidden", !canAccessPage("lease_renewals"));
    const leaseNavGroup = document.getElementById("leaseNavGroup");
    if (leaseNavGroup) leaseNavGroup.classList.toggle("hidden", !canAccessPage("lease_renewals"));
    const landlordReportsNavGroup = document.getElementById("landlordReportsNavGroup");
    if (landlordReportsNavGroup) landlordReportsNavGroup.classList.toggle("hidden", !canAccessPage("landlord_reports"));
    const complianceMenuVisible = canAccessPage("compliance") || canAccessPage("coverage") || canAccessPage("compliance_providers");
    const complianceNavGroup = document.getElementById("complianceNavGroup");
    if (complianceNavGroup) complianceNavGroup.classList.toggle("hidden", !complianceMenuVisible);
    const complianceSideSubnav = document.getElementById("complianceSideSubnav");
    if (complianceSideSubnav) complianceSideSubnav.classList.toggle("hidden", !complianceMenuVisible);
    const navCompliance = document.getElementById("navCompliance");
    if (navCompliance) navCompliance.classList.toggle("hidden", !complianceMenuVisible);
    applySideSubnavState();

    document.querySelectorAll("[data-page-tile]").forEach((tile) => {
        const pageId = tile.getAttribute("data-page-tile") || "";
        tile.classList.toggle("hidden", !canAccessPage(pageId));
    });

    const flushPropertiesBtn = document.getElementById("btnFlushProperties");
    if (flushPropertiesBtn) {
        flushPropertiesBtn.classList.toggle("hidden", !canAccessPage("system"));
    }

    if (!canAccessPage(currentDashboardTab)) {
        switchDashboardTab(firstAccessiblePage());
    }
}

async function loadCurrentPageAccess() {
    try {
        const r = await apiFetch("/user-auth/page-access");
        if (!r.ok) throw new Error(await extractErrorMessage(r));
        const data = await r.json();
        pageRegistry = Array.isArray(data.pages) ? data.pages : FALLBACK_PAGE_REGISTRY;
        allowedPages = new Set(normalizePageList(data.allowed_pages || ["portal"]));
        allowedPages.add("portal");
    } catch {
        pageRegistry = FALLBACK_PAGE_REGISTRY;
        const role = String(currentUser?.role || "READONLY").toUpperCase();
        const fallback = (USER_ROLE_ACCESS[role]?.access || []).map((label) => {
            const match = FALLBACK_PAGE_REGISTRY.find((p) => p.label === label);
            return match ? match.id : "";
        }).filter(Boolean);
        allowedPages = new Set(["portal", ...fallback]);
        if (roleHasSystemAccess(role)) allowedPages.add("system");
    }
    applyPageVisibility();
}

async function loadRolePageAccess() {
    const r = await apiFetch("/user-auth/role-page-access");
    if (!r.ok) {
        setUsersError(`Failed to load access controls: ${await extractErrorMessage(r)}`);
        return false;
    }
    const data = await r.json();
    pageRegistry = Array.isArray(data.pages) ? data.pages : FALLBACK_PAGE_REGISTRY;
    rolePagePermissions = data.permissions || {};
    return true;
}

function openUsersModal() {
    switchDashboardTab("system");
}

function closeUsersModal() {
    switchDashboardTab("portal");
}

function userInitials(user) {
    const raw = String(user?.name || user?.email || "?").trim();
    const parts = raw.includes("@")
        ? raw.split("@")[0].split(/[._-]+/)
        : raw.split(/\s+/);
    const initials = parts.filter(Boolean).slice(0, 2).map((x) => x[0]).join("");
    return escapeHtml((initials || "?").toUpperCase());
}

function avatarBlock(user, className = "system-avatar") {
    const initials = userInitials(user);
    if (!user?.avatar_url) {
        return `<span class="${className} avatar-fallback">${initials}</span>`;
    }
    return `
        <span class="avatar-wrap">
            <img class="${className}" src="${escapeHtml(user.avatar_url)}" alt="${escapeHtml(user.name || "User avatar")}" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';" />
            <span class="${className} avatar-fallback" style="display:none">${initials}</span>
        </span>
    `;
}

function formatUserDate(value) {
    if (!value) return "Never";
    try { return new Date(value).toLocaleString(); } catch { return value; }
}

function roleOptions(selected) {
    const selectedRole = String(selected || "").toUpperCase();
    const roles = userRoleKeys(STAFF_ROLE_KEYS.includes(selectedRole) ? false : true);
    return roles.map((role) => {
        return `<option value="${role}" ${role === selectedRole ? "selected" : ""}>${escapeHtml(roleTitle(role, { option: true }))}</option>`;
    }).join("");
}

function accessChips(role) {
    const allowed = rolePagePermissions[role] || [];
    const labels = allowed.length
        ? allowed.map(pageLabel)
        : (USER_ROLE_ACCESS[role]?.access || ["Portal Hub"]);
    return labels.map((item) => `<span class="access-chip">${escapeHtml(item)}</span>`).join("");
}

function renderRoleMatrix() {
    const target = document.getElementById("roleMatrix");
    if (!target) return;
    target.innerHTML = userRoleKeys().map((role) => {
        const meta = roleMeta(role);
        const allowed = new Set(rolePagePermissions[role] || []);
        return `
            <div class="role-card">
                <div class="role-card-head">
                    <span>${escapeHtml(meta.label)}</span>
                    <small>${roleHasSystemAccess(role) ? "System access" : "Staff access"}</small>
                </div>
                <p>${escapeHtml(meta.summary)}</p>
                <div class="page-permission-grid">
                    ${allPageDefinitions().map((page) => {
                        const pageId = page.id;
                        const isPortal = pageId === "portal";
                        const isSystem = pageId === "system";
                        const disabled = isPortal;
                        const checked = isPortal || allowed.has(pageId);
                        const lockedText = isPortal ? "Always shown" : (isSystem ? "Controls System/admin access" : "");
                        return `
                            <label class="page-permission ${disabled ? "locked" : ""}">
                                <input type="checkbox" data-role-page="${role}" value="${escapeHtml(pageId)}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""} />
                                <span>
                                    <strong>${escapeHtml(page.label || pageId)}</strong>
                                    <small>${escapeHtml(lockedText || page.description || "")}</small>
                                </span>
                            </label>
                        `;
                    }).join("")}
                </div>
            </div>
        `;
    }).join("");
}

async function saveRolePageAccess() {
    setUsersError("");
    const next = {};
    for (const role of userRoleKeys()) {
        const values = Array.from(document.querySelectorAll(`[data-role-page="${role}"]:checked`))
            .map((input) => input.value);
        if (!values.includes("portal")) values.unshift("portal");
        next[role] = normalizePageList(values);
    }

    const r = await apiFetch("/user-auth/role-page-access", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ permissions: next }),
    });
    if (!r.ok) {
        setUsersError(`Failed to save page access: ${await extractErrorMessage(r)}`);
        return;
    }
    const data = await r.json();
    pageRegistry = Array.isArray(data.pages) ? data.pages : pageRegistry;
    rolePagePermissions = data.permissions || next;
    setUsersError("Page access updated. Staff will see the new menu after refresh or next login.", "success");
    renderRoleMatrix();
    if (teamLoadedOnce) await loadTeamDirectory(true);
    await loadCurrentPageAccess();
}

function renderSystemStats() {
    const target = document.getElementById("systemStats");
    if (!target) return;
    const total = usersCache.length;
    const active = usersCache.filter((u) => u.is_active).length;
    const admins = usersCache.filter((u) => roleHasSystemAccess(u.role) && u.is_active).length;
    const pending = usersCache.filter((u) => u.must_change_password).length;
    target.innerHTML = `
        <div class="system-stat"><span>Total users</span><strong>${total}</strong></div>
        <div class="system-stat"><span>Active</span><strong>${active}</strong></div>
        <div class="system-stat"><span>Admin access</span><strong>${admins}</strong></div>
        <div class="system-stat"><span>Password change due</span><strong>${pending}</strong></div>
    `;
}

async function renderUsersList() {
    await loadUsersCache();
    await loadRolePageAccess();
    usersLoadedOnce = true;
    renderSystemStats();
    renderRoleMatrix();

    const list = document.getElementById("usersList");
    if (!list) return;
    if (!usersCache.length) {
        list.innerHTML = `<div class="ticket-empty"><strong>No users found</strong><div class="small muted" style="margin-top:6px">Create the first team member using the form above.</div></div>`;
        return;
    }
    list.innerHTML = usersCache.map((u) => {
        const role = String(u.role || "READONLY");
        const meta = roleMeta(role);
        const status = u.is_active ? "Active" : "Disabled";
        const passwordFlag = u.must_change_password ? `<span class="user-chip warn">Must change password</span>` : `<span class="user-chip">Password OK</span>`;
        const phoneText = String(u.phone || "").trim();
        return `
            <article class="system-user-card">
                <div class="system-user-head">
                    <div class="system-user-identity">
                        ${avatarBlock(u)}
                        <div>
                            <h3>${escapeHtml(u.name || "Unnamed user")}</h3>
                            <p>${escapeHtml(u.email || "")}</p>
                            <div class="user-chip-row">
                                <span class="user-chip ${u.is_active ? "ok" : "danger"}">${status}</span>
                                <span class="user-chip">${escapeHtml(meta.label)}</span>
                                ${roleHasSystemAccess(role) ? `<span class="user-chip warn">System access</span>` : ""}
                                ${passwordFlag}
                            </div>
                        </div>
                    </div>
                    <label class="btn avatar-upload-btn">
                        Change Avatar
                        <input type="file" accept="image/*" onchange="uploadUserAvatar(${u.id}, this)" />
                    </label>
                </div>

                <div class="system-user-form">
                    <label class="field">
                        <div class="label">Display name</div>
                        <input type="text" data-user-name="${u.id}" value="${escapeHtml(u.name || "")}" />
                    </label>
                    <label class="field">
                        <div class="label">Phone</div>
                        <input type="tel" data-user-phone="${u.id}" value="${escapeHtml(phoneText)}" placeholder="Staff phone number" />
                    </label>
                    <label class="field">
                        <div class="label">Staff title and access</div>
                        <select data-user-role="${u.id}">
                            ${roleOptions(role)}
                        </select>
                    </label>
                    <label class="system-toggle">
                        <input type="checkbox" ${u.is_active ? "checked" : ""} data-user-active="${u.id}" />
                        <span>
                            <strong>Account active</strong>
                            <small>Disabled users cannot sign in.</small>
                        </span>
                    </label>
                </div>

                <div class="access-preview">
                    <div>
                        <strong>${escapeHtml(meta.label)}</strong>
                        <p>${escapeHtml(meta.summary)}</p>
                    </div>
                    <div class="access-chip-row">${accessChips(role)}</div>
                </div>

                <div class="system-user-meta">
                    <span>Last login: ${escapeHtml(formatUserDate(u.last_login_at))}</span>
                    <span>Password changed: ${escapeHtml(formatUserDate(u.password_changed_at))}</span>
                </div>

                <div class="system-user-actions">
                    <input type="password" data-user-password="${u.id}" placeholder="New password for reset" />
                    <label class="checkbox compact">
                        <input type="checkbox" data-user-force-reset="${u.id}" checked />
                        Force change
                    </label>
                    <button class="btn" onclick="adminResetPassword(${u.id})">Reset Password</button>
                    <button class="btn primary" onclick="saveUserEdits(${u.id})">Save Changes</button>
                    <button class="btn danger" onclick="deleteUser(${u.id})">Delete</button>
                </div>
            </article>
        `;
    }).join("");
}

async function saveUserEdits(userId) {
    const nameEl = document.querySelector(`[data-user-name="${userId}"]`);
    const phoneEl = document.querySelector(`[data-user-phone="${userId}"]`);
    const roleSel = document.querySelector(`[data-user-role="${userId}"]`);
    const activeChk = document.querySelector(`[data-user-active="${userId}"]`);
    const payload = {
        name: nameEl ? String(nameEl.value || "").trim() : undefined,
        phone: phoneEl ? String(phoneEl.value || "").trim() : undefined,
        role: roleSel ? roleSel.value : undefined,
        is_active: activeChk ? !!activeChk.checked : undefined,
    };
    const r = await apiFetch(`/user-auth/users/${userId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    if (!r.ok) {
        setUsersError(`Failed to update user: ${await extractErrorMessage(r)}`);
        return;
    }
    setUsersError("User account updated.", "success");
    await renderUsersList();
    if (teamLoadedOnce) await loadTeamDirectory(true);
    await refreshAssigneeViews();
    await ensureAuthenticated();
}

async function createUserFromForm() {
    setUsersError("");
    const email = document.getElementById("newUserEmail").value.trim();
    const name = document.getElementById("newUserName").value.trim();
    const phone = document.getElementById("newUserPhone")?.value.trim() || "";
    const role = document.getElementById("newUserRole").value;
    const password = document.getElementById("newUserPassword").value;
    const force = !!document.getElementById("newUserForcePassword")?.checked;
    if (!email || !name || !password) {
        setUsersError("Email, name and password are required.");
        return;
    }
    const r = await apiFetch("/user-auth/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, name, phone, role, password, is_active: true, must_change_password: force }),
    });
    if (!r.ok) {
        setUsersError(`Failed to create user: ${await extractErrorMessage(r)}`);
        return;
    }
    document.getElementById("newUserEmail").value = "";
    document.getElementById("newUserName").value = "";
    const phoneInput = document.getElementById("newUserPhone");
    if (phoneInput) phoneInput.value = "";
    document.getElementById("newUserPassword").value = "";
    if (document.getElementById("newUserForcePassword")) document.getElementById("newUserForcePassword").checked = true;
    setUsersError("User account created.", "success");
    await renderUsersList();
    if (teamLoadedOnce) await loadTeamDirectory(true);
    await refreshAssigneeViews();
}

async function uploadUserAvatar(userId, input) {
    setUsersError("");
    const file = input && input.files ? input.files[0] : null;
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    const r = await apiFetch(`/user-auth/users/${userId}/avatar`, { method: "POST", body: form });
    if (!r.ok) {
        setUsersError(`Failed to upload avatar: ${await extractErrorMessage(r)}`);
        return;
    }
    const updated = await r.json();
    if (currentUser && Number(currentUser.id) === Number(userId)) {
        currentUser = updated;
        const accountAvatar = document.getElementById("accountBarAvatar");
        if (accountAvatar) accountAvatar.src = updated.avatar_url || "/static/logo.png";
    }
    setUsersError("Avatar updated.", "success");
    await renderUsersList();
    if (teamLoadedOnce) await loadTeamDirectory(true);
}

async function adminResetPassword(userId) {
    setUsersError("");
    const el = document.querySelector(`[data-user-password="${userId}"]`);
    const forceEl = document.querySelector(`[data-user-force-reset="${userId}"]`);
    const pw = el ? String(el.value || "") : "";
    if (!pw) {
        setUsersError("Enter a new password before resetting.");
        return;
    }
    const r = await apiFetch(`/user-auth/users/${userId}/password`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_password: pw, force_change_on_next_login: forceEl ? !!forceEl.checked : true }),
    });
    if (!r.ok) {
        setUsersError(`Password reset failed: ${await extractErrorMessage(r)}`);
        return;
    }
    if (el) el.value = "";
    setUsersError("Password reset successfully.", "success");
    await renderUsersList();
}

async function deleteUser(userId) {
    setUsersError("");
    if (!confirm("Delete this user permanently? This cannot be undone.")) return;
    const r = await apiFetch(`/user-auth/users/${userId}`, { method: "DELETE" });
    if (!r.ok) {
        setUsersError(`Delete failed: ${await extractErrorMessage(r)}`);
        return;
    }
    setUsersError("User deleted.", "success");
    await renderUsersList();
    if (teamLoadedOnce) await loadTeamDirectory(true);
    await refreshAssigneeViews();
}

function teamContactLink(user, kind) {
    if (kind === "phone") {
        const phone = String(user?.phone || "").trim();
        if (!phone) return `<span class="team-muted">No phone added</span>`;
        const href = phone.replace(/[^\d+]/g, "");
        return `<a href="tel:${escapeHtml(href)}">${escapeHtml(phone)}</a>`;
    }
    const email = String(user?.email || "").trim();
    if (!email) return `<span class="team-muted">No email added</span>`;
    return `<a href="mailto:${escapeHtml(email)}">${escapeHtml(email)}</a>`;
}

function renderTeamDirectory(users) {
    const target = document.getElementById("teamDirectoryGrid");
    const count = document.getElementById("teamDirectoryCount");
    if (!target) return;
    const activeUsers = (Array.isArray(users) ? users.filter((u) => u && u.is_active !== false) : [])
        .sort((a, b) => {
            const aRole = String(a?.role || "").toUpperCase();
            const bRole = String(b?.role || "").toUpperCase();
            const aRank = TEAM_ROLE_PRIORITY[aRole] ?? 99;
            const bRank = TEAM_ROLE_PRIORITY[bRole] ?? 99;
            if (aRank !== bRank) return aRank - bRank;
            return String(a?.name || a?.email || "").localeCompare(String(b?.name || b?.email || ""));
        });
    if (count) count.textContent = `${activeUsers.length} active team member${activeUsers.length === 1 ? "" : "s"}`;
    if (!activeUsers.length) {
        target.innerHTML = `<div class="ticket-empty"><strong>No active team members found</strong><div class="small muted" style="margin-top:6px">Create staff accounts in System and they will appear here.</div></div>`;
        return;
    }
    target.innerHTML = activeUsers.map((u) => {
        const meta = roleMeta(u.role);
        return `
            <article class="team-card">
                <div class="team-card-top">
                    ${avatarBlock(u, "team-avatar")}
                    <div class="team-card-identity">
                        <h3>${escapeHtml(u.name || "Unnamed team member")}</h3>
                        <div class="team-role-line">
                            <span class="team-role">${escapeHtml(meta.label)}</span>
                            ${u.admin_access ? `<span class="team-admin-pill">System access</span>` : ""}
                        </div>
                    </div>
                </div>
                <div class="team-contact-list">
                    <div>
                        <i class="team-contact-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M4 6.5 12 13l8-6.5"/><rect x="3" y="5" width="18" height="14" rx="3"/></svg></i>
                        <span>Email</span>
                        ${teamContactLink(u, "email")}
                    </div>
                    <div>
                        <i class="team-contact-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M8.2 3.5 10.6 8 8.8 9.8a15.2 15.2 0 0 0 5.4 5.4l1.8-1.8 4.5 2.4-.5 3.1a2 2 0 0 1-2 1.6C10 20.5 3.5 14 3.5 6a2 2 0 0 1 1.6-2Z"/></svg></i>
                        <span>Phone</span>
                        ${teamContactLink(u, "phone")}
                    </div>
                </div>
                <div class="team-card-foot">
                    <i class="team-status-dot" aria-hidden="true"></i>
                    <span>${u.last_login_at ? `Last login ${escapeHtml(formatUserDate(u.last_login_at))}` : "Login not recorded yet"}</span>
                </div>
            </article>
        `;
    }).join("");
}

async function loadTeamDirectory(force = false) {
    const target = document.getElementById("teamDirectoryGrid");
    if (!target) return;
    if (!force && teamLoadedOnce && target.innerHTML.trim()) return;
    target.innerHTML = `<div class="ticket-empty"><strong>Loading team directory...</strong><div class="small muted" style="margin-top:6px">Gathering registered staff profiles.</div></div>`;
    try {
        const r = await apiFetch("/user-auth/team");
        if (!r.ok) throw new Error(await extractErrorMessage(r));
        const data = await r.json();
        teamLoadedOnce = true;
        renderTeamDirectory(data);
    } catch (e) {
        target.innerHTML = `<div class="ticket-empty"><strong>Could not load team directory</strong><div class="small muted" style="margin-top:6px">${escapeHtml(String(e?.message || e || "Please try again."))}</div></div>`;
    }
}

function activityStatusMeta(statusCode) {
    const code = Number(statusCode || 0);
    if (code >= 500) return { label: `Failed ${code}`, cls: "danger" };
    if (code >= 400) return { label: `Blocked ${code}`, cls: "warning" };
    if (code >= 200 && code < 300) return { label: "Successful", cls: "success" };
    return { label: code ? `Status ${code}` : "Recorded", cls: "neutral" };
}

function activityActorLabel(item) {
    const name = item?.actor_name || "Unknown staff";
    const role = item?.actor_role ? roleTitle(item.actor_role) : "";
    return role ? `${name} (${role})` : name;
}

function activityActorUser(item) {
    const actorId = Number(item?.actor_user_id || 0);
    if (!actorId) return null;
    return (Array.isArray(usersCache) ? usersCache : []).find((u) => Number(u.id) === actorId) || null;
}

function activityActorAvatar(item) {
    const user = activityActorUser(item) || {};
    const name = item?.actor_name || user.name || item?.actor_email || "Staff";
    const initials = userInitials({ name });
    if (user.avatar_url) {
        return `
          <span class="activity-avatar-wrap">
            <img class="activity-avatar" src="${escapeHtml(user.avatar_url)}" alt="${escapeHtml(name)} avatar" onerror="this.style.display='none';this.nextElementSibling.style.display='inline-flex';" />
            <span class="activity-avatar activity-avatar-fallback">${initials}</span>
          </span>
        `;
    }
    return `<span class="activity-avatar-wrap"><span class="activity-avatar">${initials}</span></span>`;
}

function renderActivityStats(summary = {}) {
    const target = document.getElementById("activityStats");
    if (!target) return;
    const cards = [
        ["Today", summary.today || 0, "Actions recorded today"],
        ["Last 24h", summary.last_24h || 0, "Recent platform actions"],
        ["Staff Active", summary.staff_24h || 0, "Staff with logged actions"],
        ["Failed", summary.failed_24h || 0, "Blocked or failed actions"],
    ];
    target.innerHTML = cards.map(([label, value, hint]) => `
      <div class="activity-stat">
        <span>${escapeHtml(label)}</span>
        <strong>${Number(value || 0)}</strong>
        <small>${escapeHtml(hint)}</small>
      </div>
    `).join("");
}

function renderActivityAreaChart(summary = {}) {
    const target = document.getElementById("activityAreaChart");
    if (!target) return;
    const rows = Array.isArray(summary.areas_24h) ? summary.areas_24h : [];
    if (!rows.length) {
        target.innerHTML = `<div class="small muted">No activity recorded in the last 24 hours yet.</div>`;
        return;
    }
    const max = Math.max(...rows.map((row) => Number(row.count || 0)), 1);
    target.innerHTML = rows.map((row) => {
        const count = Number(row.count || 0);
        const width = Math.round((count / max) * 100);
        return `
          <div class="activity-bar-row">
            <span>${escapeHtml(row.area || "Portal")}</span>
            <div class="activity-bar-track"><div class="activity-bar-fill" style="width:${width}%"></div></div>
            <strong>${count}</strong>
          </div>
        `;
    }).join("");
}

function renderActivityActorOptions() {
    const select = document.getElementById("activityActorFilter");
    if (!select) return;
    const current = select.value || "";
    const options = (Array.isArray(usersCache) ? usersCache : [])
        .filter((u) => u && u.id)
        .sort((a, b) => String(a.name || a.email || "").localeCompare(String(b.name || b.email || "")));
    select.innerHTML = `<option value="">All staff</option>` + options.map((u) => (
        `<option value="${Number(u.id)}">${escapeHtml(staffOptionLabel(u))}</option>`
    )).join("");
    select.value = current;
}

function renderActivityAreaOptions() {
    const select = document.getElementById("activityAreaFilter");
    if (!select) return;
    const current = select.value || "";
    const defaults = ["Email Manager", "Maintenance", "Compliance", "Properties", "Rent Tracker", "My Space", "System Access", "Tenant Portal"];
    const areas = Array.from(new Set([...defaults, ...(Array.isArray(activityAreasCache) ? activityAreasCache : [])])).filter(Boolean).sort();
    select.innerHTML = `<option value="">All areas</option>` + areas.map((area) => (
        `<option value="${escapeHtml(area)}">${escapeHtml(area)}</option>`
    )).join("");
    select.value = areas.includes(current) ? current : "";
}

async function loadActivityAreas() {
    if (!canAccessPage("activity")) return;
    try {
        const r = await apiFetch("/activity-log/areas");
        if (!r.ok) return;
        const data = await r.json();
        activityAreasCache = Array.isArray(data.items) ? data.items : [];
        renderActivityAreaOptions();
    } catch {
        renderActivityAreaOptions();
    }
}

function activityQueryParams(page = currentActivityPage) {
    const params = new URLSearchParams();
    params.set("page", String(page || 1));
    params.set("page_size", "30");
    const mapping = [
        ["activitySearch", "q"],
        ["activityActorFilter", "actor_user_id"],
        ["activityAreaFilter", "area"],
        ["activityMailboxFilter", "mailbox"],
        ["activityStartDate", "start"],
        ["activityEndDate", "end"],
    ];
    for (const [id, key] of mapping) {
        const value = String(document.getElementById(id)?.value || "").trim();
        if (value) params.set(key, value);
    }
    return params;
}

function activityPaginationItems(page, totalPages) {
    const total = Math.max(Number(totalPages || 1), 1);
    const current = Math.min(Math.max(Number(page || 1), 1), total);
    if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
    const items = new Set([1, total, current - 1, current, current + 1]);
    if (current <= 3) [2, 3, 4].forEach((item) => items.add(item));
    if (current >= total - 2) [total - 3, total - 2, total - 1].forEach((item) => items.add(item));
    return Array.from(items)
        .filter((item) => item >= 1 && item <= total)
        .sort((a, b) => a - b);
}

function renderActivityPagination(data = {}) {
    const pageInfo = document.getElementById("activityPageInfo");
    const prevBtn = document.getElementById("activityPrevBtn");
    const nextBtn = document.getElementById("activityNextBtn");
    const pageNumbers = document.getElementById("activityPageNumbers");
    const total = Number(data.total || 0);
    const pageSize = Math.max(Number(data.page_size || 30), 1);
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    const page = Math.min(Math.max(Number(data.page || currentActivityPage || 1), 1), totalPages);

    activityTotalPages = totalPages;
    currentActivityPage = page;

    const windowDays = Number(data.window_days || 7);
    if (pageInfo) pageInfo.textContent = `Last ${windowDays} days - Page ${page} of ${totalPages} - ${total} recorded actions`;
    if (prevBtn) prevBtn.disabled = page <= 1;
    if (nextBtn) nextBtn.disabled = page >= totalPages || !data.has_more;
    if (!pageNumbers) return;
    if (totalPages <= 1) {
        pageNumbers.innerHTML = "";
        return;
    }

    pageNumbers.innerHTML = activityPaginationItems(page, totalPages).map((item, index, arr) => {
        const isActive = Number(item) === page;
        const gap = index > 0 && item - arr[index - 1] > 1 ? `<span class="activity-page-gap">...</span>` : "";
        return `${gap}
          <button class="activity-page-btn ${isActive ? "active" : ""}" type="button" onclick="goActivityPage(${Number(item)})" ${isActive ? 'aria-current="page"' : ""}>
            ${Number(item)}
          </button>
        `;
    }).join("");
}

function renderActivityLog(data = {}) {
    const list = document.getElementById("activityList");
    renderActivityStats(data.summary || {});
    renderActivityAreaChart(data.summary || {});
    renderActivityPagination(data);
    if (!list) return;
    const items = Array.isArray(data.items) ? data.items : [];
    if (!items.length) {
        list.innerHTML = `<div class="ticket-empty"><strong>No activity found in the last 7 days</strong><div class="small muted" style="margin-top:6px">Try clearing filters or perform a platform action to create the first record.</div></div>`;
        return;
    }
    list.innerHTML = items.map((item) => {
        const status = activityStatusMeta(item.status_code);
        const target = item.entity_label || [item.entity_type, item.entity_id].filter(Boolean).join(" ");
        return `
          <article class="activity-row">
            ${activityActorAvatar(item)}
            <div class="activity-row-main">
              <span class="activity-badge">${escapeHtml(item.area || "Portal")}</span>
              <h3>${escapeHtml(item.action || "Activity recorded")}</h3>
              <p>${escapeHtml(activityActorLabel(item))}${target ? ` worked on ${escapeHtml(target)}.` : " completed a platform action."}</p>
              <div class="activity-meta">
                <span>${escapeHtml(formatActivityDate(item.created_at))}</span>
              </div>
            </div>
            <div class="activity-row-side">
              <span class="activity-status ${status.cls}">${escapeHtml(status.label)}</span>
            </div>
          </article>
        `;
    }).join("");
}

async function loadActivityLog(page = currentActivityPage) {
    if (!canAccessPage("activity")) return;
    currentActivityPage = Math.max(Number(page || 1), 1);
    const list = document.getElementById("activityList");
    if (list) {
        list.innerHTML = `<div class="ticket-empty"><strong>Loading activity...</strong><div class="small muted" style="margin-top:6px">Gathering staff actions and platform changes.</div></div>`;
    }
    await loadUsersCache();
    renderActivityActorOptions();
    renderActivityAreaOptions();
    try {
        const params = activityQueryParams(currentActivityPage);
        const r = await apiFetch(`/activity-log?${params.toString()}`);
        if (!r.ok) throw new Error(await extractErrorMessage(r));
        const data = await r.json();
        activityLoadedOnce = true;
        currentActivityPage = Number(data.page || currentActivityPage);
        const total = Number(data.total || 0);
        const pageSize = Math.max(Number(data.page_size || 30), 1);
        const totalPages = Math.max(1, Math.ceil(total / pageSize));
        if (total > 0 && currentActivityPage > totalPages) {
            currentActivityPage = totalPages;
            return loadActivityLog(totalPages);
        }
        renderActivityLog(data);
    } catch (e) {
        if (list) {
            list.innerHTML = `<div class="ticket-empty"><strong>Could not load activity log</strong><div class="small muted" style="margin-top:6px">${escapeHtml(String(e?.message || e || "Please try again."))}</div></div>`;
        }
    }
}

function applyActivityFilters() {
    loadActivityLog(1);
}

function resetActivityFilters() {
    ["activitySearch", "activityActorFilter", "activityAreaFilter", "activityMailboxFilter", "activityStartDate", "activityEndDate"].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.value = "";
    });
    loadActivityLog(1);
}

function goActivityPage(page) {
    const nextPage = Math.min(Math.max(Number(page || 1), 1), Math.max(Number(activityTotalPages || 1), 1));
    if (nextPage === currentActivityPage) return;
    loadActivityLog(nextPage);
}

function prevActivityPage() {
    goActivityPage(currentActivityPage - 1);
}

function nextActivityPage() {
    goActivityPage(currentActivityPage + 1);
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
        invalidateTicketCache();
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
const ACTIVITY_LOG_TIME_ZONE = "Australia/Melbourne";

function parseUtcDateTime(dt) {
    if (!dt) return null;
    if (typeof dt === "string") {
        const value = dt.trim();
        if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value) && !/(Z|[+-]\d{2}:?\d{2})$/.test(value)) {
            return new Date(`${value}Z`);
        }
    }
    return new Date(dt);
}

function formatActivityDate(dt) {
    const parsed = parseUtcDateTime(dt);
    if (!parsed || Number.isNaN(parsed.getTime())) return dt || "-";
    try {
        return new Intl.DateTimeFormat("en-AU", {
            timeZone: ACTIVITY_LOG_TIME_ZONE,
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hour12: true,
            timeZoneName: "short",
        }).format(parsed);
    } catch {
        return parsed.toLocaleString();
    }
}
function formatDateShort(dt) {
    if (!dt) return "-";
    try { return new Date(dt).toLocaleDateString(); } catch { return dt; }
}

function switchDashboardTab(tab) {
    const requestedTab = ["portal", "notifications", "myspace", "maintenance", "inspections", "checklist", "rent", "lease_renewals", "landlord_reports", "compliance", "coverage", "compliance_providers", "properties", "team", "activity", "system", "inbox"].includes(tab) ? tab : "portal";
    if (requestedTab !== "maintenance" && maintenanceOrderModalIsOpen()) {
        closeMaintenanceOrderModal();
    }
    if (!canAccessPage(requestedTab)) {
        alert("This page is not assigned to your role.");
        currentDashboardTab = firstAccessiblePage();
    } else {
        currentDashboardTab = requestedTab;
    }
    const titles = {
        portal: ["Portal Hub", "Your workspace shortcuts for email, rent, compliance, and property setup."],
        notifications: ["Notification Center", "Assigned work, maintenance action, compliance risk, rent alerts, and private follow-ups in one place."],
        myspace: ["My Space", "Your private workspace for planning, follow-ups, snippets, notes, and staff guides."],
        inbox: ["Email Manager", "Unified inbox operations with clear action queues and fast follow-up tools."],
        maintenance: ["Maintenance", "Create, approve, quote, schedule, and complete property maintenance orders."],
        inspections: ["Inspections", "Build conflict-aware multi-agent inspection schedules with optimised travel routes and timings."],
        checklist: ["Checklist", "Start operational processes, track progress, and review completed reports."],
        rent: ["Rent Tracker", "Track rental due dates, payments, arrears, and yearly rent reporting."],
        lease_renewals: ["Lease Renewals", "Track renewal due dates, signatures, rent review details, follow-ups, and portfolio reporting."],
        landlord_reports: ["Monthly Landlord Report", "Prepare a branded owner report from live property records and verified report-only notes."],
        compliance: ["Compliance", "Create and update compliance records with calculated due dates."],
        coverage: ["Compliance Report", "Review missing and incomplete MRS, Smoke, Gas, and Electrical checks."],
        compliance_providers: ["Compliance Providers", "Manage reusable provider contacts for compliance records."],
        properties: ["Properties", "Maintain the active Victorian managed property register."],
        team: ["Our Team", "Browse registered staff profiles, roles, contact details, and profile photos."],
        activity: ["Activity Log", "Audit staff actions, platform changes, imports, uploads, and security events."],
        system: ["System Access", "Manage staff accounts, profile photos, roles, status, and password controls."],
    };
    const title = document.getElementById("topbarTitle");
    const subtitle = document.getElementById("topbarSubtitle");
    if (title) title.textContent = titles[currentDashboardTab]?.[0] || "Portal Hub";
    if (subtitle) subtitle.textContent = titles[currentDashboardTab]?.[1] || "";
    if (currentDashboardTab === "myspace") applyMySpaceView();
    
    const portalPanel = document.getElementById("portalPanel");
    const notificationsPanel = document.getElementById("notificationsPanel");
    const mySpacePanel = document.getElementById("mySpacePanel");
    const inboxPanel = document.getElementById("inboxPanel");
    const maintenancePanel = document.getElementById("maintenancePanel");
    const inspectionsPanel = document.getElementById("inspectionsPanel");
    const checklistPanel = document.getElementById("checklistPanel");
    const rentPanel = document.getElementById("rentPanel");
    const leaseRenewalsPanel = document.getElementById("leaseRenewalsPanel");
    const landlordReportsPanel = document.getElementById("landlordReportsPanel");
    const propertiesPanel = document.getElementById("propertiesPanel");
    const teamPanel = document.getElementById("teamPanel");
    const activityPanel = document.getElementById("activityPanel");
    const compliancePanel = document.getElementById("compliancePanel");
    const coveragePanel = document.getElementById("coveragePanel");
    const complianceProvidersPanel = document.getElementById("complianceProvidersPanel");
    const systemPanel = document.getElementById("systemPanel");
    const navInbox = document.getElementById("navInbox");
    const navMaintenance = document.getElementById("navMaintenance");
    const navInspections = document.getElementById("navInspections");
    const navChecklist = document.getElementById("navChecklist");
    const navMySpace = document.getElementById("navMySpace");
    const navPortal = document.getElementById("navPortal");
    const navRent = document.getElementById("navRentTracker");
    const navLeaseRenewals = document.getElementById("navLeaseRenewals");
    const navLandlordReports = document.getElementById("navLandlordReports");
    const navProperties = document.getElementById("navProperties");
    const navTeam = document.getElementById("navTeam");
    const navActivity = document.getElementById("navActivity");
    const navSystem = document.getElementById("btnSystemUsers");
    const navCompliance = document.getElementById("navCompliance");
    const navCoverage = document.getElementById("navComplianceCoverage");
    const navComplianceProviders = document.getElementById("navComplianceProviders");
    const shell = document.getElementById("dashboardShell");

    if (portalPanel) portalPanel.classList.toggle("hidden", currentDashboardTab !== "portal");
    if (notificationsPanel) notificationsPanel.classList.toggle("hidden", currentDashboardTab !== "notifications");
    if (mySpacePanel) mySpacePanel.classList.toggle("hidden", currentDashboardTab !== "myspace");
    if (inboxPanel) inboxPanel.classList.toggle("hidden", currentDashboardTab !== "inbox");
    if (maintenancePanel) maintenancePanel.classList.toggle("hidden", currentDashboardTab !== "maintenance");
    if (inspectionsPanel) inspectionsPanel.classList.toggle("hidden", currentDashboardTab !== "inspections");
    if (checklistPanel) checklistPanel.classList.toggle("hidden", currentDashboardTab !== "checklist");
    if (rentPanel) rentPanel.classList.toggle("hidden", currentDashboardTab !== "rent");
    if (leaseRenewalsPanel) leaseRenewalsPanel.classList.toggle("hidden", currentDashboardTab !== "lease_renewals");
    if (landlordReportsPanel) landlordReportsPanel.classList.toggle("hidden", currentDashboardTab !== "landlord_reports");
    if (propertiesPanel) propertiesPanel.classList.toggle("hidden", currentDashboardTab !== "properties");
    if (teamPanel) teamPanel.classList.toggle("hidden", currentDashboardTab !== "team");
    if (activityPanel) activityPanel.classList.toggle("hidden", currentDashboardTab !== "activity");
    if (compliancePanel) compliancePanel.classList.toggle("hidden", currentDashboardTab !== "compliance");
    if (coveragePanel) coveragePanel.classList.toggle("hidden", currentDashboardTab !== "coverage");
    if (complianceProvidersPanel) complianceProvidersPanel.classList.toggle("hidden", currentDashboardTab !== "compliance_providers");
    if (systemPanel) systemPanel.classList.toggle("hidden", currentDashboardTab !== "system");
    if (navPortal) navPortal.classList.toggle("active", currentDashboardTab === "portal");
    if (navMySpace) navMySpace.classList.toggle("active", currentDashboardTab === "myspace");
    if (navInbox) navInbox.classList.toggle("active", currentDashboardTab === "inbox");
    if (navMaintenance) navMaintenance.classList.toggle("active", currentDashboardTab === "maintenance");
    if (navInspections) navInspections.classList.toggle("active", currentDashboardTab === "inspections");
    if (navChecklist) navChecklist.classList.toggle("active", currentDashboardTab === "checklist");
    if (navRent) navRent.classList.toggle("active", currentDashboardTab === "rent");
    if (navLeaseRenewals) navLeaseRenewals.classList.toggle("active", currentDashboardTab === "lease_renewals");
    if (navLandlordReports) navLandlordReports.classList.toggle("active", currentDashboardTab === "landlord_reports");
    if (navProperties) navProperties.classList.toggle("active", currentDashboardTab === "properties");
    if (navTeam) navTeam.classList.toggle("active", currentDashboardTab === "team");
    if (navActivity) navActivity.classList.toggle("active", currentDashboardTab === "activity");
    if (navSystem) navSystem.classList.toggle("active", currentDashboardTab === "system");
    if (navCompliance) navCompliance.classList.toggle("active", ["compliance", "coverage", "compliance_providers"].includes(currentDashboardTab));
    if (navCoverage) navCoverage.classList.toggle("active", currentDashboardTab === "coverage");
    if (navComplianceProviders) navComplianceProviders.classList.toggle("active", currentDashboardTab === "compliance_providers");
    document.querySelectorAll("[data-compliance-view]").forEach((btn) => {
        const view = btn.getAttribute("data-compliance-view");
        const isActive = (view === "checks" && currentDashboardTab === "compliance")
            || (view === "coverage" && currentDashboardTab === "coverage")
            || (view === "providers" && currentDashboardTab === "compliance_providers");
        btn.classList.toggle("active", isActive);
    });
    document.querySelectorAll("[data-lease-renewal-view]").forEach((btn) => {
        btn.classList.toggle("active", currentDashboardTab === "lease_renewals" && btn.getAttribute("data-lease-renewal-view") === leaseRenewalViewMode);
    });
    if (shell) {
        shell.classList.toggle("inbox-mode", currentDashboardTab === "inbox");
        shell.classList.toggle("maintenance-mode", currentDashboardTab === "maintenance");
        shell.classList.toggle("inspections-mode", currentDashboardTab === "inspections");
        shell.classList.toggle("lease-renewals-mode", currentDashboardTab === "lease_renewals");
        shell.classList.toggle("landlord-reports-mode", currentDashboardTab === "landlord_reports");
        shell.classList.toggle("portal-mode", currentDashboardTab === "portal");
        shell.classList.toggle("notifications-mode", currentDashboardTab === "notifications");
        shell.classList.toggle("myspace-mode", currentDashboardTab === "myspace");
        shell.classList.toggle("rent-mode", currentDashboardTab === "rent");
        shell.classList.toggle("compliance-mode", currentDashboardTab === "compliance");
        shell.classList.toggle("coverage-mode", currentDashboardTab === "coverage");
        shell.classList.toggle("compliance-providers-mode", currentDashboardTab === "compliance_providers");
        shell.classList.toggle("properties-mode", currentDashboardTab === "properties");
        shell.classList.toggle("team-mode", currentDashboardTab === "team");
        shell.classList.toggle("activity-mode", currentDashboardTab === "activity");
        shell.classList.toggle("system-mode", currentDashboardTab === "system");
    }

    updateSyncContextUI();
    if (currentDashboardTab === "inbox") {
        loadTickets({ allowCache: ticketsLoadedOnce });
    }
    if (currentDashboardTab === "rent" && !rentLoadedOnce) {
        refreshPropertyOptions();
        loadActiveRentView();
    }
    if (currentDashboardTab === "lease_renewals" && !leaseRenewalsLoadedOnce) {
        refreshPropertyOptions();
        loadAssignableUsers();
        loadLeaseRenewals();
    }
    if (currentDashboardTab === "landlord_reports") switchLandlordReportView(landlordReportViewMode);
    if (currentDashboardTab === "maintenance" && !maintenanceLoadedOnce) {
        refreshPropertyOptions();
        loadMaintenanceTradies();
        loadMaintenanceDashboard();
    }
    if (currentDashboardTab === "inspections") {
        if (!inspectionsLoadedOnce) initInspectionsWorkspace();
        setTimeout(invalidateInspectionMap, 80);
    }
    if (currentDashboardTab === "checklist") loadChecklistRuns();
    if (currentDashboardTab === "myspace") {
        if (mySpaceViewMode === "timesheet") {
            initialiseTimesheetView();
            loadTimesheetDay();
        } else if (!mySpaceLoadedOnce) {
            loadMySpace();
        }
    }
    if (currentDashboardTab === "notifications") {
        loadNotifications();
    }
    if (currentDashboardTab === "properties" && !propertiesLoadedOnce) {
        ensureNewListingCollections();
        loadProperties();
        refreshPropertyOptions();
    }
    if (currentDashboardTab === "team" && !teamLoadedOnce) {
        loadTeamDirectory();
    }
    if (currentDashboardTab === "activity" && !activityLoadedOnce) {
        loadActivityLog(1);
        loadActivityAreas();
    }
    if (currentDashboardTab === "compliance" && !complianceLoadedOnce) {
        refreshPropertyOptions();
        loadComplianceProviders();
        loadComplianceDashboard();
    }
    if (currentDashboardTab === "coverage" && !coverageLoadedOnce) {
        loadComplianceCoverage();
    }
    if (currentDashboardTab === "compliance_providers" && !complianceProvidersLoadedOnce) {
        loadComplianceProviders(true);
    }
    if (currentDashboardTab === "system" && !usersLoadedOnce) {
        renderUsersList();
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
        btn.setAttribute("aria-expanded", String(!collapsed));
    }
}

function toggleSidebar() {
    const shell = document.getElementById("appShell");
    const collapsed = !(shell && shell.classList.contains("sidebar-collapsed"));
    localStorage.setItem("agent_sidebar_collapsed", collapsed ? "1" : "0");
    applySidebarState();
}

const INSPECTION_ROUTE_COLOURS = [
    "#2563eb", "#e11d48", "#059669", "#9333ea", "#ea580c",
    "#0891b2", "#ca8a04", "#4f46e5", "#db2777", "#16a34a",
];
const INSPECTION_PROPERTY_COLOURS = [
    "#be123c", "#1d4ed8", "#047857", "#7e22ce", "#c2410c",
    "#0e7490", "#a16207", "#4338ca", "#be185d", "#15803d",
    "#9f1239", "#0f766e", "#6d28d9", "#9a3412", "#0369a1",
    "#4d7c0f", "#b91c1c", "#075985", "#5b21b6", "#166534",
];
const INSPECTION_DEFAULT_DEPARTURE_ADDRESS = "24 Coral-Pea Way, Cranbourne West";

function inspectionEscape(value) {
    return escapeHtml(String(value == null ? "" : value));
}

function inspectionToday() {
    try {
        const parts = new Intl.DateTimeFormat("en-AU", {
            timeZone: "Australia/Melbourne",
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
        }).formatToParts(new Date());
        const values = Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
        return `${values.year}-${values.month}-${values.day}`;
    } catch {
        return new Date().toISOString().slice(0, 10);
    }
}

function inspectionDefaultPlanName(dateValue = "") {
    const raw = String(dateValue || inspectionToday()).slice(0, 10);
    return /^\d{4}-\d{2}-\d{2}$/.test(raw) ? raw : inspectionToday();
}

function inspectionParseJson(value) {
    if (typeof value !== "string") return value;
    try { return JSON.parse(value); } catch { return value; }
}

function inspectionArray(value) {
    const parsed = inspectionParseJson(value);
    return Array.isArray(parsed) ? parsed : [];
}

function inspectionNumber(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
}

function inspectionAgentId(agent) {
    return inspectionNumber(agent?.id ?? agent?.agent_id ?? agent?.user_id, 0);
}

function inspectionAgentById(agentId) {
    const id = inspectionNumber(agentId, 0);
    return inspectionAgents.find((agent) => inspectionAgentId(agent) === id) || null;
}

function inspectionAgentName(agentId, fallback = "") {
    const agent = inspectionAgentById(agentId);
    return String(agent?.name || agent?.display_name || agent?.email || fallback || (agentId ? `Agent ${agentId}` : "Auto-allocated"));
}

function inspectionAgentInitials(agent) {
    const name = String(agent?.name || agent?.display_name || agent?.email || "Agent").trim();
    const parts = name.split(/\s+/).filter(Boolean);
    return (parts.length > 1 ? `${parts[0][0]}${parts[parts.length - 1][0]}` : name.slice(0, 2)).toUpperCase();
}

function inspectionPropertyById(propertyId) {
    const id = inspectionNumber(propertyId, 0);
    return propertyOptionsCache.find((property) => inspectionNumber(property?.id, 0) === id) || null;
}

function inspectionPropertyLabel(propertyId, fallback = "") {
    const property = inspectionPropertyById(propertyId);
    return String(property?.label || propertyFullAddress(property || {}) || fallback || "");
}

function inspectionResolveManagedProperty(value) {
    const needle = String(value || "").trim().toLocaleLowerCase("en-AU");
    if (!needle) return null;
    return propertyOptionsCache.find((property) => {
        const label = String(property?.label || propertyFullAddress(property || {})).trim().toLocaleLowerCase("en-AU");
        return label === needle;
    }) || null;
}

function inspectionExtractAgentIds(value) {
    const source = Array.isArray(value) ? value : (value == null ? [] : [value]);
    return [...new Set(source.map((item) => {
        if (item && typeof item === "object") return inspectionAgentId(item);
        return inspectionNumber(item, 0);
    }).filter((id) => id > 0))];
}

function createInspectionRow(seed = {}) {
    const property = seed.property && typeof seed.property === "object" ? seed.property : null;
    const propertyId = inspectionNumber(seed.property_id ?? property?.id, 0) || null;
    const latitude = inspectionNumber(seed.latitude ?? seed.lat ?? seed.location?.latitude ?? seed.location?.lat, NaN);
    const longitude = inspectionNumber(seed.longitude ?? seed.lng ?? seed.lon ?? seed.location?.longitude ?? seed.location?.lng, NaN);
    const hasSavedCoordinates = Number.isFinite(latitude) && Number.isFinite(longitude)
        && latitude >= -39.5 && latitude <= -33.5
        && longitude >= 140.5 && longitude <= 150.5;
    const clientId = String(seed.client_id || seed.visit_id || `inspection-${Date.now()}-${++inspectionRowSequence}`);
    const singleAgent = seed.agent_id ?? seed.assigned_agent_id ?? seed.assigned_user_id;
    const agentIds = inspectionExtractAgentIds(seed.agent_ids ?? seed.assigned_agent_ids ?? seed.agents ?? singleAgent);
    return {
        client_id: clientId,
        property_id: propertyId,
        property_label: String(
            seed.property_label || seed.address || seed.property_address || property?.label ||
            property?.full_address || inspectionPropertyLabel(propertyId, "")
        ),
        agent_ids: agentIds,
        duration_minutes: Math.min(480, Math.max(5, inspectionNumber(seed.duration_minutes ?? seed.duration ?? seed.inspection_minutes, 30))),
        buffer_minutes: Math.min(240, Math.max(0, inspectionNumber(seed.buffer_minutes ?? seed.extra_buffer_minutes ?? seed.parking_minutes, 10))),
        earliest_time: String(seed.earliest_time || seed.window_start || seed.earliest || ""),
        latest_time: String(seed.latest_time || seed.window_end || seed.latest || ""),
        notes: String(seed.notes || seed.note || ""),
        property_invalid: false,
        address_verified: !!propertyId || seed.address_verified === true || (!propertyId && hasSavedCoordinates),
        address_verification_pending: false,
        address_status: "",
        address_status_type: "",
        address_verification_request: 0,
    };
}

function inspectionRowByClientId(clientId) {
    return inspectionRows.find((row) => String(row.client_id) === String(clientId)) || null;
}

function inspectionPropertyKey(source = {}, index = 0) {
    const property = source?.property && typeof source.property === "object" ? source.property : null;
    const clientId = String(source?.client_id || source?.visit_id || "");
    const row = clientId ? inspectionRowByClientId(clientId) : null;
    const propertyId = inspectionNumber(source?.property_id ?? property?.id ?? row?.property_id, 0);
    if (propertyId > 0) return `property:${propertyId}`;
    const address = String(
        row?.property_label || source?.property_address || source?.address || source?.property_label ||
        source?.full_address || property?.property_address || property?.full_address || ""
    ).trim().toLocaleLowerCase("en-AU").replace(/\s+/g, " ");
    if (address) return `address:${address}`;
    return `visit:${clientId || index}`;
}

function inspectionPropertyColour(source = {}, index = 0) {
    const key = inspectionPropertyKey(source, index);
    const propertyKeys = [];
    inspectionRows.forEach((row, rowIndex) => {
        const rowKey = inspectionPropertyKey(row, rowIndex);
        if (!propertyKeys.includes(rowKey)) propertyKeys.push(rowKey);
    });
    const matchedIndex = propertyKeys.indexOf(key);
    if (matchedIndex >= 0) {
        if (matchedIndex < INSPECTION_PROPERTY_COLOURS.length) return INSPECTION_PROPERTY_COLOURS[matchedIndex];
        const hue = (matchedIndex * 137.508) % 360;
        return `hsl(${hue.toFixed(1)} 68% 38%)`;
    }
    let hash = 0;
    for (let position = 0; position < key.length; position += 1) hash = ((hash << 5) - hash + key.charCodeAt(position)) | 0;
    return INSPECTION_PROPERTY_COLOURS[Math.abs(hash) % INSPECTION_PROPERTY_COLOURS.length];
}

function inspectionAddressKey(value) {
    return String(value || "").trim().toLocaleLowerCase("en-AU").replace(/\s+/g, " ");
}

function inspectionAddressStatusForRow(row) {
    if (row?.address_status) return { message: row.address_status, type: row.address_status_type || "" };
    if (row?.property_invalid) return { message: "Choose a managed property or a verified Victorian address.", type: "error" };
    if (row?.property_id) return { message: "Managed property selected.", type: "verified" };
    if (row?.address_verified && row?.property_label) return { message: "Verified Victorian address selected.", type: "verified" };
    return { message: "Type at least 3 characters, then select a managed or verified Victorian address.", type: "" };
}

function updateInspectionAddressStatus(card, row) {
    if (!card || !row) return;
    const status = card.querySelector("[data-inspection-address-status]");
    const input = card.querySelector('[data-inspection-field="property_label"]');
    const current = inspectionAddressStatusForRow(row);
    if (status) {
        status.textContent = current.message;
        status.className = `inspection-address-status${current.type ? ` ${current.type}` : ""}`;
    }
    if (input) {
        input.setAttribute("aria-busy", row.address_verification_pending ? "true" : "false");
        input.classList.toggle("invalid", !!row.property_invalid);
        input.classList.toggle("verified", !!row.address_verified && !row.property_invalid);
    }
}

function setInspectionAddressStatus(row, message = "", type = "", card = null) {
    if (!row) return;
    row.address_status = String(message || "");
    row.address_status_type = String(type || "");
    updateInspectionAddressStatus(card || inspectionAddressCard(row.client_id), row);
}

function inspectionAddressCard(clientId) {
    return [...document.querySelectorAll("[data-inspection-client-id]")]
        .find((card) => String(card.dataset.inspectionClientId) === String(clientId)) || null;
}

function renderInspectionPropertyOptions(remoteItems = inspectionAddressSuggestionItems) {
    const target = document.getElementById("inspectionPropertyOptions");
    if (!target) return;
    const managedLabels = new Set();
    const managedOptions = propertyOptionsCache.map((property) => {
        const label = String(property?.label || propertyFullAddress(property || {})).trim();
        if (label) managedLabels.add(inspectionAddressKey(label));
        return label ? `<option value="${inspectionEscape(label)}" label="Managed property"></option>` : "";
    }).filter(Boolean);
    inspectionAddressSuggestionsByLabel = new Map();
    const remoteOptions = [];
    inspectionArray(remoteItems).slice(0, 10).forEach((item) => {
        const label = String(item?.label || item?.text || "").trim();
        const magicKey = String(item?.magic_key || item?.magicKey || "").trim();
        const key = inspectionAddressKey(label);
        if (!label || !magicKey || managedLabels.has(key) || inspectionAddressSuggestionsByLabel.has(key)) return;
        const normalized = { label, magic_key: magicKey, source: "vicmap" };
        inspectionAddressSuggestionsByLabel.set(key, normalized);
        remoteOptions.push(`<option value="${inspectionEscape(label)}" label="Verified Victorian address"></option>`);
    });
    target.innerHTML = [...remoteOptions, ...managedOptions].join("");
    inspectionRows.forEach((row) => {
        if (row.property_id && !row.property_label) row.property_label = inspectionPropertyLabel(row.property_id, "");
    });
}

function cancelInspectionAddressSuggestionSearch(clearItems = false) {
    if (inspectionAddressSuggestionTimer) clearTimeout(inspectionAddressSuggestionTimer);
    inspectionAddressSuggestionTimer = null;
    if (inspectionAddressSuggestionController) inspectionAddressSuggestionController.abort();
    inspectionAddressSuggestionController = null;
    inspectionAddressSuggestionRequest += 1;
    if (clearItems) {
        inspectionAddressSuggestionItems = [];
        inspectionAddressSuggestionQuery = "";
        inspectionAddressSuggestionsByLabel = new Map();
        renderInspectionPropertyOptions();
    }
}

function scheduleInspectionAddressSuggestionSearch(row, control) {
    cancelInspectionAddressSuggestionSearch(false);
    const query = String(control?.value || "").trim();
    const clientId = String(row?.client_id || "");
    if (!row || !control || query.length < 3) {
        inspectionAddressSuggestionItems = [];
        inspectionAddressSuggestionQuery = "";
        renderInspectionPropertyOptions();
        setInspectionAddressStatus(row, "", "", control?.closest("[data-inspection-client-id]"));
        return;
    }
    if (inspectionAddressKey(query) === inspectionAddressKey(inspectionAddressSuggestionQuery)
        && inspectionAddressSuggestionItems.length) {
        renderInspectionPropertyOptions();
        setInspectionAddressStatus(
            row,
            "Select a verified Victorian address from the suggestions.",
            "choices",
            control.closest("[data-inspection-client-id]"),
        );
        return;
    }
    const requestId = inspectionAddressSuggestionRequest;
    const requestMailbox = normalizeMailbox(currentMailbox);
    inspectionAddressSuggestionItems = [];
    inspectionAddressSuggestionQuery = query;
    renderInspectionPropertyOptions();
    setInspectionAddressStatus(row, "Searching verified Victorian addresses…", "searching", control.closest("[data-inspection-client-id]"));
    inspectionAddressSuggestionTimer = setTimeout(async () => {
        inspectionAddressSuggestionTimer = null;
        const controller = new AbortController();
        inspectionAddressSuggestionController = controller;
        const url = new URL("/inspections/address-suggestions", window.location.origin);
        url.searchParams.set("q", query.slice(0, 100));
        try {
            const response = await apiFetch(url.pathname + url.search, { signal: controller.signal });
            const data = await response.json().catch(() => null);
            const currentRow = inspectionRowByClientId(clientId);
            if (requestId !== inspectionAddressSuggestionRequest
                || requestMailbox !== normalizeMailbox(currentMailbox)
                || currentRow !== row
                || !control.isConnected
                || String(control.value || "").trim() !== query) return;
            if (!response.ok) throw new Error(inspectionApiError(data, "Verified address suggestions are unavailable."));
            inspectionAddressSuggestionItems = inspectionArray(data?.items).slice(0, 10);
            inspectionAddressSuggestionQuery = query;
            renderInspectionPropertyOptions();
            setInspectionAddressStatus(
                row,
                inspectionAddressSuggestionItems.length
                    ? "Select a verified Victorian address from the suggestions."
                    : "No verified match yet. Add more of the street, suburb, or postcode.",
                inspectionAddressSuggestionItems.length ? "choices" : "warning",
                control.closest("[data-inspection-client-id]"),
            );
        } catch (error) {
            if (error?.name === "AbortError") return;
            if (requestId !== inspectionAddressSuggestionRequest
                || requestMailbox !== normalizeMailbox(currentMailbox)
                || !control.isConnected
                || String(control.value || "").trim() !== query) return;
            inspectionAddressSuggestionItems = [];
            renderInspectionPropertyOptions();
            setInspectionAddressStatus(
                row,
                error?.message || "Online suggestions are unavailable; managed properties remain available.",
                "error",
                control.closest("[data-inspection-client-id]"),
            );
        } finally {
            if (inspectionAddressSuggestionController === controller) inspectionAddressSuggestionController = null;
        }
    }, 325);
}

async function verifyInspectionAddressSuggestion(row, control, suggestion) {
    if (!row || !control || !suggestion) return;
    cancelInspectionAddressSuggestionSearch(false);
    const selectedLabel = String(suggestion.label || "").trim();
    const selectedKey = String(suggestion.magic_key || "").trim();
    const requestMailbox = normalizeMailbox(currentMailbox);
    const verificationRequest = inspectionNumber(row.address_verification_request, 0) + 1;
    row.address_verification_request = verificationRequest;
    row.address_verification_pending = true;
    row.address_verified = false;
    row.property_id = null;
    setInspectionAddressStatus(row, "Verifying the selected address with Vicmap…", "searching", control.closest("[data-inspection-client-id]"));
    try {
        const response = await apiFetch("/inspections/address-suggestions/resolve", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ label: selectedLabel, magic_key: selectedKey }),
        });
        const data = await response.json().catch(() => null);
        const currentRow = inspectionRowByClientId(row.client_id);
        if (requestMailbox !== normalizeMailbox(currentMailbox)
            || currentRow !== row
            || row.address_verification_request !== verificationRequest
            || !control.isConnected
            || inspectionAddressKey(control.value) !== inspectionAddressKey(selectedLabel)) return;
        if (!response.ok) throw new Error(inspectionApiError(data, "The selected address could not be verified."));
        const item = data?.item || {};
        const canonicalAddress = String(item.label || item.property_address || "").trim();
        if (!canonicalAddress || item.verified !== true) throw new Error("The selected address could not be verified.");
        row.property_label = canonicalAddress;
        row.property_id = null;
        row.address_verified = true;
        row.address_verification_pending = false;
        row.property_invalid = false;
        control.value = canonicalAddress;
        const hint = control.closest("[data-inspection-client-id]")?.querySelector("[data-inspection-property-hint]");
        if (hint) hint.textContent = canonicalAddress;
        setInspectionAddressStatus(row, "Verified Victorian address selected and ready for routing.", "verified", control.closest("[data-inspection-client-id]"));
        markInspectionPlanDirty();
    } catch (error) {
        if (requestMailbox !== normalizeMailbox(currentMailbox)
            || row.address_verification_request !== verificationRequest) return;
        row.address_verified = false;
        row.address_verification_pending = false;
        row.property_invalid = true;
        setInspectionAddressStatus(
            row,
            error?.message || "The selected address could not be verified. Choose another suggestion.",
            "error",
            control.closest("[data-inspection-client-id]"),
        );
    }
}

function availableInspectionAgents() {
    return inspectionAgents.filter((agent) => inspectionAvailableAgentIds.has(inspectionAgentId(agent)));
}

function renderInspectionAvailableAgents() {
    const target = document.getElementById("inspectionAvailableAgents");
    const meta = document.getElementById("inspectionAvailableAgentMeta");
    if (!target) return;
    if (!inspectionAgents.length) {
        target.innerHTML = `<div class="inspection-empty">No active team members are available.</div>`;
        if (meta) meta.textContent = "No active agents";
        return;
    }
    target.innerHTML = inspectionAgents.map((agent) => {
        const id = inspectionAgentId(agent);
        const checked = inspectionAvailableAgentIds.has(id);
        const name = agent?.name || agent?.display_name || agent?.email || `Agent ${id}`;
        const avatar = String(agent?.avatar_url || "").trim();
        return `<label class="inspection-agent-toggle">
            <input type="checkbox" data-inspection-available-agent="${id}" ${checked ? "checked" : ""} />
            <span class="inspection-agent-avatar">${avatar ? `<img src="${inspectionEscape(avatar)}" alt="" />` : inspectionEscape(inspectionAgentInitials(agent))}</span>
            <span>${inspectionEscape(name)}</span>
        </label>`;
    }).join("");
    const count = inspectionAvailableAgentIds.size;
    if (meta) meta.textContent = `${count} of ${inspectionAgents.length} agent${inspectionAgents.length === 1 ? "" : "s"} selected`;
}

function inspectionAgentPickerMarkup(row) {
    const available = availableInspectionAgents();
    const selectedNames = row.agent_ids.map((id) => inspectionAgentName(id)).filter(Boolean);
    const summary = selectedNames.length ? selectedNames.join(", ") : "Auto-allocate best agent";
    const options = available.length
        ? available.map((agent) => {
            const id = inspectionAgentId(agent);
            return `<label class="inspection-agent-option">
                <input type="checkbox" data-inspection-row-agent="${id}" ${row.agent_ids.includes(id) ? "checked" : ""} />
                <span>${inspectionEscape(agent?.name || agent?.email || `Agent ${id}`)}</span>
            </label>`;
        }).join("")
        : `<div class="inspection-empty">Select available agents above first.</div>`;
    return `<details class="inspection-agent-picker">
        <summary data-inspection-agent-summary>${inspectionEscape(summary)}</summary>
        <div class="inspection-agent-menu">
            <button class="inspection-agent-auto" type="button" data-inspection-action="auto-agents">Use automatic allocation</button>
            ${options}
        </div>
    </details>`;
}

function renderInspectionRows() {
    const target = document.getElementById("inspectionVisits");
    const count = document.getElementById("inspectionVisitCount");
    if (count) count.textContent = String(inspectionRows.length);
    if (!target) return;
    if (!inspectionRows.length) {
        target.innerHTML = `<div class="inspection-empty"><strong>No inspection stops yet.</strong><br />Add a property to begin building the day.</div>`;
        return;
    }
    target.innerHTML = inspectionRows.map((row, index) => {
        const propertyHint = row.property_label || "Choose a managed property or verified sales address";
        const propertyColour = inspectionPropertyColour(row, index);
        const addressStatus = inspectionAddressStatusForRow(row);
        const addressStatusType = ["verified", "searching", "choices", "warning", "error"].includes(addressStatus.type)
            ? addressStatus.type : "";
        const propertyInputId = `inspectionPropertyInput-${index + 1}`;
        const propertyStatusId = `inspectionPropertyStatus-${index + 1}`;
        return `<article class="inspection-visit-card" data-inspection-client-id="${inspectionEscape(row.client_id)}" style="--property-colour:${propertyColour}">
            <div class="inspection-visit-head">
                <div class="inspection-visit-title">
                    <span class="inspection-stop-number">${index + 1}</span>
                    <span><strong>Inspection ${index + 1}</strong><small data-inspection-property-hint>${inspectionEscape(propertyHint)}</small></span>
                </div>
                <div class="inspection-visit-actions">
                    <button class="inspection-icon-btn" type="button" title="Move earlier" aria-label="Move inspection earlier" data-inspection-action="up" ${index === 0 ? "disabled" : ""}>↑</button>
                    <button class="inspection-icon-btn" type="button" title="Move later" aria-label="Move inspection later" data-inspection-action="down" ${index === inspectionRows.length - 1 ? "disabled" : ""}>↓</button>
                    <button class="inspection-icon-btn danger" type="button" title="Remove" aria-label="Remove inspection" data-inspection-action="remove">×</button>
                </div>
            </div>
            <div class="inspection-visit-fields">
                <div class="field property-field">
                    <label class="label" for="${propertyInputId}">Property / sales address</label>
                    <input id="${propertyInputId}" type="text" list="inspectionPropertyOptions" autocomplete="off" maxlength="500" data-inspection-field="property_label"
                        aria-autocomplete="list" aria-describedby="${propertyStatusId}" aria-busy="${row.address_verification_pending ? "true" : "false"}"
                        class="${row.property_invalid ? "invalid" : (row.address_verified ? "verified" : "")}" value="${inspectionEscape(row.property_label)}" placeholder="Search managed properties or type a Victorian address…" />
                    <div id="${propertyStatusId}" class="inspection-address-status${addressStatusType ? ` ${addressStatusType}` : ""}" data-inspection-address-status aria-live="polite">${inspectionEscape(addressStatus.message)}</div>
                </div>
                <div class="field">
                    <label class="label">Inspection time</label>
                    <input type="number" min="5" max="480" step="5" data-inspection-field="duration_minutes" value="${inspectionEscape(row.duration_minutes)}" />
                    <div class="small muted" style="margin-top:4px">minutes</div>
                </div>
                <div class="field">
                    <label class="label">Parking / buffer after</label>
                    <input type="number" min="0" max="240" step="5" data-inspection-field="buffer_minutes" value="${inspectionEscape(row.buffer_minutes)}" />
                    <div class="small muted" style="margin-top:4px">minutes</div>
                </div>
                <div class="field">
                    <label class="label">Earliest arrival</label>
                    <input type="time" data-inspection-field="earliest_time" value="${inspectionEscape(row.earliest_time)}" />
                </div>
                <div class="field">
                    <label class="label">Latest arrival</label>
                    <input type="time" data-inspection-field="latest_time" value="${inspectionEscape(row.latest_time)}" />
                </div>
                <div class="field agents-field">
                    <label class="label">Assigned agents <span class="muted">(optional)</span></label>
                    ${inspectionAgentPickerMarkup(row)}
                </div>
                <div class="field notes-field">
                    <label class="label">Inspection notes <span class="muted">(optional)</span></label>
                    <textarea maxlength="2000" data-inspection-field="notes" placeholder="Access instructions, tenant constraints, key collection, parking notes…">${inspectionEscape(row.notes)}</textarea>
                </div>
            </div>
        </article>`;
    }).join("");
}

function updateInspectionAgentSummary(card, row) {
    const summary = card?.querySelector("[data-inspection-agent-summary]");
    if (!summary || !row) return;
    const names = row.agent_ids.map((id) => inspectionAgentName(id)).filter(Boolean);
    summary.textContent = names.length ? names.join(", ") : "Auto-allocate best agent";
}

function setInspectionMessage(message = "", type = "") {
    const target = document.getElementById("inspectionPlannerStatus");
    if (!target) return;
    target.textContent = String(message || "");
    target.className = `inspection-planner-message${message ? " show" : ""}${type ? ` ${type}` : ""}`;
}

function setInspectionBusy(busy, message = "") {
    ["inspectionOptimizeBtn", "inspectionSaveBtn", "inspectionNewPlanBtn", "inspectionAddVisitBtn", "inspectionRefreshPlansBtn"].forEach((id) => {
        const button = document.getElementById(id);
        if (button) button.disabled = !!busy || (id === "inspectionSaveBtn" && !inspectionLastOptimization);
    });
    if (message) setInspectionMessage(message, busy ? "busy" : "");
}

function markInspectionPlanDirty() {
    const hadRenderedResult = !!inspectionLastOptimization || !!document.querySelector("#inspectionSchedule .inspection-schedule-row");
    inspectionLastOptimization = null;
    const save = document.getElementById("inspectionSaveBtn");
    if (save) save.disabled = true;
    const mapStatus = document.getElementById("inspectionMapStatus");
    if (mapStatus && hadRenderedResult) mapStatus.textContent = "Plan changed. Optimise again to refresh routes and timings.";
}

function bindInspectionEvents() {
    if (inspectionEventsBound) return;
    inspectionEventsBound = true;
    const visits = document.getElementById("inspectionVisits");
    if (visits) {
        visits.addEventListener("input", (event) => {
            const control = event.target;
            const card = control.closest("[data-inspection-client-id]");
            const row = inspectionRowByClientId(card?.dataset.inspectionClientId);
            if (!row) return;
            const field = control.dataset.inspectionField;
            if (!field) return;
            if (field === "property_label") {
                row.property_label = control.value || "";
                row.address_verification_request = inspectionNumber(row.address_verification_request, 0) + 1;
                row.address_verification_pending = false;
                row.address_verified = false;
                row.address_status = "";
                row.address_status_type = "";
                const match = inspectionResolveManagedProperty(row.property_label);
                const onlineSuggestion = inspectionAddressSuggestionsByLabel.get(inspectionAddressKey(row.property_label));
                row.property_id = match ? inspectionNumber(match.id, 0) : null;
                row.property_invalid = !String(row.property_label).trim();
                if (match) {
                    row.address_verified = true;
                    cancelInspectionAddressSuggestionSearch(false);
                    setInspectionAddressStatus(row, "Managed property selected.", "verified", card);
                } else if (onlineSuggestion) {
                    row.property_invalid = false;
                    verifyInspectionAddressSuggestion(row, control, onlineSuggestion);
                } else {
                    scheduleInspectionAddressSuggestionSearch(row, control);
                }
                const hint = card.querySelector("[data-inspection-property-hint]");
                if (hint) hint.textContent = row.property_label || "Choose a managed property or verified sales address";
                visits.querySelectorAll("[data-inspection-client-id]").forEach((rowCard, rowIndex) => {
                    rowCard.style.setProperty(
                        "--property-colour",
                        inspectionPropertyColour(inspectionRows[rowIndex], rowIndex),
                    );
                });
            } else if (field === "duration_minutes") {
                row.duration_minutes = Math.min(480, Math.max(5, inspectionNumber(control.value, 5)));
            } else if (field === "buffer_minutes") {
                row.buffer_minutes = Math.min(240, Math.max(0, inspectionNumber(control.value, 0)));
            } else {
                row[field] = control.value || "";
            }
            markInspectionPlanDirty();
        });
        visits.addEventListener("focusin", (event) => {
            const control = event.target.closest('[data-inspection-field="property_label"]');
            if (!control) return;
            const card = control.closest("[data-inspection-client-id]");
            const row = inspectionRowByClientId(card?.dataset.inspectionClientId);
            if (!row || row.property_id || row.address_verified || String(control.value || "").trim().length < 3) return;
            scheduleInspectionAddressSuggestionSearch(row, control);
        });
        visits.addEventListener("change", (event) => {
            const control = event.target;
            const card = control.closest("[data-inspection-client-id]");
            const row = inspectionRowByClientId(card?.dataset.inspectionClientId);
            if (!row) return;
            if (control.matches("[data-inspection-row-agent]")) {
                const id = inspectionNumber(control.dataset.inspectionRowAgent, 0);
                const selected = new Set(row.agent_ids);
                if (control.checked) selected.add(id); else selected.delete(id);
                row.agent_ids = [...selected].filter((agentId) => agentId > 0);
                updateInspectionAgentSummary(card, row);
                markInspectionPlanDirty();
                return;
            }
            if (control.dataset.inspectionField === "property_label" && row.property_label !== control.value) {
                control.dispatchEvent(new Event("input", { bubbles: true }));
            }
            markInspectionPlanDirty();
        });
        visits.addEventListener("click", (event) => {
            const button = event.target.closest("[data-inspection-action]");
            if (!button) return;
            const card = button.closest("[data-inspection-client-id]");
            const clientId = card?.dataset.inspectionClientId;
            const index = inspectionRows.findIndex((row) => String(row.client_id) === String(clientId));
            if (index < 0) return;
            const action = button.dataset.inspectionAction;
            if (["remove", "up", "down"].includes(action)) cancelInspectionAddressSuggestionSearch(false);
            if (action === "remove") inspectionRows.splice(index, 1);
            if (action === "up" && index > 0) [inspectionRows[index - 1], inspectionRows[index]] = [inspectionRows[index], inspectionRows[index - 1]];
            if (action === "down" && index < inspectionRows.length - 1) [inspectionRows[index], inspectionRows[index + 1]] = [inspectionRows[index + 1], inspectionRows[index]];
            if (action === "auto-agents") {
                inspectionRows[index].agent_ids = [];
                updateInspectionAgentSummary(card, inspectionRows[index]);
                card.querySelectorAll("[data-inspection-row-agent]").forEach((input) => { input.checked = false; });
                const details = button.closest("details");
                if (details) details.open = false;
            } else {
                renderInspectionRows();
            }
            markInspectionPlanDirty();
        });
    }
    const available = document.getElementById("inspectionAvailableAgents");
    if (available) {
        available.addEventListener("change", (event) => {
            const control = event.target.closest("[data-inspection-available-agent]");
            if (!control) return;
            const id = inspectionNumber(control.dataset.inspectionAvailableAgent, 0);
            if (control.checked) inspectionAvailableAgentIds.add(id);
            else {
                inspectionAvailableAgentIds.delete(id);
                inspectionRows.forEach((row) => { row.agent_ids = row.agent_ids.filter((agentId) => agentId !== id); });
            }
            renderInspectionAvailableAgents();
            renderInspectionRows();
            markInspectionPlanDirty();
        });
    }
    const planDateControl = document.getElementById("inspectionPlanDate");
    if (planDateControl) {
        planDateControl.addEventListener("change", () => {
            const planNameControl = document.getElementById("inspectionPlanName");
            if (planNameControl) planNameControl.value = inspectionDefaultPlanName(planDateControl.value);
            markInspectionPlanDirty();
        });
    }
    ["inspectionDayStart", "inspectionDayEnd", "inspectionAllowAgentOverlap"].forEach((id) => {
        const control = document.getElementById(id);
        if (control) control.addEventListener("change", markInspectionPlanDirty);
    });
    const saved = document.getElementById("inspectionSavedPlans");
    if (saved) {
        saved.addEventListener("click", (event) => {
            const button = event.target.closest("[data-inspection-open-plan]");
            if (button) loadInspectionPlan(button.dataset.inspectionOpenPlan);
        });
        saved.addEventListener("change", (event) => {
            const select = event.target.closest("[data-inspection-plan-status]");
            if (select) updateInspectionPlanStatus(select.dataset.inspectionPlanStatus, select.value, select);
        });
    }
    window.addEventListener("resize", invalidateInspectionMap, { passive: true });
}

async function loadInspectionAgents(force = false) {
    if (!force && inspectionAgents.length) {
        renderInspectionAvailableAgents();
        return inspectionAgents;
    }
    try {
        const response = await apiFetch("/user-auth/team");
        const data = await response.json().catch(() => null);
        if (!response.ok) throw new Error(data?.detail || "Could not load the team.");
        const items = Array.isArray(data) ? data : inspectionArray(data?.items || data?.team || data?.users);
        inspectionAgents = items.filter((agent) => agent?.is_active !== false && inspectionAgentId(agent) > 0);
        const validIds = new Set(inspectionAgents.map(inspectionAgentId));
        if (!inspectionAvailabilityInitialized) {
            inspectionAvailableAgentIds = new Set(validIds);
            inspectionAvailabilityInitialized = true;
        } else {
            inspectionAvailableAgentIds = new Set([...inspectionAvailableAgentIds].filter((id) => validIds.has(id)));
        }
        renderInspectionAvailableAgents();
        renderInspectionRows();
        return inspectionAgents;
    } catch (error) {
        inspectionAgents = [];
        inspectionAvailableAgentIds = new Set();
        renderInspectionAvailableAgents();
        setInspectionMessage(error?.message || "Could not load active agents.", "error");
        return [];
    }
}

function addInspectionVisit(seed = {}) {
    inspectionRows.push(createInspectionRow(seed));
    renderInspectionRows();
    markInspectionPlanDirty();
    const target = document.getElementById("inspectionVisits");
    if (target) setTimeout(() => target.lastElementChild?.scrollIntoView({ behavior: "smooth", block: "nearest" }), 30);
}

function resetInspectionWorkspace(options = {}) {
    cancelInspectionAddressSuggestionSearch(true);
    const preserveDate = !!options.preserveDate;
    const preserveAgents = !!options.preserveAgents;
    const dateControl = document.getElementById("inspectionPlanDate");
    const date = preserveDate && dateControl?.value ? dateControl.value : inspectionToday();
    inspectionCurrentPlanId = null;
    inspectionLastOptimization = null;
    inspectionRows = [createInspectionRow()];
    if (!preserveAgents) {
        inspectionAvailabilityInitialized = false;
        inspectionAvailableAgentIds = new Set(inspectionAgents.map(inspectionAgentId));
        inspectionAvailabilityInitialized = inspectionAgents.length > 0;
    }
    const values = {
        inspectionPlanName: inspectionDefaultPlanName(date),
        inspectionPlanDate: date,
        inspectionDayStart: "09:00",
        inspectionDayEnd: "17:30",
        inspectionStartAddress: INSPECTION_DEFAULT_DEPARTURE_ADDRESS,
    };
    Object.entries(values).forEach(([id, value]) => {
        const control = document.getElementById(id);
        if (control) control.value = value;
    });
    const overlap = document.getElementById("inspectionAllowAgentOverlap");
    if (overlap) overlap.checked = false;
    const save = document.getElementById("inspectionSaveBtn");
    if (save) save.disabled = true;
    renderInspectionAvailableAgents();
    renderInspectionRows();
    clearInspectionResults();
    setInspectionMessage("");
}

function newInspectionPlan() {
    resetInspectionWorkspace({ preserveAgents: true });
    setInspectionMessage("Started a fresh inspection plan.", "success");
}

async function initInspectionsWorkspace(force = false) {
    if (!document.getElementById("inspectionsPanel")) return;
    bindInspectionEvents();
    if (!inspectionRows.length) resetInspectionWorkspace({ preserveAgents: true });
    const planDateControl = document.getElementById("inspectionPlanDate");
    const date = planDateControl?.value || inspectionToday();
    if (planDateControl) planDateControl.value = date;
    const planNameControl = document.getElementById("inspectionPlanName");
    if (planNameControl) planNameControl.value = inspectionDefaultPlanName(date);
    const departureControl = document.getElementById("inspectionStartAddress");
    if (departureControl) departureControl.value = INSPECTION_DEFAULT_DEPARTURE_ADDRESS;
    renderInspectionRows();
    ensureInspectionMap();
    if (inspectionsLoadedOnce && !force) {
        renderInspectionPropertyOptions();
        invalidateInspectionMap();
        return;
    }
    setInspectionMessage("Loading properties, agents, and recent plans…", "busy");
    await Promise.allSettled([
        refreshPropertyOptions(),
        loadInspectionAgents(force),
        loadInspectionPlans(force),
    ]);
    renderInspectionPropertyOptions();
    renderInspectionRows();
    inspectionsLoadedOnce = true;
    if (document.getElementById("inspectionPlannerStatus")?.classList.contains("busy")) setInspectionMessage("");
    invalidateInspectionMap();
}

function inspectionApiError(data, fallback = "Request failed.") {
    const detail = data?.detail ?? data?.message ?? data?.error;
    if (Array.isArray(detail)) {
        return detail.map((item) => item?.msg || item?.message || String(item)).join("; ");
    }
    if (detail && typeof detail === "object") return detail.message || JSON.stringify(detail);
    return String(detail || fallback);
}

function collectInspectionPayload() {
    const planDate = String(document.getElementById("inspectionPlanDate")?.value || "").trim();
    const dayStart = String(document.getElementById("inspectionDayStart")?.value || "").trim();
    const dayEnd = String(document.getElementById("inspectionDayEnd")?.value || "").trim();
    if (!planDate) throw new Error("Choose an inspection date.");
    if (!dayStart || !dayEnd) throw new Error("Set both the start and end of the working day.");
    if (dayStart >= dayEnd) throw new Error("The working day must end after it starts.");
    const planName = inspectionDefaultPlanName(planDate);
    const planNameControl = document.getElementById("inspectionPlanName");
    if (planNameControl) planNameControl.value = planName;
    const departureControl = document.getElementById("inspectionStartAddress");
    if (departureControl) departureControl.value = INSPECTION_DEFAULT_DEPARTURE_ADDRESS;
    const availableAgentIds = [...inspectionAvailableAgentIds].filter((id) => id > 0).sort((a, b) => a - b);
    if (!availableAgentIds.length) throw new Error("Select at least one available agent.");
    if (!inspectionRows.length) throw new Error("Add at least one inspection stop.");
    const missing = [];
    const unverified = [];
    const pending = [];
    const visits = inspectionRows.map((row, index) => {
        const propertyAddress = String(row.property_label || "").trim();
        if (!row.property_id && propertyAddress) {
            const match = inspectionResolveManagedProperty(propertyAddress);
            if (match) {
                row.property_id = inspectionNumber(match.id, 0);
                row.address_verified = true;
            }
        }
        const inspectionNumberLabel = index + 1;
        if (row.address_verification_pending) {
            pending.push(inspectionNumberLabel);
            row.property_invalid = true;
            row.address_status = "Wait for Vicmap to finish verifying this address.";
            row.address_status_type = "warning";
        } else if (!row.property_id && !propertyAddress) {
            missing.push(inspectionNumberLabel);
            row.property_invalid = true;
            row.address_status = "Choose a managed property or a verified Victorian address.";
            row.address_status_type = "error";
        } else if (!row.property_id && !row.address_verified) {
            unverified.push(inspectionNumberLabel);
            row.property_invalid = true;
            row.address_status = "Select this address from the verified Vicmap suggestions before optimising.";
            row.address_status_type = "error";
        } else {
            row.property_invalid = false;
        }
        if (row.earliest_time && row.latest_time && row.earliest_time > row.latest_time) {
            throw new Error(`Inspection ${index + 1} has a latest arrival earlier than its earliest arrival.`);
        }
        return {
            client_id: String(row.client_id),
            property_id: row.property_id ? inspectionNumber(row.property_id, 0) : null,
            property_address: row.property_id ? null : propertyAddress,
            agent_ids: row.agent_ids.filter((id) => inspectionAvailableAgentIds.has(id)),
            duration_minutes: Math.min(480, Math.max(5, inspectionNumber(row.duration_minutes, 30))),
            buffer_minutes: Math.min(240, Math.max(0, inspectionNumber(row.buffer_minutes, 0))),
            earliest_time: row.earliest_time || null,
            latest_time: row.latest_time || null,
            notes: String(row.notes || "").trim() || null,
        };
    });
    if (pending.length || missing.length || unverified.length) {
        renderInspectionRows();
        if (pending.length) {
            throw new Error(`Wait for address verification to finish for inspection${pending.length === 1 ? "" : "s"} ${pending.join(", ")}.`);
        }
        if (unverified.length) {
            throw new Error(`Select a verified Victorian address for inspection${unverified.length === 1 ? "" : "s"} ${unverified.join(", ")}.`);
        }
        throw new Error(`Choose a managed property or verified address for inspection${missing.length === 1 ? "" : "s"} ${missing.join(", ")}.`);
    }
    return {
        plan_name: planName,
        plan_date: planDate,
        day_start: dayStart,
        day_end: dayEnd,
        start_address: INSPECTION_DEFAULT_DEPARTURE_ADDRESS,
        available_agent_ids: availableAgentIds,
        allow_agent_overlap: !!document.getElementById("inspectionAllowAgentOverlap")?.checked,
        visits,
    };
}

async function optimizeInspectionPlan() {
    let payload;
    try {
        payload = collectInspectionPayload();
    } catch (error) {
        setInspectionMessage(error?.message || "Review the inspection plan.", "error");
        return;
    }
    const requestMailbox = normalizeMailbox(currentMailbox);
    setInspectionBusy(true, `Calculating the best timings and routes for ${payload.visits.length} inspection${payload.visits.length === 1 ? "" : "s"}…`);
    try {
        const response = await apiFetch("/inspections/optimize", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => null);
        if (requestMailbox !== normalizeMailbox(currentMailbox)) return;
        if (!response.ok) throw new Error(inspectionApiError(data, `Optimisation failed (${response.status}).`));
        const rawResult = data || {};
        const optimizedVisitCount = inspectionResultVisits(normalizeInspectionOptimization(rawResult)).length;
        inspectionLastOptimization = optimizedVisitCount ? rawResult : null;
        renderInspectionOptimization(rawResult);
        if (!optimizedVisitCount) {
            setInspectionMessage("No inspections could be scheduled. Review the visible warnings, availability, and time windows, then try again.", "error");
        } else if (optimizedVisitCount < payload.visits.length) {
            setInspectionMessage(`${optimizedVisitCount} of ${payload.visits.length} inspections were scheduled. Review the warnings before saving.`, "success");
        } else {
            setInspectionMessage("Routes and inspection timings are ready. Review any warnings, then save the plan.", "success");
        }
    } catch (error) {
        if (requestMailbox !== normalizeMailbox(currentMailbox)) return;
        inspectionLastOptimization = null;
        setInspectionMessage(error?.message || "Could not optimise this inspection plan.", "error");
    } finally {
        setInspectionBusy(false);
    }
}

async function saveInspectionPlan() {
    if (!inspectionLastOptimization) {
        setInspectionMessage("Optimise the plan before saving it.", "error");
        return;
    }
    let input;
    try { input = collectInspectionPayload(); }
    catch (error) {
        setInspectionMessage(error?.message || "Review the inspection plan.", "error");
        return;
    }
    const payload = {
        name: input.plan_name,
        status: "PLANNED",
        plan_date: input.plan_date,
        day_start: input.day_start,
        day_end: input.day_end,
        start_address: input.start_address,
        allow_agent_overlap: input.allow_agent_overlap,
        optimization_result: inspectionLastOptimization,
    };
    const requestMailbox = normalizeMailbox(currentMailbox);
    setInspectionBusy(true, "Saving the inspection plan…");
    try {
        const response = await apiFetch("/inspections/plans", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => null);
        if (requestMailbox !== normalizeMailbox(currentMailbox)) return;
        if (!response.ok) throw new Error(inspectionApiError(data, `Could not save the plan (${response.status}).`));
        const saved = data?.plan || data?.item || data?.data || data || {};
        inspectionCurrentPlanId = saved?.id ?? saved?.plan_id ?? inspectionCurrentPlanId;
        await loadInspectionPlans(true);
        if (requestMailbox !== normalizeMailbox(currentMailbox)) return;
        inspectionLastOptimization = null;
        setInspectionMessage("Inspection plan saved successfully.", "success");
    } catch (error) {
        if (requestMailbox !== normalizeMailbox(currentMailbox)) return;
        setInspectionMessage(error?.message || "Could not save the inspection plan.", "error");
    } finally {
        setInspectionBusy(false);
    }
}

function inspectionPlanStatusLabel(status) {
    const value = String(status || "PLANNED").toUpperCase();
    const labels = {
        DRAFT: "Draft",
        PLANNED: "Planned",
        CONFIRMED: "Confirmed",
        IN_PROGRESS: "In progress",
        COMPLETED: "Completed",
        CANCELLED: "Cancelled",
    };
    return labels[value] || value.replaceAll("_", " ").toLowerCase().replace(/^./, (char) => char.toUpperCase());
}

function inspectionPlanStatusOptions(selected) {
    const value = String(selected || "PLANNED").toUpperCase();
    const statuses = ["PLANNED", "CONFIRMED", "IN_PROGRESS", "COMPLETED", "CANCELLED"];
    if (!statuses.includes(value)) statuses.unshift(value);
    return statuses.map((status) => `<option value="${inspectionEscape(status)}" ${status === value ? "selected" : ""}>${inspectionEscape(inspectionPlanStatusLabel(status))}</option>`).join("");
}

function inspectionFormatPlanDate(value) {
    const raw = String(value || "");
    if (!raw) return "No date";
    try {
        return new Date(`${raw.slice(0, 10)}T12:00:00`).toLocaleDateString("en-AU", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
    } catch { return raw; }
}

function renderInspectionPlans() {
    const target = document.getElementById("inspectionSavedPlans");
    const meta = document.getElementById("inspectionSavedMeta");
    if (!target) return;
    if (meta) meta.textContent = `${inspectionPlans.length} recent saved plan${inspectionPlans.length === 1 ? "" : "s"}.`;
    if (!inspectionPlans.length) {
        target.innerHTML = `<div class="inspection-empty">No saved inspection plans yet. Optimise and save your first plan.</div>`;
        return;
    }
    target.innerHTML = inspectionPlans.map((plan) => {
        const id = plan?.id ?? plan?.plan_id;
        const name = plan?.name || plan?.plan_name || `Inspection plan ${id || ""}`;
        const date = plan?.plan_date || plan?.date;
        const status = plan?.status || "PLANNED";
        const current = inspectionCurrentPlanId != null && String(inspectionCurrentPlanId) === String(id);
        return `<article class="inspection-saved-plan" ${current ? `style="border-color:#d0ad53;background:#fffdf6"` : ""}>
            <div><h4>${inspectionEscape(name)}</h4><p>${inspectionEscape(inspectionFormatPlanDate(date))} · ${inspectionEscape(inspectionPlanStatusLabel(status))}</p></div>
            <div class="inspection-saved-actions">
                <select data-inspection-plan-status="${inspectionEscape(id)}" aria-label="Plan status">${inspectionPlanStatusOptions(status)}</select>
                <button class="btn" type="button" data-inspection-open-plan="${inspectionEscape(id)}">Open</button>
            </div>
        </article>`;
    }).join("");
}

async function loadInspectionPlans(force = false) {
    const target = document.getElementById("inspectionSavedPlans");
    const requestMailbox = normalizeMailbox(currentMailbox);
    if (target && (force || !inspectionPlans.length)) target.innerHTML = `<div class="inspection-empty">Loading recent inspection plans…</div>`;
    try {
        const response = await apiFetch("/inspections/plans?limit=12");
        const data = await response.json().catch(() => null);
        if (requestMailbox !== normalizeMailbox(currentMailbox)) return [];
        if (!response.ok) throw new Error(inspectionApiError(data, "Could not load saved plans."));
        const parsed = inspectionParseJson(data);
        inspectionPlans = Array.isArray(parsed)
            ? parsed
            : inspectionArray(parsed?.items || parsed?.plans || parsed?.results || parsed?.data);
        renderInspectionPlans();
        return inspectionPlans;
    } catch (error) {
        if (requestMailbox !== normalizeMailbox(currentMailbox)) return [];
        inspectionPlans = [];
        if (target) target.innerHTML = `<div class="inspection-empty">${inspectionEscape(error?.message || "Could not load saved plans.")}</div>`;
        return [];
    }
}

function normalizeInspectionOptimization(raw) {
    let current = inspectionParseJson(raw);
    if (Array.isArray(current)) return { visits: current };
    if (!current || typeof current !== "object") return {};
    for (let depth = 0; depth < 4; depth += 1) {
        const optimization = inspectionParseJson(current?.optimization_result ?? current?.optimization);
        if (optimization && typeof optimization === "object" && optimization !== current) {
            current = optimization;
            continue;
        }
        const result = inspectionParseJson(current?.result);
        if (result && typeof result === "object" && (result.visits || result.routes || result.metrics || result.schedule)) {
            current = result;
            continue;
        }
        const data = inspectionParseJson(current?.data);
        if (data && typeof data === "object" && (data.visits || data.routes || data.metrics || data.schedule || data.optimization_result)) {
            current = data;
            continue;
        }
        break;
    }
    return current && typeof current === "object" ? current : {};
}

function inspectionResultVisits(result) {
    return inspectionArray(result?.visits || result?.optimized_visits || result?.schedule || result?.appointments || result?.stops);
}

function inspectionResultRoutes(result) {
    const routes = inspectionParseJson(result?.routes || result?.agent_routes || result?.route_plans);
    if (Array.isArray(routes)) return routes;
    if (routes && typeof routes === "object") {
        return Object.entries(routes).map(([key, value]) => ({
            ...(value && typeof value === "object" ? value : { visits: value }),
            agent_id: value?.agent_id ?? key,
        }));
    }
    return [];
}

function inspectionSourceVisits(plan, optimization) {
    const request = inspectionParseJson(plan?.request_payload || plan?.input || plan?.plan_input);
    const optimizedVisits = inspectionResultVisits(optimization);
    if (optimizedVisits.length) return optimizedVisits;
    return inspectionArray(plan?.visits || plan?.inspection_visits || request?.visits);
}

async function loadInspectionPlan(planId) {
    if (!planId) return;
    const requestMailbox = normalizeMailbox(currentMailbox);
    setInspectionBusy(true, "Loading the saved inspection plan…");
    try {
        const response = await apiFetch(`/inspections/plans/${encodeURIComponent(planId)}`);
        const data = await response.json().catch(() => null);
        if (requestMailbox !== normalizeMailbox(currentMailbox)) return;
        if (!response.ok) throw new Error(inspectionApiError(data, "Could not load this plan."));
        let plan = inspectionParseJson(data?.plan || data?.item || data?.data || data) || {};
        if (plan?.plan && typeof plan.plan === "object") plan = plan.plan;
        const optimizationRaw = inspectionParseJson(plan?.optimization_result || plan?.optimization || data?.optimization_result || data?.result);
        const optimization = normalizeInspectionOptimization(optimizationRaw);
        const date = String(plan?.plan_date || plan?.date || inspectionToday()).slice(0, 10);
        const values = {
            inspectionPlanName: inspectionDefaultPlanName(date),
            inspectionPlanDate: date,
            inspectionDayStart: String(plan?.day_start || plan?.start_time || "09:00").slice(0, 5),
            inspectionDayEnd: String(plan?.day_end || plan?.end_time || "17:30").slice(0, 5),
            inspectionStartAddress: INSPECTION_DEFAULT_DEPARTURE_ADDRESS,
        };
        Object.entries(values).forEach(([id, value]) => {
            const control = document.getElementById(id);
            if (control) control.value = String(value || "");
        });
        const overlap = document.getElementById("inspectionAllowAgentOverlap");
        if (overlap) overlap.checked = !!(plan?.allow_agent_overlap ?? plan?.allow_overlap);
        const sourceVisits = inspectionSourceVisits(plan, optimization);
        inspectionRows = sourceVisits.length ? sourceVisits.map(createInspectionRow) : [createInspectionRow()];
        let planAgentIds = inspectionExtractAgentIds(plan?.available_agent_ids || optimization?.available_agent_ids);
        if (!planAgentIds.length) {
            planAgentIds = [...new Set(sourceVisits.flatMap((visit) => inspectionExtractAgentIds(
                visit?.agent_ids ?? visit?.assigned_agent_ids ?? visit?.agents ?? visit?.agent_id
            )))];
        }
        planAgentIds = planAgentIds.filter((agentId) => !!inspectionAgentById(agentId));
        if (planAgentIds.length) {
            inspectionAvailableAgentIds = new Set(planAgentIds);
            inspectionAvailabilityInitialized = true;
        }
        inspectionCurrentPlanId = plan?.id ?? plan?.plan_id ?? planId;
        const loadedOptimization = optimizationRaw && typeof optimizationRaw === "object" ? optimizationRaw : null;
        inspectionLastOptimization = null;
        renderInspectionAvailableAgents();
        renderInspectionPropertyOptions();
        renderInspectionRows();
        renderInspectionPlans();
        if (loadedOptimization) renderInspectionOptimization(loadedOptimization);
        else clearInspectionResults();
        const save = document.getElementById("inspectionSaveBtn");
        if (save) save.disabled = true;
        setInspectionMessage(`Loaded ${values.inspectionPlanName}.`, "success");
    } catch (error) {
        if (requestMailbox !== normalizeMailbox(currentMailbox)) return;
        setInspectionMessage(error?.message || "Could not load the saved inspection plan.", "error");
    } finally {
        setInspectionBusy(false);
    }
}

async function updateInspectionPlanStatus(planId, status, control = null) {
    if (!planId || !status) return;
    const requestMailbox = normalizeMailbox(currentMailbox);
    if (control) control.disabled = true;
    try {
        const response = await apiFetch(`/inspections/plans/${encodeURIComponent(planId)}/status`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status }),
        });
        const data = await response.json().catch(() => null);
        if (requestMailbox !== normalizeMailbox(currentMailbox)) return;
        if (!response.ok) throw new Error(inspectionApiError(data, "Could not update plan status."));
        const plan = inspectionPlans.find((item) => String(item?.id ?? item?.plan_id) === String(planId));
        if (plan) plan.status = data?.status || data?.plan?.status || status;
        renderInspectionPlans();
        setInspectionMessage(`Plan marked ${inspectionPlanStatusLabel(status).toLowerCase()}.`, "success");
    } catch (error) {
        if (requestMailbox !== normalizeMailbox(currentMailbox)) return;
        setInspectionMessage(error?.message || "Could not update plan status.", "error");
        await loadInspectionPlans(true);
    } finally {
        if (control?.isConnected) control.disabled = false;
    }
}

function inspectionProviderLabel(provider) {
    if (!provider) return "Route engine";
    if (typeof provider === "string") return provider;
    return String(provider.name || provider.label || provider.provider || provider.model || "Route engine");
}

function inspectionHumanize(value) {
    return String(value || "")
        .replace(/([a-z])([A-Z])/g, "$1 $2")
        .replaceAll("_", " ")
        .replace(/^./, (char) => char.toUpperCase());
}

function inspectionFormatMinutes(value) {
    const minutes = Math.round(inspectionNumber(value, 0));
    if (minutes <= 0) return "0 min";
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    const remainder = minutes % 60;
    return remainder ? `${hours}h ${remainder}m` : `${hours}h`;
}

function inspectionFormatMetricValue(key, value) {
    if (value == null || value === "") return "-";
    const name = String(key || "").toLowerCase();
    if (typeof value === "boolean") return value ? "Yes" : "No";
    if (name.includes("minute") || name.endsWith("_mins") || name.includes("duration")) return inspectionFormatMinutes(value);
    if (name.includes("distance") && typeof value === "number") return `${value.toFixed(value < 10 ? 1 : 0)} km`;
    if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(1);
    return String(value);
}

function renderInspectionMetrics(result, visits, routes) {
    const target = document.getElementById("inspectionMetrics");
    if (!target) return;
    const raw = inspectionParseJson(result?.metrics || result?.summary || {});
    let entries = [];
    if (Array.isArray(raw)) {
        entries = raw.map((item, index) => [item?.label || item?.name || `Metric ${index + 1}`, item?.value ?? item?.total ?? item]);
    } else if (raw && typeof raw === "object") {
        entries = Object.entries(raw).filter(([, value]) => value == null || ["string", "number", "boolean"].includes(typeof value));
    }
    if (!entries.length) {
        entries = [
            ["scheduled_visits", visits.length],
            ["agents_used", routes.length],
            ["warnings", inspectionArray(result?.warnings).length],
        ];
    }
    target.innerHTML = entries.slice(0, 8).map(([key, value]) => `<div class="inspection-metric">
        <span>${inspectionEscape(inspectionHumanize(key))}</span>
        <strong>${inspectionEscape(inspectionFormatMetricValue(key, value))}</strong>
    </div>`).join("");
}

function inspectionRouteAgentIds(route) {
    return inspectionExtractAgentIds(route?.agent_ids || route?.agents || route?.agent_id || route?.assigned_agent_id || route?.user_id || route?.agent);
}

function inspectionVisitAgentIds(visit) {
    return inspectionExtractAgentIds(visit?.agent_ids || visit?.assigned_agent_ids || visit?.agents || visit?.agent_id || visit?.assigned_agent_id || visit?.assigned_user_id || visit?.agent);
}

function inspectionRouteDistance(route) {
    const direct = route?.distance_km ?? route?.total_distance_km ?? route?.distance;
    if (direct != null && direct !== "") {
        const number = inspectionNumber(direct, NaN);
        if (Number.isFinite(number)) return number;
    }
    const metres = inspectionNumber(route?.distance_metres ?? route?.distance_meters ?? route?.total_distance_meters, NaN);
    return Number.isFinite(metres) ? metres / 1000 : null;
}

function inspectionRouteDuration(route) {
    const minutes = route?.drive_minutes ?? route?.travel_minutes ?? route?.total_travel_minutes ?? route?.duration_minutes ?? route?.total_duration_minutes;
    if (minutes != null && minutes !== "") return inspectionNumber(minutes, 0);
    const seconds = inspectionNumber(route?.duration_seconds ?? route?.travel_seconds, NaN);
    return Number.isFinite(seconds) ? seconds / 60 : null;
}

function inspectionRouteColour(route, index) {
    const colour = String(route?.color || route?.colour || "").trim();
    return /^#[0-9a-f]{3,8}$/i.test(colour) ? colour : INSPECTION_ROUTE_COLOURS[index % INSPECTION_ROUTE_COLOURS.length];
}

function renderInspectionRouteLegend(routes) {
    const target = document.getElementById("inspectionRouteLegend");
    if (!target) return;
    if (!routes.length) {
        target.innerHTML = `<div class="inspection-empty">No per-agent route details were returned.</div>`;
        return;
    }
    target.innerHTML = routes.map((route, index) => {
        const ids = inspectionRouteAgentIds(route);
        const returnedNames = inspectionArray(route?.agent_names);
        const names = ids.length
            ? ids.map((id, agentIndex) => inspectionAgentName(id, returnedNames[agentIndex] || route?.agent_name)).join(" + ")
            : String(route?.agent_name || route?.name || `Route ${index + 1}`);
        const stops = inspectionArray(route?.visits || route?.stops || route?.appointments).length || inspectionNumber(route?.visit_count ?? route?.stops_count, 0);
        const distance = inspectionRouteDistance(route);
        const duration = inspectionRouteDuration(route);
        const detail = [stops ? `${stops} stop${stops === 1 ? "" : "s"}` : "", distance != null ? `${distance.toFixed(1)} km` : ""].filter(Boolean).join(" · ") || "Optimised route";
        return `<div class="inspection-route-item" style="--route-colour:${inspectionRouteColour(route, index)}">
            <span class="inspection-route-swatch"></span>
            <span><strong>${inspectionEscape(names)}</strong><small>${inspectionEscape(detail)}</small></span>
            <span>${duration != null ? inspectionEscape(inspectionFormatMinutes(duration)) : ""}</span>
        </div>`;
    }).join("");
}

function inspectionFormatClock(value) {
    if (!value) return "Flexible";
    const raw = String(value);
    const timeMatch = raw.match(/(?:T|^)(\d{2}):(\d{2})/);
    if (timeMatch) {
        const hour = Number(timeMatch[1]);
        const minute = timeMatch[2];
        const suffix = hour >= 12 ? "pm" : "am";
        return `${hour % 12 || 12}:${minute}${suffix}`;
    }
    try {
        return new Intl.DateTimeFormat("en-AU", { timeZone: "Australia/Melbourne", hour: "numeric", minute: "2-digit" }).format(new Date(raw));
    } catch { return raw; }
}

function inspectionVisitAddress(visit) {
    const property = visit?.property && typeof visit.property === "object" ? visit.property : null;
    const row = inspectionRowByClientId(visit?.client_id || visit?.visit_id);
    return String(
        visit?.property_label || visit?.address || visit?.property_address || visit?.full_address ||
        property?.label || property?.full_address || property?.property_address ||
        inspectionPropertyLabel(visit?.property_id || property?.id, row?.property_label || "Inspection stop")
    );
}

function renderInspectionSchedule(visits) {
    const target = document.getElementById("inspectionSchedule");
    if (!target) return;
    if (!visits.length) {
        target.innerHTML = `<div class="inspection-empty">No calculated visits were returned.</div>`;
        return;
    }
    target.innerHTML = visits.map((visit, index) => {
        const start = visit?.scheduled_start || visit?.start_time || visit?.appointment_start || visit?.arrival_time || visit?.scheduled_time;
        const end = visit?.scheduled_end || visit?.end_time || visit?.appointment_end || visit?.departure_time;
        const ids = inspectionVisitAgentIds(visit);
        const returnedNames = inspectionArray(visit?.agent_names);
        const agentNames = ids.length
            ? ids.map((id, agentIndex) => inspectionAgentName(id, returnedNames[agentIndex])).join(", ")
            : String(returnedNames.join(", ") || visit?.agent_name || "Auto-allocated");
        const duration = visit?.duration_minutes ?? inspectionRowByClientId(visit?.client_id)?.duration_minutes;
        const detail = [agentNames, duration ? inspectionFormatMinutes(duration) : "", end ? `until ${inspectionFormatClock(end)}` : ""].filter(Boolean).join(" · ");
        const propertyColour = inspectionPropertyColour(visit, index);
        return `<div class="inspection-schedule-row" style="--property-colour:${propertyColour}">
            <span class="inspection-schedule-time">${inspectionEscape(inspectionFormatClock(start))}</span>
            <span class="inspection-schedule-seq">${index + 1}</span>
            <span class="inspection-schedule-copy"><strong>${inspectionEscape(inspectionVisitAddress(visit))}</strong><small>${inspectionEscape(detail)}</small></span>
        </div>`;
    }).join("");
}

function inspectionNoticeText(item) {
    if (item == null) return "";
    if (typeof item === "string" || typeof item === "number") return String(item);
    return String(item.message || item.detail || item.text || item.title || item.warning || item.insight || JSON.stringify(item));
}

function renderInspectionNotices(id, sectionId, values, kind = "") {
    const target = document.getElementById(id);
    const section = document.getElementById(sectionId);
    const items = inspectionArray(values).map(inspectionNoticeText).filter(Boolean);
    if (section) section.classList.toggle("hidden", !items.length);
    if (target) target.innerHTML = items.map((item) => `<div class="inspection-notice ${kind}">${inspectionEscape(item)}</div>`).join("");
}

function inspectionCoordinate(value) {
    const parsed = inspectionParseJson(value);
    if (!parsed) return null;
    if (typeof parsed === "string" && parsed.includes(",")) {
        return inspectionCoordinate(parsed.split(",").map((part) => Number(part.trim())));
    }
    if (Array.isArray(parsed) && parsed.length >= 2 && !Array.isArray(parsed[0])) {
        const first = inspectionNumber(parsed[0], NaN);
        const second = inspectionNumber(parsed[1], NaN);
        if (!Number.isFinite(first) || !Number.isFinite(second)) return null;
        const point = Math.abs(first) > 90 ? [second, first] : (Math.abs(second) > 90 ? [first, second] : [second, first]);
        return Math.abs(point[0]) <= 90 && Math.abs(point[1]) <= 180 ? point : null;
    }
    if (parsed && typeof parsed === "object") {
        const lat = inspectionNumber(parsed.lat ?? parsed.latitude, NaN);
        const lng = inspectionNumber(parsed.lng ?? parsed.lon ?? parsed.long ?? parsed.longitude, NaN);
        if (Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) <= 90 && Math.abs(lng) <= 180) return [lat, lng];
        if (parsed.coordinates) return inspectionCoordinate(parsed.coordinates);
        if (parsed.location) return inspectionCoordinate(parsed.location);
    }
    return null;
}

function decodeInspectionPolyline(encoded, precision = 5) {
    if (typeof encoded !== "string" || encoded.length < 4) return [];
    let index = 0;
    let lat = 0;
    let lng = 0;
    const coordinates = [];
    const factor = 10 ** precision;
    try {
        while (index < encoded.length) {
            let result = 0;
            let shift = 0;
            let byte;
            do {
                byte = encoded.charCodeAt(index++) - 63;
                result |= (byte & 0x1f) << shift;
                shift += 5;
            } while (byte >= 0x20 && index <= encoded.length);
            lat += (result & 1) ? ~(result >> 1) : (result >> 1);
            result = 0;
            shift = 0;
            do {
                byte = encoded.charCodeAt(index++) - 63;
                result |= (byte & 0x1f) << shift;
                shift += 5;
            } while (byte >= 0x20 && index <= encoded.length);
            lng += (result & 1) ? ~(result >> 1) : (result >> 1);
            const point = [lat / factor, lng / factor];
            if (Math.abs(point[0]) <= 90 && Math.abs(point[1]) <= 180) coordinates.push(point);
        }
    } catch { return []; }
    return coordinates;
}

function inspectionGeometryPoints(value) {
    const geometry = inspectionParseJson(value);
    if (!geometry) return [];
    if (typeof geometry === "string") return decodeInspectionPolyline(geometry);
    if (Array.isArray(geometry)) {
        if (geometry.length >= 2 && !Array.isArray(geometry[0]) && typeof geometry[0] !== "object") {
            const point = inspectionCoordinate(geometry);
            return point ? [point] : [];
        }
        return geometry.flatMap((part) => inspectionGeometryPoints(part));
    }
    if (geometry.type === "Feature") return inspectionGeometryPoints(geometry.geometry);
    if (geometry.type === "FeatureCollection") return inspectionArray(geometry.features).flatMap((feature) => inspectionGeometryPoints(feature));
    if (geometry.coordinates) return inspectionGeometryPoints(geometry.coordinates);
    if (geometry.geometry) return inspectionGeometryPoints(geometry.geometry);
    return [];
}

function inspectionVisitCoordinate(visit) {
    const property = visit?.property && typeof visit.property === "object" ? visit.property : null;
    return inspectionCoordinate(visit) || inspectionCoordinate(visit?.location) || inspectionCoordinate(visit?.coordinates) || inspectionCoordinate(visit?.geometry) || inspectionCoordinate(property);
}

function inspectionRoutePath(route) {
    const directCandidates = [route?.geometry, route?.geojson, route?.route_geometry, route?.polyline, route?.encoded_polyline, route?.coordinates, route?.path, route?.points];
    for (const candidate of directCandidates) {
        const points = inspectionGeometryPoints(candidate);
        if (points.length >= 2) return { points, approximate: false };
    }
    const legPoints = inspectionArray(route?.legs || route?.segments).flatMap((leg) => {
        for (const candidate of [leg?.geometry, leg?.geojson, leg?.polyline, leg?.coordinates, leg?.path, leg?.points]) {
            const points = inspectionGeometryPoints(candidate);
            if (points.length) return points;
        }
        return [];
    });
    if (legPoints.length >= 2) return { points: legPoints, approximate: false };
    const stopPoints = inspectionArray(route?.visits || route?.stops || route?.appointments)
        .map(inspectionVisitCoordinate).filter(Boolean);
    return { points: stopPoints, approximate: stopPoints.length >= 2 };
}

function setInspectionMapEmpty(title, text, visible = true) {
    const target = document.getElementById("inspectionMapEmpty");
    if (!target) return;
    const strong = target.querySelector("strong");
    const span = target.querySelector("span");
    if (strong) strong.textContent = title || "Route map";
    if (span) span.textContent = text || "";
    target.classList.toggle("hidden", !visible);
}

function ensureInspectionMap() {
    const target = document.getElementById("inspectionMap");
    const status = document.getElementById("inspectionMapStatus");
    if (!target) return null;
    if (inspectionMap) {
        invalidateInspectionMap();
        return inspectionMap;
    }
    if (!window.L || typeof window.L.map !== "function") {
        if (status) {
            status.textContent = "Interactive map unavailable. Timings and route summaries will still work.";
            status.classList.add("error");
        }
        setInspectionMapEmpty("Map unavailable", "The map library could not be loaded. You can still optimise and save this plan.", true);
        return null;
    }
    try {
        inspectionMap = window.L.map(target, { zoomControl: true, attributionControl: true, preferCanvas: true }).setView([-37.8136, 144.9631], 10);
        const tiles = window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            maxZoom: 19,
            attribution: "&copy; OpenStreetMap contributors",
        });
        let tileErrorShown = false;
        tiles.on("tileerror", () => {
            if (tileErrorShown) return;
            tileErrorShown = true;
            if (status) {
                status.textContent = "Map tiles are temporarily unavailable. Route summaries remain available.";
                status.classList.add("error");
            }
        });
        tiles.addTo(inspectionMap);
        inspectionMapRouteLayer = window.L.layerGroup().addTo(inspectionMap);
        inspectionMapMarkerLayer = window.L.layerGroup().addTo(inspectionMap);
        setInspectionMapEmpty("", "", false);
        if (status) {
            status.textContent = "Map ready. Optimise the plan to draw agent routes.";
            status.classList.remove("error");
        }
        setTimeout(invalidateInspectionMap, 80);
    } catch (error) {
        inspectionMap = null;
        if (status) {
            status.textContent = "Interactive map unavailable. Timings and route summaries will still work.";
            status.classList.add("error");
        }
        setInspectionMapEmpty("Map unavailable", error?.message || "The map could not be initialised.", true);
    }
    return inspectionMap;
}

function invalidateInspectionMap() {
    if (!inspectionMap) return;
    try { inspectionMap.invalidateSize({ pan: false }); } catch { /* map may be detaching */ }
}

function clearInspectionMapLayers() {
    if (inspectionMapRouteLayer) inspectionMapRouteLayer.clearLayers();
    if (inspectionMapMarkerLayer) inspectionMapMarkerLayer.clearLayers();
}

function renderInspectionMap(result, visits, routes) {
    const map = ensureInspectionMap();
    const status = document.getElementById("inspectionMapStatus");
    if (!map || !window.L) return;
    clearInspectionMapLayers();
    const bounds = [];
    let drawnRoutes = 0;
    routes.forEach((route, index) => {
        const routePath = inspectionRoutePath(route);
        if (routePath.points.length < 2) return;
        const colour = inspectionRouteColour(route, index);
        window.L.polyline(routePath.points, {
            color: colour,
            weight: routePath.approximate ? 3 : 5,
            opacity: routePath.approximate ? .65 : .86,
            dashArray: routePath.approximate ? "6 8" : null,
            lineCap: "round",
            lineJoin: "round",
        }).addTo(inspectionMapRouteLayer);
        bounds.push(...routePath.points);
        drawnRoutes += 1;
    });
    let markers = 0;
    visits.forEach((visit, index) => {
        const point = inspectionVisitCoordinate(visit);
        if (!point) return;
        const colour = inspectionPropertyColour(visit, index);
        const icon = window.L.divIcon({
            className: "",
            html: `<span class="inspection-leaflet-stop" style="--property-colour:${colour}">${index + 1}</span>`,
            iconSize: [26, 26],
            iconAnchor: [13, 13],
        });
        const marker = window.L.marker(point, { icon, keyboard: true });
        const returnedNames = inspectionArray(visit?.agent_names);
        const names = inspectionVisitAgentIds(visit).map((id, agentIndex) => inspectionAgentName(id, returnedNames[agentIndex])).join(", ") || returnedNames.join(", ") || visit?.agent_name || "Auto-allocated";
        marker.bindPopup(`<strong>${inspectionEscape(inspectionVisitAddress(visit))}</strong><br>${inspectionEscape(inspectionFormatClock(visit?.scheduled_start || visit?.start_time || visit?.arrival_time))} · ${inspectionEscape(names)}`);
        marker.addTo(inspectionMapMarkerLayer);
        bounds.push(point);
        markers += 1;
    });
    setInspectionMapEmpty("", "", false);
    if (bounds.length) {
        try { map.fitBounds(bounds, { padding: [36, 36], maxZoom: 15 }); } catch { /* keep current view */ }
    } else {
        map.setView([-37.8136, 144.9631], 10);
    }
    if (status) {
        status.classList.remove("error");
        status.textContent = bounds.length
            ? `Showing ${markers} inspection stop${markers === 1 ? "" : "s"} and ${drawnRoutes} mapped route${drawnRoutes === 1 ? "" : "s"}.`
            : "Optimisation completed, but the route provider did not return map coordinates.";
    }
    setTimeout(invalidateInspectionMap, 50);
}

function renderInspectionOptimization(raw) {
    const result = normalizeInspectionOptimization(raw);
    const visits = inspectionResultVisits(result);
    const routes = inspectionResultRoutes(result);
    const provider = document.getElementById("inspectionProvider");
    if (provider) provider.textContent = inspectionProviderLabel(result?.provider || inspectionParseJson(raw)?.provider);
    renderInspectionMetrics(result, visits, routes);
    renderInspectionRouteLegend(routes);
    renderInspectionSchedule(visits);
    const warnings = [...inspectionArray(result?.warnings || result?.conflicts || inspectionParseJson(raw)?.warnings)];
    inspectionArray(result?.unscheduled).forEach((item) => {
        const reason = inspectionNoticeText(item?.reason || item);
        const clientId = item?.client_id ? ` (${item.client_id})` : "";
        if (reason) warnings.push(`Unscheduled inspection${clientId}: ${reason}`);
    });
    renderInspectionNotices("inspectionWarnings", "inspectionWarningsSection", warnings, "");
    renderInspectionNotices("inspectionInsights", "inspectionInsightsSection", result?.insights || result?.recommendations || inspectionParseJson(raw)?.insights, "insight");
    renderInspectionMap(result, visits, routes);
    const save = document.getElementById("inspectionSaveBtn");
    if (save) save.disabled = !inspectionLastOptimization;
}

function clearInspectionResults() {
    const metrics = document.getElementById("inspectionMetrics");
    const legend = document.getElementById("inspectionRouteLegend");
    const schedule = document.getElementById("inspectionSchedule");
    if (metrics) metrics.innerHTML = `<div class="inspection-empty" style="grid-column:1/-1">Metrics appear after optimisation.</div>`;
    if (legend) legend.innerHTML = `<div class="inspection-empty">No routes calculated yet.</div>`;
    if (schedule) schedule.innerHTML = `<div class="inspection-empty">The calculated visit order and timings will appear here.</div>`;
    renderInspectionNotices("inspectionWarnings", "inspectionWarningsSection", [], "");
    renderInspectionNotices("inspectionInsights", "inspectionInsightsSection", [], "insight");
    clearInspectionMapLayers();
    const provider = document.getElementById("inspectionProvider");
    if (provider) provider.textContent = "OpenStreetMap";
    if (inspectionMap) {
        inspectionMap.setView([-37.8136, 144.9631], 10);
        const status = document.getElementById("inspectionMapStatus");
        if (status) {
            status.textContent = "Map ready. Optimise the plan to draw agent routes.";
            status.classList.remove("error");
        }
    }
}

function maintenanceStatusLabel(status) {
    const labels = {
        NEW: "New",
        WAITING_OWNER_APPROVAL: "Waiting Owner Approval",
        OWNER_APPROVED: "Owner Approved",
        OWNER_DECLINED: "Owner Declined",
        OWNER_ARRANGING: "Owner Arranging",
        QUOTE_REQUESTED: "Quote Requested",
        QUOTE_RECEIVED: "Quote Received",
        TRADIE_ARRANGED: "Tradie Arranged",
        TENANT_NOTIFIED: "Tenant Notified",
        COMPLETED: "Completed",
        CANCELLED: "Cancelled",
    };
    return labels[String(status || "NEW").toUpperCase()] || String(status || "New").replaceAll("_", " ");
}

function maintenanceStatusClass(status) {
    const key = String(status || "").toUpperCase();
    if (key === "COMPLETED") return "done";
    if (key === "WAITING_OWNER_APPROVAL" || key === "QUOTE_REQUESTED") return "wait";
    if (key === "OWNER_DECLINED" || key === "CANCELLED") return "stop";
    if (key === "OWNER_APPROVED" || key === "QUOTE_RECEIVED" || key === "TRADIE_ARRANGED" || key === "TENANT_NOTIFIED") return "action";
    return "";
}

function maintenanceStatusChip(status) {
    return `<span class="maintenance-status ${maintenanceStatusClass(status)}">${escapeHtml(maintenanceStatusLabel(status))}</span>`;
}

function maintenanceSourceChip(source) {
    return String(source || "").toLowerCase() === "tenant_portal"
        ? `<span class="maintenance-source-chip">Tenant Portal</span>`
        : "";
}

function maintenanceDateInputValue(value) {
    if (!value) return "";
    try {
        const d = new Date(value);
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    } catch {
        return "";
    }
}

function maintenanceDateTimeInputValue(value) {
    if (!value) return "";
    try {
        const d = new Date(value);
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}T${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    } catch {
        return "";
    }
}

function maintenanceDatePayload(value, dateOnly = false) {
    const raw = String(value || "").trim();
    if (!raw) return null;
    return dateOnly ? `${raw}T00:00:00` : raw;
}

function maintenanceMoney(value) {
    const amount = Number(value || 0);
    if (!(amount > 0)) return "-";
    return amount.toLocaleString(undefined, { style: "currency", currency: "AUD" });
}

function moveMaintenanceFormTo(hostId) {
    const form = document.getElementById("maintenanceFormCard");
    const host = document.getElementById(hostId);
    if (form && host && form.parentElement !== host) host.appendChild(form);
}

function maintenanceOrderModalIsOpen() {
    const modal = document.getElementById("maintenanceOrderModal");
    return Boolean(modal && !modal.classList.contains("hidden"));
}

function closeMaintenanceOrderModal() {
    const modal = document.getElementById("maintenanceOrderModal");
    if (modal) modal.classList.add("hidden");
    moveMaintenanceFormTo("maintenanceNewFormHost");
    selectedMaintenanceOrderId = null;
    renderMaintenanceList(Object.values(maintenanceOrdersCache));
}

function cancelMaintenanceForm() {
    if (maintenanceOrderModalIsOpen()) {
        closeMaintenanceOrderModal();
        return;
    }
    resetMaintenanceForm();
}

function switchMaintenanceView(mode = "dashboard") {
    const view = ["dashboard", "new", "active", "complete", "tenants", "tradies"].includes(mode) ? mode : "dashboard";
    maintenanceViewMode = view;
    document.querySelectorAll("[data-maintenance-view]").forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.maintenanceView === view);
    });
    const dashboard = document.getElementById("maintenanceDashboardView");
    const work = document.getElementById("maintenanceWorkView");
    const formHost = document.getElementById("maintenanceNewFormHost");
    const queue = document.getElementById("maintenanceQueueView");
    const tenantView = document.getElementById("maintenanceTenantRegistrationsView");
    const tradieView = document.getElementById("maintenanceTradiesView");

    [dashboard, work, tenantView, tradieView].forEach((el) => {
        if (el) el.classList.add("hidden");
    });
    closeMaintenanceOrderModal();
    if (formHost) formHost.classList.add("hidden");
    if (queue) queue.classList.remove("hidden");

    if (view === "dashboard") {
        if (dashboard) dashboard.classList.remove("hidden");
        loadMaintenanceDashboard(currentMaintenancePage || 1);
        return;
    }
    if (view === "tenants") {
        if (tenantView) tenantView.classList.remove("hidden");
        loadTenantRegistrations();
        return;
    }
    if (view === "tradies") {
        if (tradieView) tradieView.classList.remove("hidden");
        loadMaintenanceTradies();
        return;
    }

    if (work) work.classList.remove("hidden");
    if (view === "new") {
        resetMaintenanceForm();
        if (formHost) formHost.classList.remove("hidden");
        if (queue) queue.classList.add("hidden");
        return;
    }

    const statusFilter = document.getElementById("maintenanceStatusFilter");
    if (statusFilter) statusFilter.value = view === "complete" ? "COMPLETED" : "OPEN";
    currentMaintenancePage = 1;
    loadMaintenanceDashboard(1);
}

function renderMaintenanceTradieOptions() {
    const list = document.getElementById("maintenanceTradieOptions");
    if (!list) return;
    list.innerHTML = maintenanceTradiesCache
        .filter((item) => item.is_active !== false)
        .map((item) => `<option value="${escapeHtml(item.company || item.label || "")}"></option>`)
        .join("");
}

function findMaintenanceTradieByCompany(value) {
    const key = String(value || "").trim().toLowerCase();
    if (!key) return null;
    return maintenanceTradiesCache.find((item) => String(item.company || "").trim().toLowerCase() === key)
        || maintenanceTradiesCache.find((item) => String(item.label || "").trim().toLowerCase() === key)
        || null;
}

function applyMaintenanceTradieToOrder(value) {
    const tradie = findMaintenanceTradieByCompany(value || document.getElementById("maintenanceTradieCompany")?.value);
    if (!tradie) return;
    const set = (id, text) => {
        const el = document.getElementById(id);
        if (el) el.value = text || "";
    };
    set("maintenanceTradieCompany", tradie.company);
    set("maintenanceTradieName", tradie.contact_name);
    set("maintenanceTradieEmail", tradie.email);
    set("maintenanceTradiePhone", tradie.phone);
}

function renderMaintenanceAssigneeOptions(selectedId = "") {
    const sel = document.getElementById("maintenanceAssignee");
    if (!sel) return;
    const selected = selectedId ? String(selectedId) : sel.value || "";
    sel.innerHTML = [
        `<option value="">Unassigned</option>`,
        ...assignableUsers.map((u) => {
            const value = String(u.id);
            const label = staffOptionLabel(u);
            return `<option value="${escapeHtml(value)}" ${value === selected ? "selected" : ""}>${escapeHtml(label)}</option>`;
        }),
    ].join("");
    sel.value = selected;
}

function updateMaintenancePropertySelection() {
    const search = document.getElementById("maintenancePropertySearch");
    const hidden = document.getElementById("maintenancePropertyId");
    if (!search || !hidden) return null;
    const match = resolvePropertySearchValue(search.value);
    hidden.value = match ? String(match.id) : "";
    if (match) applyPropertyContactsToMaintenance(match, false);
    return match;
}

function setMaintenanceFieldFromProperty(id, value, force = false) {
    const el = document.getElementById(id);
    const text = String(value || "").trim();
    if (!el || !text) return;
    if (force || !String(el.value || "").trim()) el.value = text;
}

function applyPropertyContactsToMaintenance(property, force = false) {
    if (!property) return;
    const owner = property.primary_owner || propertyPrimaryContact(property.owners);
    const tenant = property.primary_tenant || propertyPrimaryContact(property.tenants);
    setMaintenanceFieldFromProperty("maintenanceOwnerName", owner.name, force);
    setMaintenanceFieldFromProperty("maintenanceOwnerEmail", owner.email, force);
    setMaintenanceFieldFromProperty("maintenanceOwnerPhone", owner.phone, force);
    setMaintenanceFieldFromProperty("maintenanceTenantName", tenant.name, force);
    setMaintenanceFieldFromProperty("maintenanceTenantEmail", tenant.email, force);
    setMaintenanceFieldFromProperty("maintenanceTenantPhone", tenant.phone, force);
    if (force) {
        setMaintenanceFieldFromProperty("maintenancePropertySearch", property.label || propertyFullAddress(property), true);
        setMaintenanceFieldFromProperty("maintenancePropertyId", property.id, true);
    }
}

async function openMaintenanceForProperty(propertyId) {
    const id = Number(propertyId);
    if (!propertyOptionsCache.length) await refreshPropertyOptions();
    const property = propertyOptionsCache.find((p) => Number(p.id) === id) || propertyResultsCache[id];
    if (!property) {
        alert("Could not find this property in the current property register.");
        return;
    }
    switchDashboardTab("maintenance");
    switchMaintenanceView("new");
    applyPropertyContactsToMaintenance(property, true);
    const title = document.getElementById("maintenanceTitle");
    if (title && !String(title.value || "").trim()) title.focus();
}

function maintenanceFormPayload() {
    const selectedProperty = updateMaintenancePropertySelection();
    const propertyInput = document.getElementById("maintenancePropertySearch");
    const quotedRaw = document.getElementById("maintenanceQuotedAmount")?.value || "";
    return {
        property_id: selectedProperty ? Number(selectedProperty.id) : null,
        property_address: selectedProperty ? selectedProperty.property_address : String(propertyInput?.value || "").trim(),
        suburb: selectedProperty ? selectedProperty.suburb : null,
        state_code: selectedProperty ? selectedProperty.state_code : "VIC",
        postcode: selectedProperty ? selectedProperty.postcode : null,
        title: String(document.getElementById("maintenanceTitle")?.value || "").trim(),
        category: String(document.getElementById("maintenanceCategory")?.value || "").trim(),
        priority: String(document.getElementById("maintenancePriority")?.value || "normal").trim(),
        description: String(document.getElementById("maintenanceDescription")?.value || "").trim(),
        access_notes: String(document.getElementById("maintenanceAccessNotes")?.value || "").trim(),
        owner_name: String(document.getElementById("maintenanceOwnerName")?.value || "").trim(),
        owner_email: String(document.getElementById("maintenanceOwnerEmail")?.value || "").trim(),
        owner_phone: String(document.getElementById("maintenanceOwnerPhone")?.value || "").trim(),
        tenant_name: String(document.getElementById("maintenanceTenantName")?.value || "").trim(),
        tenant_email: String(document.getElementById("maintenanceTenantEmail")?.value || "").trim(),
        tenant_phone: String(document.getElementById("maintenanceTenantPhone")?.value || "").trim(),
        due_by: maintenanceDatePayload(document.getElementById("maintenanceDueBy")?.value, true),
        assignee_user_id: document.getElementById("maintenanceAssignee")?.value ? Number(document.getElementById("maintenanceAssignee").value) : null,
        tradie_name: String(document.getElementById("maintenanceTradieName")?.value || "").trim(),
        tradie_company: String(document.getElementById("maintenanceTradieCompany")?.value || "").trim(),
        tradie_email: String(document.getElementById("maintenanceTradieEmail")?.value || "").trim(),
        tradie_phone: String(document.getElementById("maintenanceTradiePhone")?.value || "").trim(),
        tradie_scheduled_for: maintenanceDatePayload(document.getElementById("maintenanceTradieScheduledFor")?.value),
        quoted_amount: quotedRaw ? Number(quotedRaw) : null,
        quote_notes: String(document.getElementById("maintenanceQuoteNotes")?.value || "").trim(),
    };
}

function resetMaintenanceForm() {
    selectedMaintenanceOrderId = null;
    moveMaintenanceFormTo("maintenanceNewFormHost");
    const ids = [
        "maintenanceOrderId", "maintenancePropertyId", "maintenancePropertySearch", "maintenanceTitle",
        "maintenanceDescription", "maintenanceAccessNotes", "maintenanceOwnerName", "maintenanceOwnerEmail",
        "maintenanceOwnerPhone", "maintenanceTenantName", "maintenanceTenantEmail", "maintenanceTenantPhone",
        "maintenanceDueBy", "maintenanceTradieName", "maintenanceTradieCompany", "maintenanceTradieEmail",
        "maintenanceTradiePhone", "maintenanceTradieScheduledFor", "maintenanceQuotedAmount", "maintenanceQuoteNotes",
    ];
    ids.forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.value = "";
    });
    const category = document.getElementById("maintenanceCategory");
    const priority = document.getElementById("maintenancePriority");
    if (category) category.value = "General";
    if (priority) priority.value = "normal";
    renderMaintenanceAssigneeOptions("");
    const title = document.getElementById("maintenanceFormTitle");
    const subtitle = document.getElementById("maintenanceFormSubtitle");
    if (title) title.textContent = "New Maintenance Order";
    if (subtitle) subtitle.textContent = "Create the job once, then update it as approval, quotes, tradies, and completion move forward.";
    const newButton = document.getElementById("maintenanceFormNewButton");
    const saveButton = document.getElementById("maintenanceSaveButton");
    const secondaryButton = document.getElementById("maintenanceSecondaryButton");
    if (newButton) newButton.classList.remove("hidden");
    if (saveButton) saveButton.textContent = "Create Order";
    if (secondaryButton) secondaryButton.textContent = "Clear";
}

function fillMaintenanceForm(order) {
    if (!order) return;
    const setVal = (id, value = "") => {
        const el = document.getElementById(id);
        if (el) el.value = value || "";
    };
    setVal("maintenanceOrderId", order.id);
    setVal("maintenancePropertyId", order.property_id);
    setVal("maintenancePropertySearch", order.property_label || order.property_address);
    setVal("maintenanceTitle", order.title);
    setVal("maintenanceCategory", order.category || "General");
    setVal("maintenancePriority", order.priority || "normal");
    setVal("maintenanceDescription", order.description);
    setVal("maintenanceAccessNotes", order.access_notes);
    setVal("maintenanceOwnerName", order.owner_name);
    setVal("maintenanceOwnerEmail", order.owner_email);
    setVal("maintenanceOwnerPhone", order.owner_phone);
    setVal("maintenanceTenantName", order.tenant_name);
    setVal("maintenanceTenantEmail", order.tenant_email);
    setVal("maintenanceTenantPhone", order.tenant_phone);
    setVal("maintenanceDueBy", maintenanceDateInputValue(order.due_by));
    setVal("maintenanceTradieName", order.tradie_name);
    setVal("maintenanceTradieCompany", order.tradie_company);
    setVal("maintenanceTradieEmail", order.tradie_email);
    setVal("maintenanceTradiePhone", order.tradie_phone);
    setVal("maintenanceTradieScheduledFor", maintenanceDateTimeInputValue(order.tradie_scheduled_for));
    setVal("maintenanceQuotedAmount", order.quoted_amount || "");
    setVal("maintenanceQuoteNotes", order.quote_notes || order.owner_decision_notes || order.completion_notes || "");
    renderMaintenanceAssigneeOptions(order.assignee_user_id || "");
    const title = document.getElementById("maintenanceFormTitle");
    const subtitle = document.getElementById("maintenanceFormSubtitle");
    if (title) title.textContent = "Edit Request Details";
    if (subtitle) subtitle.textContent = "Update the request fields here, then continue the workflow alongside them.";
    const newButton = document.getElementById("maintenanceFormNewButton");
    const saveButton = document.getElementById("maintenanceSaveButton");
    const secondaryButton = document.getElementById("maintenanceSecondaryButton");
    if (newButton) newButton.classList.add("hidden");
    if (saveButton) saveButton.textContent = "Save Changes";
    if (secondaryButton) secondaryButton.textContent = "Close";
}

async function saveMaintenanceOrder() {
    const payload = maintenanceFormPayload();
    if (!payload.property_address && !payload.property_id) {
        alert("Select or enter a property first.");
        return;
    }
    if (!payload.title || !payload.description) {
        alert("Issue title and description are required.");
        return;
    }
    const id = document.getElementById("maintenanceOrderId")?.value || "";
    const r = await apiFetch(id ? `/maintenance/orders/${id}` : "/maintenance/orders", {
        method: id ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    if (!r.ok) {
        alert(`Failed to save maintenance order: ${await extractErrorMessage(r)}`);
        return;
    }
    const order = await r.json();
    selectedMaintenanceOrderId = order.id;
    fillMaintenanceForm(order);
    renderMaintenanceDetail(order);
    if (!id) {
        await loadNotifications();
        switchMaintenanceView("active");
        await openMaintenanceOrder(order.id);
        return;
    }
    await loadMaintenanceDashboard(currentMaintenancePage || 1);
    await loadNotifications();
}

function renderMaintenanceSummary(summary = {}) {
    const setText = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = String(val || 0);
    };
    setText("maintenanceKpiOpen", summary.open);
    setText("maintenanceKpiWaiting", summary.waiting_owner);
    setText("maintenanceKpiQuotes", summary.quotes);
    setText("maintenanceKpiScheduled", summary.scheduled);
    setText("maintenanceKpiCompleted", summary.completed);
}

function renderMaintenanceList(items) {
    const list = document.getElementById("maintenanceList");
    if (!list) return;
    maintenanceOrdersCache = {};
    items.forEach((item) => { maintenanceOrdersCache[item.id] = item; });
    if (!items.length) {
        list.innerHTML = `<div class="ticket-empty"><strong>No maintenance orders found</strong><div class="small muted" style="margin-top:6px">Create a new order or adjust the filters.</div></div>`;
        return;
    }
    list.innerHTML = items.map((item) => `
      <article class="maintenance-order-card ${Number(selectedMaintenanceOrderId) === Number(item.id) ? "active" : ""}">
        <div>
          <h4>${escapeHtml(item.reference || `#${item.id}`)} ${escapeHtml(item.title || "Maintenance order")}</h4>
          <p>${escapeHtml(item.property_label || item.property_address || "-")}</p>
          <p>${escapeHtml(item.category || "General")} - ${escapeHtml(item.priority || "normal")} - Updated ${escapeHtml(formatDateShort(item.updated_at))}</p>
        </div>
        <div class="maintenance-order-card-actions">
          <div class="row" style="gap:6px;flex-wrap:wrap">
            ${item.info_request && item.info_request.required ? `<span class="maintenance-status wait">Info Required</span>` : ""}
            ${maintenanceSourceChip(item.source)}${maintenanceStatusChip(item.status)}
          </div>
          <button class="btn primary" type="button"
            onclick="openMaintenanceOrder(${item.id})">Manage Request</button>
        </div>
      </article>
    `).join("");
}

function tenantRegistrationStatusChip(item) {
    const active = item && item.is_active !== false;
    const verified = item && item.is_verified === true;
    return `
      <span class="maintenance-status ${active ? "done" : "stop"}">${active ? "Active" : "Inactive"}</span>
      <span class="maintenance-status ${verified ? "action" : "wait"}">${verified ? "Verified" : "Pending"}</span>
    `;
}

function renderTenantRegistrations(items = []) {
    tenantRegistrationsCache = Array.isArray(items) ? items : [];
    const list = document.getElementById("tenantRegistrationsList");
    if (!list) return;
    if (!tenantRegistrationsCache.length) {
        list.innerHTML = `<div class="ticket-empty"><strong>No tenant registrations found</strong><div class="small muted" style="margin-top:6px">Tenant portal registrations will appear here.</div></div>`;
        return;
    }
    list.innerHTML = tenantRegistrationsCache.map((item) => `
      <article class="tenant-registration-card">
        <div>
          <div class="row" style="gap:8px;flex-wrap:wrap">${tenantRegistrationStatusChip(item)}</div>
          <h4 style="margin-top:10px">${escapeHtml(item.name || item.email || "Tenant")}</h4>
          <p>${escapeHtml(item.email || "-")} ${item.phone ? `- ${escapeHtml(item.phone)}` : ""}</p>
          ${item.preferred_contact_method ? `<p><strong>Preferred contact:</strong> ${escapeHtml(item.preferred_contact_method)}</p>` : ""}
          <p><strong>Property:</strong> ${escapeHtml(item.property_label || item.property_address || "-")}</p>
          <p class="small muted">Registered ${escapeHtml(formatDateShort(item.created_at))}${item.last_login_at ? ` - Last login ${escapeHtml(formatDateShort(item.last_login_at))}` : ""}</p>
        </div>
        <div class="row" style="justify-content:flex-end">
          <button class="btn" onclick="updateTenantRegistration(${item.id}, { is_verified: ${item.is_verified ? "false" : "true"} })">${item.is_verified ? "Unverify" : "Verify"}</button>
          <button class="btn ${item.is_active ? "danger" : "primary"}" onclick="updateTenantRegistration(${item.id}, { is_active: ${item.is_active ? "false" : "true"} })">${item.is_active ? "Deactivate" : "Activate"}</button>
          <button class="btn danger" onclick="deleteTenantRegistration(${item.id})">Delete</button>
        </div>
      </article>
    `).join("");
}

async function loadTenantRegistrations() {
    const list = document.getElementById("tenantRegistrationsList");
    if (list) list.innerHTML = `<div class="ticket-empty">Loading tenant registrations...</div>`;
    const url = new URL("/tenant/api/admin/registrations", window.location.origin);
    const query = document.getElementById("tenantRegistrationSearch")?.value || "";
    const active = document.getElementById("tenantRegistrationStatus")?.value || "all";
    if (query.trim()) url.searchParams.set("query", query.trim());
    if (active) url.searchParams.set("active", active);
    const r = await apiFetch(url.toString());
    if (!r.ok) {
        if (list) list.innerHTML = `<div class="ticket-empty"><strong>Failed to load tenant registrations</strong><div class="small muted">${escapeHtml(await extractErrorMessage(r))}</div></div>`;
        return;
    }
    const data = await r.json();
    renderTenantRegistrations(Array.isArray(data.items) ? data.items : []);
}

async function updateTenantRegistration(tenantId, payload) {
    const r = await apiFetch(`/tenant/api/admin/registrations/${tenantId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
    });
    if (!r.ok) {
        alert(`Failed to update tenant registration: ${await extractErrorMessage(r)}`);
        return;
    }
    await loadTenantRegistrations();
}

async function deleteTenantRegistration(tenantId, email = "") {
    const tenant = tenantRegistrationsCache.find((item) => Number(item.id) === Number(tenantId));
    const label = email || tenant?.email || "this tenant account";
    if (!confirm(`Delete tenant portal account for ${label}? Maintenance history will stay, but this tenant will no longer be able to log in.`)) return;
    const r = await apiFetch(`/tenant/api/admin/registrations/${tenantId}`, { method: "DELETE" });
    if (!r.ok) {
        alert(`Failed to delete tenant registration: ${await extractErrorMessage(r)}`);
        return;
    }
    await loadTenantRegistrations();
}

function clearMaintenanceTradieForm() {
    ["tradieId", "tradieCompany", "tradieContactName", "tradieTradeType", "tradieEmail", "tradiePhone", "tradieNotes"].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.value = "";
    });
    const active = document.getElementById("tradieActive");
    if (active) active.value = "true";
}

function fillMaintenanceTradieForm(tradieId) {
    const item = maintenanceTradiesCache.find((tradie) => Number(tradie.id) === Number(tradieId));
    if (!item) return;
    const set = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.value = value || "";
    };
    set("tradieId", item.id);
    set("tradieCompany", item.company);
    set("tradieContactName", item.contact_name);
    set("tradieTradeType", item.trade_type);
    set("tradieEmail", item.email);
    set("tradiePhone", item.phone);
    set("tradieNotes", item.notes);
    const active = document.getElementById("tradieActive");
    if (active) active.value = item.is_active === false ? "false" : "true";
    document.getElementById("tradieCompany")?.focus();
}

function maintenanceTradiePayload() {
    return {
        company: String(document.getElementById("tradieCompany")?.value || "").trim(),
        contact_name: String(document.getElementById("tradieContactName")?.value || "").trim(),
        trade_type: String(document.getElementById("tradieTradeType")?.value || "").trim(),
        email: String(document.getElementById("tradieEmail")?.value || "").trim(),
        phone: String(document.getElementById("tradiePhone")?.value || "").trim(),
        notes: String(document.getElementById("tradieNotes")?.value || "").trim(),
        is_active: (document.getElementById("tradieActive")?.value || "true") === "true",
    };
}

async function saveMaintenanceTradie() {
    const payload = maintenanceTradiePayload();
    if (!payload.company) {
        alert("Enter a tradie company or name first.");
        return;
    }
    const id = document.getElementById("tradieId")?.value || "";
    const r = await apiFetch(id ? `/maintenance/tradies/${id}` : "/maintenance/tradies", {
        method: id ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    if (!r.ok) {
        alert(`Failed to save tradie: ${await extractErrorMessage(r)}`);
        return;
    }
    clearMaintenanceTradieForm();
    await loadMaintenanceTradies();
}

function renderMaintenanceTradies(items = []) {
    maintenanceTradiesCache = Array.isArray(items) ? items : [];
    renderMaintenanceTradieOptions();
    const list = document.getElementById("maintenanceTradiesList");
    if (!list) return;
    if (!maintenanceTradiesCache.length) {
        list.innerHTML = `<div class="ticket-empty"><strong>No tradies found</strong><div class="small muted" style="margin-top:6px">Register your preferred trades and contractors above.</div></div>`;
        return;
    }
    list.innerHTML = maintenanceTradiesCache.map((item) => `
      <article class="tradie-card">
        <div>
          <div class="row" style="gap:8px;flex-wrap:wrap">
            <span class="maintenance-status ${item.is_active ? "done" : "stop"}">${item.is_active ? "Active" : "Inactive"}</span>
            ${item.trade_type ? `<span class="maintenance-status action">${escapeHtml(item.trade_type)}</span>` : ""}
          </div>
          <h4 style="margin-top:10px">${escapeHtml(item.company || "Tradie")}</h4>
          <p>${escapeHtml(item.contact_name || "")}${item.email ? ` - ${escapeHtml(item.email)}` : ""}${item.phone ? ` - ${escapeHtml(item.phone)}` : ""}</p>
          ${item.notes ? `<p class="small muted">${escapeHtml(item.notes)}</p>` : ""}
        </div>
        <div class="row" style="justify-content:flex-end">
          <button class="btn" onclick="fillMaintenanceTradieForm(${item.id})">Edit</button>
          <button class="btn ${item.is_active ? "danger" : "primary"}" onclick="toggleMaintenanceTradie(${item.id}, ${item.is_active ? "false" : "true"})">${item.is_active ? "Deactivate" : "Activate"}</button>
        </div>
      </article>
    `).join("");
}

async function loadMaintenanceTradies() {
    const url = new URL("/maintenance/tradies", window.location.origin);
    const query = document.getElementById("tradieSearch")?.value || "";
    const active = document.getElementById("tradieStatus")?.value || "active";
    if (query.trim()) url.searchParams.set("query", query.trim());
    if (active) url.searchParams.set("active", active);
    const list = document.getElementById("maintenanceTradiesList");
    if (list) list.innerHTML = `<div class="ticket-empty">Loading tradies...</div>`;
    const r = await apiFetch(url.toString());
    if (!r.ok) {
        if (list) list.innerHTML = `<div class="ticket-empty"><strong>Failed to load tradies</strong><div class="small muted">${escapeHtml(await extractErrorMessage(r))}</div></div>`;
        return;
    }
    const data = await r.json();
    renderMaintenanceTradies(Array.isArray(data.items) ? data.items : []);
}

async function toggleMaintenanceTradie(tradieId, active) {
    const r = await apiFetch(`/maintenance/tradies/${tradieId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_active: !!active }),
    });
    if (!r.ok) {
        alert(`Failed to update tradie: ${await extractErrorMessage(r)}`);
        return;
    }
    await loadMaintenanceTradies();
}

async function loadMaintenanceDashboard(page = null) {
    if (page !== null) currentMaintenancePage = page;
    const p = currentMaintenancePage || 1;
    const status = document.getElementById("maintenanceStatusFilter")?.value || "OPEN";
    const query = document.getElementById("maintenanceSearchBox")?.value || "";
    const url = new URL("/maintenance/orders", window.location.origin);
    url.searchParams.set("page", String(p));
    url.searchParams.set("page_size", "25");
    if (status) url.searchParams.set("status", status);
    if (query.trim()) url.searchParams.set("query", query.trim());

    const [summaryResp, itemsResp] = await Promise.all([
        apiFetch("/maintenance/summary"),
        apiFetch(url.toString()),
    ]);
    if (summaryResp.ok) renderMaintenanceSummary(await summaryResp.json());
    if (!itemsResp.ok) {
        const list = document.getElementById("maintenanceList");
        if (list) list.innerHTML = `<div class="ticket-empty"><strong>Failed to load maintenance orders</strong><div class="small muted">${escapeHtml(await extractErrorMessage(itemsResp))}</div></div>`;
        return;
    }
    const data = await itemsResp.json();
    const items = Array.isArray(data.items) ? data.items : [];
    maintenanceLoadedOnce = true;
    renderMaintenanceList(items);
    const pi = document.getElementById("maintenancePageInfo");
    if (pi) {
        const total = Number(data.total || 0);
        const pageNow = Number(data.page || 1);
        const sizeNow = Number(data.page_size || 25);
        const pages = sizeNow > 0 ? Math.max(1, Math.ceil(total / sizeNow)) : 1;
        pi.textContent = `Page ${pageNow} of ${pages} - ${total} maintenance orders`;
    }
    const btnPrev = document.getElementById("maintenanceBtnPrev");
    const btnNext = document.getElementById("maintenanceBtnNext");
    if (btnPrev) btnPrev.disabled = Number(data.page || 1) <= 1;
    if (btnNext) btnNext.disabled = !Boolean(data.has_more);
}

function prevMaintenancePage() {
    if (currentMaintenancePage <= 1) return;
    currentMaintenancePage -= 1;
    loadMaintenanceDashboard();
}

function nextMaintenancePage() {
    currentMaintenancePage += 1;
    loadMaintenanceDashboard();
}

async function openMaintenanceOrder(orderId) {
    selectedMaintenanceOrderId = orderId;
    const modal = document.getElementById("maintenanceOrderModal");
    const title = document.getElementById("maintenanceDetailTitle");
    const sub = document.getElementById("maintenanceDetailSub");
    const status = document.getElementById("maintenanceDetailStatus");
    const body = document.getElementById("maintenanceDetailBody");
    if (title) title.textContent = "Loading maintenance request...";
    if (sub) sub.textContent = "Please wait while the latest details are loaded.";
    if (status) {
        status.className = "maintenance-status";
        status.textContent = "Loading";
    }
    if (body) body.innerHTML = `<div class="maintenance-detail-panel"><div class="small muted">Loading workflow and activity...</div></div>`;
    if (modal) {
        modal.classList.remove("hidden");
        const modalBody = modal.querySelector(".modal-body");
        if (modalBody) modalBody.scrollTop = 0;
    }
    renderMaintenanceList(Object.values(maintenanceOrdersCache));
    const r = await apiFetch(`/maintenance/orders/${orderId}`);
    if (!r.ok) {
        closeMaintenanceOrderModal();
        alert(`Failed to open maintenance order: ${await extractErrorMessage(r)}`);
        return;
    }
    const order = await r.json();
    if (!maintenanceOrderModalIsOpen() || Number(selectedMaintenanceOrderId) !== Number(orderId)) return;
    moveMaintenanceFormTo("maintenanceModalFormHost");
    fillMaintenanceForm(order);
    renderMaintenanceDetail(order);
    renderMaintenanceList(Object.values(maintenanceOrdersCache));
}

function renderMaintenanceDetail(order) {
    const title = document.getElementById("maintenanceDetailTitle");
    const sub = document.getElementById("maintenanceDetailSub");
    const status = document.getElementById("maintenanceDetailStatus");
    const body = document.getElementById("maintenanceDetailBody");
    const reference = order.reference || `#${order.id}`;
    const tenantVerificationWarning = order.source === "tenant_portal" && order.tenant_is_verified === false;
    if (title) title.textContent = `${reference} ${order.title || "Maintenance order"}`;
    if (sub) sub.textContent = order.property_label || order.property_address || "";
    if (status) {
        status.className = `maintenance-status ${maintenanceStatusClass(order.status)}`;
        status.textContent = maintenanceStatusLabel(order.status);
    }
    if (!body) return;
    const attachments = Array.isArray(order.attachments) ? order.attachments : [];
    const events = Array.isArray(order.events) ? order.events : [];
    const infoRequest = order.info_request || null;
    body.innerHTML = `
      <div class="maintenance-detail-panel">
        <h4>Order Snapshot</h4>
        ${tenantVerificationWarning ? `
          <div class="maintenance-warning">
            <strong>Tenant account pending verification</strong>
            This request came through the tenant portal, but the account has not been verified against the property register yet. Continue the job if needed, but double-check tenant/property details before approving access or arranging attendance.
          </div>
        ` : ""}
        ${infoRequest ? `
          <div class="maintenance-warning ${infoRequest.required ? "" : "resolved"}">
            <strong>${infoRequest.required ? "Tenant information required" : "Tenant update received"}</strong>
            ${escapeHtml(infoRequest.required ? (infoRequest.message || "Waiting for tenant update.") : `Tenant responded ${formatDateShort(infoRequest.responded_at)}.`)}
          </div>
        ` : ""}
        <div class="maintenance-meta-grid">
          <div><span>Owner</span>${escapeHtml(order.owner_name || "-")}<br>${escapeHtml(order.owner_email || "")}</div>
          <div><span>Tenant</span>${escapeHtml(order.tenant_name || "-")}<br>${escapeHtml(order.tenant_email || "")}</div>
          <div><span>Source</span>${order.source === "tenant_portal" ? "Tenant Portal" : "Staff Portal"}<br>${escapeHtml(order.tenant_submitted_at ? `Submitted ${formatDateShort(order.tenant_submitted_at)}` : "")}</div>
          <div><span>Tenant Match</span>${order.tenant_account_id ? (order.tenant_is_verified ? "Verified tenant account" : "Pending staff verification") : "No tenant portal account"}<br>${escapeHtml(order.tenant_is_active === false ? "Tenant account inactive" : "")}</div>
          <div><span>Preferred Contact</span>${escapeHtml(order.tenant_preferred_contact || "-")}<br>${escapeHtml(order.tenant_phone || "")}</div>
          <div><span>Tradie</span>${escapeHtml(order.tradie_company || order.tradie_name || "-")}<br>${escapeHtml(order.tradie_phone || order.tradie_email || "")}</div>
          <div><span>Quote</span>${escapeHtml(maintenanceMoney(order.quoted_amount))}<br>${escapeHtml(order.quote_received_at ? `Received ${formatDateShort(order.quote_received_at)}` : "No quote uploaded")}</div>
          <div><span>Due / Follow-up</span>${escapeHtml(formatDateShort(order.due_by))}</div>
          <div><span>Scheduled</span>${escapeHtml(formatDate(order.tradie_scheduled_for))}</div>
        </div>
        <p class="small muted" style="margin-top:12px">${escapeHtml(order.description || "")}</p>
      </div>

      <div class="maintenance-detail-panel">
        <h4>Workflow Actions</h4>
        <div class="maintenance-action-strip">
          <button class="btn" onclick="openMaintenanceEmailDraft(${order.id}, 'owner')">Draft Owner Approval Email</button>
          <button class="btn" onclick="setMaintenanceStatus(${order.id}, 'OWNER_APPROVED')">Mark Owner Approved</button>
          <button class="btn" onclick="setMaintenanceStatus(${order.id}, 'OWNER_DECLINED')">Mark Owner Declined</button>
          <button class="btn" onclick="setMaintenanceStatus(${order.id}, 'OWNER_ARRANGING')">Owner Arranging Themselves</button>
          <button class="btn" onclick="setMaintenanceStatus(${order.id}, 'QUOTE_REQUESTED')">Looking for Quote</button>
          ${order.tenant_account_id ? `<button class="btn" onclick="requestMaintenanceInfo(${order.id})">Request Tenant Info</button>` : ""}
          <button class="btn" onclick="openMaintenanceEmailDraft(${order.id}, 'tradie')">Draft Tradie Work Order</button>
          <button class="btn" onclick="setMaintenanceStatus(${order.id}, 'TRADIE_ARRANGED')">Tradie Arranged</button>
          <button class="btn" onclick="openMaintenanceEmailDraft(${order.id}, 'tenant')">Draft Tenant Arrangement</button>
          <button class="btn primary" onclick="setMaintenanceStatus(${order.id}, 'COMPLETED')">Complete Job</button>
          <button class="btn danger" onclick="setMaintenanceStatus(${order.id}, 'CANCELLED')">Cancel</button>
          <button class="btn danger" onclick="deleteMaintenanceOrder(${order.id})">Delete Order</button>
        </div>
      </div>

      <div class="maintenance-detail-panel">
        <h4>Upload Quote / Media</h4>
        <div class="maintenance-action-strip">
          <input class="wide" type="file" id="maintenanceQuoteFile" />
          <input type="number" min="0" step="0.01" id="maintenanceUploadAmount" placeholder="Quoted amount" value="${order.quoted_amount || ""}" />
          <input type="text" id="maintenanceUploadNotes" placeholder="Quote notes" value="${escapeHtml(order.quote_notes || "")}" />
          <button class="btn primary" onclick="uploadMaintenanceQuote(${order.id})">Upload Quote</button>
        </div>
        <div class="maintenance-action-strip" style="margin-top:14px">
          <input class="wide" type="file" id="maintenanceMediaFiles" accept="image/*,video/*" multiple />
          <input class="wide" type="text" id="maintenanceMediaNotes" placeholder="Photo/video notes, e.g. tenant sent by SMS, before/after repair..." />
          <button class="btn primary" onclick="uploadMaintenanceMedia(${order.id})">Upload Photos / Videos</button>
          <div class="small muted" style="align-self:center">Images and videos only, up to 25MB each.</div>
        </div>
        <div style="margin-top:10px">
          ${attachments.length ? attachments.map((a) => `
            <div class="maintenance-attachment">
              <div><strong>${escapeHtml(a.filename || "Attachment")}</strong><div class="small muted">${escapeHtml(a.kind || "GENERAL")} - ${escapeHtml(a.source === "tenant" ? "Tenant" : "Staff")} - ${escapeHtml(formatDateShort(a.created_at))}</div></div>
              <div class="row">
                <button class="btn" onclick="openMaintenanceAttachment(${a.id})">Open</button>
                <button class="btn danger" onclick="deleteMaintenanceAttachment(${a.id})">Delete</button>
              </div>
            </div>
          `).join("") : `<div class="small muted">No attachments uploaded yet.</div>`}
        </div>
      </div>

      <div class="maintenance-detail-panel">
        <h4>Activity + Notes</h4>
        <div class="field">
          <div class="label">Add Internal Note</div>
          <textarea id="maintenanceNewNote" placeholder="Add an internal update, owner response, quote chase-up, or completion note..."></textarea>
        </div>
        <button class="btn" onclick="addMaintenanceNote(${order.id})">Add Note</button>
        <div class="maintenance-timeline" style="margin-top:12px">
          ${events.length ? events.map((e) => `
            <div class="maintenance-event">
              <strong>${escapeHtml((e.event_type || "activity").replaceAll("_", " "))} - ${escapeHtml(formatDate(e.created_at))}</strong>
              <span>${escapeHtml(e.detail || "")}</span>
              <div class="small muted">${escapeHtml(e.actor_name || "System")}</div>
            </div>
          `).join("") : `<div class="small muted">No activity yet.</div>`}
        </div>
      </div>
    `;
}

async function setMaintenanceStatus(orderId, status) {
    const note = ["OWNER_APPROVED", "OWNER_DECLINED", "OWNER_ARRANGING", "COMPLETED", "CANCELLED"].includes(status)
        ? prompt("Optional note for this status change:") || ""
        : "";
    const r = await apiFetch(`/maintenance/orders/${orderId}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, note }),
    });
    if (!r.ok) {
        alert(`Failed to update maintenance status: ${await extractErrorMessage(r)}`);
        return;
    }
    const order = await r.json();
    fillMaintenanceForm(order);
    renderMaintenanceDetail(order);
    await loadMaintenanceDashboard(currentMaintenancePage || 1);
    await loadNotifications();
}

async function requestMaintenanceInfo(orderId) {
    const message = prompt("What information do you need from the tenant? This will appear in their portal and an email will ask them to check the portal.");
    const text = String(message || "").trim();
    if (!text) return;
    const r = await apiFetch(`/maintenance/orders/${orderId}/request-info`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
    });
    if (!r.ok) {
        alert(`Failed to request tenant information: ${await extractErrorMessage(r)}`);
        return;
    }
    const order = await r.json();
    fillMaintenanceForm(order);
    renderMaintenanceDetail(order);
    await loadMaintenanceDashboard(currentMaintenancePage || 1);
    await loadNotifications();
    if (order.info_email_sent === false) {
        alert("Information request was added to the tenant portal, but the email could not be sent automatically.");
    }
}

async function deleteMaintenanceOrder(orderId) {
    if (!confirm("Delete this maintenance order permanently? Attachments and activity history for this order will also be removed.")) return;
    const r = await apiFetch(`/maintenance/orders/${orderId}`, { method: "DELETE" });
    if (!r.ok) {
        alert(`Failed to delete maintenance order: ${await extractErrorMessage(r)}`);
        return;
    }
    closeMaintenanceOrderModal();
    resetMaintenanceForm();
    currentMaintenancePage = 1;
    await loadMaintenanceDashboard(1);
    await loadNotifications();
}

function closeMaintenanceEmailDraftModal() {
    const modal = document.getElementById("maintenanceEmailDraftModal");
    if (modal) modal.classList.add("hidden");
}

function setMaintenanceDraftError(message = "") {
    const error = document.getElementById("maintenanceEmailDraftError");
    if (!error) return;
    error.style.display = message ? "block" : "none";
    error.textContent = message;
}

function maintenanceDraftText() {
    const to = String(document.getElementById("maintenanceEmailDraftTo")?.value || "").trim();
    const subject = String(document.getElementById("maintenanceEmailDraftSubject")?.value || "").trim();
    const body = String(document.getElementById("maintenanceEmailDraftBody")?.value || "").trim();
    return `To: ${to}\nSubject: ${subject}\n\n${body}`;
}

async function copyTextToClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return true;
    }
    const temp = document.createElement("textarea");
    temp.value = text;
    temp.setAttribute("readonly", "");
    temp.style.position = "fixed";
    temp.style.left = "-9999px";
    document.body.appendChild(temp);
    temp.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(temp);
    return ok;
}

async function copyMaintenanceEmailDraft() {
    try {
        await copyTextToClipboard(maintenanceDraftText());
        setMaintenanceDraftError("");
        alert("Full email draft copied.");
    } catch {
        setMaintenanceDraftError("Could not copy automatically. Please select the draft text and copy it manually.");
    }
}

async function copyMaintenanceEmailBody() {
    const body = String(document.getElementById("maintenanceEmailDraftBody")?.value || "").trim();
    try {
        await copyTextToClipboard(body);
        setMaintenanceDraftError("");
        alert("Email body copied.");
    } catch {
        setMaintenanceDraftError("Could not copy automatically. Please select the email body and copy it manually.");
    }
}

async function openMaintenanceEmailDraft(orderId, kind) {
    const r = await apiFetch(`/maintenance/orders/${orderId}/email-draft/${encodeURIComponent(kind)}`);
    if (!r.ok) {
        alert(`Failed to generate email draft: ${await extractErrorMessage(r)}`);
        return;
    }
    const draft = await r.json();
    const modal = document.getElementById("maintenanceEmailDraftModal");
    const title = document.getElementById("maintenanceEmailDraftTitle");
    const subtitle = document.getElementById("maintenanceEmailDraftSubtitle");
    const orderInput = document.getElementById("maintenanceEmailDraftOrderId");
    const statusInput = document.getElementById("maintenanceEmailDraftNextStatus");
    const toInput = document.getElementById("maintenanceEmailDraftTo");
    const subjectInput = document.getElementById("maintenanceEmailDraftSubject");
    const bodyInput = document.getElementById("maintenanceEmailDraftBody");
    const markBtn = document.getElementById("maintenanceEmailDraftMarkBtn");
    if (title) title.textContent = `${draft.label || "Maintenance"} Draft`;
    if (subtitle) subtitle.textContent = "No email will be sent from the portal. Edit, copy, and send manually.";
    if (orderInput) orderInput.value = String(orderId || "");
    if (statusInput) statusInput.value = String(draft.next_status || "");
    if (toInput) toInput.value = draft.to_email || "";
    if (subjectInput) subjectInput.value = draft.subject || "";
    if (bodyInput) bodyInput.value = draft.body_text || "";
    if (markBtn) markBtn.textContent = draft.next_status_label ? `Mark ${draft.next_status_label}` : "Mark Step Complete";
    setMaintenanceDraftError("");
    if (modal) modal.classList.remove("hidden");
}

async function markMaintenanceDraftStatus() {
    const orderId = document.getElementById("maintenanceEmailDraftOrderId")?.value || "";
    const nextStatus = document.getElementById("maintenanceEmailDraftNextStatus")?.value || "";
    if (!orderId || !nextStatus) {
        setMaintenanceDraftError("No workflow status is attached to this draft.");
        return;
    }
    if (!confirm("Only continue after staff have manually sent this email. Update the maintenance workflow status now?")) return;
    closeMaintenanceEmailDraftModal();
    await setMaintenanceStatus(orderId, nextStatus);
}

async function sendMaintenanceOwnerEmail(orderId) {
    return openMaintenanceEmailDraft(orderId, "owner");
}

async function sendMaintenanceTenantEmail(orderId) {
    return openMaintenanceEmailDraft(orderId, "tenant");
}

async function sendMaintenanceTradieEmail(orderId) {
    return openMaintenanceEmailDraft(orderId, "tradie");
}

async function uploadMaintenanceQuote(orderId) {
    const fileInput = document.getElementById("maintenanceQuoteFile");
    const file = fileInput && fileInput.files ? fileInput.files[0] : null;
    if (!file) {
        alert("Choose a quote or attachment file first.");
        return;
    }
    const form = new FormData();
    form.append("kind", "QUOTE");
    form.append("file", file, file.name);
    const amount = document.getElementById("maintenanceUploadAmount")?.value || "";
    const notes = document.getElementById("maintenanceUploadNotes")?.value || "";
    if (amount) form.append("quoted_amount", amount);
    if (notes) {
        form.append("quote_notes", notes);
        form.append("notes", notes);
    }
    const r = await apiFetch(`/maintenance/orders/${orderId}/attachments`, { method: "POST", body: form });
    if (!r.ok) {
        alert(`Quote upload failed: ${await extractErrorMessage(r)}`);
        return;
    }
    const order = await r.json();
    fillMaintenanceForm(order);
    renderMaintenanceDetail(order);
    await loadMaintenanceDashboard(currentMaintenancePage || 1);
    await loadNotifications();
}

async function uploadMaintenanceMedia(orderId) {
    const fileInput = document.getElementById("maintenanceMediaFiles");
    const files = Array.from((fileInput && fileInput.files) || []);
    if (!files.length) {
        alert("Choose one or more photos/videos first.");
        return;
    }
    const maxFileBytes = 25 * 1024 * 1024;
    const invalid = files.find((file) => {
        const type = String(file.type || "");
        return file.size > maxFileBytes || (type && !(type.startsWith("image/") || type.startsWith("video/")));
    });
    if (invalid) {
        alert("Photos/videos must be image or video files and 25MB or smaller each.");
        return;
    }
    const notes = document.getElementById("maintenanceMediaNotes")?.value || "";
    let latestOrder = null;
    for (const file of files) {
        const form = new FormData();
        form.append("kind", "MEDIA");
        form.append("file", file, file.name);
        if (notes) form.append("notes", notes);
        const r = await apiFetch(`/maintenance/orders/${orderId}/attachments`, { method: "POST", body: form });
        if (!r.ok) {
            alert(`Media upload failed for ${file.name}: ${await extractErrorMessage(r)}`);
            break;
        }
        latestOrder = await r.json();
    }
    if (fileInput) fileInput.value = "";
    const notesEl = document.getElementById("maintenanceMediaNotes");
    if (notesEl) notesEl.value = "";
    if (latestOrder) {
        fillMaintenanceForm(latestOrder);
        renderMaintenanceDetail(latestOrder);
        await loadMaintenanceDashboard(currentMaintenancePage || 1);
        await loadNotifications();
    }
}

async function openMaintenanceAttachment(attachmentId) {
    const r = await apiFetch(`/maintenance/attachments/${attachmentId}/view`);
    if (!r.ok) {
        alert(`Could not open attachment: ${await extractErrorMessage(r)}`);
        return;
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank", "noopener,noreferrer");
    setTimeout(() => URL.revokeObjectURL(url), 60000);
}

async function deleteMaintenanceAttachment(attachmentId) {
    if (!confirm("Delete this maintenance attachment?")) return;
    const r = await apiFetch(`/maintenance/attachments/${attachmentId}`, { method: "DELETE" });
    if (!r.ok) {
        alert(`Delete failed: ${await extractErrorMessage(r)}`);
        return;
    }
    const order = await r.json();
    fillMaintenanceForm(order);
    renderMaintenanceDetail(order);
    await loadMaintenanceDashboard(currentMaintenancePage || 1);
}

async function addMaintenanceNote(orderId) {
    const noteEl = document.getElementById("maintenanceNewNote");
    const note = String(noteEl?.value || "").trim();
    if (!note) {
        alert("Write a note first.");
        return;
    }
    const r = await apiFetch(`/maintenance/orders/${orderId}/notes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note }),
    });
    if (!r.ok) {
        alert(`Failed to add note: ${await extractErrorMessage(r)}`);
        return;
    }
    if (noteEl) noteEl.value = "";
    const order = await r.json();
    renderMaintenanceDetail(order);
    await loadMaintenanceDashboard(currentMaintenancePage || 1);
    await loadNotifications();
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

function clearRentFilters() {
    const status = document.getElementById("rentStatusFilter");
    const frequency = document.getElementById("rentFrequencyFilter");
    const query = document.getElementById("rentSearchBox");
    if (status) status.value = "";
    if (frequency) frequency.value = "";
    if (query) query.value = "";
    currentRentPage = 1;
    loadActiveRentView(1);
}

function toggleRentDueDay() {
    const frequency = document.getElementById("rentNewFrequency")?.value || "MONTHLY";
    const field = document.getElementById("rentNewDueDayField");
    if (field) field.classList.toggle("hidden", frequency !== "MONTHLY");
}

async function addRentTrackedProperty() {
    const propertySearchEl = document.getElementById("rentNewPropertySearch");
    const propertyIdEl = document.getElementById("rentNewPropertyId");
    const frequencyEl = document.getElementById("rentNewFrequency");
    const startEl = document.getElementById("rentNewTrackingStart");
    const dueDayEl = document.getElementById("rentNewDueDay");
    const meta = document.getElementById("rentAddPropertyMeta");
    const button = document.getElementById("rentAddPropertyBtn");
    const selectedProperty = resolvePropertySearchValue(propertySearchEl?.value || "");
    const propertyId = Number(selectedProperty?.id || propertyIdEl?.value || 0);
    const trackingStart = String(startEl?.value || "").trim();
    if (!propertyId) {
        if (meta) meta.textContent = "Select a property from the property database list.";
        propertySearchEl?.focus();
        return;
    }
    if (!trackingStart) {
        if (meta) meta.textContent = "Choose the date tracking should begin.";
        startEl?.focus();
        return;
    }

    const payload = {
        property_id: propertyId,
        frequency: frequencyEl?.value || "MONTHLY",
        tracking_start: trackingStart,
    };
    const dueDay = Number(dueDayEl?.value || 0);
    if (payload.frequency === "MONTHLY" && dueDay > 0) payload.due_day = dueDay;

    if (button) button.disabled = true;
    if (meta) meta.textContent = "Adding property and creating tracking periods...";
    const response = await apiFetch("/rent-tracker/properties", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    if (button) button.disabled = false;
    if (!response.ok) {
        if (meta) meta.textContent = await extractErrorMessage(response);
        return;
    }
    const result = await response.json();
    if (meta) meta.textContent = `${result.property_address} added with ${result.created_periods} editable tracking periods.`;
    if (propertySearchEl) propertySearchEl.value = "";
    if (propertyIdEl) propertyIdEl.value = "";
    if (dueDayEl) dueDayEl.value = "";
    currentRentPage = 1;
    const startDate = new Date(`${trackingStart}T00:00:00`);
    const now = new Date();
    if (startDate < new Date(now.getFullYear(), now.getMonth(), 1)) switchRentViewMode("year");
    else await loadActiveRentView(1);
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
    const start = document.getElementById("rentNewTrackingStart");
    if (start && !start.value) {
        const today = new Date();
        start.value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
        start.min = `${today.getFullYear()}-01-01`;
        start.max = `${today.getFullYear()}-12-31`;
    }
    if (rentViewMode === "year") return loadRentYearReport(page);
    return loadRentTracker(page);
}

function monthCellSelect(item) {
    if (!item || !item.id) return `<span class="small muted">—</span>`;
    const status = String(item.status || "DUE").toUpperCase();
    const extra = Number(item.extra_items || 0);
    const due = item.due_date ? formatDateShort(item.due_date) : "";
    const partialAmount = (typeof item.partial_amount === "number" && item.partial_amount > 0) ? Number(item.partial_amount).toFixed(2) : "";
    return `
      <div class="rent-cell">
        <select aria-label="Rent status" onchange="updateRentMonthCell(${item.id}, this.value, ${item.partial_amount || 0})">
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
        <div class="rent-cell-raw">${escapeHtml(item.raw_value || due || "No entry")}</div>
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

    setText("rentKpiTotal", summary.properties || 0);
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
                    <div class="small muted">${Number((r.counts || {}).PAID || 0)} of ${Number(r.total_items || 0)} paid</div>
                    <div class="rent-progress"><span style="width:${Math.round((Number((r.counts || {}).PAID || 0) / Math.max(1, Number(r.total_items || 0))) * 100)}%"></span></div>
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
    loadNotifications();
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
    setText("rentKpiTotal", summary.properties || 0);
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
                    <div class="small muted">${Number((r.counts || {}).PAID || 0)} of ${Number(r.total_items || 0)} paid</div>
                    <div class="rent-progress"><span style="width:${Math.round((Number((r.counts || {}).PAID || 0) / Math.max(1, Number(r.total_items || 0))) * 100)}%"></span></div>
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
    loadNotifications();
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
        const counts = (j && j.status_counts) || {};
        meta.textContent = `Imported ${rows} monthly entries for ${y} | ${counts.PAID || 0} paid | ${(counts.DUE || 0) + (counts.PARTIAL || 0) + (counts.AWAITING_CLEARANCE || 0)} pending | ${f.name}`;
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
    if (key === "GAS" || key === "ELECTRICAL" || key === "MRS") return "2 years";
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
    if (key === "GAS" || key === "ELECTRICAL" || key === "MRS") return addYearsToDateInput(doneDate, 2);
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

function propertyText(value, fallback = "-") {
    const text = String(value ?? "").trim();
    return text || fallback;
}

function propertyFullAddress(row) {
    const tail = [row.suburb, row.state_code, row.postcode].map((x) => String(x || "").trim()).filter(Boolean).join(" ");
    return [row.property_address, tail].map((x) => String(x || "").trim()).filter(Boolean).join(", ");
}

function propertyContactList(book) {
    const contacts = book && Array.isArray(book.contacts) ? book.contacts : [];
    return contacts.filter((contact) => contact && (contact.name || contact.email || contact.mobile || contact.phone || (Array.isArray(contact.phones) && contact.phones.length)));
}

function propertyContactPhones(contact) {
    const phones = Array.isArray(contact?.phones) ? contact.phones : [];
    return [...phones, contact?.mobile, contact?.phone]
        .map((x) => String(x || "").trim())
        .filter(Boolean)
        .filter((value, index, arr) => arr.findIndex((other) => other.replace(/\D+/g, "") === value.replace(/\D+/g, "")) === index);
}

function propertyPrimaryContact(book) {
    const contacts = propertyContactList(book);
    if (!contacts.length) return { name: "", email: "", phone: "" };
    const first = contacts[0];
    const phones = propertyContactPhones(first);
    return {
        name: String(first.name || ""),
        email: String(first.email || ""),
        phone: String(first.mobile || first.phone || phones[0] || ""),
    };
}

function renderPropertyContactBook(book, emptyText) {
    const contacts = propertyContactList(book);
    const extraMobiles = book && Array.isArray(book.extra_mobiles) ? book.extra_mobiles : [];
    const extraPhones = book && Array.isArray(book.extra_phones) ? book.extra_phones : [];
    const contactHtml = contacts.length
        ? contacts.map((contact) => {
            const phones = propertyContactPhones(contact);
            const phoneHtml = phones.length
                ? phones.map((phone) => `<a href="tel:${escapeHtml(String(phone).replace(/\s+/g, ""))}">${escapeHtml(String(phone))}</a>`).join("")
                : `<span>No phone recorded</span>`;
            const email = String(contact.email || "").trim();
            return `
              <div class="property-contact">
                <strong>${escapeHtml(String(contact.name || "Contact"))}${contact.is_company ? " (Company)" : ""}</strong>
                ${email ? `<a href="mailto:${escapeHtml(email)}">${escapeHtml(email)}</a>` : `<span>No email recorded</span>`}
                ${phoneHtml}
              </div>
            `;
        }).join("")
        : `<div class="property-extra-note">${escapeHtml(emptyText)}</div>`;
    const extras = [...extraMobiles, ...extraPhones]
        .map((x) => String(x || "").trim())
        .filter(Boolean)
        .filter((value, index, arr) => arr.findIndex((other) => other.replace(/\D+/g, "") === value.replace(/\D+/g, "")) === index);
    const extraHtml = extras.length
        ? `<div class="property-extra-note"><strong>Additional numbers not assigned to a specific person:</strong><br>${extras.map((x) => escapeHtml(x)).join(", ")}</div>`
        : "";
    return contactHtml + extraHtml;
}

function propertyContactInputRow(type, contact = {}) {
    const checked = contact.is_company ? "checked" : "";
    return `
      <div class="property-contact-edit-row" data-contact-type="${escapeHtml(type)}">
        <div class="field">
          <div class="label">Name</div>
          <input data-contact-field="name" value="${escapeHtml(String(contact.name || ""))}" placeholder="${type === "owners" ? "Landlord name" : "Tenant name"}" />
        </div>
        <div class="field">
          <div class="label">Email</div>
          <input data-contact-field="email" type="email" value="${escapeHtml(String(contact.email || ""))}" placeholder="email@example.com" />
        </div>
        <div class="field">
          <div class="label">Mobile</div>
          <input data-contact-field="mobile" value="${escapeHtml(String(contact.mobile || ""))}" placeholder="Mobile number" />
        </div>
        <div class="field">
          <div class="label">Phone</div>
          <input data-contact-field="phone" value="${escapeHtml(String(contact.phone || ""))}" placeholder="Other phone" />
        </div>
        <label class="checkbox compact property-company-toggle ${type === "owners" ? "" : "hidden"}">
          <input data-contact-field="is_company" type="checkbox" ${checked} />
          Company
        </label>
        <button class="btn danger" type="button" onclick="this.closest('.property-contact-edit-row')?.remove()">Remove</button>
      </div>
    `;
}

function renderPropertyEditorContacts(type, contacts) {
    const target = document.getElementById(type === "owners" ? "propertyEditOwnersList" : "propertyEditTenantsList");
    if (!target) return;
    const rows = Array.isArray(contacts) && contacts.length ? contacts : [{}];
    target.innerHTML = rows.map((contact) => propertyContactInputRow(type, contact)).join("");
}

function addPropertyEditorContact(type) {
    const target = document.getElementById(type === "owners" ? "propertyEditOwnersList" : "propertyEditTenantsList");
    if (!target) return;
    target.insertAdjacentHTML("beforeend", propertyContactInputRow(type, {}));
}

function collectPropertyEditorContacts(type) {
    const target = document.getElementById(type === "owners" ? "propertyEditOwnersList" : "propertyEditTenantsList");
    if (!target) return [];
    return Array.from(target.querySelectorAll(".property-contact-edit-row")).map((row) => {
        const field = (name) => row.querySelector(`[data-contact-field="${name}"]`);
        const contact = {
            name: String(field("name")?.value || "").trim(),
            email: String(field("email")?.value || "").trim(),
            mobile: String(field("mobile")?.value || "").trim(),
            phone: String(field("phone")?.value || "").trim(),
            is_company: !!field("is_company")?.checked,
        };
        return contact;
    }).filter((contact) => contact.name || contact.email || contact.mobile || contact.phone);
}

function propertyContactsForPayload(book) {
    return propertyContactList(book).map((contact) => ({
        name: String(contact.name || "").trim(),
        email: String(contact.email || "").trim(),
        mobile: String(contact.mobile || "").trim(),
        phone: String(contact.phone || "").trim(),
        phones: propertyContactPhones(contact),
        is_company: !!contact.is_company,
        lease_start_date: String(contact.lease_start_date || "").trim(),
        lease_end_date: String(contact.lease_end_date || "").trim(),
        lease_amount: String(contact.lease_amount || "").trim(),
        lease_frequency: String(contact.lease_frequency || "").trim(),
    }));
}

function listingArray(value) {
    return Array.isArray(value) ? value.filter((item) => item && typeof item === "object") : [];
}

function listingCollectionTarget(scope, kind) {
    const ids = {
        "new:occupants": "newListingOccupantsList",
        "new:keys": "newListingKeysList",
        "new:social": "newListingSocialList",
        "edit:occupants": "propertyEditOccupantsList",
        "edit:keys": "propertyEditKeysList",
        "edit:social": "propertyEditSocialList",
    };
    return document.getElementById(ids[`${scope}:${kind}`] || "");
}

function listingOccupantRow(item = {}) {
    const frequency = String(item.lease_frequency || "").toUpperCase();
    return `<div class="property-collection-row occupant-row" data-listing-row="occupants">
        <div class="field"><div class="label">Contact Name</div><input data-listing-field="name" value="${escapeHtml(String(item.name || ""))}" placeholder="Occupant name" /></div>
        <div class="field"><div class="label">Email</div><input data-listing-field="email" type="email" value="${escapeHtml(String(item.email || ""))}" placeholder="occupant@example.com" /></div>
        <div class="field"><div class="label">Phone</div><input data-listing-field="mobile" value="${escapeHtml(String(item.mobile || item.phone || ""))}" placeholder="Phone number" /></div>
        <div class="field"><div class="label">Lease Start Date</div><input data-listing-field="lease_start_date" type="date" value="${escapeHtml(String(item.lease_start_date || ""))}" /></div>
        <div class="field"><div class="label">Lease End Date</div><input data-listing-field="lease_end_date" type="date" value="${escapeHtml(String(item.lease_end_date || ""))}" /></div>
        <div class="field"><div class="label">Lease Amount</div><input data-listing-field="lease_amount" value="${escapeHtml(String(item.lease_amount || ""))}" placeholder="Example: 650" /></div>
        <div class="field"><div class="label">Frequency</div><select data-listing-field="lease_frequency">
          <option value="" ${!frequency ? "selected" : ""}>Select frequency</option>
          <option value="WEEKLY" ${frequency === "WEEKLY" ? "selected" : ""}>Weekly</option>
          <option value="FORTNIGHTLY" ${frequency === "FORTNIGHTLY" ? "selected" : ""}>Fortnightly</option>
          <option value="MONTHLY" ${frequency === "MONTHLY" ? "selected" : ""}>Monthly</option>
        </select></div>
        <button class="btn danger" type="button" onclick="removeListingCollectionRow(this)">Remove</button>
      </div>`;
}

function listingKeyRow(item = {}) {
    return `<div class="property-collection-row" data-listing-row="keys">
        <div class="field"><div class="label">Key Number</div><input data-listing-field="key_number" value="${escapeHtml(String(item.key_number || ""))}" placeholder="Example: DP034" /></div>
        <div class="field"><div class="label">Description</div><input data-listing-field="description" value="${escapeHtml(String(item.description || ""))}" placeholder="Example: Front door" /></div>
        <div class="field"><div class="label">Location</div><input data-listing-field="location" value="${escapeHtml(String(item.location || ""))}" placeholder="Where the key is held" /></div>
        <button class="btn" type="button" onclick="generateListingKeyNumber(this)">Generate Number</button>
        <button class="btn danger" type="button" onclick="removeListingCollectionRow(this)">Remove</button>
      </div>`;
}

function listingSocialRow(item = {}) {
    return `<div class="property-collection-row social-row" data-listing-row="social">
        <div class="field"><div class="label">Date</div><input data-listing-field="date" type="date" value="${escapeHtml(String(item.date || ""))}" /></div>
        <div class="field"><div class="label">Platform</div><input data-listing-field="platform" value="${escapeHtml(String(item.platform || ""))}" placeholder="Instagram, Facebook..." /></div>
        <div class="field"><div class="label">Post URL</div><input data-listing-field="url" type="url" value="${escapeHtml(String(item.url || ""))}" placeholder="https://" /></div>
        <div class="field"><div class="label">Notes</div><input data-listing-field="notes" maxlength="1000" value="${escapeHtml(String(item.notes || ""))}" placeholder="Campaign or post notes" /></div>
        <button class="btn danger" type="button" onclick="removeListingCollectionRow(this)">Remove</button>
      </div>`;
}

function listingCollectionMarkup(kind, item) {
    if (kind === "occupants") return listingOccupantRow(item);
    if (kind === "keys") return listingKeyRow(item);
    return listingSocialRow(item);
}

function renderListingCollection(scope, kind, items, options = {}) {
    const target = listingCollectionTarget(scope, kind);
    if (!target) return;
    const rows = listingArray(items);
    const seedEmpty = !!options.seedEmpty;
    target.innerHTML = rows.length
        ? rows.map((item) => listingCollectionMarkup(kind, item)).join("")
        : (seedEmpty ? listingCollectionMarkup(kind, {}) : `<div class="property-collection-empty" data-listing-empty>No ${kind === "social" ? "social media history" : kind} recorded.</div>`);
}

function addListingCollectionRow(scope, kind, item = {}) {
    const target = listingCollectionTarget(scope, kind);
    if (!target) return;
    target.querySelector("[data-listing-empty]")?.remove();
    target.insertAdjacentHTML("beforeend", listingCollectionMarkup(kind, item));
}

function removeListingCollectionRow(button) {
    const row = button?.closest("[data-listing-row]");
    const target = row?.parentElement;
    const kind = row?.dataset.listingRow || "items";
    row?.remove();
    if (target && !target.querySelector("[data-listing-row]")) {
        target.innerHTML = `<div class="property-collection-empty" data-listing-empty>No ${kind === "social" ? "social media history" : kind} recorded.</div>`;
    }
}

function generateListingKeyNumber(button) {
    const row = button?.closest('[data-listing-row="keys"]');
    const input = row?.querySelector('[data-listing-field="key_number"]');
    if (!input || String(input.value || "").trim()) return;
    const stamp = Date.now().toString(36).slice(-6).toUpperCase();
    input.value = `KEY-${stamp}`;
    input.dispatchEvent(new Event("input", { bubbles: true }));
}

function collectListingCollection(scope, kind) {
    const target = listingCollectionTarget(scope, kind);
    if (!target) return [];
    const field = (row, name) => String(row.querySelector(`[data-listing-field="${name}"]`)?.value || "").trim();
    return Array.from(target.querySelectorAll(`[data-listing-row="${kind}"]`)).map((row) => {
        if (kind === "occupants") {
            const mobile = field(row, "mobile");
            return {
                name: field(row, "name"), email: field(row, "email"), mobile, phone: "",
                phones: mobile ? [mobile] : [], is_company: false,
                lease_start_date: field(row, "lease_start_date"), lease_end_date: field(row, "lease_end_date"),
                lease_amount: field(row, "lease_amount"), lease_frequency: field(row, "lease_frequency"),
            };
        }
        if (kind === "keys") {
            return { key_number: field(row, "key_number"), description: field(row, "description"), location: field(row, "location") };
        }
        return { date: field(row, "date"), platform: field(row, "platform"), url: field(row, "url"), notes: field(row, "notes") };
    }).filter((item) => Object.values(item).some((value) => Array.isArray(value) ? value.length : Boolean(value)));
}

function ensureNewListingCollections(force = false) {
    if (newListingCollectionsReady && !force) return;
    renderListingCollection("new", "occupants", [], { seedEmpty: true });
    renderListingCollection("new", "keys", [], { seedEmpty: true });
    renderListingCollection("new", "social", [], { seedEmpty: true });
    newListingCollectionsReady = true;
}

function listingInspectionIsCompleted(item) {
    const date = String(item?.date || "").trim();
    const finish = String(item?.finish_time || item?.start_time || "23:59").trim();
    if (!date) return false;
    const timestamp = new Date(`${date}T${finish || "23:59"}:00`).getTime();
    return Number.isFinite(timestamp) && timestamp < Date.now();
}

function listingInspectionRow(item = {}) {
    const id = String(item.id || `inspection-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`);
    return `<div class="property-collection-row inspection-row" data-listing-inspection-id="${escapeHtml(id)}">
        <div class="field"><div class="label">Date</div><input data-inspection-field="date" type="date" value="${escapeHtml(String(item.date || ""))}" /></div>
        <div class="field"><div class="label">Start</div><input data-inspection-field="start_time" type="time" value="${escapeHtml(String(item.start_time || ""))}" /></div>
        <div class="field"><div class="label">Finish</div><input data-inspection-field="finish_time" type="time" value="${escapeHtml(String(item.finish_time || ""))}" /></div>
        <div class="field"><div class="label">Notes</div><input data-inspection-field="notes" maxlength="1000" value="${escapeHtml(String(item.notes || ""))}" placeholder="Inspection notes" /></div>
        <button class="btn danger" type="button" onclick="removePropertyListingInspection(this)">Remove</button>
      </div>`;
}

function renderPropertyListingInspections(items) {
    const current = document.getElementById("propertyCurrentInspections");
    const completed = document.getElementById("propertyCompletedInspections");
    if (!current || !completed) return;
    const rows = listingArray(items);
    const upcoming = rows.filter((item) => !listingInspectionIsCompleted(item));
    const archived = rows.filter(listingInspectionIsCompleted);
    current.innerHTML = upcoming.length ? upcoming.map(listingInspectionRow).join("") : `<div class="property-collection-empty" data-inspection-empty>There are no current inspection times.</div>`;
    completed.innerHTML = archived.length ? archived.map(listingInspectionRow).join("") : `<div class="property-collection-empty" data-inspection-empty>There are no completed inspection times.</div>`;
}

function collectPropertyListingInspections(showErrors = true) {
    const rows = Array.from(document.querySelectorAll("#propertyCurrentInspections [data-listing-inspection-id], #propertyCompletedInspections [data-listing-inspection-id]"));
    const items = rows.map((row) => {
        const value = (name) => String(row.querySelector(`[data-inspection-field="${name}"]`)?.value || "").trim();
        return { id: row.dataset.listingInspectionId, date: value("date"), start_time: value("start_time"), finish_time: value("finish_time"), notes: value("notes") };
    });
    const invalid = items.find((item) => !item.date || !item.start_time || !item.finish_time || item.finish_time <= item.start_time);
    if (invalid && showErrors) alert("Each inspection needs a date and a finish time later than its start time.");
    return invalid ? null : items;
}

function addPropertyListingInspection() {
    const item = {
        id: `inspection-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
        date: String(document.getElementById("propertyInspectionDate")?.value || "").trim(),
        start_time: String(document.getElementById("propertyInspectionStart")?.value || "").trim(),
        finish_time: String(document.getElementById("propertyInspectionFinish")?.value || "").trim(),
        notes: String(document.getElementById("propertyInspectionNotes")?.value || "").trim(),
    };
    if (!item.date || !item.start_time || !item.finish_time || item.finish_time <= item.start_time) {
        alert("Choose an inspection date and a finish time later than the start time.");
        return;
    }
    const existing = collectPropertyListingInspections(false) || [];
    renderPropertyListingInspections([...existing, item]);
    ["propertyInspectionDate", "propertyInspectionStart", "propertyInspectionFinish", "propertyInspectionNotes"].forEach((id) => {
        const control = document.getElementById(id);
        if (control) control.value = "";
    });
}

function removePropertyListingInspection(button) {
    button?.closest("[data-listing-inspection-id]")?.remove();
    const remaining = collectPropertyListingInspections(false) || [];
    renderPropertyListingInspections(remaining);
}

function switchPropertyListingTab(tab) {
    const requested = String(tab || "overview");
    const isOpen = String(document.getElementById("propertyEditListingStatus")?.value || "OPEN").toUpperCase() === "OPEN";
    propertyListingActiveTab = (!isOpen && ["enquiries", "offers"].includes(requested)) ? "overview" : requested;
    document.querySelectorAll("#propertyEditorModal [data-property-listing-tab]").forEach((button) => {
        button.classList.toggle("active", button.dataset.propertyListingTab === propertyListingActiveTab);
    });
    document.querySelectorAll("#propertyEditorModal [data-property-listing-panel]").forEach((panel) => {
        const unavailable = !isOpen && ["enquiries", "offers"].includes(panel.dataset.propertyListingPanel);
        panel.classList.toggle("hidden", unavailable || panel.dataset.propertyListingPanel !== propertyListingActiveTab);
    });
}

function updatePropertyListingOpenTabs() {
    const isOpen = String(document.getElementById("propertyEditListingStatus")?.value || "OPEN").toUpperCase() === "OPEN";
    document.querySelectorAll("#propertyEditorModal button.listing-open-only").forEach((button) => button.classList.toggle("hidden", !isOpen));
    if (!isOpen && ["enquiries", "offers"].includes(propertyListingActiveTab)) propertyListingActiveTab = "overview";
    switchPropertyListingTab(propertyListingActiveTab);
}

function getPropertyEditorPayload() {
    const occupants = collectListingCollection("edit", "occupants");
    const keys = collectListingCollection("edit", "keys");
    const inspections = collectPropertyListingInspections(true);
    if (inspections === null) return null;
    return {
        property_address: String(document.getElementById("propertyEditAddress")?.value || "").trim(),
        suburb: String(document.getElementById("propertyEditSuburb")?.value || "").trim(),
        state_code: "VIC",
        postcode: String(document.getElementById("propertyEditPostcode")?.value || "").trim(),
        crm_property_id: String(document.getElementById("propertyEditCrmId")?.value || "").trim(),
        property_type: String(document.getElementById("propertyEditType")?.value || "").trim(),
        rental_type: String(document.getElementById("propertyEditRentalType")?.value || "").trim(),
        listing_status: String(document.getElementById("propertyEditListingStatus")?.value || "OPEN").toUpperCase(),
        key_number: String(keys[0]?.key_number || "").trim(),
        tenancy_status: String(document.getElementById("propertyEditTenancyStatus")?.value || "").trim(),
        owner_is_company: !!document.getElementById("propertyEditOwnerCompany")?.checked,
        owners: collectPropertyEditorContacts("owners"),
        tenants: occupants,
        occupants,
        keys,
        social_media_history: collectListingCollection("edit", "social"),
        inspections,
    };
}

function setPropertyEditorValue(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = value || "";
}

function openPropertyEditor(propertyId) {
    const row = propertyResultsCache[propertyId];
    if (!row) {
        alert("Listing details are not loaded. Refresh the listing register and try again.");
        return;
    }
    editingPropertyId = Number(propertyId);
    setPropertyEditorValue("propertyEditAddress", row.property_address);
    setPropertyEditorValue("propertyEditSuburb", row.suburb);
    setPropertyEditorValue("propertyEditPostcode", row.postcode);
    setPropertyEditorValue("propertyEditCrmId", row.crm_property_id);
    setPropertyEditorValue("propertyEditType", row.property_type);
    setPropertyEditorValue("propertyEditRentalType", row.rental_type);
    setPropertyEditorValue("propertyEditTenancyStatus", row.tenancy_status);
    setPropertyEditorValue("propertyEditListingStatus", String(row.listing_status || "OPEN").toUpperCase());
    const ownerCompany = document.getElementById("propertyEditOwnerCompany");
    if (ownerCompany) ownerCompany.checked = !!row.owner_is_company;
    renderPropertyEditorContacts("owners", propertyContactsForPayload(row.owners));
    const occupants = listingArray(row.occupants).length ? listingArray(row.occupants) : propertyContactsForPayload(row.tenants);
    const keys = listingArray(row.keys).length ? listingArray(row.keys) : (row.key_number ? [{ key_number: row.key_number, description: "", location: "" }] : []);
    renderListingCollection("edit", "occupants", occupants);
    renderListingCollection("edit", "keys", keys);
    renderListingCollection("edit", "social", row.social_media_history);
    renderPropertyListingInspections(row.inspections);
    const title = document.getElementById("propertyEditorTitle");
    const subtitle = document.getElementById("propertyEditorSubtitle");
    if (title) title.textContent = propertyFullAddress(row) || "View Listing";
    if (subtitle) subtitle.textContent = `${String(row.listing_status || "OPEN").toUpperCase() === "OPEN" ? "Open" : "Closed"} listing${row.crm_property_id ? ` · ${row.crm_property_id}` : ""}`;
    const status = document.getElementById("propertyEditStatus");
    if (status) {
        status.style.display = "none";
        status.textContent = "";
    }
    const modal = document.getElementById("propertyEditorModal");
    if (modal) modal.classList.remove("hidden");
    propertyListingActiveTab = "overview";
    updatePropertyListingOpenTabs();
}

function closePropertyEditor() {
    const modal = document.getElementById("propertyEditorModal");
    if (modal) modal.classList.add("hidden");
    editingPropertyId = null;
}

async function savePropertyEditor() {
    if (!editingPropertyId) return;
    const payload = getPropertyEditorPayload();
    if (!payload) return;
    if (!payload.property_address) {
        alert("Listing address is required.");
        return;
    }
    const btn = document.getElementById("propertyEditSaveBtn");
    const oldText = btn ? btn.textContent : "";
    if (btn) {
        btn.disabled = true;
        btn.textContent = "Saving...";
    }
    try {
        const r = await apiFetch(`/properties/${editingPropertyId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        if (!r.ok) {
            alert(`Failed to save listing: ${await extractErrorMessage(r)}`);
            return;
        }
        closePropertyEditor();
        propertiesLoadedOnce = false;
        await loadProperties(currentPropertiesPage || 1);
        await refreshPropertyOptions();
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = oldText || "Save Property";
        }
    }
}

function renderPropertyProfileRow(row) {
    const address = propertyFullAddress(row);
    const ownerCount = propertyContactList(row.owners).length;
    const occupants = listingArray(row.occupants).length ? listingArray(row.occupants) : propertyContactsForPayload(row.tenants);
    const keys = listingArray(row.keys).length ? listingArray(row.keys) : (row.key_number ? [{ key_number: row.key_number }] : []);
    const inspections = listingArray(row.inspections);
    const upcomingInspections = inspections.filter((item) => !listingInspectionIsCompleted(item)).length;
    const status = String(row.listing_status || "OPEN").toUpperCase();
    const owner = propertyPrimaryContact(row.owners);
    return `
      <article class="property-profile-row">
        <section class="property-listing-card">
          <div class="property-listing-main">
            <div class="property-listing-title-row">
              <h3>${escapeHtml(address || "Listing")}</h3>
              <span class="property-listing-status ${status === "OPEN" ? "" : "closed"}">${escapeHtml(status === "OPEN" ? "Open" : "Closed")}</span>
            </div>
            <div class="property-listing-sub">${escapeHtml([
                propertyText(row.property_type, "Property type not set"),
                owner.name ? `Landlord: ${owner.name}` : `${ownerCount} landlord contact${ownerCount === 1 ? "" : "s"}`,
                propertyText(row.tenancy_status, "Tenancy status not set"),
            ].join(" · "))}</div>
            <div class="property-listing-meta">
              <span>${occupants.length} occupant${occupants.length === 1 ? "" : "s"}</span>
              <span>${keys.length} key${keys.length === 1 ? "" : "s"}</span>
              <span>${upcomingInspections} upcoming inspection${upcomingInspections === 1 ? "" : "s"}</span>
              ${row.crm_property_id ? `<span>${escapeHtml(String(row.crm_property_id))}</span>` : ""}
            </div>
          </div>
          <div class="property-listing-actions">
            <button class="btn primary" onclick="openPropertyEditor(${Number(row.id)})">View Listing</button>
            <button class="btn" onclick="openMaintenanceForProperty(${Number(row.id)})">Maintenance</button>
            <button class="btn danger" onclick="deleteProperty(${Number(row.id)})">Delete</button>
          </div>
        </section>
      </article>
    `;
}

async function loadProperties(page = null) {
    if (page !== null) currentPropertiesPage = page;
    const p = currentPropertiesPage || 1;
    const { query } = getPropertyFilters();
    const url = new URL("/properties", window.location.origin);
    url.searchParams.set("page", String(p));
    url.searchParams.set("page_size", "25");
    if (query) url.searchParams.set("query", query);

    const results = document.getElementById("propertiesProfileResults");
    if (results) results.innerHTML = `<div class="ticket-empty"><strong>Loading listings...</strong></div>`;

    const r = await apiFetch(url.toString());
    const t = await r.text();
    if (!r.ok) {
        if (results) results.innerHTML = `<div class="ticket-empty"><strong>Failed to load listings</strong><div class="small muted" style="margin-top:6px">${escapeHtml(t)}</div></div>`;
        return;
    }
    const data = JSON.parse(t);
    propertiesLoadedOnce = true;
    const items = Array.isArray(data.items) ? data.items : [];
    propertyResultsCache = {};
    items.forEach((row) => { propertyResultsCache[row.id] = row; });
    if (results) {
        if (!items.length) {
            results.innerHTML = `<div class="ticket-empty"><strong>No listings found</strong><div class="small muted" style="margin-top:6px">Try searching by address, owner, occupant, email, phone, or key.</div></div>`;
        } else {
            results.innerHTML = items.map(renderPropertyProfileRow).join("");
        }
    }
    const pi = document.getElementById("propertiesPageInfo");
    if (pi) {
        const total = Number(data.total || 0);
        const pageNow = Number(data.page || 1);
        const sizeNow = Number(data.page_size || 25);
        const pages = sizeNow > 0 ? Math.max(1, Math.ceil(total / sizeNow)) : 1;
        pi.textContent = `Page ${pageNow} of ${pages} - ${total} listings`;
    }
    const btnPrev = document.getElementById("propertiesBtnPrev");
    const btnNext = document.getElementById("propertiesBtnNext");
    if (btnPrev) btnPrev.disabled = Number(data.page || 1) <= 1;
    if (btnNext) btnNext.disabled = !Boolean(data.has_more);
}

function clearPropertySearch() {
    const search = document.getElementById("propertySearchBox");
    if (search) search.value = "";
    currentPropertiesPage = 1;
    loadProperties(1);
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

function manualPropertyContact(prefix) {
    const name = String(document.getElementById(`${prefix}Name`)?.value || "").trim();
    const email = String(document.getElementById(`${prefix}Email`)?.value || "").trim();
    const phone = String(document.getElementById(`${prefix}Phone`)?.value || "").trim();
    if (!name && !email && !phone) return null;
    return {
        name,
        email,
        mobile: phone,
        phone: "",
        phones: phone ? [phone] : [],
        is_company: false,
    };
}

async function createPropertyFromForm() {
    ensureNewListingCollections();
    if (autocompleteNewPropertyFields() === false) return;
    const property_address = (document.getElementById("newPropertyAddress")?.value || "").trim();
    const suburb = (document.getElementById("newPropertySuburb")?.value || "").trim();
    const state_code = "VIC";
    const postcode = (document.getElementById("newPropertyPostcode")?.value || "").trim();
    const listing_status = String(document.getElementById("newPropertyListingStatus")?.value || "OPEN").toUpperCase();
    const tenancy_status = (document.getElementById("newPropertyTenancyStatus")?.value || "").trim();
    const owner = manualPropertyContact("newPropertyOwner");
    const occupants = collectListingCollection("new", "occupants");
    const keys = collectListingCollection("new", "keys");
    const social_media_history = collectListingCollection("new", "social");
    const key_number = String(keys[0]?.key_number || "").trim();
    if (!property_address) {
        alert("Listing address is required.");
        return;
    }
    const r = await apiFetch("/properties", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            property_address,
            suburb,
            state_code,
            postcode,
            listing_status,
            key_number,
            tenancy_status,
            owners: owner ? [owner] : [],
            tenants: occupants,
            occupants,
            keys,
            social_media_history,
            inspections: [],
        }),
    });
    const t = await r.text();
    if (!r.ok) {
        alert(`Failed to add listing (${r.status}):\n\n${t}`);
        return;
    }
    [
        "newPropertyAddress",
        "newPropertySuburb",
        "newPropertyPostcode",
        "newPropertyTenancyStatus",
        "newPropertyOwnerName",
        "newPropertyOwnerEmail",
        "newPropertyOwnerPhone",
    ].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.value = "";
    });
    const listingStatus = document.getElementById("newPropertyListingStatus");
    if (listingStatus) listingStatus.value = "OPEN";
    newListingCollectionsReady = false;
    ensureNewListingCollections(true);
    currentPropertiesPage = 1;
    propertiesLoadedOnce = false;
    await loadProperties();
    await refreshPropertyOptions();
}

async function deleteProperty(propertyId, label = "this listing") {
    if (!confirm(`Delete ${label} from the active listing register? Compliance records will be kept in history.`)) return;
    const r = await apiFetch(`/properties/${propertyId}`, { method: "DELETE" });
    const t = await r.text();
    if (!r.ok) {
        alert(`Failed to delete listing (${r.status}):\n\n${t}`);
        return;
    }
    currentPropertiesPage = 1;
    propertiesLoadedOnce = false;
    await loadProperties();
    await refreshPropertyOptions();
    if (complianceLoadedOnce) await loadComplianceDashboard(1);
    if (coverageLoadedOnce) await loadComplianceCoverage(1);
}

async function flushProperties() {
    const confirmed = confirm(
        "Flush the active property register?\n\n" +
        "This will archive all currently active properties so the list becomes empty. " +
        "Compliance and maintenance history will be kept, and the next CRM import can reactivate matching properties."
    );
    if (!confirmed) return;

    const meta = document.getElementById("propertyImportMeta");
    if (meta) meta.textContent = "Flushing active property register...";
    const r = await apiFetch("/properties/flush", { method: "DELETE" });
    const t = await r.text();
    if (!r.ok) {
        if (meta) meta.textContent = "Property flush failed.";
        alert(`Property flush failed (${r.status}):\n\n${t}`);
        return;
    }
    let j = null;
    try { j = JSON.parse(t); } catch { j = null; }
    if (meta) meta.textContent = `Flushed ${j?.deleted || 0} active properties at ${new Date().toLocaleString()}. Import the latest CRM workbook to rebuild the register.`;
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
    if (meta) {
        const imported = j?.imported_rows ?? "-";
        const created = j?.created ?? 0;
        const updated = j?.updated ?? 0;
        const reactivated = j?.reactivated ?? 0;
        const archived = j?.duplicates_archived ?? 0;
        meta.textContent = `Processed ${imported} CRM property profiles from ${f.name}: ${created} new, ${updated} updated, ${reactivated} reactivated, ${archived} duplicates archived at ${new Date().toLocaleString()}.`;
    }
    currentPropertiesPage = 1;
    propertiesLoadedOnce = false;
    await loadProperties();
    await refreshPropertyOptions();
}

function clearPropertyOptionsState() {
    cancelInspectionAddressSuggestionSearch(true);
    propertyOptionsCache = [];
    propertyOptionsByLabel = {};
    addressSuggestionsByLabel = {};
    [
        "compliancePropertyOptions",
        "maintenancePropertyOptions",
        "inspectionPropertyOptions",
        "rentPropertyOptions",
        "landlordReportPropertyOptions",
        "leaseRenewalPropertyOptions",
        "propertyAddressSuggestions",
    ].forEach((id) => {
        const list = document.getElementById(id);
        if (list) list.innerHTML = "";
    });
    [
        "compliancePropertyId",
        "maintenancePropertyId",
        "rentNewPropertyId",
        "landlordReportPropertyId",
        "leaseRenewalPropertyId",
    ].forEach((id) => {
        const hidden = document.getElementById(id);
        if (hidden) hidden.value = "";
    });
}

async function refreshPropertyOptions() {
    const search = document.getElementById("compliancePropertySearch");
    const hidden = document.getElementById("compliancePropertyId");
    const complianceList = document.getElementById("compliancePropertyOptions");
    const maintenanceSearch = document.getElementById("maintenancePropertySearch");
    const maintenanceHidden = document.getElementById("maintenancePropertyId");
    const maintenanceList = document.getElementById("maintenancePropertyOptions");
    const leaseSearch = document.getElementById("leaseRenewalPropertySearch");
    const leaseHidden = document.getElementById("leaseRenewalPropertyId");
    const leaseList = document.getElementById("leaseRenewalPropertyOptions");
    const landlordReportSearch = document.getElementById("landlordReportPropertySearch");
    const landlordReportHidden = document.getElementById("landlordReportPropertyId");
    const landlordReportList = document.getElementById("landlordReportPropertyOptions");
    const rentSearch = document.getElementById("rentNewPropertySearch");
    const rentHidden = document.getElementById("rentNewPropertyId");
    const rentList = document.getElementById("rentPropertyOptions");
    const requestMailbox = normalizeMailbox(currentMailbox);
    try {
        const r = await apiFetch("/properties/options");
        if (requestMailbox !== normalizeMailbox(currentMailbox)) return;
        if (!r.ok) {
            clearPropertyOptionsState();
            return;
        }
        const data = await r.json();
        if (requestMailbox !== normalizeMailbox(currentMailbox)) return;
        propertyOptionsCache = Array.isArray(data.items) ? data.items : [];
        propertyOptionsByLabel = {};
        propertyOptionsCache.forEach((p) => {
            [
                p.label,
                p.property_address,
                propertyFullAddress(p),
                p.crm_property_id,
                [p.property_address, p.suburb].filter(Boolean).join(", "),
            ].forEach((value) => {
                const key = String(value || "").trim().toLowerCase();
                if (key) propertyOptionsByLabel[key] = p;
            });
        });
        const optionsHtml = propertyOptionsCache
            .map((p) => `<option value="${escapeHtml(p.label || "")}"></option>`)
            .join("");
        if (complianceList) complianceList.innerHTML = optionsHtml;
        if (maintenanceList) maintenanceList.innerHTML = optionsHtml;
        if (leaseList) leaseList.innerHTML = optionsHtml;
        if (landlordReportList) landlordReportList.innerHTML = optionsHtml;
        if (rentList) rentList.innerHTML = optionsHtml;
        renderInspectionPropertyOptions();
        renderAddressSuggestionOptions();
        if (search && hidden) {
            const match = resolvePropertySearchValue(search.value);
            hidden.value = match ? String(match.id) : "";
        }
        if (maintenanceSearch && maintenanceHidden) {
            const match = resolvePropertySearchValue(maintenanceSearch.value);
            maintenanceHidden.value = match ? String(match.id) : "";
        }
        if (leaseSearch && leaseHidden) {
            const match = resolvePropertySearchValue(leaseSearch.value);
            leaseHidden.value = match ? String(match.id) : "";
        }
        if (landlordReportSearch && landlordReportHidden) {
            const match = resolvePropertySearchValue(landlordReportSearch.value);
            landlordReportHidden.value = match ? String(match.id) : "";
        }
        if (rentSearch && rentHidden) {
            const match = resolvePropertySearchValue(rentSearch.value);
            rentHidden.value = match ? String(match.id) : "";
        }
    } catch {
        if (requestMailbox !== normalizeMailbox(currentMailbox)) return;
        clearPropertyOptionsState();
    }
}

function resolvePropertySearchValue(value) {
    const needle = String(value || "").trim().toLowerCase();
    if (!needle) return null;
    if (propertyOptionsByLabel[needle]) return propertyOptionsByLabel[needle];
    const matches = propertyOptionsCache.filter((p) => String(p.label || "").toLowerCase().includes(needle));
    return matches.length === 1 ? matches[0] : null;
}

function landlordReportMelbourneDate() {
    const parts = new Intl.DateTimeFormat("en-AU", {
        timeZone: "Australia/Melbourne",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
    }).formatToParts(new Date());
    const values = Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
    return `${values.year}-${values.month}-${values.day}`;
}

function landlordReportMonthRange(monthValue) {
    const match = /^(\d{4})-(\d{2})$/.exec(String(monthValue || ""));
    if (!match) return { startDate: "", endDate: "" };
    const year = Number(match[1]);
    const month = Number(match[2]);
    const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
    return {
        startDate: `${match[1]}-${match[2]}-01`,
        endDate: `${match[1]}-${match[2]}-${String(lastDay).padStart(2, "0")}`,
    };
}

async function purgeNoReplyNeededTickets() {
    const count = Number(document.getElementById("tabNoReplyNeededCount")?.textContent || 0);
    const message = count > 0
        ? `Clear ${count} Reply Not Needed ticket${count === 1 ? "" : "s"} from this mailbox?\n\nThis removes only AgentBot ticket records. It does not delete or move any Gmail messages.`
        : "Clear all Reply Not Needed tickets from this mailbox?\n\nThis does not delete or move any Gmail messages.";
    if (!confirm(message)) return;
    const button = document.getElementById("purgeNoReplyNeededBtn");
    if (button) {
        button.disabled = true;
        button.textContent = "Clearing...";
    }
    try {
        const response = await apiFetch("/tickets/no-reply-needed/purge", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confirm: "PURGE" }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Could not clear tickets.");
        invalidateTicketCache();
        await loadTickets();
        await loadNotifications();
        alert(data.message || "Reply Not Needed tickets cleared.");
    } catch (error) {
        alert(`Could not clear Reply Not Needed tickets: ${error?.message || error}`);
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = "Clear Reply Not Needed";
        }
    }
}

function switchLandlordReportView(view = "builder") {
    landlordReportViewMode = ["data", "saved"].includes(view) ? view : "builder";
    document.getElementById("landlordReportBuilderView")?.classList.toggle("hidden", landlordReportViewMode !== "builder");
    document.getElementById("landlordReportDataView")?.classList.toggle("hidden", landlordReportViewMode !== "data");
    document.getElementById("landlordReportSavedView")?.classList.toggle("hidden", landlordReportViewMode !== "saved");
    document.querySelectorAll("[data-landlord-report-view]").forEach((button) => {
        button.classList.toggle("active", button.dataset.landlordReportView === landlordReportViewMode);
    });
    if (currentDashboardTab !== "landlord_reports") return;
    const title = document.getElementById("topbarTitle");
    const subtitle = document.getElementById("topbarSubtitle");
    if (title) title.textContent = landlordReportViewMode === "data" ? "Landlord Report Data" : landlordReportViewMode === "saved" ? "Saved Landlord Reports" : "Monthly Landlord Report";
    if (subtitle) subtitle.textContent = landlordReportViewMode === "data"
        ? "Manage persistent invoice exports used automatically across landlord reports."
        : landlordReportViewMode === "saved"
            ? "Search, download, and manage previously generated landlord PDFs."
            : "Prepare a branded owner report from live property records and verified report-only notes.";
    if (landlordReportViewMode === "data") loadLandlordReportData();
    else if (landlordReportViewMode === "saved") loadSavedLandlordReports();
    else if (!landlordReportLoadedOnce) initLandlordReportBuilder();
}

function landlordReportSixMonthRange(monthValue) {
    const end = landlordReportMonthRange(monthValue);
    const match = /^(\d{4})-(\d{2})$/.exec(String(monthValue || ""));
    if (!match || !end.endDate) return { startDate: "", endDate: "" };
    const firstMonth = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 6, 1));
    return {
        startDate: firstMonth.toISOString().slice(0, 10),
        endDate: end.endDate,
    };
}

function landlordReportPeriod() {
    const mode = document.getElementById("landlordReportPeriodMode")?.value || "month";
    if (mode === "custom") {
        return {
            startDate: document.getElementById("landlordReportStartDate")?.value || "",
            endDate: document.getElementById("landlordReportEndDate")?.value || "",
        };
    }
    const monthValue = document.getElementById("landlordReportMonth")?.value || "";
    return mode === "six_months"
        ? landlordReportSixMonthRange(monthValue)
        : landlordReportMonthRange(monthValue);
}

function setLandlordReportMessage(message = "", type = "error") {
    const element = document.getElementById("landlordReportMessage");
    if (!element) return;
    element.textContent = message;
    element.className = `landlord-report-message${message ? " show" : ""}${type ? ` ${type}` : ""}`;
}

function setLandlordReportPreviewPlaceholder(title, detail) {
    const preview = document.getElementById("landlordReportPreview");
    if (!preview) return;
    preview.innerHTML = `<div class="landlord-report-placeholder"><div><img src="/static/dons_premier_transparent_v2.png" alt="" /><strong>${escapeHtml(title)}</strong><p>${escapeHtml(detail)}</p></div></div>`;
}

function landlordReportPeriodModeChanged() {
    const mode = document.getElementById("landlordReportPeriodMode")?.value || "month";
    const custom = mode === "custom";
    document.getElementById("landlordReportMonthField")?.classList.toggle("hidden", custom);
    document.getElementById("landlordReportCustomPeriod")?.classList.toggle("hidden", !custom);
    const monthLabel = document.getElementById("landlordReportMonthLabel");
    if (monthLabel) monthLabel.textContent = mode === "six_months" ? "Ending month and year" : "Month and year";
    scheduleLandlordReportContext();
}

function bindLandlordReportEvents() {
    if (landlordReportEventsBound) return;
    landlordReportEventsBound = true;
    const propertySearch = document.getElementById("landlordReportPropertySearch");
    const propertyChanged = () => {
        const match = resolvePropertySearchValue(propertySearch?.value || "");
        const hidden = document.getElementById("landlordReportPropertyId");
        if (hidden) hidden.value = match ? String(match.id) : "";
        if (match) scheduleLandlordReportContext();
    };
    propertySearch?.addEventListener("input", propertyChanged);
    propertySearch?.addEventListener("change", propertyChanged);
    ["landlordReportMonth", "landlordReportStartDate", "landlordReportEndDate"].forEach((id) => {
        document.getElementById(id)?.addEventListener("change", scheduleLandlordReportContext);
    });
    [
        "landlordReportLandlordName",
        "landlordReportManager",
        "landlordReportPreparedDate",
        "landlordReportIntro",
        "landlordReportOverallSummary",
        "landlordReportAdditionalNotes",
        "landlordReportIncludeEmpty",
        "landlordReportIncludePhotos",
        "landlordReportIncludeFinancial",
        "landlordReportIncludeInternal",
        "landlordReportHeroPhoto",
    ].forEach((id) => {
        const element = document.getElementById(id);
        element?.addEventListener("input", scheduleLandlordReportPreview);
        element?.addEventListener("change", scheduleLandlordReportPreview);
    });
    renderLandlordReportDetailPicker();
}

async function initLandlordReportBuilder() {
    bindLandlordReportEvents();
    const today = landlordReportMelbourneDate();
    const month = today.slice(0, 7);
    const monthInput = document.getElementById("landlordReportMonth");
    const prepared = document.getElementById("landlordReportPreparedDate");
    const activityDate = document.getElementById("landlordReportActivityDate");
    if (monthInput && !monthInput.value) monthInput.value = month;
    if (prepared && !prepared.value) prepared.value = today;
    if (activityDate && !activityDate.value) activityDate.value = today;
    landlordReportPeriodModeChanged();
    if (!propertyOptionsCache.length) await refreshPropertyOptions();
    landlordReportLoadedOnce = true;
    const selected = resolvePropertySearchValue(document.getElementById("landlordReportPropertySearch")?.value || "");
    if (selected) await loadLandlordReportContext();
}

function resetLandlordReportBuilder() {
    landlordReportLoadedOnce = false;
    landlordReportDataLoaded = false;
    savedLandlordReportsLoaded = false;
    savedLandlordReportsCache = {};
    landlordReportContext = null;
    landlordReportPropertyKey = "";
    landlordReportSectionOrder = [];
    landlordReportSelectedSections = new Set();
    landlordReportSectionNotes = {};
    landlordReportActivities = [];
    landlordReportEditingActivityId = "";
    landlordReportSelectedPhotoIds = new Set();
    landlordReportOnlyPhotos = [];
    landlordReportPendingPhotoIds = [];
    landlordReportOnlyPdfs = [];
    landlordReportPendingPdfIds = [];
    landlordReportPendingFilesChanged = false;
    landlordReportDetailValues = {};
    landlordReportContextRequest += 1;
    landlordReportPreviewRequest += 1;
    if (landlordReportContextTimer) clearTimeout(landlordReportContextTimer);
    if (landlordReportPreviewTimer) clearTimeout(landlordReportPreviewTimer);
    landlordReportContextTimer = null;
    landlordReportPreviewTimer = null;
    const emptyValues = [
        "landlordReportPropertySearch",
        "landlordReportPropertyId",
        "landlordReportLandlordName",
        "landlordReportIntro",
        "landlordReportOverallSummary",
        "landlordReportAdditionalNotes",
    ];
    emptyValues.forEach((id) => {
        const element = document.getElementById(id);
        if (element) element.value = "";
    });
    renderLandlordReportDetailPicker();
    const manager = document.getElementById("landlordReportManager");
    if (manager) manager.innerHTML = '<option value="">Select property manager</option>';
    const sections = document.getElementById("landlordReportSectionList");
    if (sections) sections.innerHTML = '<div class="landlord-report-empty">Sections load after a property is selected.</div>';
    const sourceSummary = document.getElementById("landlordReportSourceSummary");
    if (sourceSummary) sourceSummary.innerHTML = "";
    const counter = document.getElementById("landlordReportSectionCount");
    if (counter) counter.textContent = "Select a property to load sections.";
    const limitations = document.getElementById("landlordReportLimitations");
    if (limitations) {
        limitations.innerHTML = "";
        limitations.classList.add("hidden");
    }
    renderLandlordReportActivities();
    setLandlordReportMessage("", "");
    setLandlordReportPreviewPlaceholder("Your report preview will appear here", "Select a managed property, choose the sections, and add any landlord-facing notes.");
}

function scheduleLandlordReportContext() {
    if (landlordReportContextTimer) clearTimeout(landlordReportContextTimer);
    landlordReportContextTimer = setTimeout(() => loadLandlordReportContext(), 260);
}

function landlordReportManagerOptions(selectedId) {
    const manager = document.getElementById("landlordReportManager");
    if (!manager) return;
    const staff = Array.isArray(landlordReportContext?.staff) ? landlordReportContext.staff : [];
    manager.innerHTML = [
        '<option value="">Select property manager</option>',
        ...staff.map((user) => {
            const selected = String(user.id) === String(selectedId || "") ? " selected" : "";
            const role = String(user.role || "").replaceAll("_", " ").toLowerCase();
            const label = user.name || user.email || "Staff member";
            return `<option value="${Number(user.id)}"${selected}>${escapeHtml(label)}${role ? ` (${escapeHtml(role)})` : ""}</option>`;
        }),
    ].join("");
}

function renderLandlordReportSourceSummary() {
    const container = document.getElementById("landlordReportSourceSummary");
    if (!container) return;
    const summary = landlordReportContext?.source_summary;
    if (!summary) {
        container.innerHTML = "";
        return;
    }
    const items = [
        ["Maintenance", summary.maintenance_orders],
        ["Rent records", summary.rent_records],
        ["Compliance", summary.compliance_records],
        ["Lease", summary.lease_record_available ? "Available" : "Not recorded"],
        ["Photos", summary.supporting_photos],
    ];
    container.innerHTML = items.map(([label, value]) => `<span>${escapeHtml(label)}: ${escapeHtml(String(value ?? 0))}</span>`).join("");
}

async function uploadLandlordReportInvoices() {
    switchLandlordReportView("data");
    return;
    const input = document.getElementById("landlordReportInvoiceFile");
    const file = input?.files?.[0];
    if (!file) {
        setLandlordReportMessage("Choose the CRM Outgoing invoices Report .csv or .xlsx file first.", "error");
        return;
    }
    const button = document.getElementById("landlordReportInvoiceUpload");
    if (button) button.disabled = true;
    const meta = document.getElementById("landlordReportInvoiceMeta");
    if (meta) meta.textContent = "Reading and matching invoice rows…";
    try {
        const form = new FormData();
        form.append("file", file, file.name);
        const response = await apiFetch("/landlord-reports/invoice-workbook", { method: "POST", body: form });
        if (!response.ok) throw new Error(await extractErrorMessage(response));
        const result = await response.json();
        landlordReportInvoiceRows = Array.isArray(result.rows) ? result.rows : [];
        landlordReportInvoiceFilename = result.filename || file.name;
        updateLandlordReportInvoiceMeta(result);
        setLandlordReportMessage(`Invoice data loaded temporarily: ${result.matched_count || 0} of ${result.row_count || 0} rows matched to managed properties.`, "success");
        scheduleLandlordReportPreview();
    } catch (error) {
        if (meta) meta.textContent = "Invoice workbook could not be loaded.";
        setLandlordReportMessage(error.message || "Invoice workbook could not be loaded.", "error");
    } finally {
        if (button) button.disabled = false;
    }
}

function setLandlordReportDataMessage(message = "", type = "") {
    const element = document.getElementById("landlordReportDataMessage");
    if (!element) return;
    element.textContent = message;
    element.className = `landlord-report-message${message ? " show" : ""}${type ? ` ${type}` : ""}`;
}

function landlordReportDataCurrency(value) {
    return new Intl.NumberFormat("en-AU", { style: "currency", currency: "AUD" }).format(Number(value || 0));
}

function renderLandlordReportData(imports = []) {
    const byType = Object.fromEntries(imports.map((item) => [item.report_type, item]));
    ["outgoing", "incoming", "bond", "mortgage"].forEach((reportType) => {
        const item = byType[reportType];
        const status = document.getElementById(`landlordReportDataStatus-${reportType}`);
        if (!status) return;
        if (!item?.row_count) {
            status.innerHTML = "<strong>Not uploaded</strong><br/>Choose the matching CRM CSV to make it available to every report.";
            return;
        }
        const imported = item.imported_at ? new Date(item.imported_at).toLocaleString("en-AU") : "Unknown time";
        status.innerHTML = `<strong>${escapeHtml(item.filename || "Invoice report")}</strong><br/>${item.row_count} rows | ${item.matched_count} matched | ${item.unmatched_count} unmatched<br/>Source total: ${escapeHtml(landlordReportDataCurrency(item.total_amount))} | Replaced ${escapeHtml(imported)}`;
    });
    const totalRows = imports.reduce((sum, item) => sum + Number(item.row_count || 0), 0);
    const totalMatched = imports.reduce((sum, item) => sum + Number(item.matched_count || 0), 0);
    const summary = document.getElementById("landlordReportDataSummary");
    if (summary) summary.textContent = `${totalRows} stored rows across four report types; ${totalMatched} rows matched to managed properties.`;
}

async function loadLandlordReportData(force = false) {
    if (landlordReportDataLoaded && !force) return;
    try {
        const response = await apiFetch("/landlord-reports/invoice-data");
        if (!response.ok) throw new Error(await extractErrorMessage(response));
        const result = await response.json();
        renderLandlordReportData(result.imports || []);
        landlordReportDataLoaded = true;
        setLandlordReportDataMessage("", "");
    } catch (error) {
        setLandlordReportDataMessage(error.message || "Stored report data could not be loaded.", "error");
    }
}

async function uploadLandlordReportData(reportType) {
    const input = document.getElementById(`landlordReportDataFile-${reportType}`);
    const file = input?.files?.[0];
    if (!file) {
        setLandlordReportDataMessage("Choose the matching CSV file first.", "error");
        return;
    }
    const button = document.getElementById(`landlordReportDataUpload-${reportType}`);
    if (button) button.disabled = true;
    setLandlordReportDataMessage(`Importing ${file.name} and matching properties…`, "info");
    try {
        const form = new FormData();
        form.append("file", file, file.name);
        const response = await apiFetch(`/landlord-reports/invoice-data/${encodeURIComponent(reportType)}`, { method: "POST", body: form });
        if (!response.ok) throw new Error(await extractErrorMessage(response));
        const result = await response.json();
        renderLandlordReportData(result.imports || []);
        landlordReportDataLoaded = true;
        if (input) input.value = "";
        setLandlordReportDataMessage(`${result.import?.label || "Invoice data"} saved. ${result.import?.matched_count || 0} of ${result.import?.row_count || 0} rows matched; this replaces the previous ${reportType} import.`, "success");
        scheduleLandlordReportPreview();
    } catch (error) {
        setLandlordReportDataMessage(error.message || "Invoice data could not be imported.", "error");
    } finally {
        if (button) button.disabled = false;
    }
}

function setSavedLandlordReportMessage(message = "", type = "") {
    const element = document.getElementById("landlordReportSavedMessage");
    if (!element) return;
    element.textContent = message;
    element.className = `landlord-report-message${message ? " show" : ""}${type ? ` ${type}` : ""}`;
}

function savedLandlordReportFileSize(bytes) {
    const value = Number(bytes || 0);
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function renderSavedLandlordReports(reports = []) {
    const body = document.getElementById("landlordReportSavedRows");
    const count = document.getElementById("landlordReportSavedCount");
    if (count) count.textContent = `${reports.length} saved report${reports.length === 1 ? "" : "s"} found.`;
    if (!body) return;
    savedLandlordReportsCache = Object.fromEntries(reports.map((report) => [Number(report.id), report]));
    if (!reports.length) {
        body.innerHTML = '<tr><td colspan="6" class="muted">No saved reports match this address search.</td></tr>';
        return;
    }
    body.innerHTML = reports.map((report) => {
        const generated = report.generated_at ? new Date(report.generated_at).toLocaleString("en-AU") : "—";
        return `<tr>
          <td>${escapeHtml(report.property_address || "Property")}</td>
          <td>${escapeHtml(report.duration || "—")}</td>
          <td>${escapeHtml(report.period_label || `${report.period_start || ""} to ${report.period_end || ""}`)}</td>
          <td>${escapeHtml(generated)}</td>
          <td>${escapeHtml(savedLandlordReportFileSize(report.file_size))}</td>
          <td><div class="landlord-report-saved-actions"><button class="btn" type="button" onclick="downloadSavedLandlordReport(${Number(report.id)})">Download</button><button class="btn danger" type="button" onclick="deleteSavedLandlordReport(${Number(report.id)})">Delete</button></div></td>
        </tr>`;
    }).join("");
}

async function loadSavedLandlordReports(force = false) {
    if (savedLandlordReportsLoaded && !force) return;
    const search = String(document.getElementById("landlordReportSavedSearch")?.value || "").trim();
    try {
        const response = await apiFetch(`/landlord-reports/saved?search=${encodeURIComponent(search)}`);
        if (!response.ok) throw new Error(await extractErrorMessage(response));
        const result = await response.json();
        renderSavedLandlordReports(result.reports || []);
        savedLandlordReportsLoaded = true;
        setSavedLandlordReportMessage("", "");
    } catch (error) {
        setSavedLandlordReportMessage(error.message || "Saved reports could not be loaded.", "error");
    }
}

function scheduleSavedLandlordReportSearch() {
    savedLandlordReportsLoaded = false;
    if (savedLandlordReportSearchTimer) clearTimeout(savedLandlordReportSearchTimer);
    savedLandlordReportSearchTimer = setTimeout(() => loadSavedLandlordReports(true), 300);
}

async function downloadSavedLandlordReport(reportId) {
    const filename = savedLandlordReportsCache[reportId]?.filename || "Landlord-Report.pdf";
    try {
        const response = await apiFetch(`/landlord-reports/saved/${reportId}/download`);
        if (!response.ok) throw new Error(await extractErrorMessage(response));
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = filename || "Landlord-Report.pdf";
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 30000);
    } catch (error) {
        setSavedLandlordReportMessage(error.message || "The saved report could not be downloaded.", "error");
    }
}

async function deleteSavedLandlordReport(reportId) {
    const address = savedLandlordReportsCache[reportId]?.property_address || "this property";
    if (!confirm(`Permanently delete the saved report for ${address}? This cannot be undone.`)) return;
    try {
        const response = await apiFetch(`/landlord-reports/saved/${reportId}`, { method: "DELETE" });
        if (!response.ok) throw new Error(await extractErrorMessage(response));
        savedLandlordReportsLoaded = false;
        await loadSavedLandlordReports(true);
        setSavedLandlordReportMessage("Saved report permanently deleted.", "success");
    } catch (error) {
        setSavedLandlordReportMessage(error.message || "The saved report could not be deleted.", "error");
    }
}

function renderLandlordReportSections() {
    const container = document.getElementById("landlordReportSectionList");
    const definitions = Array.isArray(landlordReportContext?.sections) ? landlordReportContext.sections : [];
    if (!container || !definitions.length) return;
    const byId = Object.fromEntries(definitions.map((section) => [section.id, section]));
    landlordReportSectionOrder = landlordReportSectionOrder.filter((id) => byId[id]);
    definitions.forEach((section) => {
        if (!landlordReportSectionOrder.includes(section.id)) landlordReportSectionOrder.push(section.id);
    });
    container.innerHTML = landlordReportSectionOrder.map((sectionId, index) => {
        const section = byId[sectionId];
        const checked = landlordReportSelectedSections.has(sectionId) ? " checked" : "";
        return `<div class="landlord-report-section" data-report-section="${escapeHtml(sectionId)}">
          <input id="landlordReportSection_${escapeHtml(sectionId)}" type="checkbox" data-section-id="${escapeHtml(sectionId)}"${checked} onchange="landlordReportSectionChanged()" />
          <label for="landlordReportSection_${escapeHtml(sectionId)}"><strong>${escapeHtml(`${index + 1}. ${section.title}`)}</strong><small>${escapeHtml(section.source || "Report entry")}</small></label>
          <div class="landlord-report-order" aria-label="Reorder ${escapeHtml(section.title)}">
            <button type="button" title="Move section up" aria-label="Move ${escapeHtml(section.title)} up" onclick="moveLandlordReportSection('${escapeHtml(sectionId)}',-1)"${index === 0 ? " disabled" : ""}>&uarr;</button>
            <button type="button" title="Move section down" aria-label="Move ${escapeHtml(section.title)} down" onclick="moveLandlordReportSection('${escapeHtml(sectionId)}',1)"${index === landlordReportSectionOrder.length - 1 ? " disabled" : ""}>&darr;</button>
          </div>
        </div>`;
    }).join("");
    renderLandlordReportSectionSelectors();
    updateLandlordReportSectionCount();
}

function renderLandlordReportSectionSelectors() {
    const definitions = Array.isArray(landlordReportContext?.sections) ? landlordReportContext.sections : [];
    const ordered = landlordReportSectionOrder.map((id) => definitions.find((section) => section.id === id)).filter(Boolean);
    const note = document.getElementById("landlordReportNoteSection");
    const activity = document.getElementById("landlordReportActivitySection");
    const noteSelected = note?.value || "";
    const activitySelected = activity?.value || "";
    const options = ordered.map((section) => `<option value="${escapeHtml(section.id)}">${escapeHtml(section.title)}</option>`).join("");
    if (note) {
        note.innerHTML = `<option value="">Choose section</option>${options}`;
        if (ordered.some((section) => section.id === noteSelected)) note.value = noteSelected;
    }
    if (activity) {
        activity.innerHTML = options;
        if (ordered.some((section) => section.id === activitySelected)) activity.value = activitySelected;
    }
    showLandlordReportSectionNote();
}

function landlordReportReadSelection() {
    landlordReportSelectedSections = new Set(
        Array.from(document.querySelectorAll("#landlordReportSectionList input[data-section-id]:checked"))
            .map((input) => input.dataset.sectionId)
            .filter(Boolean)
    );
    return landlordReportSectionOrder.filter((id) => landlordReportSelectedSections.has(id));
}

function landlordReportSelectionAfterAction(sectionIds, action) {
    return action === "clear" ? [] : Array.from(new Set(sectionIds || []));
}

function landlordReportSelectAll() {
    const all = landlordReportSelectionAfterAction(landlordReportSectionOrder, "select");
    landlordReportSelectedSections = new Set(all);
    renderLandlordReportSections();
    scheduleLandlordReportPreview();
}

function landlordReportClearAll() {
    landlordReportSelectedSections = new Set(landlordReportSelectionAfterAction(landlordReportSectionOrder, "clear"));
    renderLandlordReportSections();
    setLandlordReportMessage("Select at least one section before previewing or generating the report.", "info");
}

function landlordReportSectionChanged() {
    landlordReportReadSelection();
    updateLandlordReportSectionCount();
    scheduleLandlordReportPreview();
}

function updateLandlordReportSectionCount() {
    const selected = landlordReportReadSelection().length;
    const total = landlordReportSectionOrder.length;
    const counter = document.getElementById("landlordReportSectionCount");
    if (counter) counter.textContent = `${selected} of ${total} sections selected`;
}

function moveLandlordReportSection(sectionId, direction) {
    landlordReportReadSelection();
    const index = landlordReportSectionOrder.indexOf(sectionId);
    const target = index + Number(direction || 0);
    if (index < 0 || target < 0 || target >= landlordReportSectionOrder.length) return;
    [landlordReportSectionOrder[index], landlordReportSectionOrder[target]] = [landlordReportSectionOrder[target], landlordReportSectionOrder[index]];
    renderLandlordReportSections();
    scheduleLandlordReportPreview();
}

function showLandlordReportSectionNote() {
    const sectionId = document.getElementById("landlordReportNoteSection")?.value || "";
    const note = document.getElementById("landlordReportSectionNote");
    if (note) {
        note.disabled = !sectionId;
        note.value = sectionId ? (landlordReportSectionNotes[sectionId] || "") : "";
    }
}

function updateLandlordReportSectionNote() {
    const sectionId = document.getElementById("landlordReportNoteSection")?.value || "";
    if (!sectionId) return;
    const value = document.getElementById("landlordReportSectionNote")?.value || "";
    if (value.trim()) landlordReportSectionNotes[sectionId] = value;
    else delete landlordReportSectionNotes[sectionId];
    scheduleLandlordReportPreview();
}

function renderLandlordReportPhotos(resetSelection = false) {
    const photos = Array.isArray(landlordReportContext?.available_photos) ? landlordReportContext.available_photos : [];
    const availableIds = new Set(photos.map((photo) => Number(photo.attachment_id)));
    if (resetSelection) landlordReportSelectedPhotoIds = new Set(availableIds);
    else landlordReportSelectedPhotoIds = new Set([...landlordReportSelectedPhotoIds].filter((id) => availableIds.has(Number(id))));
    const container = document.getElementById("landlordReportPhotoList");
    const hero = document.getElementById("landlordReportHeroPhoto");
    if (container) {
        container.innerHTML = photos.length
            ? photos.map((photo) => {
                const id = Number(photo.attachment_id);
                return `<label class="landlord-report-photo"><input type="checkbox" data-report-photo-id="${id}"${landlordReportSelectedPhotoIds.has(id) ? " checked" : ""} onchange="landlordReportPhotoSelectionChanged()" /><span><strong>${escapeHtml(photo.caption || photo.filename || "Property photo")}</strong><br/><span class="muted">${escapeHtml(photo.date || "Date not recorded")}</span></span></label>`;
            }).join("")
            : '<div class="landlord-report-empty" style="grid-column:1/-1">No supported maintenance photos were recorded for this period.</div>';
    }
    if (hero) {
        const previous = hero.value;
        hero.innerHTML = '<option value="">No cover image</option>' + photos.map((photo) => `<option value="${Number(photo.attachment_id)}">${escapeHtml(photo.caption || photo.filename || "Property photo")}</option>`).join("");
        if (photos.some((photo) => String(photo.attachment_id) === previous)) hero.value = previous;
    }
}

function landlordReportPhotoSelectionChanged() {
    landlordReportSelectedPhotoIds = new Set(
        Array.from(document.querySelectorAll("#landlordReportPhotoList input[data-report-photo-id]:checked"))
            .map((input) => Number(input.dataset.reportPhotoId))
            .filter(Number.isFinite)
    );
    const hero = document.getElementById("landlordReportHeroPhoto");
    if (hero?.value && !landlordReportSelectedPhotoIds.has(Number(hero.value))) hero.value = "";
    scheduleLandlordReportPreview();
}

function landlordReportStatusLabel(value) {
    const labels = {
        completed: "Completed",
        in_progress: "In progress",
        awaiting_landlord_approval: "Awaiting landlord approval",
        scheduled: "Scheduled",
    };
    return labels[value] || String(value || "In progress").replaceAll("_", " ");
}

function renderLandlordReportDetailPicker() {
    const picker = document.getElementById("landlordReportDetailPicker");
    const container = document.getElementById("landlordReportDetailOverrides");
    const selected = Object.keys(landlordReportDetailValues);
    if (picker) {
        const available = LANDLORD_REPORT_DETAIL_FIELDS.filter((label) => !selected.includes(label));
        picker.innerHTML = available.length
            ? available.map((label) => `<option value="${escapeHtml(label)}">${escapeHtml(label)}</option>`).join("")
            : '<option value="">All available fields added</option>';
        picker.disabled = !available.length;
    }
    if (!container) return;
    container.innerHTML = selected.length ? selected.map((label) => `<div class="field"><label class="label">${escapeHtml(label)}</label><div class="row"><input style="flex:1" data-report-detail="${escapeHtml(label)}" value="${escapeHtml(landlordReportDetailValues[label] || "")}" oninput="updateLandlordReportDetail(this)" /><button class="btn danger" type="button" onclick="removeLandlordReportDetail('${escapeHtml(label)}')">Remove</button></div></div>`).join("") : '<div class="landlord-report-empty wide">No custom PDF fields added.</div>';
}

function addLandlordReportDetail() {
    const label = document.getElementById("landlordReportDetailPicker")?.value || "";
    if (!label) return;
    landlordReportDetailValues[label] = "";
    renderLandlordReportDetailPicker();
    document.querySelector(`[data-report-detail="${CSS.escape(label)}"]`)?.focus();
}

function updateLandlordReportDetail(input) {
    const label = input?.dataset?.reportDetail;
    if (!label) return;
    landlordReportDetailValues[label] = input.value || "";
    scheduleLandlordReportPreview();
}

function removeLandlordReportDetail(label) {
    delete landlordReportDetailValues[label];
    renderLandlordReportDetailPicker();
    scheduleLandlordReportPreview();
}

function renderLandlordReportActivities() {
    const container = document.getElementById("landlordReportActivityList");
    if (!container) return;
    const sections = Object.fromEntries((landlordReportContext?.sections || []).map((section) => [section.id, section.title]));
    if (!landlordReportActivities.length) {
        container.innerHTML = '<div class="landlord-report-empty">No report-only activities added.</div>';
        return;
    }
    container.innerHTML = landlordReportActivities.map((item) => {
        const attachments = [
            (item.photo_ids || []).length ? `${item.photo_ids.length} photo${item.photo_ids.length === 1 ? "" : "s"}` : "",
            (item.pdf_ids || []).length ? `${item.pdf_ids.length} PDF${item.pdf_ids.length === 1 ? "" : "s"}` : "",
        ].filter(Boolean).join(" | ");
        return `<article class="landlord-report-activity"><div><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(sections[item.section_id] || item.section_id)} | ${escapeHtml(item.date || "No date")} | ${escapeHtml(landlordReportStatusLabel(item.status))}${attachments ? ` | ${escapeHtml(attachments)}` : ""}${item.internal ? " | Internal" : ""}</p></div><div class="landlord-report-activity-actions"><button class="btn" type="button" onclick="editLandlordReportActivity('${escapeHtml(item.id)}')">Edit</button><button class="btn danger" type="button" onclick="removeLandlordReportActivity('${escapeHtml(item.id)}')">Remove</button></div></article>`;
    }).join("");
}

function resetLandlordReportActivityForm() {
    landlordReportEditingActivityId = "";
    ["landlordReportActivityTitle", "landlordReportActivityCategory", "landlordReportActivityContractor", "landlordReportActivityAmount", "landlordReportActivityDescription", "landlordReportActivityAction"].forEach((id) => {
        const element = document.getElementById(id);
        if (element) element.value = "";
    });
    const status = document.getElementById("landlordReportActivityStatus");
    const internal = document.getElementById("landlordReportActivityInternal");
    const dateInput = document.getElementById("landlordReportActivityDate");
    if (status) status.value = "completed";
    if (internal) internal.checked = false;
    if (dateInput) dateInput.value = landlordReportMelbourneDate();
    landlordReportPendingPhotoIds = [];
    landlordReportPendingPdfIds = [];
    landlordReportPendingFilesChanged = false;
    const photoInput = document.getElementById("landlordReportActivityPhotos");
    if (photoInput) photoInput.value = "";
    const photoStatus = document.getElementById("landlordReportActivityPhotoStatus");
    if (photoStatus) photoStatus.textContent = "No files selected. Up to 20 images (7.5MB each) and 5 PDFs (15MB each; 40MB total). PDF pages are appended to the downloaded report.";
    const save = document.getElementById("landlordReportActivitySave");
    if (save) save.textContent = "Add Activity";
    document.getElementById("landlordReportActivityCancel")?.classList.add("hidden");
}

async function landlordReportActivityPhotosChanged(files) {
    const selected = Array.from(files || []);
    const allowedImages = new Set(["image/jpeg", "image/png", "image/webp", "image/gif"]);
    const imageFiles = selected.filter((file) => allowedImages.has(file.type));
    const pdfFiles = selected.filter((file) => file.type === "application/pdf" || /\.pdf$/i.test(file.name || ""));
    if (imageFiles.length + pdfFiles.length !== selected.length) {
        setLandlordReportMessage("Use JPG, PNG, WebP, GIF or PDF files only.", "error");
        return;
    }
    if (imageFiles.length > 20 || pdfFiles.length > 5) {
        setLandlordReportMessage("Attach up to 20 images and 5 PDF reports to one activity.", "error");
        return;
    }
    if (imageFiles.some((file) => file.size > 7500000) || pdfFiles.some((file) => file.size > 15000000)) {
        setLandlordReportMessage("Images must be no larger than 7.5MB and PDFs no larger than 15MB each.", "error");
        return;
    }
    if (pdfFiles.reduce((total, file) => total + file.size, 0) > 40000000) {
        setLandlordReportMessage("Attached PDF reports cannot exceed 40MB in total.", "error");
        return;
    }
    const readFiles = (items, offset = 0, forcePdf = false) => Promise.all(items.map((file, index) => new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            const dataUrl = forcePdf
                ? String(reader.result || "").replace(/^data:[^;]*;base64,/, "data:application/pdf;base64,")
                : reader.result;
            resolve({ id: -(Date.now() + offset + index + Math.floor(Math.random() * 1000)), filename: file.name, caption: file.name.replace(/\.[^.]+$/, ""), data_url: dataUrl });
        };
        reader.onerror = reject;
        reader.readAsDataURL(file);
    })));
    const [photoAdditions, pdfAdditions] = await Promise.all([readFiles(imageFiles), readFiles(pdfFiles, 10000, true)]);
    if (landlordReportEditingActivityId) {
        const existing = landlordReportActivities.find((item) => item.id === landlordReportEditingActivityId);
        const replacedIds = new Set(existing?.photo_ids || []);
        const replacedPdfIds = new Set(existing?.pdf_ids || []);
        landlordReportOnlyPhotos = landlordReportOnlyPhotos.filter((photo) => !replacedIds.has(photo.id));
        landlordReportOnlyPdfs = landlordReportOnlyPdfs.filter((pdf) => !replacedPdfIds.has(pdf.id));
    }
    landlordReportOnlyPhotos.push(...photoAdditions);
    landlordReportOnlyPdfs.push(...pdfAdditions);
    landlordReportPendingPhotoIds = photoAdditions.map((photo) => photo.id);
    landlordReportPendingPdfIds = pdfAdditions.map((pdf) => pdf.id);
    landlordReportPendingFilesChanged = true;
    const status = document.getElementById("landlordReportActivityPhotoStatus");
    const summary = [
        photoAdditions.length ? `${photoAdditions.length} photo${photoAdditions.length === 1 ? "" : "s"}` : "",
        pdfAdditions.length ? `${pdfAdditions.length} PDF${pdfAdditions.length === 1 ? "" : "s"}` : "",
    ].filter(Boolean).join(" and ");
    if (status) status.textContent = `${summary || "No files"} ready for this activity.${pdfAdditions.length ? " PDF pages will be appended to the downloaded report." : ""}`;
}

function saveLandlordReportActivity() {
    const sectionId = document.getElementById("landlordReportActivitySection")?.value || "";
    const title = String(document.getElementById("landlordReportActivityTitle")?.value || "").trim();
    if (!sectionId || !title) {
        setLandlordReportMessage("Choose a report section and add an activity title.", "error");
        return;
    }
    const amountRaw = document.getElementById("landlordReportActivityAmount")?.value || "";
    const existing = landlordReportActivities.find((item) => item.id === landlordReportEditingActivityId);
    const item = {
        id: existing?.id || `report-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        section_id: sectionId,
        date: document.getElementById("landlordReportActivityDate")?.value || null,
        title,
        description: document.getElementById("landlordReportActivityDescription")?.value || null,
        status: document.getElementById("landlordReportActivityStatus")?.value || "in_progress",
        category: document.getElementById("landlordReportActivityCategory")?.value || null,
        contractor: document.getElementById("landlordReportActivityContractor")?.value || null,
        amount: amountRaw === "" ? null : Number(amountRaw),
        landlord_action: document.getElementById("landlordReportActivityAction")?.value || null,
        internal: !!document.getElementById("landlordReportActivityInternal")?.checked,
        photo_ids: landlordReportPendingFilesChanged ? [...landlordReportPendingPhotoIds] : (existing?.photo_ids || []),
        pdf_ids: landlordReportPendingFilesChanged ? [...landlordReportPendingPdfIds] : (existing?.pdf_ids || []),
    };
    if (existing) landlordReportActivities = landlordReportActivities.map((entry) => entry.id === existing.id ? item : entry);
    else landlordReportActivities.push(item);
    landlordReportSelectedSections.add(sectionId);
    renderLandlordReportSections();
    renderLandlordReportActivities();
    resetLandlordReportActivityForm();
    setLandlordReportMessage("Report activity added. Source property records were not changed.", "success");
    scheduleLandlordReportPreview();
}

function editLandlordReportActivity(activityId) {
    const item = landlordReportActivities.find((entry) => entry.id === activityId);
    if (!item) return;
    landlordReportEditingActivityId = item.id;
    const values = {
        landlordReportActivitySection: item.section_id,
        landlordReportActivityDate: item.date || "",
        landlordReportActivityTitle: item.title || "",
        landlordReportActivityStatus: item.status || "in_progress",
        landlordReportActivityCategory: item.category || "",
        landlordReportActivityContractor: item.contractor || "",
        landlordReportActivityAmount: item.amount ?? "",
        landlordReportActivityDescription: item.description || "",
        landlordReportActivityAction: item.landlord_action || "",
    };
    Object.entries(values).forEach(([id, value]) => {
        const element = document.getElementById(id);
        if (element) element.value = value;
    });
    const internal = document.getElementById("landlordReportActivityInternal");
    if (internal) internal.checked = !!item.internal;
    landlordReportPendingPhotoIds = [...(item.photo_ids || [])];
    landlordReportPendingPdfIds = [...(item.pdf_ids || [])];
    landlordReportPendingFilesChanged = false;
    const photoStatus = document.getElementById("landlordReportActivityPhotoStatus");
    if (photoStatus) {
        const existingFiles = [
            landlordReportPendingPhotoIds.length ? `${landlordReportPendingPhotoIds.length} photo(s)` : "",
            landlordReportPendingPdfIds.length ? `${landlordReportPendingPdfIds.length} PDF(s)` : "",
        ].filter(Boolean).join(" and ");
        photoStatus.textContent = existingFiles ? `${existingFiles} attached. Choose files to replace them.` : "No files selected. Up to 20 images (7.5MB each) and 5 PDFs (15MB each; 40MB total).";
    }
    const save = document.getElementById("landlordReportActivitySave");
    if (save) save.textContent = "Update Activity";
    document.getElementById("landlordReportActivityCancel")?.classList.remove("hidden");
    document.getElementById("landlordReportActivitiesHeading")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function removeLandlordReportActivity(activityId) {
    const item = landlordReportActivities.find((entry) => entry.id === activityId);
    if (!item || !confirm(`Remove "${item.title}" from this report?`)) return;
    landlordReportActivities = landlordReportActivities.filter((entry) => entry.id !== activityId);
    const usedPhotoIds = new Set(landlordReportActivities.flatMap((entry) => entry.photo_ids || []));
    const usedPdfIds = new Set(landlordReportActivities.flatMap((entry) => entry.pdf_ids || []));
    landlordReportOnlyPhotos = landlordReportOnlyPhotos.filter((photo) => usedPhotoIds.has(photo.id));
    landlordReportOnlyPdfs = landlordReportOnlyPdfs.filter((pdf) => usedPdfIds.has(pdf.id));
    if (landlordReportEditingActivityId === activityId) resetLandlordReportActivityForm();
    renderLandlordReportActivities();
    scheduleLandlordReportPreview();
}

async function loadLandlordReportContext() {
    const propertySearch = document.getElementById("landlordReportPropertySearch");
    const property = resolvePropertySearchValue(propertySearch?.value || "");
    const hidden = document.getElementById("landlordReportPropertyId");
    if (hidden) hidden.value = property ? String(property.id) : "";
    const period = landlordReportPeriod();
    if (!property || !period.startDate || !period.endDate) return;
    if (period.endDate < period.startDate) {
        setLandlordReportMessage("The reporting end date must be on or after the start date.", "error");
        return;
    }
    const requestId = ++landlordReportContextRequest;
    setLandlordReportMessage("Loading linked property records...", "info");
    setLandlordReportPreviewPlaceholder("Loading property records", "Maintenance, rent, lease, compliance, tenancy, and supporting photo records are being prepared.");
    try {
        const query = new URLSearchParams({
            property_id: String(property.id),
            start_date: period.startDate,
            end_date: period.endDate,
        });
        const response = await apiFetch(`/landlord-reports/context?${query}`);
        if (!response.ok) throw new Error(await extractErrorMessage(response));
        const context = await response.json();
        if (requestId !== landlordReportContextRequest) return;
        const propertyChanged = landlordReportPropertyKey !== String(context.property?.id || property.id);
        landlordReportContext = context;
        landlordReportPropertyKey = String(context.property?.id || property.id);
        if (propertyChanged) {
            landlordReportActivities = [];
            landlordReportSectionNotes = {};
            landlordReportSectionOrder = (context.sections || []).map((section) => section.id);
            landlordReportSelectedSections = new Set(context.default_sections || []);
            landlordReportOnlyPhotos = [];
            landlordReportPendingPhotoIds = [];
            landlordReportOnlyPdfs = [];
            landlordReportPendingPdfIds = [];
            landlordReportPendingFilesChanged = false;
            landlordReportDetailValues = {};
            renderLandlordReportDetailPicker();
            resetLandlordReportActivityForm();
        }
        const landlord = document.getElementById("landlordReportLandlordName");
        if (landlord && (propertyChanged || !landlord.value.trim())) landlord.value = context.suggested_landlord_name || "";
        const currentManager = document.getElementById("landlordReportManager")?.value || "";
        landlordReportManagerOptions(propertyChanged ? context.suggested_property_manager_id : currentManager || context.suggested_property_manager_id);
        renderLandlordReportSourceSummary();
        renderLandlordReportSections();
        renderLandlordReportPhotos(propertyChanged);
        renderLandlordReportActivities();
        document.getElementById("landlordReportSaveDefaults")?.classList.toggle("hidden", !context.can_manage_defaults);
        const limitations = document.getElementById("landlordReportLimitations");
        if (limitations) {
            limitations.classList.toggle("hidden", !(context.data_limitations || []).length);
            limitations.innerHTML = `<strong>Data coverage</strong><br/>${(context.data_limitations || []).map(escapeHtml).join("<br/>")}`;
        }
        setLandlordReportMessage("", "");
        await previewLandlordReport();
    } catch (error) {
        if (requestId !== landlordReportContextRequest) return;
        landlordReportContext = null;
        setLandlordReportMessage(error.message || "The property report data could not be loaded.", "error");
        setLandlordReportPreviewPlaceholder("Report data unavailable", "Check the property and reporting period, then try again.");
    }
}

function buildLandlordReportPayload(showErrors = true) {
    const propertyId = Number(document.getElementById("landlordReportPropertyId")?.value || 0);
    const period = landlordReportPeriod();
    const selectedSections = landlordReportReadSelection();
    const preparedDate = document.getElementById("landlordReportPreparedDate")?.value || "";
    if (!propertyId || !period.startDate || !period.endDate || !preparedDate) {
        if (showErrors) setLandlordReportMessage("Select a managed property, reporting period, and prepared date.", "error");
        return null;
    }
    if (period.endDate < period.startDate) {
        if (showErrors) setLandlordReportMessage("The reporting end date must be on or after the start date.", "error");
        return null;
    }
    if (!selectedSections.length) {
        if (showErrors) setLandlordReportMessage("Select at least one report section.", "info");
        return null;
    }
    const selectedNotes = Object.fromEntries(Object.entries(landlordReportSectionNotes).filter(([sectionId, value]) => selectedSections.includes(sectionId) && String(value || "").trim()));
    const detailOverrides = Object.fromEntries(Object.entries(landlordReportDetailValues)
        .map(([label, value]) => [label, String(value || "").trim()]).filter(([, value]) => value));
    landlordReportPhotoSelectionChangedSilently();
    return {
        property_id: propertyId,
        start_date: period.startDate,
        end_date: period.endDate,
        prepared_date: preparedDate,
        landlord_name: document.getElementById("landlordReportLandlordName")?.value || null,
        property_manager_id: Number(document.getElementById("landlordReportManager")?.value || 0) || null,
        intro_message: document.getElementById("landlordReportIntro")?.value || null,
        overall_summary: document.getElementById("landlordReportOverallSummary")?.value || null,
        additional_notes: document.getElementById("landlordReportAdditionalNotes")?.value || null,
        include_no_activity: !!document.getElementById("landlordReportIncludeEmpty")?.checked,
        include_photos: !!document.getElementById("landlordReportIncludePhotos")?.checked,
        include_financial: !!document.getElementById("landlordReportIncludeFinancial")?.checked,
        include_internal_notes: !!document.getElementById("landlordReportIncludeInternal")?.checked,
        selected_sections: selectedSections,
        section_notes: selectedNotes,
        manual_activities: landlordReportActivities,
        photo_attachment_ids: [...landlordReportSelectedPhotoIds],
        hero_photo_id: Number(document.getElementById("landlordReportHeroPhoto")?.value || 0) || null,
        detail_overrides: detailOverrides,
        report_only_photos: landlordReportOnlyPhotos,
        report_only_pdfs: landlordReportOnlyPdfs,
    };
}

function landlordReportPhotoSelectionChangedSilently() {
    landlordReportSelectedPhotoIds = new Set(
        Array.from(document.querySelectorAll("#landlordReportPhotoList input[data-report-photo-id]:checked"))
            .map((input) => Number(input.dataset.reportPhotoId))
            .filter(Number.isFinite)
    );
}

function scheduleLandlordReportPreview() {
    if (!landlordReportContext || currentDashboardTab !== "landlord_reports") return;
    if (landlordReportPreviewTimer) clearTimeout(landlordReportPreviewTimer);
    landlordReportPreviewTimer = setTimeout(() => previewLandlordReport(), 480);
}

async function previewLandlordReport(showErrors = false) {
    const payload = buildLandlordReportPayload(showErrors);
    if (!payload) return;
    const requestId = ++landlordReportPreviewRequest;
    const meta = document.getElementById("landlordReportPreviewMeta");
    if (meta) meta.textContent = "Updating preview...";
    try {
        const response = await apiFetch("/landlord-reports/preview", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        if (!response.ok) throw new Error(await extractErrorMessage(response));
        const result = await response.json();
        if (requestId !== landlordReportPreviewRequest) return;
        const preview = document.getElementById("landlordReportPreview");
        if (preview) preview.innerHTML = result.html || "";
        const excluded = Array.isArray(result.excluded_empty_sections) ? result.excluded_empty_sections.length : 0;
        if (meta) meta.textContent = `${result.included_sections?.length || 0} sections included${excluded ? `, ${excluded} empty excluded` : ""}`;
        setLandlordReportMessage("", "");
    } catch (error) {
        if (requestId !== landlordReportPreviewRequest) return;
        if (meta) meta.textContent = "Preview could not be updated.";
        setLandlordReportMessage(error.message || "The report preview could not be created.", "error");
    }
}

async function downloadLandlordReport() {
    const payload = buildLandlordReportPayload(true);
    if (!payload) return;
    const button = document.getElementById("landlordReportDownloadBtn");
    const original = button?.textContent || "Generate PDF";
    if (button) {
        button.disabled = true;
        button.textContent = "Generating...";
    }
    setLandlordReportMessage("Generating the final A4 PDF...", "info");
    try {
        const response = await apiFetch("/landlord-reports/pdf", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        if (!response.ok) throw new Error(await extractErrorMessage(response));
        const blob = await response.blob();
        const disposition = response.headers.get("Content-Disposition") || "";
        const filenameMatch = /filename="?([^";]+)"?/i.exec(disposition);
        const fallback = `Monthly-Property-Report_${payload.start_date}_${payload.end_date}.pdf`;
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = filenameMatch?.[1] || fallback;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 30000);
        savedLandlordReportsLoaded = false;
        setLandlordReportMessage("PDF generated, downloaded, and saved in Saved Reports.", "success");
        if (button) button.textContent = "Regenerate PDF";
    } catch (error) {
        setLandlordReportMessage(error.message || "The PDF could not be generated. Please review the report and try again.", "error");
        if (button) button.textContent = original;
    } finally {
        if (button) button.disabled = false;
    }
}

async function saveLandlordReportDefaults() {
    const selectedSections = landlordReportReadSelection();
    if (!selectedSections.length) {
        setLandlordReportMessage("Select at least one section before saving report defaults.", "info");
        return;
    }
    try {
        const response = await apiFetch("/landlord-reports/defaults", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ selected_sections: selectedSections }),
        });
        if (!response.ok) throw new Error(await extractErrorMessage(response));
        setLandlordReportMessage("Default report sections saved for future reports.", "success");
    } catch (error) {
        setLandlordReportMessage(error.message || "Report defaults could not be saved.", "error");
    }
}

const LEASE_RENEWAL_STATUSES = [
    "NOT_STARTED",
    "PREPARING_RENEWAL",
    "SENT_TO_OWNER",
    "OWNER_SIGNED",
    "SENT_TO_TENANT",
    "TENANT_SIGNED",
    "PARTIALLY_SIGNED",
    "FULLY_SIGNED",
    "PERIODIC_CONFIRMED",
    "TENANT_VACATING",
    "ADVERTISED",
    "ON_HOLD",
    "COMPLETED",
];

function leaseRenewalStatusLabel(status) {
    const labels = {
        NOT_STARTED: "Not Started",
        PREPARING_RENEWAL: "Preparing Renewal",
        SENT_TO_OWNER: "Sent To Owner",
        OWNER_SIGNED: "Owner Signed",
        SENT_TO_TENANT: "Sent To Tenant",
        TENANT_SIGNED: "Tenant Signed",
        PARTIALLY_SIGNED: "Partially Signed",
        FULLY_SIGNED: "Fully Signed",
        PERIODIC_CONFIRMED: "Periodic Confirmed",
        TENANT_VACATING: "Tenant Vacating",
        ADVERTISED: "Advertised",
        ON_HOLD: "On Hold",
        COMPLETED: "Completed",
    };
    return labels[String(status || "NOT_STARTED").toUpperCase()] || String(status || "").replaceAll("_", " ");
}

function leaseRenewalStatusClass(status) {
    const key = String(status || "").toUpperCase();
    if (["FULLY_SIGNED", "PERIODIC_CONFIRMED", "COMPLETED"].includes(key)) return "done";
    if (["SENT_TO_OWNER", "SENT_TO_TENANT", "PARTIALLY_SIGNED"].includes(key)) return "wait";
    if (["TENANT_VACATING", "ADVERTISED", "ON_HOLD"].includes(key)) return "stop";
    if (["PREPARING_RENEWAL", "OWNER_SIGNED", "TENANT_SIGNED"].includes(key)) return "action";
    return "";
}

function leaseRenewalStatusChip(status) {
    return `<span class="maintenance-status ${leaseRenewalStatusClass(status)}">${escapeHtml(leaseRenewalStatusLabel(status))}</span>`;
}

function leaseRenewalStateChip(state) {
    const key = String(state || "ACTIVE").toUpperCase();
    if (key === "COMPLETED") return `<span class="compliance-status current">Completed</span>`;
    if (key === "OVERDUE") return `<span class="compliance-status overdue">Overdue</span>`;
    if (key === "DUE_SOON") return `<span class="compliance-status due-soon">Due Soon</span>`;
    if (key === "MISSING_DETAILS") return `<span class="compliance-status missing">Missing Details</span>`;
    return `<span class="compliance-status action">Active</span>`;
}

function leaseRenewalMoney(value) {
    const amount = Number(value || 0);
    if (!(amount > 0)) return "-";
    return amount.toLocaleString(undefined, { style: "currency", currency: "AUD" });
}

function leaseRenewalDatePayload(id) {
    return isoDateOrNull(document.getElementById(id)?.value || "");
}

function renderLeaseRenewalAssigneeOptions(selectedId = "") {
    const ids = ["leaseRenewalAssignee", "leaseRenewalAssignedFilter"];
    ids.forEach((id) => {
        const sel = document.getElementById(id);
        if (!sel) return;
        const current = id === "leaseRenewalAssignee" && selectedId ? String(selectedId) : sel.value || "";
        const first = id === "leaseRenewalAssignedFilter" ? "All staff" : "Unassigned";
        sel.innerHTML = [
            `<option value="">${first}</option>`,
            ...assignableUsers.map((u) => {
                const value = String(u.id);
                return `<option value="${escapeHtml(value)}" ${value === current ? "selected" : ""}>${escapeHtml(staffOptionLabel(u))}</option>`;
            }),
        ].join("");
        sel.value = current;
    });
}

function updateLeaseRenewalPropertySelection() {
    const search = document.getElementById("leaseRenewalPropertySearch");
    const hidden = document.getElementById("leaseRenewalPropertyId");
    if (!search || !hidden) return null;
    const match = resolvePropertySearchValue(search.value);
    hidden.value = match ? String(match.id) : "";
    renderLeaseRenewalPropertyContacts(match);
    return match;
}

function renderLeaseRenewalPropertyContacts(property) {
    const target = document.getElementById("leaseRenewalPropertyContacts");
    if (!target) return;
    if (!property) {
        target.innerHTML = `<div class="small muted">Select a property to preview owner and tenant contacts.</div>`;
        return;
    }
    const owner = property.primary_owner || propertyPrimaryContact(property.owners);
    const tenant = property.primary_tenant || propertyPrimaryContact(property.tenants);
    target.innerHTML = `
      <div class="maintenance-meta-grid">
        <div><span>Owner</span>${escapeHtml(owner.name || "-")}<br>${escapeHtml(owner.email || owner.phone || "")}</div>
        <div><span>Tenant</span>${escapeHtml(tenant.name || "-")}<br>${escapeHtml(tenant.email || tenant.phone || "")}</div>
        <div><span>Tenancy Status</span>${escapeHtml(property.tenancy_status || "-")}</div>
      </div>
    `;
}

function updateLeaseRenewalDueFromLeaseEnd() {
    const end = document.getElementById("leaseRenewalCurrentEnd")?.value || "";
    const due = document.getElementById("leaseRenewalDueDate");
    if (end && due && !due.value) due.value = end;
}

function resetLeaseRenewalForm() {
    selectedLeaseRenewalId = null;
    [
        "leaseRenewalRecordId",
        "leaseRenewalPropertyId",
        "leaseRenewalPropertySearch",
        "leaseRenewalCurrentStart",
        "leaseRenewalCurrentEnd",
        "leaseRenewalDueDate",
        "leaseRenewalSentDate",
        "leaseRenewalResentDate",
        "leaseRenewalProposedStart",
        "leaseRenewalProposedEnd",
        "leaseRenewalCurrentRent",
        "leaseRenewalProposedRent",
        "leaseRenewalRentIncreaseDate",
        "leaseRenewalOwnerSigned",
        "leaseRenewalTenantSigned",
        "leaseRenewalFollowUp",
        "leaseRenewalNotes",
    ].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.value = "";
    });
    const status = document.getElementById("leaseRenewalStatus");
    const term = document.getElementById("leaseRenewalProposedTerm");
    if (status) status.value = "NOT_STARTED";
    if (term) term.value = "12 months";
    renderLeaseRenewalAssigneeOptions("");
    const assignee = document.getElementById("leaseRenewalAssignee");
    if (assignee) assignee.value = "";
    renderLeaseRenewalPropertyContacts(null);
    renderLeaseRenewalDetail(null);
    const title = document.getElementById("leaseRenewalFormTitle");
    if (title) title.textContent = "Track Renewal";
}

function fillLeaseRenewalForm(row) {
    if (!row) return;
    selectedLeaseRenewalId = row.id;
    const set = (id, value = "") => {
        const el = document.getElementById(id);
        if (el) el.value = value ?? "";
    };
    set("leaseRenewalRecordId", row.id);
    set("leaseRenewalPropertyId", row.property_id);
    set("leaseRenewalPropertySearch", row.property_label || row.property_address || "");
    set("leaseRenewalStatus", row.status || "NOT_STARTED");
    set("leaseRenewalCurrentStart", dateInputValue(row.current_lease_start));
    set("leaseRenewalCurrentEnd", dateInputValue(row.current_lease_end));
    set("leaseRenewalDueDate", dateInputValue(row.renewal_due_date));
    set("leaseRenewalSentDate", dateInputValue(row.lease_sent_date));
    set("leaseRenewalResentDate", dateInputValue(row.last_resent_date));
    set("leaseRenewalProposedStart", dateInputValue(row.proposed_lease_start));
    set("leaseRenewalProposedEnd", dateInputValue(row.proposed_lease_end));
    set("leaseRenewalProposedTerm", row.proposed_term || "");
    set("leaseRenewalCurrentRent", row.current_rent || "");
    set("leaseRenewalProposedRent", row.proposed_rent || "");
    set("leaseRenewalRentIncreaseDate", dateInputValue(row.rent_increase_date));
    set("leaseRenewalOwnerSigned", dateInputValue(row.owner_signed_date));
    set("leaseRenewalTenantSigned", dateInputValue(row.tenant_signed_date));
    set("leaseRenewalFollowUp", dateInputValue(row.follow_up_date));
    set("leaseRenewalNotes", row.notes || "");
    renderLeaseRenewalAssigneeOptions(row.assigned_user_id || "");
    renderLeaseRenewalPropertyContacts(propertyOptionsCache.find((p) => Number(p.id) === Number(row.property_id)) || row);
    const title = document.getElementById("leaseRenewalFormTitle");
    if (title) title.textContent = `Edit Renewal #${row.id}`;
}

function leaseRenewalPayload() {
    const selectedProperty = updateLeaseRenewalPropertySelection();
    const currentRent = document.getElementById("leaseRenewalCurrentRent")?.value || "";
    const proposedRent = document.getElementById("leaseRenewalProposedRent")?.value || "";
    return {
        property_id: selectedProperty ? Number(selectedProperty.id) : Number(document.getElementById("leaseRenewalPropertyId")?.value || 0),
        status: document.getElementById("leaseRenewalStatus")?.value || "NOT_STARTED",
        current_lease_start: leaseRenewalDatePayload("leaseRenewalCurrentStart"),
        current_lease_end: leaseRenewalDatePayload("leaseRenewalCurrentEnd"),
        renewal_due_date: leaseRenewalDatePayload("leaseRenewalDueDate"),
        lease_sent_date: leaseRenewalDatePayload("leaseRenewalSentDate"),
        last_resent_date: leaseRenewalDatePayload("leaseRenewalResentDate"),
        proposed_lease_start: leaseRenewalDatePayload("leaseRenewalProposedStart"),
        proposed_lease_end: leaseRenewalDatePayload("leaseRenewalProposedEnd"),
        proposed_term: String(document.getElementById("leaseRenewalProposedTerm")?.value || "").trim(),
        current_rent: currentRent ? Number(currentRent) : null,
        proposed_rent: proposedRent ? Number(proposedRent) : null,
        rent_increase_date: leaseRenewalDatePayload("leaseRenewalRentIncreaseDate"),
        owner_signed_date: leaseRenewalDatePayload("leaseRenewalOwnerSigned"),
        tenant_signed_date: leaseRenewalDatePayload("leaseRenewalTenantSigned"),
        follow_up_date: leaseRenewalDatePayload("leaseRenewalFollowUp"),
        assigned_user_id: document.getElementById("leaseRenewalAssignee")?.value ? Number(document.getElementById("leaseRenewalAssignee").value) : null,
        notes: String(document.getElementById("leaseRenewalNotes")?.value || "").trim(),
    };
}

async function saveLeaseRenewalRecord() {
    const payload = leaseRenewalPayload();
    if (!payload.property_id) {
        alert("Search and select a property from the property list first.");
        return;
    }
    const id = document.getElementById("leaseRenewalRecordId")?.value || "";
    const r = await apiFetch(id ? `/lease-renewals/records/${id}` : "/lease-renewals/records", {
        method: id ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    if (!r.ok) {
        alert(`Failed to save lease renewal: ${await extractErrorMessage(r)}`);
        return;
    }
    const row = await r.json();
    selectedLeaseRenewalId = row.id;
    leaseRenewalRecordsCache[row.id] = row;
    fillLeaseRenewalForm(row);
    renderLeaseRenewalDetail(row);
    leaseRenewalsLoadedOnce = false;
    await loadLeaseRenewals(currentLeaseRenewalPage || 1);
    await loadNotifications();
}

function renderLeaseRenewalSummary(summary = {}) {
    const set = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = String(value || 0);
    };
    set("leaseKpiDue30", summary.due_next_30);
    set("leaseKpiDue60", summary.due_next_60);
    set("leaseKpiOverdue", summary.overdue);
    set("leaseKpiAwaitingOwner", summary.awaiting_owner);
    set("leaseKpiAwaitingTenant", summary.awaiting_tenant);
    set("leaseKpiSigned", summary.fully_signed);
    set("leaseKpiPeriodic", summary.periodic_confirmed);
    set("leaseKpiMissing", summary.missing_details);
}

function renderLeaseRenewalAttention(items = []) {
    const target = document.getElementById("leaseRenewalAttentionList");
    if (!target) return;
    if (!items.length) {
        target.innerHTML = `<div class="ticket-empty"><strong>No urgent lease renewal items</strong><div class="small muted" style="margin-top:6px">Overdue, due-soon, missing-detail, and stale follow-up renewals will appear here.</div></div>`;
        return;
    }
    target.innerHTML = items.map((row) => `
      <button class="maintenance-order-card" type="button" onclick="openLeaseRenewalRecord(${row.id})">
        <div class="row space">
          <h4>${escapeHtml(row.property_label || row.property_address || "Lease renewal")}</h4>
          <div class="row" style="gap:6px;justify-content:flex-end">${leaseRenewalStateChip(row.state)}${leaseRenewalStatusChip(row.status)}</div>
        </div>
        <p>Renewal due ${escapeHtml(formatDateShort(row.renewal_due_date))} - Follow-up ${escapeHtml(formatDateShort(row.follow_up_date))}</p>
        <p>${escapeHtml(row.assigned_user_name ? `Assigned to ${row.assigned_user_name}` : "Unassigned")}</p>
      </button>
    `).join("");
}

function getLeaseRenewalFilters() {
    return {
        query: String(document.getElementById("leaseRenewalSearchBox")?.value || "").trim(),
        status: String(document.getElementById("leaseRenewalStatusFilter")?.value || "").trim(),
        window: String(document.getElementById("leaseRenewalWindowFilter")?.value || "").trim(),
        assigned: String(document.getElementById("leaseRenewalAssignedFilter")?.value || "").trim(),
    };
}

async function loadLeaseRenewals(page = null) {
    if (page !== null) currentLeaseRenewalPage = page;
    const p = currentLeaseRenewalPage || 1;
    const { query, status, window: dueWindow, assigned } = getLeaseRenewalFilters();
    const url = new URL("/lease-renewals/records", window.location.origin);
    url.searchParams.set("page", String(p));
    url.searchParams.set("page_size", "25");
    if (query) url.searchParams.set("query", query);
    if (status) url.searchParams.set("status", status);
    if (dueWindow) url.searchParams.set("window", dueWindow);
    if (assigned) url.searchParams.set("assigned_user_id", assigned);

    const body = document.getElementById("leaseRenewalReportBody");
    if (body) body.innerHTML = `<tr><td colspan="11" class="muted">Loading lease renewals...</td></tr>`;
    await loadAssignableUsers();
    renderLeaseRenewalAssigneeOptions();
    const [summaryResp, recordsResp] = await Promise.all([
        apiFetch("/lease-renewals/summary"),
        apiFetch(url.toString()),
    ]);
    if (!summaryResp.ok || !recordsResp.ok) {
        const errorText = !recordsResp.ok ? await extractErrorMessage(recordsResp) : await extractErrorMessage(summaryResp);
        if (body) body.innerHTML = `<tr><td colspan="11" class="muted">Failed to load lease renewals: ${escapeHtml(errorText)}</td></tr>`;
        return;
    }
    const summary = await summaryResp.json();
    const data = await recordsResp.json();
    const total = Number(data.total || 0);
    const size = Math.max(Number(data.page_size || 25), 1);
    const totalPages = Math.max(1, Math.ceil(total / size));
    if (total > 0 && Number(data.page || p) > totalPages) {
        return loadLeaseRenewals(totalPages);
    }
    leaseRenewalsLoadedOnce = true;
    renderLeaseRenewalSummary(summary);
    renderLeaseRenewalAttention(Array.isArray(summary.needs_attention) ? summary.needs_attention : []);
    renderLeaseRenewalReport(data);
}

function leaseRenewalPaginationItems(page, totalPages) {
    const total = Math.max(Number(totalPages || 1), 1);
    if (total <= 9) return Array.from({ length: total }, (_, index) => index + 1);
    const items = new Set([1, total, page - 1, page, page + 1]);
    if (page <= 3) [2, 3, 4].forEach((item) => items.add(item));
    if (page >= total - 2) [total - 3, total - 2, total - 1].forEach((item) => items.add(item));
    return Array.from(items)
        .filter((item) => item >= 1 && item <= total)
        .sort((a, b) => a - b);
}

function renderLeaseRenewalReport(data = {}) {
    const body = document.getElementById("leaseRenewalReportBody");
    const items = Array.isArray(data.items) ? data.items : [];
    leaseRenewalRecordsCache = {};
    items.forEach((row) => { leaseRenewalRecordsCache[row.id] = row; });
    if (body) {
        if (!items.length) {
            body.innerHTML = `<tr><td colspan="11" class="muted">No lease renewal records found for this filter.</td></tr>`;
        } else {
            body.innerHTML = items.map((row) => `
              <tr>
                <td>
                  <div style="font-weight:800">${escapeHtml(row.property_label || row.property_address || "")}</div>
                  <div class="small muted">${escapeHtml(row.tenancy_status || "-")}</div>
                </td>
                <td>${leaseRenewalStateChip(row.state)}<div style="margin-top:6px">${leaseRenewalStatusChip(row.status)}</div></td>
                <td>${escapeHtml(formatDateShort(row.current_lease_end))}</td>
                <td>${escapeHtml(formatDateShort(row.renewal_due_date))}</td>
                <td>${escapeHtml(formatDateShort(row.lease_sent_date))}</td>
                <td>${escapeHtml(formatDateShort(row.owner_signed_date))}</td>
                <td>${escapeHtml(formatDateShort(row.tenant_signed_date))}</td>
                <td>${escapeHtml(row.proposed_term || "-")}<div class="small muted">${escapeHtml(leaseRenewalMoney(row.current_rent))} to ${escapeHtml(leaseRenewalMoney(row.proposed_rent))}</div></td>
                <td>${escapeHtml(formatDateShort(row.follow_up_date))}</td>
                <td>${escapeHtml(row.assigned_user_name || "-")}</td>
                <td><button class="btn" onclick="openLeaseRenewalRecord(${row.id})">Open</button></td>
              </tr>
            `).join("");
        }
    }
    const pi = document.getElementById("leaseRenewalPageInfo");
    if (pi) {
        const total = Number(data.total || 0);
        const pageNow = Number(data.page || 1);
        const sizeNow = Number(data.page_size || 25);
        const pages = sizeNow > 0 ? Math.max(1, Math.ceil(total / sizeNow)) : 1;
        currentLeaseRenewalPage = Math.min(Math.max(pageNow, 1), pages);
        leaseRenewalTotalPages = pages;
        pi.textContent = `Page ${pageNow} of ${pages} - ${total} lease renewal records`;
    }
    const prev = document.getElementById("leaseRenewalBtnPrev");
    const next = document.getElementById("leaseRenewalBtnNext");
    const pageNumbers = document.getElementById("leaseRenewalPageNumbers");
    if (prev) prev.disabled = currentLeaseRenewalPage <= 1;
    if (next) next.disabled = currentLeaseRenewalPage >= leaseRenewalTotalPages || !Boolean(data.has_more);
    if (pageNumbers) {
        pageNumbers.innerHTML = leaseRenewalPaginationItems(currentLeaseRenewalPage, leaseRenewalTotalPages).map((item, index, arr) => {
            const gap = index > 0 && item - arr[index - 1] > 1 ? `<span class="activity-page-gap">...</span>` : "";
            const active = Number(item) === currentLeaseRenewalPage;
            return `${gap}<button class="activity-page-btn ${active ? "active" : ""}" type="button" onclick="goLeaseRenewalPage(${Number(item)})" ${active ? 'aria-current="page"' : ""}>${Number(item)}</button>`;
        }).join("");
    }
}

function renderLeaseRenewalDetail(row) {
    const title = document.getElementById("leaseRenewalDetailTitle");
    const sub = document.getElementById("leaseRenewalDetailSub");
    const status = document.getElementById("leaseRenewalDetailStatus");
    const body = document.getElementById("leaseRenewalDetailBody");
    if (!row) {
        if (title) title.textContent = "No renewal selected";
        if (sub) sub.textContent = "Create or open a lease renewal record.";
        if (status) status.textContent = "Ready";
        if (body) body.innerHTML = `<div class="ticket-empty"><strong>No lease renewal selected</strong><div class="small muted" style="margin-top:6px">Save a record or open one from the report to see details and history.</div></div>`;
        return;
    }
    if (title) title.textContent = row.property_label || row.property_address || `Lease Renewal #${row.id}`;
    if (sub) sub.textContent = `Renewal due ${formatDateShort(row.renewal_due_date)} - Updated ${formatDateShort(row.updated_at)}`;
    if (status) {
        status.className = `maintenance-status ${leaseRenewalStatusClass(row.status)}`;
        status.textContent = leaseRenewalStatusLabel(row.status);
    }
    if (!body) return;
    const owner = row.primary_owner || {};
    const tenant = row.primary_tenant || {};
    const events = Array.isArray(row.events) ? row.events : [];
    body.innerHTML = `
      <div class="maintenance-detail-panel">
        <h4>Renewal Snapshot</h4>
        <div class="maintenance-meta-grid">
          <div><span>Owner</span>${escapeHtml(owner.name || "-")}<br>${escapeHtml(owner.email || owner.phone || "")}</div>
          <div><span>Tenant</span>${escapeHtml(tenant.name || "-")}<br>${escapeHtml(tenant.email || tenant.phone || "")}</div>
          <div><span>Current Lease End</span>${escapeHtml(formatDateShort(row.current_lease_end))}</div>
          <div><span>Renewal Due</span>${escapeHtml(formatDateShort(row.renewal_due_date))}</div>
          <div><span>Lease Sent</span>${escapeHtml(formatDateShort(row.lease_sent_date))}</div>
          <div><span>Follow-up</span>${escapeHtml(formatDateShort(row.follow_up_date))}</div>
          <div><span>Proposed Term</span>${escapeHtml(row.proposed_term || "-")}</div>
          <div><span>Rent</span>${escapeHtml(leaseRenewalMoney(row.current_rent))} to ${escapeHtml(leaseRenewalMoney(row.proposed_rent))}</div>
          <div><span>Assigned</span>${escapeHtml(row.assigned_user_name || "-")}</div>
        </div>
        ${row.notes ? `<p class="small muted" style="margin-top:12px">${escapeHtml(row.notes)}</p>` : ""}
      </div>
      <div class="maintenance-detail-panel">
        <h4>Quick Status</h4>
        <div class="maintenance-action-strip">
          ${["SENT_TO_OWNER", "OWNER_SIGNED", "SENT_TO_TENANT", "PARTIALLY_SIGNED", "FULLY_SIGNED", "PERIODIC_CONFIRMED", "TENANT_VACATING", "ON_HOLD"].map((statusKey) => (
            `<button class="btn" onclick="setLeaseRenewalStatus(${row.id}, '${statusKey}')">${escapeHtml(leaseRenewalStatusLabel(statusKey))}</button>`
          )).join("")}
          <button class="btn danger" onclick="deleteLeaseRenewalRecord(${row.id})">Delete Record</button>
        </div>
      </div>
      <div class="maintenance-detail-panel">
        <h4>Notes & History</h4>
        <div class="field">
          <div class="label">Add Note</div>
          <textarea id="leaseRenewalNewNote" placeholder="Add follow-up notes, owner instructions, tenant response, or rent review context."></textarea>
        </div>
        <div class="row" style="margin-top:10px">
          <button class="btn primary" onclick="addLeaseRenewalNote(${row.id})">Add Note</button>
        </div>
        <div class="maintenance-events">
          ${events.length ? events.map((event) => `
            <div class="maintenance-event">
              <strong>${escapeHtml(String(event.event_type || "activity").replaceAll("_", " "))} - ${escapeHtml(formatDate(event.created_at))}</strong>
              <p>${escapeHtml(event.detail || "")}</p>
              <span>${escapeHtml(event.actor_name || "System")}</span>
            </div>
          `).join("") : `<div class="small muted">No history yet.</div>`}
        </div>
      </div>
    `;
}

async function openLeaseRenewalRecord(recordId) {
    const r = await apiFetch(`/lease-renewals/records/${recordId}`);
    if (!r.ok) {
        alert(`Failed to open lease renewal: ${await extractErrorMessage(r)}`);
        return;
    }
    const row = await r.json();
    selectedLeaseRenewalId = row.id;
    leaseRenewalRecordsCache[row.id] = row;
    switchLeaseRenewalView("track");
    fillLeaseRenewalForm(row);
    renderLeaseRenewalDetail(row);
}

async function setLeaseRenewalStatus(recordId, status) {
    const r = await apiFetch(`/lease-renewals/records/${recordId}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
    });
    if (!r.ok) {
        alert(`Failed to update status: ${await extractErrorMessage(r)}`);
        return;
    }
    const row = await r.json();
    fillLeaseRenewalForm(row);
    renderLeaseRenewalDetail(row);
    await loadLeaseRenewals(currentLeaseRenewalPage || 1);
    await loadNotifications();
}

async function addLeaseRenewalNote(recordId) {
    const note = String(document.getElementById("leaseRenewalNewNote")?.value || "").trim();
    if (!note) {
        alert("Enter a note first.");
        return;
    }
    const r = await apiFetch(`/lease-renewals/records/${recordId}/notes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note }),
    });
    if (!r.ok) {
        alert(`Failed to add note: ${await extractErrorMessage(r)}`);
        return;
    }
    const row = await r.json();
    fillLeaseRenewalForm(row);
    renderLeaseRenewalDetail(row);
}

async function deleteLeaseRenewalRecord(recordId) {
    const row = leaseRenewalRecordsCache[recordId] || {};
    if (!confirm(`Delete lease renewal record for ${row.property_label || row.property_address || "this property"}?`)) return;
    const r = await apiFetch(`/lease-renewals/records/${recordId}`, { method: "DELETE" });
    if (!r.ok) {
        alert(`Failed to delete lease renewal: ${await extractErrorMessage(r)}`);
        return;
    }
    resetLeaseRenewalForm();
    await loadLeaseRenewals(1);
    await loadNotifications();
}

function prevLeaseRenewalPage() {
    goLeaseRenewalPage(currentLeaseRenewalPage - 1);
}

function nextLeaseRenewalPage() {
    goLeaseRenewalPage(currentLeaseRenewalPage + 1);
}

function goLeaseRenewalPage(page) {
    const nextPage = Math.min(Math.max(Number(page || 1), 1), Math.max(Number(leaseRenewalTotalPages || 1), 1));
    if (nextPage === currentLeaseRenewalPage) return;
    loadLeaseRenewals(nextPage);
}

function switchLeaseRenewalView(view = "dashboard") {
    const nextView = ["dashboard", "track", "report"].includes(view) ? view : "dashboard";
    leaseRenewalViewMode = nextView;
    document.querySelectorAll("[data-lease-renewal-view]").forEach((btn) => {
        btn.classList.toggle("active", btn.getAttribute("data-lease-renewal-view") === nextView);
    });
    const dashboard = document.getElementById("leaseRenewalDashboardView");
    const track = document.getElementById("leaseRenewalTrackView");
    const report = document.getElementById("leaseRenewalReportView");
    if (dashboard) dashboard.classList.toggle("hidden", nextView !== "dashboard");
    if (track) track.classList.toggle("hidden", nextView !== "track");
    if (report) report.classList.toggle("hidden", nextView !== "report");
    if (nextView === "track") {
        refreshPropertyOptions();
        renderLeaseRenewalAssigneeOptions(selectedLeaseRenewalId ? document.getElementById("leaseRenewalAssignee")?.value || "" : "");
    }
    if (nextView === "dashboard" || nextView === "report") {
        loadLeaseRenewals(nextView === "report" ? currentLeaseRenewalPage : 1);
    }
}

function updateCompliancePropertySelection() {
    const search = document.getElementById("compliancePropertySearch");
    const hidden = document.getElementById("compliancePropertyId");
    if (!search || !hidden) return null;
    const match = resolvePropertySearchValue(search.value);
    hidden.value = match ? String(match.id) : "";
    return match;
}

function renderComplianceProviderOptions() {
    const list = document.getElementById("complianceProviderOptions");
    if (!list) return;
    list.innerHTML = complianceProvidersCache
        .filter((provider) => provider && provider.is_active !== false)
        .map((provider) => `<option value="${escapeHtml(provider.name || "")}"></option>`)
        .join("");
}

function renderComplianceProviders() {
    const body = document.getElementById("complianceProvidersTableBody");
    const meta = document.getElementById("complianceProvidersMeta");
    if (!body) return;
    const query = String(document.getElementById("complianceProvidersSearch")?.value || "").trim().toLowerCase();
    const items = complianceProvidersCache.filter((provider) => {
        if (!query) return true;
        return [provider.name, provider.contact_name, provider.email, provider.phone, provider.notes]
            .some((value) => String(value || "").toLowerCase().includes(query));
    });
    if (meta) {
        const activeCount = complianceProvidersCache.filter((provider) => provider.is_active !== false).length;
        meta.textContent = `${activeCount} active provider${activeCount === 1 ? "" : "s"} - ${complianceProvidersCache.length} total saved`;
    }
    if (!items.length) {
        body.innerHTML = `<tr><td colspan="6" class="muted">No compliance providers found.</td></tr>`;
        return;
    }
    body.innerHTML = items.map((provider) => `
        <tr>
          <td>
            <div style="font-weight:800">${escapeHtml(provider.name || "-")}</div>
            <div class="small muted">${provider.is_active === false ? "Inactive" : "Active provider"}</div>
          </td>
          <td>${escapeHtml(provider.contact_name || "-")}</td>
          <td>${escapeHtml(provider.email || "-")}</td>
          <td>${escapeHtml(provider.phone || "-")}</td>
          <td class="small">${escapeHtml(provider.notes || "-")}</td>
          <td>
            <div class="row">
              <button class="btn" onclick="editComplianceProvider(${provider.id})">Edit</button>
              <button class="btn danger" onclick="deleteComplianceProvider(${provider.id})">Deactivate</button>
            </div>
          </td>
        </tr>
    `).join("");
}

async function loadComplianceProviders(force = false) {
    if (complianceProvidersLoadedOnce && !force) {
        renderComplianceProviderOptions();
        renderComplianceProviders();
        return;
    }
    const r = await apiFetch("/compliance/providers?include_inactive=true");
    if (!r.ok) {
        const body = document.getElementById("complianceProvidersTableBody");
        if (body) body.innerHTML = `<tr><td colspan="6" class="muted">Failed to load providers: ${escapeHtml(await extractErrorMessage(r))}</td></tr>`;
        return;
    }
    const data = await r.json();
    complianceProvidersCache = Array.isArray(data.items) ? data.items : [];
    complianceProvidersLoadedOnce = true;
    renderComplianceProviderOptions();
    renderComplianceProviders();
}

function resetComplianceProviderForm() {
    editingComplianceProviderId = null;
    const title = document.getElementById("complianceProviderFormTitle");
    if (title) title.textContent = "Add Provider";
    ["complianceProviderName", "complianceProviderContact", "complianceProviderEmail", "complianceProviderPhone", "complianceProviderNotes"].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.value = "";
    });
    const active = document.getElementById("complianceProviderActive");
    if (active) active.checked = true;
}

function complianceProviderPayload() {
    return {
        name: String(document.getElementById("complianceProviderName")?.value || "").trim(),
        contact_name: String(document.getElementById("complianceProviderContact")?.value || "").trim() || null,
        email: String(document.getElementById("complianceProviderEmail")?.value || "").trim() || null,
        phone: String(document.getElementById("complianceProviderPhone")?.value || "").trim() || null,
        notes: String(document.getElementById("complianceProviderNotes")?.value || "").trim() || null,
        is_active: Boolean(document.getElementById("complianceProviderActive")?.checked),
    };
}

function editComplianceProvider(providerId) {
    const provider = complianceProvidersCache.find((item) => Number(item.id) === Number(providerId));
    if (!provider) return;
    editingComplianceProviderId = provider.id;
    const title = document.getElementById("complianceProviderFormTitle");
    if (title) title.textContent = "Edit Provider";
    const set = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.value = value || "";
    };
    set("complianceProviderName", provider.name);
    set("complianceProviderContact", provider.contact_name);
    set("complianceProviderEmail", provider.email);
    set("complianceProviderPhone", provider.phone);
    set("complianceProviderNotes", provider.notes);
    const active = document.getElementById("complianceProviderActive");
    if (active) active.checked = provider.is_active !== false;
    document.getElementById("complianceProviderName")?.focus();
}

async function saveComplianceProvider() {
    const payload = complianceProviderPayload();
    if (!payload.name) {
        alert("Provider name is required.");
        return;
    }
    const url = editingComplianceProviderId
        ? `/compliance/providers/${editingComplianceProviderId}`
        : "/compliance/providers";
    const r = await apiFetch(url, {
        method: editingComplianceProviderId ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    if (!r.ok) {
        alert(`Failed to save provider: ${await extractErrorMessage(r)}`);
        return;
    }
    resetComplianceProviderForm();
    await loadComplianceProviders(true);
}

async function deleteComplianceProvider(providerId) {
    const provider = complianceProvidersCache.find((item) => Number(item.id) === Number(providerId));
    if (!confirm(`Deactivate ${provider?.name || "this provider"}? Existing compliance records will keep their provider text.`)) return;
    const r = await apiFetch(`/compliance/providers/${providerId}`, { method: "DELETE" });
    if (!r.ok) {
        alert(`Failed to deactivate provider: ${await extractErrorMessage(r)}`);
        return;
    }
    if (Number(editingComplianceProviderId) === Number(providerId)) resetComplianceProviderForm();
    await loadComplianceProviders(true);
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
    loadNotifications();
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
    loadComplianceProviders();
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
    loadNotifications();
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

function setTab(tab, shouldLoad = true) {
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

    const purgeButton = document.getElementById("purgeNoReplyNeededBtn");
    if (purgeButton) {
        const canPurge = canAccessPage("system");
        purgeButton.classList.toggle("hidden", tab !== "no_reply_needed" || !canPurge);
    }

    if (shouldLoad) loadTickets({ allowCache: true });
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

        warnIfSyncHitLimit(j);
        if (j && j.target_mailbox) {
            setMailboxSummary(j.target_mailbox);
        }

        setLastSyncSummary();

        invalidateTicketCache();
        await loadTickets();
        await loadNotifications();
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
        const startEl = document.getElementById("startDate") || document.getElementById("fromDate");
        const endEl = document.getElementById("endDate") || document.getElementById("toDate");
        const maxEl = document.getElementById("maxThreads") || document.getElementById("limit");
        const start = startEl ? (startEl.value || currentDateFilter.start || "") : (currentDateFilter.start || "");
        const end = endEl ? (endEl.value || currentDateFilter.end || "") : (currentDateFilter.end || "");
        const maxThreads = parseInt((maxEl && maxEl.value) ? maxEl.value : "200", 10);

        if (start || end) {
            currentDateFilter = { start, end };
        }

        const url = new URL("/sync/check-updates", window.location.origin);
        // Safety cap; frequent use should stay light.
        url.searchParams.set("max_threads", String(!Number.isNaN(maxThreads) && maxThreads > 0 ? maxThreads : 200));
        if (start) url.searchParams.set("fallback_start", start);
        if (currentMailbox) url.searchParams.set("mailbox", currentMailbox);

        const r = await apiFetch(url.toString(), { method: "POST" });
        const text = await r.text();
        if (!r.ok) {
            alert(`Check Updates failed (${r.status}):\n\n${text}`);
            return;
        }

        const j = JSON.parse(text);
        warnIfSyncHitLimit(j);

        setLastSyncSummary();

        invalidateTicketCache();
        await loadTickets();
        await loadNotifications();
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


// Manual category selection removed; staff assignment is handled per ticket.

function statusOptions(selected) {
    const opts = [
        ["PENDING", "Pending"],
        ["IN_PROGRESS", "In Progress"],
        ["RESPONDED", "Responded"],
        ["NO_REPLY_NEEDED", "Reply Not Needed"]
    ];
    return opts.map(([v, label]) => `<option value="${v}" ${v === selected ? "selected" : ""}>${label}</option>`).join("");
}

function assigneeOptions(selectedId, selectedLabel = "") {
    const selected = selectedId == null ? "" : String(selectedId);
    const base = [`<option value="" ${selected ? "" : "selected"}>Unassigned</option>`];
    const hasSelected = assignableUsers.some((u) => String(u.id) === selected);
    if (selected && !hasSelected) {
        base.push(`<option value="${escapeHtml(selected)}" selected>${escapeHtml(selectedLabel || "Assigned staff")}</option>`);
    }
    assignableUsers.forEach((u) => {
        const value = String(u.id);
        const label = staffOptionLabel(u);
        base.push(`<option value="${escapeHtml(value)}" ${value === selected ? "selected" : ""}>${escapeHtml(label)}</option>`);
    });
    return base.join("");
}

function assigneeBadge(t) {
    if (!t.assignee_user_id) return `<span class="badge assigned">Unassigned</span>`;
    return `<span class="badge assigned">Assigned: ${escapeHtml(t.assignee_name || t.assignee_email || "Staff")}</span>`;
}

async function loadAssignableUsers() {
    try {
        const r = await apiFetch("/tickets/assignees");
        if (!r.ok) return;
        const data = await r.json();
        assignableUsers = Array.isArray(data.items) ? data.items : [];
        renderMaintenanceAssigneeOptions();
    } catch {
        assignableUsers = [];
        renderMaintenanceAssigneeOptions();
    }
}

async function refreshAssigneeViews() {
    await loadAssignableUsers();
    invalidateTicketCache();
    await loadTickets();
    await loadNotifications();
}

async function updateAssignee(threadId, value, control = null) {
    const previous = control ? (control.getAttribute("data-current-assignee") || "") : "";
    if (control) control.disabled = true;
    try {
        const assigneeId = value ? Number(value) : null;
        const r = await apiFetch(`/tickets/${encodeURIComponent(threadId)}/assignee`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ assignee_user_id: Number.isFinite(assigneeId) ? assigneeId : null }),
        });
        if (!r.ok) {
            if (control) control.value = previous;
            alert(`Assignment failed: ${await extractErrorMessage(r)}`);
            return;
        }
        const result = await r.json();
        if (control) control.setAttribute("data-current-assignee", value || "");
        if (currentViewerThreadId === threadId && currentViewerTicket) {
            currentViewerTicket = {
                ...currentViewerTicket,
                assignee_user_id: result.assignee_user_id,
                assignee_name: result.assignee_name,
                assignee_email: result.assignee_email,
                assignee_avatar_url: result.assignee_avatar_url,
                status: result.status || currentViewerTicket.status,
                is_not_replied: result.is_not_replied,
            };
            renderViewerWorkflow(currentViewerTicket);
        }
        invalidateTicketCache();
        await loadTickets();
        await loadNotifications();
    } catch (e) {
        if (control) control.value = previous;
        alert("Assignment failed: " + e);
    } finally {
        if (control) control.disabled = false;
    }
}

function ticketInitial(t) {
    const value = String(t.from_name || t.from_email || "?").trim();
    return escapeHtml((value[0] || "?").toUpperCase());
}

function ticketStatusLabel(status) {
    const key = String(status || "").toUpperCase();
    if (key === "IN_PROGRESS") return "In Progress";
    if (key === "RESPONDED") return "Responded";
    if (key === "NO_REPLY_NEEDED") return "No Reply Needed";
    return "Pending";
}

function ticketStatusClass(status) {
    const key = String(status || "").toUpperCase();
    if (key === "IN_PROGRESS") return "progress";
    if (key === "RESPONDED") return "responded";
    if (key === "NO_REPLY_NEEDED") return "closed";
    return "pending";
}

function viewerAssigneeLabel(ticket) {
    if (!ticket || !ticket.assignee_user_id) return "Unassigned";
    return ticket.assignee_name || ticket.assignee_email || "Assigned staff";
}

function setViewerWorkflowBusy(isBusy) {
    document.querySelectorAll("[data-viewer-control]").forEach((control) => {
        control.disabled = Boolean(isBusy);
    });
}

function renderViewerWorkflow(ticket) {
    const wrap = document.getElementById("viewerWorkflow");
    if (!wrap) return;
    if (!ticket || !ticket.thread_id) {
        wrap.classList.add("hidden");
        wrap.innerHTML = "";
        currentViewerThreadId = null;
        currentViewerTicket = null;
        return;
    }

    currentViewerThreadId = ticket.thread_id;
    currentViewerTicket = ticket;
    const assignee = viewerAssigneeLabel(ticket);
    wrap.classList.remove("hidden");
    wrap.innerHTML = `
      <div class="viewer-workflow-summary">
        <span class="ticket-status-pill ${ticketStatusClass(ticket.status)}">${escapeHtml(ticketStatusLabel(ticket.status))}</span>
        <span class="viewer-assignee-chip">${escapeHtml(assignee)}</span>
      </div>
      <div class="viewer-workflow-controls">
        <label class="viewer-control">
          <span>Status</span>
          <select data-viewer-control data-viewer-status-select data-current-status="${escapeHtml(ticket.status || "")}">
            ${statusOptions(ticket.status)}
          </select>
        </label>
        <label class="viewer-control">
          <span>Assign</span>
          <select data-viewer-control data-viewer-assignee-select data-current-assignee="${escapeHtml(String(ticket.assignee_user_id || ""))}">
            ${assigneeOptions(ticket.assignee_user_id, assignee)}
          </select>
        </label>
        <button class="btn" type="button" data-viewer-control data-viewer-status="IN_PROGRESS">Start</button>
        <button class="btn" type="button" data-viewer-control data-viewer-status="RESPONDED">Responded</button>
        <button class="btn" type="button" data-viewer-control data-viewer-status="NO_REPLY_NEEDED">No Reply Needed</button>
      </div>
    `;

    const statusSelect = wrap.querySelector("[data-viewer-status-select]");
    if (statusSelect) {
        statusSelect.addEventListener("change", () => updateViewerStatus(ticket.thread_id, statusSelect.value, statusSelect));
    }
    const assigneeSelect = wrap.querySelector("[data-viewer-assignee-select]");
    if (assigneeSelect) {
        assigneeSelect.addEventListener("change", () => updateViewerAssignee(ticket.thread_id, assigneeSelect.value, assigneeSelect));
    }
    wrap.querySelectorAll("[data-viewer-status]").forEach((btn) => {
        btn.addEventListener("click", () => updateViewerStatus(ticket.thread_id, btn.getAttribute("data-viewer-status") || "PENDING"));
    });
}

async function updateViewerStatus(threadId, status, control = null) {
    const previous = control ? (control.getAttribute("data-current-status") || currentViewerTicket?.status || "") : currentViewerTicket?.status || "";
    if (control) control.disabled = true;
    setViewerWorkflowBusy(true);
    try {
        const r = await apiFetch(`/tickets/${encodeURIComponent(threadId)}/status`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status }),
        });
        const text = await r.text();
        if (!r.ok) {
            if (control && previous) control.value = previous;
            alert(`Status update failed (${r.status}):\n\n${text}`);
            return;
        }
        let result = {};
        try { result = JSON.parse(text); } catch { result = {}; }
        currentViewerTicket = {
            ...(currentViewerTicket || {}),
            thread_id: threadId,
            status: result.status || status,
            is_not_replied: result.is_not_replied,
        };
        renderViewerWorkflow(currentViewerTicket);
        invalidateTicketCache();
        await loadTickets();
        await loadNotifications();
    } catch (e) {
        if (control && previous) control.value = previous;
        alert("Status update failed: " + e);
    } finally {
        setViewerWorkflowBusy(false);
    }
}

async function updateViewerAssignee(threadId, value, control = null) {
    const previous = control ? (control.getAttribute("data-current-assignee") || currentViewerTicket?.assignee_user_id || "") : currentViewerTicket?.assignee_user_id || "";
    if (control) control.disabled = true;
    setViewerWorkflowBusy(true);
    try {
        const assigneeId = value ? Number(value) : null;
        const r = await apiFetch(`/tickets/${encodeURIComponent(threadId)}/assignee`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ assignee_user_id: Number.isFinite(assigneeId) ? assigneeId : null }),
        });
        if (!r.ok) {
            if (control) control.value = previous ? String(previous) : "";
            alert(`Assignment failed: ${await extractErrorMessage(r)}`);
            return;
        }
        const result = await r.json();
        currentViewerTicket = {
            ...(currentViewerTicket || {}),
            thread_id: threadId,
            assignee_user_id: result.assignee_user_id,
            assignee_name: result.assignee_name,
            assignee_email: result.assignee_email,
            assignee_avatar_url: result.assignee_avatar_url,
            status: result.status || currentViewerTicket?.status,
            is_not_replied: result.is_not_replied,
        };
        renderViewerWorkflow(currentViewerTicket);
        invalidateTicketCache();
        await loadTickets();
        await loadNotifications();
    } catch (e) {
        if (control) control.value = previous ? String(previous) : "";
        alert("Assignment failed: " + e);
    } finally {
        setViewerWorkflowBusy(false);
    }
}

function renderTicket(t) {
    const useGoodUi = !!document.querySelector(".page") && !document.querySelector(".tabbtn");

    const due = t.due_at ? `Due: ${formatDate(t.due_at)}` : "Due: -";
    const last = t.last_message_at ? `Last: ${formatDate(t.last_message_at)}` : "Last: -";

    // Legacy manual category removed from UI; prefer AI category.
    const cat = "";
    const assignee = t.assignee_name || t.assignee_email || "";

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
        card.setAttribute("data-ticket-card", "1");
        card.setAttribute("data-thread-id", t.thread_id || "");

        const priority = String(t.priority || "medium").toLowerCase();
        const priBadge = priority === "high"
            ? `<span class="badge priority">High</span>`
            : (priority === "low" ? `<span class="badge">Low</span>` : `<span class="badge">Medium</span>`);

        const unreadBadge = t.is_unread ? `<span class="badge unread">Unread</span>` : "";
        const nrBadge = t.is_not_replied ? `<span class="badge priority">Not Replied</span>` : "";
        const slaBadge = slaOverdue ? `<span class="badge overdue">Overdue</span>` : "";
        const senderName = t.from_name || t.from_email || "(unknown sender)";
        const senderEmail = t.from_email || "";
        const lastShort = t.last_message_at ? formatDateShort(t.last_message_at) : "-";
        const statusPill = `<span class="ticket-status-pill ${ticketStatusClass(t.status)}">${ticketStatusLabel(t.status)}</span>`;

        card.innerHTML = `
          <div class="ticket-main">
            <div class="ticket-avatar">${ticketInitial(t)}</div>
            <div class="ticket-content">
              <div class="ticket-topline">
                <div class="ticket-sender">${escapeHtml(senderName)}</div>
                <div class="ticket-time">${escapeHtml(lastShort)}</div>
              </div>
              <h4>${escapeHtml(t.subject || "(no subject)")}</h4>
              <div class="from">${senderEmail ? escapeHtml(senderEmail) : "Unknown email address"}</div>
              <div class="snippet">${escapeHtml(t.snippet || "")}</div>

              <div class="badge-row">
                ${statusPill}
                ${priBadge}
                ${aiBadges(t)}
                ${cat ? `<span class="badge">${escapeHtml(cat)}</span>` : ``}
                ${assigneeBadge(t)}
                ${nrBadge}
                ${unreadBadge}
                ${slaBadge}
              </div>

              <div class="ticket-meta">
                <span>${escapeHtml(last)}</span>
                <span>${escapeHtml(due)}</span>
                <span>${escapeHtml(slaText)}</span>
              </div>
            </div>
          </div>

          <div class="ticket-right">
            <div class="ticket-actions">
              <button class="btn primary" onclick="openThread('${t.thread_id}')">Open</button>
              <button class="btn" onclick="openAckModal('${t.thread_id}')">Quick Reply</button>
              <button class="btn" onclick="openAiReplyModal('${t.thread_id}')">AI Draft</button>
            </div>

            <div class="ticket-controls">
              <div class="field">
                <div class="label">Status</div>
                <select data-current-status="${escapeHtml(t.status || "")}" onchange="updateStatus('${t.thread_id}', this.value, this)">
                  ${statusOptions(t.status)}
                </select>
              </div>
              <div class="ticket-assignment">
                <div class="label">Assigned To</div>
                <select data-current-assignee="${escapeHtml(String(t.assignee_user_id || ""))}" onchange="updateAssignee('${t.thread_id}', this.value, this)">
                  ${assigneeOptions(t.assignee_user_id, assignee)}
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
    card.setAttribute("data-ticket-card", "1");
    card.setAttribute("data-thread-id", t.thread_id || "");

    const catBadge = ""; // legacy manual category removed; use AI category badge instead
    const assigneeBadgeLegacy = `<span class="px-2 py-0.5 rounded-full text-xs bg-slate-50 text-slate-700 border">${assignee ? `Assigned: ${escapeHtml(assignee)}` : "Unassigned"}</span>`;

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
        ${assigneeBadgeLegacy}
        
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
        data-current-status="${escapeHtml(t.status || "")}"
        onchange="updateStatus('${t.thread_id}', this.value, this)">
        ${statusOptions(t.status)}
      </select>
      <label class="w-full text-xs text-slate-500">Assigned To</label>
      <select class="w-full px-3 py-2 rounded-lg border bg-white"
        data-current-assignee="${escapeHtml(String(t.assignee_user_id || ""))}"
        onchange="updateAssignee('${t.thread_id}', this.value, this)">
        ${assigneeOptions(t.assignee_user_id, assignee)}
      </select>
      <!-- Manual category removed; AI category is computed automatically -->
    </div>
  `;

    return card;
}

function ticketCacheKey() {
    return JSON.stringify({
        mailbox: currentMailbox || "",
        tab: currentTab,
        page: currentPage,
        pageSize,
        start: currentDateFilter.start || "",
        end: currentDateFilter.end || "",
        search: (currentSearch || "").trim(),
    });
}

function invalidateTicketCache() {
    ticketTabCache.clear();
}

function renderTicketListData(data) {
    const items = Array.isArray(data.items) ? data.items : [];
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
    setText("tabAssignedToMeCount", c.assigned_to_me ?? 0);

    const list = document.getElementById("ticketList");
    if (!list) return;
    list.innerHTML = "";
    items.forEach((ticket) => list.appendChild(renderTicket(ticket)));
    if (!items.length) {
        list.innerHTML = `<div class="ticket-empty"><strong>No tickets in this queue</strong><div class="small muted" style="margin-top:6px">Try another status tab or clear the search filter.</div></div>`;
    }
    renderPagination(data);
}

async function loadTickets({ allowCache = false } = {}) {
    const requestKey = ticketCacheKey();
    const cached = ticketTabCache.get(requestKey);
    if (allowCache && cached && (Date.now() - cached.savedAt) < TICKET_TAB_CACHE_MS) {
        renderTicketListData(cached.data);
        ticketsLoadedOnce = true;
        return cached.data;
    }

    if (ticketLoadController) ticketLoadController.abort();
    const controller = new AbortController();
    ticketLoadController = controller;
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

    try {
        const r = await apiFetch(url, { signal: controller.signal });
        if (!r.ok) throw new Error(await extractErrorMessage(r));
        const data = await r.json();
        ticketTabCache.set(requestKey, { savedAt: Date.now(), data });
        ticketsLoadedOnce = true;
        if (requestKey === ticketCacheKey()) renderTicketListData(data);
        return data;
    } catch (error) {
        if (error?.name === "AbortError") return null;
        const list = document.getElementById("ticketList");
        if (list && requestKey === ticketCacheKey()) {
            list.innerHTML = `<div class="ticket-empty"><strong>Email tickets could not be loaded</strong><div class="small muted" style="margin-top:6px">${escapeHtml(String(error?.message || error))}</div></div>`;
        }
        return null;
    } finally {
        if (ticketLoadController === controller) ticketLoadController = null;
    }
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
    if (info) info.textContent = `Page ${page} of ${totalPages} - ${total} tickets`;
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


function ticketBelongsToCurrentTab(status, isNotReplied) {
    const key = String(status || "").toUpperCase();
    if (currentTab === "all") return true;
    if (currentTab === "awaiting_reply") {
        return Boolean(isNotReplied) && key === "PENDING";
    }
    if (currentTab === "in_progress") return key === "IN_PROGRESS";
    if (currentTab === "responded") return key === "RESPONDED";
    if (currentTab === "no_reply_needed") return key === "NO_REPLY_NEEDED";
    return true;
}

async function updateStatus(threadId, status, control = null) {
    const previous = control ? (control.getAttribute("data-current-status") || control.value || "") : "";
    if (control) control.disabled = true;
    try {
        const r = await apiFetch(`/tickets/${encodeURIComponent(threadId)}/status`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status })
        });
        const text = await r.text();
        if (!r.ok) {
            if (control && previous) control.value = previous;
            alert(`Status update failed (${r.status}):\n\n${text}`);
            await loadTickets();
            await loadNotifications();
            return;
        }
        let result = {};
        try { result = JSON.parse(text); } catch { result = {}; }
        const nextStatus = result.status || status;
        if (control) {
            control.value = nextStatus;
            control.setAttribute("data-current-status", nextStatus);
        }
        if (currentViewerThreadId === threadId && currentViewerTicket) {
            currentViewerTicket = {
                ...currentViewerTicket,
                status: nextStatus,
                is_not_replied: result.is_not_replied,
            };
            renderViewerWorkflow(currentViewerTicket);
        }
        const card = control ? control.closest(".ticket, [data-ticket-card]") : null;
        if (card && !ticketBelongsToCurrentTab(nextStatus, result.is_not_replied)) {
            card.remove();
        }
        invalidateTicketCache();
        await loadTickets();
        await loadNotifications();
    } catch (e) {
        if (control && previous) control.value = previous;
        alert("Status update failed: " + e);
        await loadTickets();
        await loadNotifications();
    } finally {
        if (control) control.disabled = false;
    }
}

async function openThread(threadId) {
    const modal = document.getElementById("threadModal");
    const content = document.getElementById("threadContent");
    const gmailLink = document.getElementById("gmailLink");

    const viewerBackdrop = document.getElementById("viewerBackdrop");
    const viewerFrame = document.getElementById("viewerFrame");
    const viewerTitle = document.getElementById("viewerTitle");
    const viewerSubtitle = document.getElementById("viewerSubtitle");
    const viewerWorkflow = document.getElementById("viewerWorkflow");

    const useViewer = (!modal || !content) && viewerBackdrop && viewerFrame;
    currentViewerThreadId = threadId;
    currentViewerTicket = null;
    if (viewerWorkflow) {
        viewerWorkflow.classList.add("hidden");
        viewerWorkflow.innerHTML = "";
    }

    if (useViewer) {
        viewerBackdrop.classList.add("show");
        if (viewerTitle) viewerTitle.textContent = "Thread";
        if (viewerSubtitle) viewerSubtitle.textContent = "Loading conversation...";
        viewerFrame.srcdoc = `<div style="font-family:Segoe UI,system-ui,sans-serif; padding:24px; color:#334155">Loading conversation...</div>`;
    } else if (modal && content) {
        modal.classList.remove("hidden");
        content.innerHTML = `<div class="text-sm text-slate-600">Loading thread...</div>`;
    } else {
        alert("Thread viewer UI is missing from the page (threadModal/threadContent).");
        return;
    }

    const r = await apiFetch(`/threads/${encodeURIComponent(threadId)}`);
    const t = await r.text();
    if (!r.ok) {
        renderViewerWorkflow(null);
        if (useViewer) viewerFrame.srcdoc = `<pre style="white-space:pre-wrap; color:#b91c1c; padding:16px">${escapeHtml(t)}</pre>`;
        else content.innerHTML = `<pre class="text-xs text-red-700 whitespace-pre-wrap">${t}</pre>`;
        return;
    }

    const j = JSON.parse(t);
    currentViewerThreadId = j.thread_id || threadId;
    currentViewerTicket = j.ticket || null;
    if (currentViewerTicket && !assignableUsers.length) {
        await loadAssignableUsers();
    }
    renderViewerWorkflow(currentViewerTicket);
    const gmailThreadId = j.gmail_thread_id || "";
    const threadMailbox = j.mailbox || currentMailbox || "";
    if (gmailLink) {
        gmailLink.dataset.gmailThreadId = gmailThreadId;
        gmailLink.dataset.mailbox = threadMailbox;
        gmailLink.dataset.gmailUrl = j.gmail_url || j.gmail_thread_url || "";
        gmailLink.href = getGmailUrlForThread(threadMailbox, gmailThreadId, gmailLink.dataset.gmailUrl) || "#";
    }
    const gmailAccessBtn = document.getElementById("gmailAccessBtn");
    if (gmailAccessBtn) {
        gmailAccessBtn.style.display = needsDelegatedGmailUrl(threadMailbox) ? "inline-flex" : "none";
    }
    const messages = Array.isArray(j.messages) ? j.messages : [];
    const threadSubject = messages.find((m) => m.subject)?.subject || "Email conversation";
    const lastMessageDate = messages.length ? (messages[messages.length - 1].date || "") : "";
    if (viewerTitle) viewerTitle.textContent = threadSubject;
    if (viewerSubtitle) {
        const countText = `${messages.length} message${messages.length === 1 ? "" : "s"}`;
        viewerSubtitle.textContent = [countText, lastMessageDate].filter(Boolean).join(" - ");
    }

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
        return `<a class="attachment-chip" href="${url}" target="_blank" rel="noreferrer">
          <span>${label}</span>
          <strong>${escapeHtmlLocal(name)}</strong>
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
        const attachmentsHtml = atts.length ? `<div class="attachments">${atts.map(a => attachmentBadge(a, threadId, msgId)).join("")}</div>` : "";

        const htmlBlock = `
          <div class="message-body" data-mode="html">
            <iframe id="${iframeId}" class="message-frame"
              sandbox="allow-popups allow-forms allow-same-origin" referrerpolicy="no-referrer"></iframe>
          </div>
        `;

        const sender = escapeHtmlLocal(m.from || "(unknown sender)");
        const recipient = escapeHtmlLocal(m.to || "");
        const subject = escapeHtmlLocal(m.subject || "(no subject)");
        const date = escapeHtmlLocal(m.date || "");
        const avatarText = escapeHtmlLocal((m.from || "?").trim().slice(0, 1).toUpperCase() || "?");

        return `
        <article class="message-card" data-msg-card="1">
          <div class="message-head">
            <div class="sender-mark">${avatarText}</div>
            <div class="message-meta">
              <div class="message-from">${sender}</div>
              <div class="message-subject">${subject}</div>
              <div class="message-route">${recipient ? `To ${recipient}` : ""}</div>
              ${attachmentsHtml}
            </div>
            <div class="message-date">${date}</div>
          </div>
          ${htmlBlock}
        </article>
      `;
    };

    const threadHtml = `
      <!doctype html>
      <html>
      <head>
        <meta charset="utf-8" />
        <style>
          *{box-sizing:border-box}
          body{
            margin:0;
            font-family:"Segoe UI", Aptos, ui-sans-serif, system-ui, -apple-system, sans-serif;
            color:#101828;
            background:#eef3f8;
          }
          .thread-shell{padding:22px; max-width:1040px; margin:0 auto}
          .thread-summary{
            border:1px solid #d8e2ee;
            background:#fff;
            border-radius:18px;
            padding:18px 20px;
            box-shadow:0 12px 28px rgba(15,23,42,.08);
            margin-bottom:16px;
          }
          .thread-summary h1{margin:0; font-size:20px; line-height:1.25; letter-spacing:-.01em}
          .thread-summary p{margin:8px 0 0; color:#667085; font-size:13px}
          .message-card{
            border:1px solid #d8e2ee;
            border-radius:18px;
            background:#fff;
            box-shadow:0 10px 26px rgba(15,23,42,.07);
            margin-top:14px;
            overflow:hidden;
          }
          .message-head{
            display:grid;
            grid-template-columns:42px minmax(0, 1fr) auto;
            gap:12px;
            padding:16px;
            border-bottom:1px solid #edf1f6;
            background:linear-gradient(180deg,#ffffff 0%,#f8fafc 100%);
          }
          .sender-mark{
            width:42px;
            height:42px;
            border-radius:14px;
            display:flex;
            align-items:center;
            justify-content:center;
            background:#111827;
            color:#f9e7a7;
            font-weight:900;
          }
          .message-meta{min-width:0}
          .message-from{font-weight:900; font-size:14px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis}
          .message-subject{margin-top:3px; color:#344054; font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis}
          .message-route{margin-top:3px; color:#667085; font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis}
          .message-date{color:#667085; font-size:12px; white-space:nowrap; text-align:right; padding-top:2px}
          .message-body{padding:14px 16px 16px}
          .message-frame{
            width:100%;
            min-height:180px;
            height:260px;
            border:1px solid #e4e9f1;
            border-radius:14px;
            background:#fff;
            display:block;
          }
          .attachments{display:flex; flex-wrap:wrap; gap:8px; margin-top:10px}
          .attachment-chip{
            display:inline-flex;
            align-items:center;
            gap:8px;
            min-width:0;
            max-width:320px;
            padding:7px 9px;
            border:1px solid #d8e2ee;
            border-radius:999px;
            background:#fff;
            color:#344054;
            text-decoration:none;
            font-size:12px;
          }
          .attachment-chip span{
            padding:2px 7px;
            border-radius:999px;
            background:#f1f5f9;
            color:#475569;
            border:1px solid #e2e8f0;
            font-weight:900;
            flex:0 0 auto;
          }
          .attachment-chip strong{
            min-width:0;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
            font-weight:800;
          }
          @media (max-width:720px){
            .thread-shell{padding:12px}
            .message-head{grid-template-columns:38px minmax(0, 1fr); gap:10px}
            .message-date{grid-column:2; text-align:left; padding-top:0}
          }
        </style>
      </head>
      <body>
      <div class="thread-shell">
        <section class="thread-summary">
          <h1>${escapeHtmlLocal(threadSubject)}</h1>
          <p>${messages.length} message${messages.length === 1 ? "" : "s"}${lastMessageDate ? ` - Latest ${escapeHtmlLocal(lastMessageDate)}` : ""}</p>
        </section>
        ${messages.map((m, idx) => renderMessage(m, idx)).join("")}
      </div>
      </body>
      </html>
    `;

    const populateIframes = (rootDoc) => {
        messages.forEach((m, idx) => {
            const iframe = rootDoc.getElementById(`msg_iframe_${idx}`);
            if (!iframe) return;
            let html = rewriteCid(m.body_html || "", m.id);
            if (settings.proxyRemoteImages) {
                html = rewriteRemoteImagesToProxy(html);
            }
            iframe.onload = () => {
                try {
                    const doc = iframe.contentDocument || iframe.contentWindow?.document;
                    const height = Math.max(
                        doc?.body?.scrollHeight || 0,
                        doc?.documentElement?.scrollHeight || 0,
                        180
                    );
                    iframe.style.height = `${Math.min(height + 28, 900)}px`;
                } catch (e) { }
            };
            iframe.srcdoc = `<!doctype html><html><head><base target="_blank"><style>html,body{margin:0;padding:0;background:#fff}body{padding:16px;font-family:"Segoe UI",Arial,sans-serif;color:#101828;font-size:14px;line-height:1.5}img{max-width:100%;height:auto}</style></head><body>${html}</body></html>`;
        });
    };

    if (useViewer) {
        // Populate message iframes after the viewer frame loads its srcdoc.
        viewerFrame.onload = () => {
            try { populateIframes(viewerFrame.contentDocument); } catch (e) { }
        };
        viewerFrame.srcdoc = threadHtml;
    } else {
        content.innerHTML = threadHtml;
        populateIframes(document);
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
    currentViewerThreadId = null;
    currentViewerTicket = null;
    renderViewerWorkflow(null);
    const frame = document.getElementById("viewerFrame");
    if (frame) frame.srcdoc = "";
    const gmailLink = document.getElementById("gmailLink");
    if (gmailLink) {
        gmailLink.href = "#";
        gmailLink.dataset.gmailThreadId = "";
        gmailLink.dataset.mailbox = "";
        gmailLink.dataset.gmailUrl = "";
    }
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
        invalidateTicketCache();
        await loadTickets();
        await loadNotifications();
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
    invalidateTicketCache();
    await loadTickets();
    await loadNotifications();
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
    invalidateTicketCache();
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

function setModalStatus(elementId, message, type = "error") {
    const el = document.getElementById(elementId);
    if (!el) return;
    const text = String(message || "").trim();
    el.textContent = text;
    el.style.display = text ? "block" : "none";
    el.classList.toggle("success", type === "success");
}

function openForgotPasswordModal() {
    const modal = document.getElementById("forgotPasswordModal");
    const email = document.getElementById("forgotPasswordEmail");
    if (email) email.value = document.getElementById("loginEmail")?.value || "";
    setModalStatus("forgotPasswordStatus", "");
    if (modal) modal.classList.remove("hidden");
    setTimeout(() => email?.focus(), 50);
}

function closeForgotPasswordModal() {
    const modal = document.getElementById("forgotPasswordModal");
    if (modal) modal.classList.add("hidden");
}

async function submitForgotPassword() {
    const emailEl = document.getElementById("forgotPasswordEmail");
    const btn = document.getElementById("forgotPasswordBtn");
    const email = String(emailEl?.value || "").trim();
    if (!email) {
        setModalStatus("forgotPasswordStatus", "Enter your email address.");
        return;
    }
    const oldText = btn?.textContent || "Send Reset Link";
    if (btn) {
        btn.disabled = true;
        btn.textContent = "Sending...";
    }
    try {
        const r = await fetch("/user-auth/forgot-password", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email }),
        });
        if (!r.ok) {
            setModalStatus("forgotPasswordStatus", await extractErrorMessage(r));
            return;
        }
        const data = await r.json();
        setModalStatus("forgotPasswordStatus", data.message || "If that email is registered, a reset link has been sent.", "success");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = oldText;
        }
    }
}

function openResetPasswordModal(token) {
    const modal = document.getElementById("resetPasswordModal");
    const tokenEl = document.getElementById("resetPasswordToken");
    const pw = document.getElementById("resetPasswordNew");
    const confirm = document.getElementById("resetPasswordConfirm");
    if (tokenEl) tokenEl.value = token || "";
    if (pw) pw.value = "";
    if (confirm) confirm.value = "";
    setModalStatus("resetPasswordStatus", "");
    showLoginModal();
    if (modal) modal.classList.remove("hidden");
    setTimeout(() => pw?.focus(), 50);
}

function closeResetPasswordModal() {
    const modal = document.getElementById("resetPasswordModal");
    if (modal) modal.classList.add("hidden");
}

function clearResetTokenFromUrl() {
    try {
        const params = new URLSearchParams(window.location.search);
        if (!params.has("reset_token")) return;
        params.delete("reset_token");
        const next = `${window.location.pathname}${params.toString() ? `?${params.toString()}` : ""}${window.location.hash || ""}`;
        window.history.replaceState({}, "", next);
    } catch {
        // Ignore URL cleanup failures.
    }
}

async function submitResetPassword() {
    const token = String(document.getElementById("resetPasswordToken")?.value || "").trim();
    const password = document.getElementById("resetPasswordNew")?.value || "";
    const confirm = document.getElementById("resetPasswordConfirm")?.value || "";
    const btn = document.getElementById("resetPasswordBtn");
    if (!token) {
        setModalStatus("resetPasswordStatus", "Reset token is missing. Please request a new link.");
        return;
    }
    if (!password || password !== confirm) {
        setModalStatus("resetPasswordStatus", "Passwords do not match.");
        return;
    }
    const oldText = btn?.textContent || "Update Password";
    if (btn) {
        btn.disabled = true;
        btn.textContent = "Updating...";
    }
    try {
        const r = await fetch("/user-auth/reset-password", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token, new_password: password }),
        });
        if (!r.ok) {
            setModalStatus("resetPasswordStatus", await extractErrorMessage(r));
            return;
        }
        clearResetTokenFromUrl();
        setModalStatus("resetPasswordStatus", "Password updated. You can now log in.", "success");
        setTimeout(() => {
            closeResetPasswordModal();
            const loginPassword = document.getElementById("loginPassword");
            if (loginPassword) loginPassword.focus();
        }, 900);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = oldText;
        }
    }
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
    startInactivityGuard(false);
    await loadUsersCache();
    await loadCurrentPageAccess();

    // Legacy badge
    const badge = document.getElementById("userBadge");
    if (badge) badge.textContent = `Signed in as: ${currentUser.name} (${roleTitle(currentUser.role)})`;

    // Good UI pill
    const authText = document.getElementById("authText");
    if (authText) authText.textContent = `Signed in as ${currentUser.name} (${roleTitle(currentUser.role)})`;
    const accountName = document.getElementById("accountBarUserName");
    if (accountName) accountName.textContent = `${currentUser.name} (${roleTitle(currentUser.role)})`;
    const accountAvatar = document.getElementById("accountBarAvatar");
    if (accountAvatar) {
        accountAvatar.src = currentUser.avatar_url || "/static/logo.png";
    }
    const authDot = document.getElementById("authDot");
    if (authDot) {
        authDot.classList.add("green");
    }
    const systemBtn = document.getElementById("btnSystemUsers");
    if (systemBtn) systemBtn.style.display = canAccessPage("system") ? "flex" : "none";
    const portalSystemTile = document.getElementById("portalSystemTile");
    if (portalSystemTile) portalSystemTile.classList.toggle("hidden", !canAccessPage("system"));
    if (!canAccessPage(currentDashboardTab)) {
        switchDashboardTab(firstAccessiblePage());
    }

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
        recordStaffActivity(true);
        hideLoginModal();
        await ensureAuthenticated();
        await initMailboxes();
        ticketsLoadedOnce = false;
        invalidateTicketCache();
        await Promise.all([
            refreshGoogleStatus(),
            loadAssignableUsers(),
            loadNotifications(),
        ]);
        // Autopilot feature removed.
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = btnText || "Sign in";
        }
    }
}

function logout(message = "") {
    try {
        if (authToken) {
            apiFetch("/user-auth/logout", { method: "POST" }).catch(() => {});
        }
    } catch {
        // Logout should always clear local access even if audit recording fails.
    }
    authToken = "";
    currentUser = null;
    ticketsLoadedOnce = false;
    invalidateTicketCache();
    binduCurrentConversationId = null;
    binduHistoryLoaded = false;
    const binduMessages = document.getElementById("binduMessages");
    if (binduMessages) binduMessages.innerHTML = binduWelcome();
    closeBindu();
    stopInactivityGuard();
    localStorage.removeItem("agent_auth_token");
    localStorage.removeItem(STAFF_ACTIVITY_KEY);
    allowedPages = new Set(["portal"]);
    rolePagePermissions = {};
    teamLoadedOnce = false;
    activityLoadedOnce = false;
    mySpaceLoadedOnce = false;
    mySpaceViewMode = "workspace";
    timesheetDayCache = null;
    timesheetLoadedDate = "";
    timesheetCanReview = false;
    timesheetCanDeleteReports = false;
    timesheetReportsLoadedOnce = false;
    timesheetStaffCache = [];
    timesheetStaffLoadedOnce = false;
    leaseRenewalsLoadedOnce = false;
    currentLeaseRenewalPage = 1;
    leaseRenewalTotalPages = 1;
    selectedLeaseRenewalId = null;
    leaseRenewalRecordsCache = {};
    resetLandlordReportBuilder();
    applyPageVisibility();

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
    notificationItems = [];
    renderNotifications({ total: 0, items: [], categories: {} });
    resetLoginRecaptcha();
    showLoginModal();
    if (message) setLoginError(message);
}

window.addEventListener("load", async () => {
    setupLoginMotion();
    setupBindu();
    // Footer year
    const yearEl = document.getElementById("year");
    if (yearEl) yearEl.textContent = String(new Date().getFullYear());

    loadSettings();
    setLastSyncSummary();
    try {
        const params = new URLSearchParams(window.location.search);
        const resetToken = String(params.get("reset_token") || "").trim();
        if (resetToken) {
            authToken = "";
            localStorage.removeItem("agent_auth_token");
            openResetPasswordModal(resetToken);
            return;
        }
    } catch {
        // Continue normal login flow.
    }
    const ok = await ensureAuthenticated();
    if (!ok) return;

    document.addEventListener("click", (ev) => {
        const trigger = document.getElementById("accountMenuTrigger");
        const dd = document.getElementById("accountMenuDropdown");
        if (dd && trigger) {
            if (!dd.contains(ev.target) && !trigger.contains(ev.target)) {
                dd.classList.remove("show");
            }
        }
    });

    const maintenanceOrderModal = document.getElementById("maintenanceOrderModal");
    if (maintenanceOrderModal) {
        maintenanceOrderModal.addEventListener("click", (ev) => {
            if (ev.target === maintenanceOrderModal) closeMaintenanceOrderModal();
        });
    }
    document.addEventListener("keydown", (ev) => {
        const openModals = Array.from(document.querySelectorAll(".modal-backdrop:not(.hidden)"));
        const topModal = openModals[openModals.length - 1];
        if (ev.key === "Escape" && topModal?.id === "maintenanceOrderModal") {
            closeMaintenanceOrderModal();
        }
    });

    await initMailboxes();
    await Promise.all([
        refreshGoogleStatus(),
        loadAssignableUsers(),
        loadNotifications(),
    ]);

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
    const maintenancePropertySearch = document.getElementById("maintenancePropertySearch");
    if (maintenancePropertySearch) {
        maintenancePropertySearch.addEventListener("input", updateMaintenancePropertySelection);
        maintenancePropertySearch.addEventListener("change", updateMaintenancePropertySelection);
    }
    const leaseRenewalPropertySearch = document.getElementById("leaseRenewalPropertySearch");
    if (leaseRenewalPropertySearch) {
        leaseRenewalPropertySearch.addEventListener("input", updateLeaseRenewalPropertySelection);
        leaseRenewalPropertySearch.addEventListener("change", updateLeaseRenewalPropertySelection);
    }
    const leaseRenewalCurrentEnd = document.getElementById("leaseRenewalCurrentEnd");
    if (leaseRenewalCurrentEnd) {
        leaseRenewalCurrentEnd.addEventListener("change", updateLeaseRenewalDueFromLeaseEnd);
    }
    ["leaseRenewalWindowFilter", "leaseRenewalStatusFilter", "leaseRenewalAssignedFilter"].forEach((id) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.addEventListener("change", () => {
            if (currentDashboardTab === "lease_renewals") {
                loadLeaseRenewals(1);
            }
        });
    });
    const leaseRenewalSearch = document.getElementById("leaseRenewalSearchBox");
    if (leaseRenewalSearch) {
        let tmr = null;
        leaseRenewalSearch.addEventListener("input", () => {
            if (tmr) clearTimeout(tmr);
            tmr = setTimeout(() => {
                if (currentDashboardTab === "lease_renewals") {
                    loadLeaseRenewals(1);
                }
            }, 250);
        });
    }
    const maintenanceTradieCompany = document.getElementById("maintenanceTradieCompany");
    if (maintenanceTradieCompany) {
        maintenanceTradieCompany.addEventListener("change", () => applyMaintenanceTradieToOrder());
        maintenanceTradieCompany.addEventListener("blur", () => applyMaintenanceTradieToOrder());
    }
    const maintenanceStatusFilter = document.getElementById("maintenanceStatusFilter");
    if (maintenanceStatusFilter) {
        maintenanceStatusFilter.addEventListener("change", () => {
            if (currentDashboardTab === "maintenance") {
                currentMaintenancePage = 1;
                loadMaintenanceDashboard();
            }
        });
    }
    const maintenanceSearch = document.getElementById("maintenanceSearchBox");
    if (maintenanceSearch) {
        let tmr = null;
        maintenanceSearch.addEventListener("input", () => {
            if (tmr) clearTimeout(tmr);
            tmr = setTimeout(() => {
                if (currentDashboardTab === "maintenance") {
                    currentMaintenancePage = 1;
                    loadMaintenanceDashboard();
                }
            }, 250);
        });
    }
    const tenantRegistrationSearch = document.getElementById("tenantRegistrationSearch");
    if (tenantRegistrationSearch) {
        let tmr = null;
        tenantRegistrationSearch.addEventListener("input", () => {
            if (tmr) clearTimeout(tmr);
            tmr = setTimeout(() => {
                if (currentDashboardTab === "maintenance" && maintenanceViewMode === "tenants") {
                    loadTenantRegistrations();
                }
            }, 250);
        });
    }
    const tenantRegistrationStatus = document.getElementById("tenantRegistrationStatus");
    if (tenantRegistrationStatus) {
        tenantRegistrationStatus.addEventListener("change", () => {
            if (currentDashboardTab === "maintenance" && maintenanceViewMode === "tenants") {
                loadTenantRegistrations();
            }
        });
    }
    const tradieSearch = document.getElementById("tradieSearch");
    if (tradieSearch) {
        let tmr = null;
        tradieSearch.addEventListener("input", () => {
            if (tmr) clearTimeout(tmr);
            tmr = setTimeout(() => {
                if (currentDashboardTab === "maintenance" && maintenanceViewMode === "tradies") {
                    loadMaintenanceTradies();
                }
            }, 250);
        });
    }
    const tradieStatus = document.getElementById("tradieStatus");
    if (tradieStatus) {
        tradieStatus.addEventListener("change", () => {
            if (currentDashboardTab === "maintenance" && maintenanceViewMode === "tradies") {
                loadMaintenanceTradies();
            }
        });
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
    const complianceProvidersSearch = document.getElementById("complianceProvidersSearch");
    if (complianceProvidersSearch) {
        complianceProvidersSearch.addEventListener("input", renderComplianceProviders);
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
            if (currentDashboardTab === "activity") {
                currentActivityPage = 1;
                loadActivityLog();
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
    switchDashboardTab("portal");
    updateSyncContextUI();

    // Prepare the inbox controls without loading tickets until Email Manager opens.
    setTab(currentTab, false);
});
