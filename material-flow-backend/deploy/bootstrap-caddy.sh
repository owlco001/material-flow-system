#!/usr/bin/env bash
# 智慧工厂测试后端一键部署（Caddy 版）：systemd 常驻 + Caddy 反代(自动 HTTPS)
# + 演示数据 + 测试账号 owlco/test123（免改密）。
#
# 用法（VPS 上 root 执行一行即可；DNS 请先指向本机）：
#   curl -sSL https://raw.githubusercontent.com/owlco001/material-flow-system/main/material-flow-backend/deploy/bootstrap-caddy.sh | sudo bash
#
# 环境变量：DOMAIN（默认 factory.claws4u.com）、BACKEND_PORT（默认 8000）
# 幂等：重复执行 = 更新到 GitHub main 最新版并重载服务。
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "请用 root 运行" >&2; exit 1; }
DOMAIN="${DOMAIN:-factory.claws4u.com}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
PREFIX=/srv/material-flow

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[31m部署失败：%s\033[0m\n' "$*" >&2; exit 1; }

log "1/5 拉取代码"
command -v git >/dev/null || { apt-get update -qq && apt-get install -y -qq git || fail "git 安装失败"; }
rm -rf /tmp/mf-deploy
git clone --depth 1 https://github.com/owlco001/material-flow-system /tmp/mf-deploy || fail "代码拉取失败"

log "2/5 官方 bootstrap（跳过 nginx，用 Caddy）"
printf '%s\n' 'test123' | bash /tmp/mf-deploy/material-flow-backend/deploy/bootstrap.sh --no-nginx --admin-password-stdin

log "3/5 演示数据 + 测试账号免改密"
cd "$PREFIX"
export MATERIAL_FLOW_DATA="$PREFIX/data" MATERIAL_FLOW_UPLOADS="$PREFIX/uploads"
ENABLE_TEST_DATA_SEED=true TEST_DATA_SEED_PASSWORD=test123 \
  "$PREFIX/.venv/bin/python" -m app.seed --seed || fail "演示数据写入失败"
"$PREFIX/.venv/bin/python" - <<'PY'
import os, sqlite3
db = os.path.join(os.environ["MATERIAL_FLOW_DATA"], "material_flow.db")
c = sqlite3.connect(db)
c.execute("UPDATE users SET must_change_password=0 WHERE username='owlco'")
c.commit()
row = c.execute("SELECT username, role, must_change_password FROM users WHERE username='owlco'").fetchone()
print("admin:", row)
c.close()
PY

log "4/5 Caddy 反代 $DOMAIN"
CADDYFILE=/etc/caddy/Caddyfile
[ -f "$CADDYFILE" ] || fail "找不到 $CADDYFILE（本脚本需要 Caddy 已安装）"
command -v caddy >/dev/null || fail "找不到 caddy 命令"
cp -a "$CADDYFILE" "$CADDYFILE.bak.$(date +%Y%m%d%H%M%S)"
if ! grep -q "$DOMAIN" "$CADDYFILE"; then
  cat >> "$CADDYFILE" <<EOF

$DOMAIN {
	handle / {
		redir /admin/login 302
	}
	handle {
		reverse_proxy 127.0.0.1:$BACKEND_PORT
	}
}
EOF
  echo "已追加站点块"
else
  echo "站点块已存在，跳过追加"
fi
if caddy validate --config "$CADDYFILE" --adapter caddyfile >/dev/null 2>&1; then
  systemctl reload caddy || fail "caddy reload 失败，已备份 $CADDYFILE.bak.*"
else
  echo "警告：caddy validate 未通过，跳过 reload（服务本体不受影响），请手工检查 $CADDYFILE"
fi

log "5/5 冒烟检查"
export BACKEND_PORT
HEALTH="$("$PREFIX/.venv/bin/python" - <<'PY'
import os, urllib.request
port = os.environ["BACKEND_PORT"]
print(urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=10).status)
PY
)"
[ "$HEALTH" = "200" ] || fail "健康检查失败（HTTP $HEALTH）"
echo "healthz: $HEALTH"
systemctl --no-pager --full is-active material-flow

log "部署完成"
printf '地址：https://%s\n账号：owlco / test123\n' "$DOMAIN"
printf '更新版本：重新执行本脚本即可（DNS 已配好无需再动）。\n'
