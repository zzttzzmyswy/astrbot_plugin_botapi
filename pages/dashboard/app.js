const bridge = window.AstrBotPluginPage;
let accounts = [];

function log(...a) { console.log("[BotAPI]", ...a); }

function withTimeout(p, ms, msg) {
  return Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error(msg)), ms))]);
}

function showStatus(msg) {
  document.getElementById("account-list").innerHTML =
    `<tr class="empty-row"><td colspan="8">${esc(msg)}</td></tr>`;
}

// ── 页内对话框（iframe sandbox 无 allow-modals，原生 confirm/prompt/alert 全被拦）──

function confirmDialog(message) {
  return new Promise((resolve) => {
    document.getElementById("confirm-msg").textContent = message;
    const modal = document.getElementById("modal-confirm");
    const ok = document.getElementById("btn-confirm-ok");
    const cancel = document.getElementById("btn-confirm-cancel");
    const done = (val) => { modal.classList.add("hidden"); ok.onclick = null; cancel.onclick = null; resolve(val); };
    modal.classList.remove("hidden");
    ok.onclick = () => done(true);
    cancel.onclick = () => done(false);
  });
}

function promptDialog(current) {
  return new Promise((resolve) => {
    const input = document.getElementById("input-nick-new");
    input.value = current || "";
    const modal = document.getElementById("modal-nickname");
    const ok = document.getElementById("btn-nick-save");
    const cancel = document.getElementById("btn-nick-cancel");
    const done = (val) => { modal.classList.add("hidden"); ok.onclick = null; cancel.onclick = null; resolve(val); };
    modal.classList.remove("hidden");
    setTimeout(() => input.focus(), 0);
    ok.onclick = () => done(input.value.trim());
    cancel.onclick = () => done(null);
    input.onkeydown = (e) => { if (e.key === "Enter") done(input.value.trim()); if (e.key === "Escape") done(null); };
  });
}

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 3500);
}

// ── 主流程 ──

async function init() {
  setupToolbar();
  setupChat();
  wireDelegation();
  if (!bridge) { showStatus("Bridge 未就绪（请在 WebUI 插件页内打开此页面）"); log("no bridge"); return; }
  try {
    await withTimeout(bridge.ready(), 8000, "Bridge 握手超时（是否在 WebUI 插件页内打开？）");
    log("bridge ready");
  } catch (e) { showStatus(e.message); log("bridge fail", e); return; }
  await refresh();
}

async function refresh() {
  const btn = document.getElementById("btn-refresh");
  const orig = btn.textContent;
  btn.disabled = true; btn.textContent = "刷新中…";
  try {
    const stats = await bridge.apiGet("stats");
    log("stats ok", stats);
    accounts = stats.per_account || [];
    document.getElementById("total-accounts").textContent = stats.total_accounts ?? "-";
    document.getElementById("online-count").textContent = stats.total_online ?? "-";
    document.getElementById("total-messages").textContent = stats.total_messages ?? "-";
    renderAccounts();
  } catch (err) {
    showStatus("加载失败: " + (err?.message || err));
    log("refresh fail", err);
  } finally {
    btn.disabled = false; btn.textContent = orig;
  }
}

function renderAccounts() {
  const tbody = document.getElementById("account-list");
  if (!accounts.length) { tbody.innerHTML = '<tr class="empty-row"><td colspan="8">暂无账户</td></tr>'; return; }
  tbody.innerHTML = accounts.map(a => `
    <tr>
      <td>${esc(a.nickname || "-")}</td>
      <td><code>${esc(a.token_preview)}</code></td>
      <td><code>${esc(a.token_hash)}</code></td>
      <td><span class="badge ${a.online ? 'badge-online' : 'badge-offline'}">${a.online ? '在线' : '离线'}</span></td>
      <td>${a.message_count ?? 0}</td>
      <td>${a.sse_connections || 0}</td>
      <td>${a.last_active ? new Date(a.last_active * 1000).toLocaleString('zh-CN') : '-'}</td>
      <td>
        <button class="btn btn-sm btn-primary" data-action="chat" data-hash="${esc(a.token_hash)}" data-nickname="${esc(a.nickname || "")}">对话</button>
        <button class="btn btn-sm btn-secondary" data-action="export" data-hash="${esc(a.token_hash)}" data-nickname="${esc(a.nickname || "")}">导出</button>
        <button class="btn btn-sm btn-secondary" data-action="nickname" data-hash="${esc(a.token_hash)}" data-nickname="${esc(a.nickname || "")}">改名</button>
        <button class="btn btn-sm btn-danger" data-action="delete" data-hash="${esc(a.token_hash)}">删除</button>
      </td>
    </tr>`).join('');
}

