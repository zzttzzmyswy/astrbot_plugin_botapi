#!/usr/bin/env bash
# BotAPI 插件自检脚本
# 用法:
#   ./selfcheck.sh --base http://localhost:6186 --token YOUR_TOKEN
#   ./selfcheck.sh --base https://your.domain --token YOUR_TOKEN --msg "你好"   # 含收发检查(需 LLM)
#
# 检查项:
#   1) 服务存活 + 鉴权（错 token 401 / 对 token 200）—— 不需 LLM
#   2) /history 历史                                —— 不需 LLM
#   3) /stream SSE 保活 ping                        —— 不需 LLM
#   4) --msg: 发消息 + SSE 收回复（final）          —— 需 LLM provider
set -uo pipefail

BASE="http://localhost:6186"
TOKEN=""
MSG=""
PING_WAIT=35      # 等 ping 的秒数（服务端 30s 发一次）
REPLY_WAIT=20     # 等回复的秒数

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base) BASE="$2"; shift 2;;
    --token) TOKEN="$2"; shift 2;;
    --msg) MSG="$2"; shift 2;;
    -h|--help) sed -n '2,12p' "$0"; exit 0;;
    *) echo "未知参数: $1"; exit 1;;
  esac
done

# 规整 base：去掉末尾斜杠，去掉可能附带的 /api/v1/botapi
BASE="${BASE%/}"
BASE="${BASE%/api/v1/botapi}"
API="$BASE/api/v1/botapi"

if [[ -z "$TOKEN" ]]; then echo "✗ 缺 --token（botapi 平台配置里的 tokens 之一）"; exit 1; fi

PASS=0; FAIL=0
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; PASS=$((PASS+1)); }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL=$((FAIL+1)); }
info() { printf "  \033[2m·\033[0m %s\n" "$1"; }

echo "=== BotAPI 自检 ==="
info "base=$API  token=${TOKEN:0:4}***"

# ── 1. 服务存活 + 鉴权 ──
echo "[1/4] 服务存活 + 鉴权"
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 \
  -X POST "$API/auth" -H "Content-Type: application/json" -d '{"token":"__wrong_token__"}')
if [[ "$code" == "401" ]]; then ok "错 token 返回 401（服务在跑 + 鉴权生效）";
else bad "错 token 期望 401，实际 $code（服务没起？端口/enable 不对？）"; fi

resp=$(curl -s --max-time 8 -X POST "$API/auth" -H "Content-Type: application/json" -d "{\"token\":\"$TOKEN\"}")
if echo "$resp" | grep -q "session_id"; then ok "对 token 返回 session_id：$(echo "$resp" | head -c 120)";
else bad "对 token 未返回 session_id，响应: $(echo "$resp" | head -c 200)"; fi

# ── 2. 历史 ──
echo "[2/4] /history"
resp=$(curl -s --max-time 8 "$API/history?since=0" -H "Authorization: Bearer $TOKEN")
if echo "$resp" | grep -q "messages"; then ok "history 返回: $(echo "$resp" | head -c 120)";
else bad "history 异常: $(echo "$resp" | head -c 200)"; fi

# ── 3. SSE ping ──
echo "[3/4] /stream SSE 保活 ping（最多等 ${PING_WAIT}s）"
out=$(curl -s -N --max-time "$PING_WAIT" "$API/stream" -H "Authorization: Bearer $TOKEN" 2>/dev/null)
if echo "$out" | grep -q "event: ping"; then ok "收到 SSE ping（SSE 通路 OK）";
else bad "未收到 ping（SSE 被 nginx 缓冲？proxy_buffering off / proxy_read_timeout 86400s）"; fi

# ── 4. 收发（需 LLM）──
echo "[4/4] 发消息 + SSE 收回复"
if [[ -z "$MSG" ]]; then
  info "跳过（加 --msg '你好' 测试收发，需 LLM provider 配置好）"
else
  tmp=$(mktemp)
  # 先开 SSE 流（回复只推给活跃连接）
  curl -s -N "$API/stream" -H "Authorization: Bearer $TOKEN" >"$tmp" 2>/dev/null &
  sse_pid=$!
  sleep 1.5   # 让 SSE 连上 + 注册队列
  mresp=$(curl -s --max-time 8 -X POST "$API/message" -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" -d "{\"text\":\"$MSG\"}")
  if echo "$mresp" | grep -q "message_id"; then ok "/message 返回: $(echo "$mresp" | head -c 120)";
  else bad "/message 异常: $(echo "$mresp" | head -c 200)"; kill "$sse_pid" 2>/dev/null; fi
  info "等回复 ${REPLY_WAIT}s …"
  sleep "$REPLY_WAIT"
  kill "$sse_pid" 2>/dev/null; wait "$sse_pid" 2>/dev/null
  if grep -q "final" "$tmp"; then
    ok "SSE 收到回复（final）"
    info "事件类型: $(grep -o 'event: [a-z_]*' "$tmp" | sort -u | tr '\n' ' ')"
  else
    bad "SSE 未收到 final（LLM provider 没配/没启用流式？查 AstrBot 日志 pipeline 报错）"
    info "SSE 原始输出: $(head -c 300 "$tmp")"
  fi
  rm -f "$tmp"
fi

echo "=== 结果: ${PASS} 通过, ${FAIL} 失败 ==="
exit $FAIL
