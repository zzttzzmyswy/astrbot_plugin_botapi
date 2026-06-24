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

async function init() {
  setupToolbar();        // 顶部按钮（刷新/新增/创建/取消）
  wireDelegation();      // 表格事件委托（改名/删除）—— 不用内联 onclick
  if (!bridge) {
    showStatus("Bridge 未就绪（请在 WebUI 插件页内打开此页面）");
    log("no bridge");
    return;
  }
  try {
    await withTimeout(bridge.ready(), 8000, "Bridge 握手超时（是否在 WebUI 插件页内打开？）");
    log("bridge ready");
  } catch (e) {
    showStatus(e.message);
    log("bridge fail", e);
    return;
  }
  await refresh();
}

async function refresh() {
  const btn = document.getElementById("btn-refresh");
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = "刷新中…";   // 可见反馈：按钮确实响应了
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
    btn.disabled = false;
    btn.textContent = orig;
  }
}

function renderAccounts() {
  const tbody = document.getElementById("account-list");
  if (!accounts.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="8">暂无账户</td></tr>';
    return;
  }
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
        <button class="btn btn-sm btn-secondary" data-action="nickname" data-hash="${esc(a.token_hash)}" data-nickname="${esc(a.nickname || "")}">改名</button>
        <button class="btn btn-sm btn-danger" data-action="delete" data-hash="${esc(a.token_hash)}">删除</button>
      </td>
    </tr>`).join('');
}

// 事件委托：一个监听器处理所有行的改名/删除（避免内联 onclick 的 CSP/作用域/引号转义问题）
function wireDelegation() {
  document.getElementById("account-list").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    const hash = btn.dataset.hash;
    const nick = btn.dataset.nickname || "";
    if (action === "nickname") await setNickname(hash, nick);
    else if (action === "delete") await deleteAccount(hash);
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
    } catch (err) { alert("创建失败: " + (err?.message || err)); }
  });
}

async function setNickname(tokenHash, current) {
  const nickname = prompt("设置昵称/备注（留空清除）：", current);
  if (nickname === null) return;   // 取消
  try {
    await bridge.apiPost(`accounts/${tokenHash}/nickname`, { nickname: nickname.trim() });
    await refresh();
  } catch (err) { alert("设置失败: " + (err?.message || err)); }
}

async function deleteAccount(tokenHash) {
  if (!confirm(`确定删除 ${tokenHash}？`)) return;
  try {
    await bridge.apiPost(`accounts/${tokenHash}/delete`, {});
    await refresh();
  } catch (err) { alert("删除失败: " + (err?.message || err)); }
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = String(s ?? "");
  return d.innerHTML;
}

init();
log("app.js loaded");
