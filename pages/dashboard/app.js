const bridge = window.AstrBotPluginPage;
let accounts = [];

async function init() {
  await bridge.ready();
  await refresh();
  setupEventListeners();
}

async function refresh() {
  try {
    const stats = await bridge.apiGet("stats");   // bridge 解包 .data
    accounts = stats.per_account || [];
    document.getElementById("total-accounts").textContent = stats.total_accounts ?? "-";
    document.getElementById("online-count").textContent = stats.total_online ?? "-";
    document.getElementById("total-messages").textContent = stats.total_messages ?? "-";
    renderAccounts();
  } catch (err) { console.error("刷新失败:", err); }
}

function renderAccounts() {
  const tbody = document.getElementById("account-list");
  if (!accounts.length) { tbody.innerHTML = '<tr class="empty-row"><td colspan="7">暂无账户</td></tr>'; return; }
  tbody.innerHTML = accounts.map(a => `
    <tr>
      <td><code>${esc(a.token_preview)}</code></td>
      <td><code>${esc(a.token_hash)}</code></td>
      <td><span class="badge ${a.online ? 'badge-online' : 'badge-offline'}">${a.online ? '在线' : '离线'}</span></td>
      <td>${a.message_count ?? 0}</td>
      <td>${a.sse_connections || 0}</td>
      <td>${a.last_active ? new Date(a.last_active * 1000).toLocaleString('zh-CN') : '-'}</td>
      <td><button class="btn btn-sm btn-danger" onclick="deleteAccount('${esc(a.token_hash)}')">删除</button></td>
    </tr>`).join('');
}

function setupEventListeners() {
  document.getElementById("btn-add").addEventListener("click", () => document.getElementById("modal-add").classList.remove("hidden"));
  document.getElementById("btn-cancel").addEventListener("click", () => document.getElementById("modal-add").classList.add("hidden"));
  document.getElementById("btn-refresh").addEventListener("click", refresh);
  document.getElementById("btn-create").addEventListener("click", async () => {
    const token = document.getElementById("input-token").value.trim();
    try {
      await bridge.apiPost("accounts", { token: token || undefined });
      document.getElementById("modal-add").classList.add("hidden");
      document.getElementById("input-token").value = "";
      await refresh();
    } catch (err) { alert("创建失败: " + err.message); }
  });
}

async function deleteAccount(tokenHash) {
  if (!confirm(`确定删除 ${tokenHash}？`)) return;
  await bridge.apiPost(`accounts/${tokenHash}/delete`, {});   // 用 apiPost（无 apiDelete）
  await refresh();
}
window.deleteAccount = deleteAccount;

function esc(s) { const d = document.createElement("div"); d.textContent = String(s); return d.innerHTML; }

init();