function wireDelegation() {
  document.getElementById("account-list").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    const hash = btn.dataset.hash;
    const nick = btn.dataset.nickname || "";
    if (action === "nickname") await setNickname(hash, nick);
    else if (action === "delete") await deleteAccount(hash);
    else if (action === "export") openExport(hash, nick);
    else if (action === "chat") openChat(hash, nick);
  });
}

function setupToolbar() {
  document.getElementById("btn-add").addEventListener("click", () =>
    document.getElementById("modal-add").classList.remove("hidden"));
  document.getElementById("btn-cancel").addEventListener("click", () =>
    document.getElementById("modal-add").classList.add("hidden"));
  document.getElementById("btn-refresh").addEventListener("click", refresh);
  document.getElementById("btn-create").addEventListener("click", async () => {
    const token = document.getElementById("input-token").value.trim();
    const nickname = document.getElementById("input-nickname").value.trim();
    try {
      await bridge.apiPost("accounts", { token: token || undefined, nickname: nickname || undefined });
      document.getElementById("modal-add").classList.add("hidden");
      document.getElementById("input-token").value = "";
      document.getElementById("input-nickname").value = "";
      await refresh();
    } catch (err) { toast("创建失败: " + (err?.message || err)); }
  });
  // 导出模态
  document.getElementById("btn-export-cancel").addEventListener("click", () =>
    document.getElementById("modal-export").classList.add("hidden"));
  document.getElementById("btn-export-md").addEventListener("click", () =>
    doExport(exportTarget.hash, "md"));
  document.getElementById("btn-export-json").addEventListener("click", () =>
    doExport(exportTarget.hash, "json"));
}

let exportTarget = { hash: "", nickname: "" };

function openExport(tokenHash, nickname) {
  exportTarget = { hash: tokenHash, nickname: nickname || "" };
  document.getElementById("export-msg").textContent =
    `导出「${nickname || tokenHash}」的完整对话记录，选择格式（无条数上限）：`;
  document.getElementById("modal-export").classList.remove("hidden");
}

