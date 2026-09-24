#!/usr/bin/env bash
# 后端一键部署：material-flow-backend（docs/DEPLOYMENT.md「One-command bootstrap」）。
#
# 用法（在仓库根或 material-flow-backend/ 内执行，需 root）：
#   bash deploy/bootstrap.sh                          # 全流程：安装 + 管理员设定 + 服务 + 反代 + 冒烟
#   bash deploy/bootstrap.sh --no-nginx --no-service  # 只装运行环境（试装/开发）
#   printf '%s\n' '<一次性密码>' | bash deploy/bootstrap.sh --admin-password-stdin
#
# 安全约定：脚本不含任何服务器地址 / 凭据；管理员密码只经 read -s（不回显）或 stdin
# 读入，随即写入 0600 的环境文件，绝不进入命令行参数 / 日志 / shell 历史。
set -euo pipefail

PREFIX="${MATERIAL_FLOW_PREFIX:-/srv/material-flow}"
ENV_FILE="${MATERIAL_FLOW_ENV_FILE:-/etc/material-flow/material-flow.env}"
SERVICE_USER="${MATERIAL_FLOW_SERVICE_USER:-material-flow}"
RUN_MIGRATE_SERVICE=1
RUN_NGINX=1
PASSWORD_FROM_STDIN=0

usage() { grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --service-user) SERVICE_USER="$2"; shift 2 ;;
    --no-service) RUN_MIGRATE_SERVICE=0; shift ;;
    --no-nginx) RUN_NGINX=0; shift ;;
    --admin-password-stdin) PASSWORD_FROM_STDIN=1; shift ;;
    -h|--help) usage ;;
    *) echo "未知参数：$1" >&2; usage 1 ;;
  esac
