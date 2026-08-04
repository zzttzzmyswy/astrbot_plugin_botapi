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

function sessionNameDialog(current) {
  return new Promise((resolve) => {
    const input = document.getElementById("input-session-name");
    input.value = current || "";
    const modal = document.getElementById("modal-session-name");
    const ok = document.getElementById("btn-session-name-save");
    const cancel = document.getElementById("btn-session-name-cancel");
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
  wireSessions();
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
      <td><code>${esc(a.token_preview)}</code></td>
      <td><code>${esc(a.token_hash)}</code></td>
      <td><span class="badge ${a.online ? 'badge-online' : 'badge-offline'}">${a.online ? '在线' : '离线'}</span></td>
      <td>${a.message_count ?? 0}</td>
      <td>${a.sse_connections || 0}</td>
      <td>${a.last_active ? new Date(a.last_active * 1000).toLocaleString('zh-CN') : '-'}</td>
      <td>${a.bound_platform ? `<span class="badge badge-bound" title="已绑定 ${esc(a.bound_platform)}">${esc(a.bound_platform)}</span>` : '<span class="badge badge-offline">未绑定</span>'}</td>
      <td>
        <button class="btn btn-sm btn-secondary" data-action="bind" data-hash="${esc(a.token_hash)}">绑定</button>
        ${a.bound_platform ? `<button class="btn btn-sm btn-secondary" data-action="unbind" data-hash="${esc(a.token_hash)}">解绑</button>` : ''}
        <button class="btn btn-sm btn-primary" data-action="chat" data-hash="${esc(a.token_hash)}">对话</button>
        <button class="btn btn-sm btn-secondary" data-action="export" data-hash="${esc(a.token_hash)}">导出</button>
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
    if (action === "delete") await deleteAccount(hash);
    else if (action === "export") openExport(hash);
    else if (action === "chat") openSessions(hash);
    else if (action === "bind") openBind(hash);
    else if (action === "unbind") await unbindAccount(hash);
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
    try {
      await bridge.apiPost("accounts", { token: token || undefined });
      document.getElementById("modal-add").classList.add("hidden");
      document.getElementById("input-token").value = "";
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
  // 绑定模态
  document.getElementById("btn-bind-save").addEventListener("click", bindAccount);
  document.getElementById("btn-bind-cancel").addEventListener("click", () =>
    document.getElementById("modal-bind").classList.add("hidden"));
}

let exportTarget = { hash: "" };

function openExport(tokenHash) {
  exportTarget = { hash: tokenHash };
  document.getElementById("export-msg").textContent =
    `导出「${tokenHash}」的完整对话记录，选择格式（无条数上限）：`;
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

async function deleteAccount(tokenHash) {
  if (!(await confirmDialog(`确定删除 ${tokenHash}？此操作不可撤销。`))) return;  // 页内模态（非 confirm()）
  try {
    await bridge.apiPost(`accounts/${tokenHash}/delete`, {});
    await refresh();
  } catch (err) { toast("删除失败: " + (err?.message || err)); }
}

// ── 绑定机器人（下拉选活跃平台，页内模态替代 prompt）──

let bindTarget = { hash: "" };

async function openBind(tokenHash) {
  bindTarget = { hash: tokenHash };
  const select = document.getElementById("select-bind-platform");
  select.innerHTML = '<option value="">加载中...</option>';
  document.getElementById("modal-bind").classList.remove("hidden");
  let platforms = [];
  try {
    const res = await bridge.apiGet("platforms");
    platforms = (res.platforms && res.platforms.length) ? res.platforms : [];
  } catch (err) {
    log("load platforms ERR", err);
  }
  if (!platforms.length) {
    document.getElementById("bind-msg").textContent =
      "没有可绑定的活跃平台（需在 AstrBot 中启用其他平台）。";
  } else {
    document.getElementById("bind-msg").textContent =
      `选择「${bindTarget.hash}」使用的机器人（绑定后对话走该平台的 LLM 配置）：`;
  }
  select.innerHTML = platforms.map(p => `<option value="${esc(p)}">${esc(p)}</option>`).join("");
}

async function bindAccount() {
  const platformId = document.getElementById("select-bind-platform").value;
  if (!platformId) { toast("没有可绑定的平台"); return; }
  try {
    await bridge.apiPost(`accounts/${bindTarget.hash}/bind`, { platform_id: platformId });
    document.getElementById("modal-bind").classList.add("hidden");
    toast(`已绑定 ${platformId}`);
    await refresh();
  } catch (err) { toast("绑定失败: " + (err?.message || err)); }
}

async function unbindAccount(tokenHash) {
  if (!(await confirmDialog(`确定解绑该账户？解绑后回到默认单机路由。`))) return;
  try {
    await bridge.apiPost(`accounts/${tokenHash}/unbind`, {});
    await refresh();
  } catch (err) { toast("解绑失败: " + (err?.message || err)); }
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = String(s ?? "");
  return d.innerHTML.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// ── 整页对话（admin 以 token 身份在同一会话发话，轮询 conversation_manager 收回复）──
// 历史读 conversation_manager（LLM 真实对话上下文）；每次拉取全量重绘消息列表
// （renderMessages），以服务端返回为准重建 DOM，避免增量去重漂移。

const chat = { hash: "", sid: "", timer: null, active: false, rendered: new Set() };

// ── 会话列表（账户 → 会话下钻）──
// 每账户可建多个会话；进入某会话后 chat.sid 置为对应 sid，
// loadHistory/pollOnce/sendChat 的请求体都会带 session_id。

const sessions = { hash: "", defaultId: "default" };

function openSessions(tokenHash) {
  log("openSessions", tokenHash);
  sessions.hash = tokenHash;
  document.getElementById("main-view").classList.add("hidden");
  document.getElementById("sessions-view").classList.remove("hidden");
  document.getElementById("sessions-title").textContent = `会话：${tokenHash}`;
  renderSessions();
}

function closeSessions() {
  stopPoll();
  chat.active = false;
  document.getElementById("sessions-view").classList.add("hidden");
  document.getElementById("main-view").classList.remove("hidden");
}

async function renderSessions() {
  const tbody = document.getElementById("session-list");
  tbody.innerHTML = '<tr class="empty-row"><td colspan="4">加载中...</td></tr>';
  try {
    const res = await bridge.apiGet(`sessions/${sessions.hash}`);
    sessions.defaultId = res.default_id || "default";
    const list = res.sessions || [];
    if (!list.length) {
      tbody.innerHTML = '<tr class="empty-row"><td colspan="4">暂无会话</td></tr>';
      return;
    }
    tbody.innerHTML = list.map(s => {
      const isDefault = s.id === sessions.defaultId;
      return `
        <tr>
          <td>${esc(s.name || s.id)}</td>
          <td class="session-meta">${s.created_at ? new Date(s.created_at * 1000).toLocaleString('zh-CN') : '-'}</td>
          <td><code>${esc(s.id)}</code></td>
          <td>
            <button class="btn btn-sm btn-primary" data-saction="enter" data-sid="${esc(s.id)}">进入对话</button>
            <button class="btn btn-sm btn-secondary" data-saction="rename" data-sid="${esc(s.id)}" data-name="${esc(s.name || s.id)}">改名</button>
            ${isDefault ? '' : `<button class="btn btn-sm btn-danger" data-saction="delete" data-sid="${esc(s.id)}">删除</button>`}
          </td>
        </tr>`;
    }).join('');
  } catch (err) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="4">加载失败: ${esc(err?.message || err)}</td></tr>`;
    log("renderSessions ERR", err);
  }
}

function openChatSession(tokenHash, sid) {
  log("openChatSession", tokenHash, sid);
  chat.hash = tokenHash;
  chat.sid = sid || "";
  chat.active = true;
  chat.rendered = new Set();
  pollFailCount = 0;
  document.getElementById("sessions-view").classList.add("hidden");
  document.getElementById("chat-view").classList.remove("hidden");
  document.getElementById("chat-title").textContent = `对话：${tokenHash} / ${chat.sid}`;
  document.getElementById("chat-messages").innerHTML = "";
  loadHistory();
  startPoll();
}

function closeChat() {
  chat.active = false;
  stopPoll();
  pollFailCount = 0;
  document.getElementById("chat-view").classList.add("hidden");
  document.getElementById("sessions-view").classList.remove("hidden");
  renderSessions();
}

async function createSession(tokenHash) {
  const name = await sessionNameDialog("");
  if (name === null) return;
  if (!name) { toast("会话名称不能为空"); return; }
  try {
    await bridge.apiPost(`sessions/${tokenHash}`, { name });
    await renderSessions();
  } catch (err) { toast("新建失败: " + (err?.message || err)); }
}

async function renameSession(tokenHash, sid, current) {
  const name = await sessionNameDialog(current || "");
  if (name === null) return;
  if (!name) { toast("会话名称不能为空"); return; }
  try {
    await bridge.apiPost(`sessions/${tokenHash}/${sid}/rename`, { name });
    await renderSessions();
  } catch (err) { toast("改名失败: " + (err?.message || err)); }
}

async function deleteSession(tokenHash, sid) {
  if (!(await confirmDialog(`确定删除该会话？此操作不可撤销。`))) return;
  try {
    await bridge.apiPost(`sessions/${tokenHash}/${sid}/delete`, {});
    if (chat.active && chat.sid === sid) closeChat();
    else await renderSessions();
  } catch (err) { toast("删除失败: " + (err?.message || err)); }
}

function wireSessions() {
  document.getElementById("btn-sessions-back").addEventListener("click", closeSessions);
  document.getElementById("btn-sessions-add").addEventListener("click", () => createSession(sessions.hash));
  document.getElementById("session-list").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-saction]");
    if (!btn) return;
    const action = btn.dataset.saction;
    const sid = btn.dataset.sid;
    if (action === "enter") openChatSession(sessions.hash, sid);
    else if (action === "rename") await renameSession(sessions.hash, sid, btn.dataset.name || "");
    else if (action === "delete") await deleteSession(sessions.hash, sid);
  });
}

async function loadHistory() {
  try {
    const res = await bridge.apiPost(`sessions/${chat.hash}/history`, { limit: 200, session_id: chat.sid });
    const msgs = res.messages || [];
    renderMessages(msgs);
    log("loadHistory ok", `msgs=${msgs.length}`);
  } catch (err) { log("loadHistory ERR", err); toast("加载历史失败: " + (err?.message || err)); }
}

let pollFailCount = 0;

async function pollOnce() {
  if (!chat.active) return;
  try {
    const res = await bridge.apiPost(`sessions/${chat.hash}/history`, { limit: 200, session_id: chat.sid });
    pollFailCount = 0;
    renderMessages(res.messages || []);
  } catch (err) {
    // 连续失败达到阈值：停止轮询并提示，避免会话被删/网络中断时无限空转。
    pollFailCount++;
    log("poll ERR", err, `fail=${pollFailCount}`);
    if (pollFailCount >= 3) {
      stopPoll();
      chat.active = false;
      toast("历史拉取连续失败，已停止刷新。请检查会话是否仍存在。");
    }
  }
}

// 全量重绘消息列表：每次拉取都以服务端返回为准重建 DOM，
// 避免增量去重 Set 无限增长、以及 50 条窗口滑动导致的「旧消息残留 + 新头被
// 误判重复而跳过」漂移。会话历史来自 conversation_manager（完整 context，
// 无截断），limit 200 足够覆盖常规对话；超出部分由服务端截尾。
function renderMessages(msgs) {
  const box = document.getElementById("chat-messages");
  box.innerHTML = "";
  chat.rendered = new Set();
  let added = 0;
  for (const m of msgs) {
    if (appendBubble(m)) added++;
  }
  if (added) scrollChatBottom();
  log("renderMessages", `total=${msgs.length} rendered=${added}`);
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
  log("sendChat", JSON.stringify({ hash: chat.hash, text }));
  try {
    await bridge.apiPost(`sessions/${chat.hash}/chat`, { text, session_id: chat.sid });
    await pollOnce();   // 立即拉一次（用户行+回复随 pipeline 落库后即显示）
  } catch (err) { log("sendChat ERR", err); toast("发送失败: " + (err?.message || err)); }
}

// 返回 true 表示新气泡已追加，false 表示跳过(去重或空内容)。
function appendBubble(m) {
  const content = m.content || "";
  // 跳过空内容气泡：conversation_manager 历史里工具调用帧等 assistant 项
  // content 可能为空，渲染成空 bot 气泡是噪音。
  if (!content.trim()) return false;
  const sig = `${m.role}:${content}`;
  if (chat.rendered.has(sig)) return false;
  chat.rendered.add(sig);
  const box = document.getElementById("chat-messages");
  const ts = m.timestamp ? new Date(m.timestamp * 1000).toLocaleTimeString("zh-CN") : "";
  const role = m.role, typ = m.type;
  let html;
  if (role === "user") {
    html = `<div class="bubble bubble-user"><div>${esc(content)}</div><div class="bubble-meta">${esc(ts)}</div></div>`;
  } else if (typ === "thinking") {
    html = `<details class="bubble bubble-thinking"><summary>💭 思考 · ${esc(ts)}</summary><div class="bubble-thinking-body">${esc(content)}</div></details>`;
  } else if (typ === "tool_status") {
    html = `<div class="bubble bubble-tool">🔨 ${esc(content)}</div>`;
  } else {
    html = `<div class="bubble bubble-bot"><div>${esc(content)}</div><div class="bubble-meta">${esc(ts)}</div></div>`;
  }
  box.insertAdjacentHTML("beforeend", html);
  return true;
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