async function doExport(tokenHash, fmt) {
  try {
    const res = await bridge.apiPost(`accounts/${tokenHash}/export`, { format: fmt });
    const blob = new Blob([res.content], { type: res.mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = res.filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    document.getElementById("modal-export").classList.add("hidden");
    toast(`已导出 ${res.filename}`);
  } catch (err) {
    toast("导出失败: " + (err?.message || err));
  }
}

async function setNickname(tokenHash, current) {
  const nickname = await promptDialog(current);   // 页内模态（非 prompt()）
  if (nickname === null) return;                   // 取消
  try {
    await bridge.apiPost(`accounts/${tokenHash}/nickname`, { nickname });
    await refresh();
  } catch (err) { toast("设置失败: " + (err?.message || err)); }
}

async function deleteAccount(tokenHash) {
  if (!(await confirmDialog(`确定删除 ${tokenHash}？此操作不可撤销。`))) return;  // 页内模态（非 confirm()）
  try {
    await bridge.apiPost(`accounts/${tokenHash}/delete`, {});
    await refresh();
  } catch (err) { toast("删除失败: " + (err?.message || err)); }
}

function esc(s) { const d = document.createElement("div"); d.textContent = String(s ?? ""); return d.innerHTML; }

// ── 整页对话（admin 以 token 身份在同一会话发话，轮询历史收回复）──

const chat = { hash: "", nick: "", maxId: 0, timer: null, active: false };

function openChat(tokenHash, nickname) {
  chat.hash = tokenHash;
  chat.nick = nickname || tokenHash;
  chat.maxId = 0;
  chat.active = true;
  document.getElementById("main-view").classList.add("hidden");
  document.getElementById("chat-view").classList.remove("hidden");
  document.getElementById("chat-title").textContent = `对话：${chat.nick}`;
  document.getElementById("chat-messages").innerHTML = "";
  loadHistory();
  startPoll();
}

function closeChat() {
  chat.active = false;
  stopPoll();
  document.getElementById("chat-view").classList.add("hidden");
  document.getElementById("main-view").classList.remove("hidden");
}

async function loadHistory() {
  try {
    const res = await bridge.apiPost(`sessions/${chat.hash}/history`, { limit: 50 });
    const msgs = res.messages || [];
    msgs.forEach(appendBubble);
    if (msgs.length) {
      chat.maxId = Math.max(...msgs.map((m) => parseInt(m.message_id) || 0));
      scrollChatBottom();
    }
  } catch (err) { toast("加载历史失败: " + (err?.message || err)); }
}

async function pollOnce() {
  if (!chat.active) return;
  try {
    const res = await bridge.apiPost(`sessions/${chat.hash}/history`, { since: chat.maxId });
    const msgs = res.messages || [];
    if (msgs.length) {
      msgs.forEach(appendBubble);
      chat.maxId = Math.max(chat.maxId, ...msgs.map((m) => parseInt(m.message_id) || 0));
      scrollChatBottom();
    }
  } catch (err) { log("poll fail", err); }   // 单次失败静默，下个周期重试
}

function startPoll() {
  stopPoll();
  const tick = async () => {
    if (!chat.active) return;
    if (!document.hidden) await pollOnce();
    chat.timer = setTimeout(tick, 1200);
  };
  chat.timer = setTimeout(tick, 1200);
}

function stopPoll() {
  if (chat.timer) { clearTimeout(chat.timer); chat.timer = null; }
}

async function sendChat() {
  const ta = document.getElementById("chat-input");
  const text = ta.value.trim();
  if (!text) return;
  ta.value = "";
  ta.style.height = "auto";
  try {
    await bridge.apiPost(`sessions/${chat.hash}/chat`, { text });
    await pollOnce();   // 立即拉一次，用户行 ~1s 内回显
  } catch (err) { toast("发送失败: " + (err?.message || err)); }
}

function appendBubble(m) {
  const box = document.getElementById("chat-messages");
  const ts = m.timestamp ? new Date(m.timestamp * 1000).toLocaleTimeString("zh-CN") : "";
  const role = m.role, typ = m.type;
  let html;
  if (role === "user") {
    html = `<div class="bubble bubble-user"><div>${esc(m.content || "")}</div><div class="bubble-meta">${esc(ts)}</div></div>`;
  } else if (typ === "thinking") {
    html = `<details class="bubble bubble-thinking"><summary>💭 思考 · ${esc(ts)}</summary><div class="bubble-thinking-body">${esc(m.content || "")}</div></details>`;
  } else if (typ === "tool_status") {
    html = `<div class="bubble bubble-tool">🔨 ${esc(m.content || "")}</div>`;
  } else {
    html = `<div class="bubble bubble-bot"><div>${esc(m.content || "")}</div><div class="bubble-meta">${esc(ts)}</div></div>`;
  }
  box.insertAdjacentHTML("beforeend", html);
}

function scrollChatBottom() {
  const box = document.getElementById("chat-messages");
  box.scrollTop = box.scrollHeight;
}

function setupChat() {
  document.getElementById("btn-chat-back").addEventListener("click", closeChat);
  document.getElementById("btn-chat-send").addEventListener("click", sendChat);
  const ta = document.getElementById("chat-input");
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); }
  });
  ta.addEventListener("input", () => {
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 120) + "px";
  });
}

init();
log("app.js loaded");