done

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[31m部署失败：%s\033[0m\n' "$*" >&2; exit 1; }

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_SRC="$REPO_ROOT/material-flow-backend"
[ -f "$BACKEND_SRC/app/main.py" ] || BACKEND_SRC="$REPO_ROOT"
[ -f "$BACKEND_SRC/app/main.py" ] || fail "找不到 material-flow-backend/app/main.py（请在仓库内执行）"
[ "$(id -u)" -eq 0 ] || fail "需要 root（写 $ENV_FILE / 安装 systemd 单元 / 建服务用户）"
command -v python3 >/dev/null || fail "缺少 python3"

log "1/7 运行目录与虚拟环境：$PREFIX"
install -d -m 0755 "$PREFIX"
# 数据/上传目录显式锚定到本 prefix（试装/多实例与既有部署完全隔离；
# 环境文件里显式设置的 MATERIAL_FLOW_DATA/UPLOADS 仍可覆盖）。
export MATERIAL_FLOW_DATA="${MATERIAL_FLOW_DATA:-$PREFIX/data}"
export MATERIAL_FLOW_UPLOADS="${MATERIAL_FLOW_UPLOADS:-$PREFIX/uploads}"
rsync -a --delete \
  --exclude 'data/' --exclude 'uploads/' --exclude 'backups/' --exclude '.venv/' \
  --exclude '__pycache__/' --exclude '*.pyc' --exclude '.pytest_cache/' \
  "$BACKEND_SRC/" "$PREFIX/"
install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$PREFIX/data" "$PREFIX/uploads" "$PREFIX/backups" \
  2>/dev/null || { id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home "$PREFIX" --shell /usr/sbin/nologin "$SERVICE_USER"; \
                   install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$PREFIX/data" "$PREFIX/uploads" "$PREFIX/backups"; }
if [ ! -x "$PREFIX/.venv/bin/python" ]; then
  python3 -m venv "$PREFIX/.venv"
fi
"$PREFIX/.venv/bin/python" -m pip install --quiet --upgrade pip
"$PREFIX/.venv/bin/python" -m pip install --quiet -r "$PREFIX/requirements.txt"

log "2/7 环境文件与管理员设定：$ENV_FILE"
install -d -m 0750 "$(dirname "$ENV_FILE")"
if [ ! -f "$ENV_FILE" ]; then
  install -m 0600 /dev/null "$ENV_FILE"
fi
if grep -q '^INITIAL_ADMIN_PASSWORD=' "$ENV_FILE"; then
  echo "环境文件已含 INITIAL_ADMIN_PASSWORD，跳过管理员设定（保持幂等）。"
else
  echo "设定初始管理员（用户名固定 owlco，首次登录强制改密）："
  if [ "$PASSWORD_FROM_STDIN" -eq 1 ]; then
    IFS= read -r ADMIN_PASSWORD
  else
    IFS= read -rs -p '初始管理员密码（不少于 8 位，输入不回显）: ' ADMIN_PASSWORD; echo
    IFS= read -rs -p '再次输入确认: ' ADMIN_PASSWORD_AGAIN; echo
    [ "$ADMIN_PASSWORD" = "$ADMIN_PASSWORD_AGAIN" ] || fail "两次输入不一致"
  fi
  [ "${#ADMIN_PASSWORD}" -ge 8 ] || fail "密码不少于 8 位"
  printf 'INITIAL_ADMIN_PASSWORD=%s\n' "$ADMIN_PASSWORD" >> "$ENV_FILE"
  unset ADMIN_PASSWORD ADMIN_PASSWORD_AGAIN
  chmod 0600 "$ENV_FILE"
  echo "已写入 $ENV_FILE（0600）。该密码为一次性临时凭据，首登必须改密。"
fi

log "3/7 数据库迁移"
set -a; . "$ENV_FILE"; set +a
( cd "$PREFIX" && "$PREFIX/.venv/bin/python" -m app.migrate )

if [ "$RUN_MIGRATE_SERVICE" -eq 1 ]; then
  log "4/7 systemd 服务 material-flow"
  install -m 0644 "$PREFIX/material-flow.service" /etc/systemd/system/material-flow.service
  systemctl daemon-reload
  systemctl enable --now material-flow >/dev/null 2>&1
  systemctl --no-pager --full status material-flow | head -5 || true
else
  log "4/7 跳过 systemd（--no-service）"
fi

if [ "$RUN_NGINX" -eq 1 ]; then
  log "5/7 nginx 反代（client_max_body_size 32m，模型上传上限 15MiB 需要；根路径 302 /admin/login）"
  NGINX_CONF=/etc/nginx/sites-available/material-flow
  cat > "$NGINX_CONF" <<'NGINX'
server {
    listen 80 default_server;
    server_name _;
    # GLB 上传上限 15MiB，必须放大（默认 1m 会 413 被客户端显示为网络错误）
    client_max_body_size 32m;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location = / {
        return 302 /admin/login;
    }
}
NGINX
  ln -sfn "$NGINX_CONF" /etc/nginx/sites-enabled/material-flow
  nginx -t >/dev/null && systemctl reload nginx || echo "警告：nginx 配置检查未通过，请手工核对（服务本体不受影响）"
else
  log "5/7 跳过 nginx（--no-nginx）"
fi

log "6/7 冒烟检查"
if [ "$RUN_MIGRATE_SERVICE" -eq 1 ]; then
  HEALTH="$("$PREFIX/.venv/bin/python" - <<'PY'
import urllib.request
print(urllib.request.urlopen("http://127.0.0.1:8000/healthz", timeout=10).status)
PY
)"
  [ "$HEALTH" = "200" ] || fail "健康检查失败（HTTP $HEALTH）"
  echo "healthz: $HEALTH"
else
  echo "跳过健康检查（--no-service 未启动服务）；管理 CLI 自检："
  ( cd "$PREFIX" && "$PREFIX/.venv/bin/python" -m app.manage_admin status )
fi

log "7/7 部署完成"
cat <<'DONE'
后续步骤：
  1. 用初始用户名 owlco 登录 Web 管理台 /admin/login，按提示改密（强制）。
  2. 建真实管理账号（Web「用户管理」，或 CLI：
     .venv/bin/python -m app.manage_admin create --employee-no <工号> --name <姓名>）。
  3. 按需停用临时账号：.venv/bin/python -m app.manage_admin list / reset-password。
  4. 备份：data/、uploads/ 目录（SQLite 热备见 docs/DEPLOYMENT.md「Backup & upgrade」）。
  5. 生产暴露请置于反向代理 TLS 之后（当前为 HTTP，仅限内网/受控网络）。
DONE
