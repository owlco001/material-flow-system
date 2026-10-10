#!/usr/bin/env bash
# 后端一键更新：从 GitHub 拉取指定版本，备份数据库与代码，替换代码、装依赖、迁移、
# 重启并做健康检查；健康检查失败自动回滚到更新前的代码。
#
# 用法（root）：
#   sudo bash /srv/material-flow/deploy/update.sh            # 更新到 main 最新
#   sudo bash /srv/material-flow/deploy/update.sh --sha <40位commit>
#   sudo bash /srv/material-flow/deploy/update.sh --ref v0.5.38   # 分支 / 标签
#   sudo bash /srv/material-flow/deploy/update.sh --check    # 只看是否有更新
#   sudo bash /srv/material-flow/deploy/update.sh --rollback # 回滚到最近一次代码备份
#
# 服务器上还没有本脚本时（老部署），一行搞定（国内服务器用 Gitee 那行）：
#   curl -fsSL https://raw.githubusercontent.com/owlco001/material-flow-system/main/material-flow-backend/deploy/update.sh | sudo bash
#   curl -fsSL https://gitee.com/owlco001/material-flow-system/raw/main/material-flow-backend/deploy/update.sh | sudo UPDATE_SOURCE=gitee bash
#
# 代码来源 UPDATE_SOURCE：auto（默认，先 GitHub，10 秒连不上自动改用 Gitee 镜像）| github | gitee
#
# 管理台「系统更新」页的「开始更新」也调用本脚本：服务写入
# backups/update-request，material-flow-update.path 触发以 root 运行的
# material-flow-update.service（--from-request），无需 sudo。
#
# 数据、上传、APK 发布包、虚拟环境、环境文件均不会被改动。
set -euo pipefail

PREFIX="${MATERIAL_FLOW_PREFIX:-/srv/material-flow}"
ENV_FILE="${MATERIAL_FLOW_ENV_FILE:-/etc/material-flow/material-flow.env}"
SERVICE_USER="${MATERIAL_FLOW_SERVICE_USER:-material-flow}"
REPO="${UPDATE_GITHUB_REPO:-owlco001/material-flow-system}"
GITEE_REPO="${UPDATE_GITEE_REPO:-owlco001/material-flow-system}"
REF="${UPDATE_GITHUB_BRANCH:-main}"
if [ -z "${UPDATE_SOURCE:-}" ] && [ -f "$ENV_FILE" ]; then
  UPDATE_SOURCE="$(sed -n 's/^UPDATE_SOURCE=["'\'']\{0,1\}\([a-z]*\).*/\1/p' "$ENV_FILE" | tail -1)"
fi
SOURCE="${UPDATE_SOURCE:-auto}"
case "$SOURCE" in auto|github|gitee) ;; *) SOURCE=auto ;; esac
SERVICE="material-flow"
HEALTH_URL="http://127.0.0.1:8000/healthz"
KEEP_BACKUPS="${KEEP_BACKUPS:-10}"

BACKUPS="$PREFIX/backups"
STATUS_FILE="$BACKUPS/update-status.json"
REQUEST_FILE="$BACKUPS/update-request"
LOCK_FILE="/run/material-flow-update.lock"

# 本脚本会被新代码覆盖：若从文件运行，先复制到临时文件再执行，避免 bash 读到一半被替换
if [ -z "${MF_UPDATE_REEXEC:-}" ] && [ -f "${BASH_SOURCE[0]:-}" ]; then
  TMP_SELF="$(mktemp /tmp/mf-update.XXXXXX.sh)"
  cp "${BASH_SOURCE[0]}" "$TMP_SELF"
  export MF_UPDATE_REEXEC="$TMP_SELF"
  exec bash "$TMP_SELF" "$@"
fi
[ -n "${MF_UPDATE_REEXEC:-}" ] && trap 'rm -f "$MF_UPDATE_REEXEC"' EXIT

SHA=""
MODE="update"
ACTOR="${SUDO_USER:-root}"

usage() { sed -n '2,20p' "${MF_UPDATE_REEXEC:-${BASH_SOURCE[0]:-$0}}" 2>/dev/null | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --sha) SHA="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --check) MODE="check"; shift ;;
    --rollback) MODE="rollback"; shift ;;
    --from-request) MODE="request"; shift ;;
    --yes|-y) shift ;;  # 兼容：本脚本不交互
    -h|--help) usage ;;
    *) echo "未知参数：$1" >&2; usage 1 ;;
  esac
done

log()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }

PY="$PREFIX/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

json_str() { "$PY" -c 'import json,sys;print(json.dumps(sys.argv[1],ensure_ascii=False))' "$1"; }

set_status() {  # set_status <state> [error]
  install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$BACKUPS" 2>/dev/null || mkdir -p "$BACKUPS"
  local err="${2:-}"
  printf '{"state":"%s","at":"%s","sha":%s,"actor":%s,"error":%s,"source":"update.sh"}\n' \
    "$1" "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)" "$(json_str "$SHA")" "$(json_str "$ACTOR")" "$(json_str "$err")" \
    > "$STATUS_FILE.tmp"
  chmod 0644 "$STATUS_FILE.tmp"
  chown "$SERVICE_USER:$SERVICE_USER" "$STATUS_FILE.tmp" 2>/dev/null || true
  mv -f "$STATUS_FILE.tmp" "$STATUS_FILE"
}

die() {
  printf '\033[31m更新失败：%s\033[0m\n' "$*" >&2
  [ "$MODE" = check ] || set_status failed "$*"
  exit 1
}

if [ "$MODE" = request ]; then
  [ -f "$REQUEST_FILE" ] || exit 0
  REQ="$(cat "$REQUEST_FILE")"; rm -f "$REQUEST_FILE"
  SHA="$("$PY" -c 'import json,sys;print(json.loads(sys.argv[1]).get("sha",""))' "$REQ" 2>/dev/null || true)"
  ACTOR="$("$PY" -c 'import json,sys;print(json.loads(sys.argv[1]).get("actor","web"))' "$REQ" 2>/dev/null || echo web)"
  [[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || die "更新请求中的 sha 无效"
  MODE=update
fi

[ "$(id -u)" -eq 0 ] || die "需要 root：sudo bash $0"
command -v curl >/dev/null || die "缺少 curl"
command -v rsync >/dev/null || die "缺少 rsync（apt install rsync）"
[ -d "$PREFIX/app" ] || die "$PREFIX 下没有后端代码，首次部署请用 deploy/bootstrap.sh"

exec 9>"$LOCK_FILE"
flock -n 9 || die "已有更新在执行"

deployed_sha() { cat "$PREFIX/DEPLOYED_SHA" 2>/dev/null | tr -d '[:space:]' || true; }

gh_api() { curl -fsSL --retry "${1:-3}" --max-time "${2:-30}" -H 'Accept: application/vnd.github+json' -H 'User-Agent: material-flow-updater' "https://api.github.com/repos/$REPO/$3"; }
gitee_api() { curl -fsSL --retry 3 --max-time 30 "https://gitee.com/api/v5/repos/$GITEE_REPO/$1"; }
PARSE_COMMIT='import json,sys;d=json.load(sys.stdin);print(d["sha"]);print(d["commit"]["message"].splitlines()[0][:120])'

resolve_sha() {
  # 输出：sha、提交说明、实际使用的来源（github/gitee）
  local out
  if [ "$SOURCE" != gitee ]; then
    if [ "$SOURCE" = github ]; then
      out="$(gh_api 3 30 "commits/$REF" | "$PY" -c "$PARSE_COMMIT")" && { echo "$out"; echo github; return 0; }
      return 1
    fi
    out="$(gh_api 0 10 "commits/$REF" 2>/dev/null | "$PY" -c "$PARSE_COMMIT" 2>/dev/null)" && { echo "$out"; echo github; return 0; }
    echo "  GitHub 不可达，改用 Gitee 镜像 $GITEE_REPO" >&2
  fi
  out="$(gitee_api "commits/$REF" | "$PY" -c "$PARSE_COMMIT")" && { echo "$out"; echo gitee; return 0; }
  return 1
}

download_src() {
  # $1=sha $2=目标文件；按来源下载，GitHub 失败时（auto 模式）回退 Gitee
  local sha="$1" dst="$2"
  if [ "$SOURCE" != gitee ] && [ "${USED_SOURCE:-}" != gitee ]; then
    curl -fsSL --retry 3 --max-time 300 -H 'User-Agent: material-flow-updater' \
      -o "$dst" "https://codeload.github.com/$REPO/tar.gz/$sha" && return 0
    [ "$SOURCE" = github ] && return 1
    echo "  GitHub 下载失败，改用 Gitee 镜像" >&2
  fi
  # 注意：Gitee 对自定义 User-Agent 会返回网页而不是压缩包，这里用 curl 默认 UA
  if curl -fsSL --retry 3 --max-time 300 -o "$dst" \
       "https://gitee.com/$GITEE_REPO/repository/archive/$sha.tar.gz" && gzip -t "$dst" 2>/dev/null; then
    return 0
  fi
  # 压缩包不可用时用 git 浅克隆兜底
  command -v git >/dev/null || { echo "  Gitee 压缩包下载失败，且没有 git 可兜底" >&2; return 1; }
  echo "  Gitee 压缩包不可用，改用 git 拉取" >&2
  local d; d="$(mktemp -d)"
  git -C "$d" init -q && git -C "$d" fetch -q --depth 1 "https://gitee.com/$GITEE_REPO.git" "$REF" \
    && [ "$(git -C "$d" rev-parse FETCH_HEAD)" = "$sha" ] \
    && git -C "$d" archive --format=tar.gz --prefix="src-$sha/" -o "$dst" FETCH_HEAD
  local rc=$?; rm -rf "$d"
  [ $rc -eq 0 ] || echo "  Gitee 上找不到 ${sha:0:12}（镜像可能尚未同步该版本）" >&2
  return $rc
}

health_ok() {
  local i
  for i in $(seq 1 30); do
    if curl -fsS --max-time 5 "$HEALTH_URL" 2>/dev/null | grep -q '"status": *"ok"'; then return 0; fi
    sleep 2
  done
  return 1
}

install_units() {
  # 服务单元（含可写目录）与网页触发用的 path/oneshot 单元；有变化才重载
  local changed=0 f
  for f in material-flow.service deploy/material-flow-update.service deploy/material-flow-update.path; do
    [ -f "$PREFIX/$f" ] || continue
    local dst="/etc/systemd/system/$(basename "$f")"
    if ! cmp -s "$PREFIX/$f" "$dst"; then install -m 0644 "$PREFIX/$f" "$dst"; changed=1; fi
  done
  [ "$changed" -eq 1 ] && systemctl daemon-reload
  if [ -f /etc/systemd/system/material-flow-update.path ]; then
    systemctl enable --now material-flow-update.path >/dev/null 2>&1 || warn "material-flow-update.path 启用失败"
  fi
  # 旧版 sudo 提权方式已废弃（服务开了 NoNewPrivileges，sudo 本来就不可用）
  rm -f /etc/sudoers.d/material-flow
  install -d -m 0755 -o "$SERVICE_USER" -g "$SERVICE_USER" "$PREFIX/app-releases"
  install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$BACKUPS"
}

backup_db() {
  local dest="$1" data_dir="${MATERIAL_FLOW_DATA:-$PREFIX/data}"
  mkdir -p "$dest"
  [ -d "$data_dir" ] || return 0
  "$PY" - "$data_dir" "$dest" <<'PY'
import pathlib, sqlite3, sys
src_dir, dest = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
n = 0
for p in sorted(src_dir.glob("*.db")):
    s = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=30)
    d = sqlite3.connect(dest / p.name)
    s.backup(d); d.close(); s.close(); n += 1
    print(f"  数据库已热备：{p.name}")
if n == 0:
    print("  未找到 *.db，跳过数据库备份")
PY
}

backup_code() {
  local dest="$1"
  mkdir -p "$dest/code"
  rsync -a --exclude 'data/' --exclude 'uploads/' --exclude '.venv/' --exclude 'app-releases/' \
    --exclude 'backups/' --exclude '__pycache__/' --exclude '*.bak*' "$PREFIX/" "$dest/code/"
}

sync_code() {  # sync_code <src_dir>
  rsync -a --delete \
    --exclude 'data/' --exclude 'uploads/' --exclude '.venv/' --exclude 'app-releases/' \
    --exclude 'backups/' --exclude 'DEPLOYED_SHA' --exclude '__pycache__/' --exclude '*.pyc' \
    --exclude '.pytest_cache/' --exclude '.env' --exclude '*.bak*' --exclude '*.bak-*/' \
    "$1/" "$PREFIX/"
  chown -R root:root "$PREFIX/app" "$PREFIX/deploy" 2>/dev/null || true
  chmod -R a+rX "$PREFIX/app" "$PREFIX/deploy" 2>/dev/null || true
}

pip_install() {
  [ -x "$PREFIX/.venv/bin/python" ] || "$(command -v python3)" -m venv "$PREFIX/.venv"
  "$PREFIX/.venv/bin/python" -m pip install --quiet --disable-pip-version-check -r "$PREFIX/requirements.txt"
}

migrate() {
  ( set -a; [ -f "$ENV_FILE" ] && . "$ENV_FILE"; set +a
    cd "$PREFIX" && runuser -u "$SERVICE_USER" -- env MATERIAL_FLOW_DATA="${MATERIAL_FLOW_DATA:-$PREFIX/data}" \
      MATERIAL_FLOW_UPLOADS="${MATERIAL_FLOW_UPLOADS:-$PREFIX/uploads}" "$PREFIX/.venv/bin/python" -m app.migrate )
}

prune_backups() {
  { ls -1dt "$BACKUPS"/update-2* 2>/dev/null || true; } | tail -n +"$((KEEP_BACKUPS + 1))" | xargs -r rm -rf
  # 旧版网页更新留下的 tarball
  rm -f "$BACKUPS"/update-*.tar.gz
}

do_rollback() {  # do_rollback <backup_dir>
  local bk="$1"
  [ -d "$bk/code/app" ] || die "备份不完整：$bk"
  log "回滚代码到 $bk"
  sync_code "$bk/code"
  [ -f "$bk/DEPLOYED_SHA" ] && cp "$bk/DEPLOYED_SHA" "$PREFIX/DEPLOYED_SHA"
  pip_install || warn "回滚时依赖安装失败"
  install_units
  systemctl restart "$SERVICE"
  health_ok && log "已回滚，服务正常" || warn "回滚后健康检查仍失败，请看：journalctl -u $SERVICE -n 100"
}

# ---------------------------------------------------------------------------

if [ "$MODE" = rollback ]; then
  LAST="$( { ls -1dt "$BACKUPS"/update-2* 2>/dev/null || true; } | head -1)"
  [ -n "$LAST" ] || die "没有可回滚的备份"
  set_status applying "rollback"
  do_rollback "$LAST"
  set_status done
  exit 0
fi

CURRENT="$(deployed_sha)"
if [ -z "$SHA" ]; then
  log "查询 $REF 最新版本（来源：$SOURCE）"
  OUT="$(resolve_sha)" || die "查询最新版本失败（GitHub 与 Gitee 均不可访问，或分支不存在）"
  SHA="$(sed -n 1p <<<"$OUT")"; MSG="$(sed -n 2p <<<"$OUT")"; USED_SOURCE="$(sed -n 3p <<<"$OUT")"
  echo "  最新：${SHA:0:12}  $MSG  [$USED_SOURCE]"
fi
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || die "sha 格式无效：$SHA"
CUR_SHORT="${CURRENT:0:12}"; echo "  当前：${CUR_SHORT:-（未记录）}"

if [ "$MODE" = check ]; then
  [ "$SHA" = "$CURRENT" ] && echo "已是最新" || echo "有更新：${CURRENT:0:12} → ${SHA:0:12}"
  exit 0
fi
if [ "$SHA" = "$CURRENT" ] && [ -z "${FORCE:-}" ]; then
  log "已是最新（${SHA:0:12}），无需更新。强制重装：FORCE=1 sudo bash $0"
  set_status done
  exit 0
fi

set_status downloading
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"; [ -n "${MF_UPDATE_REEXEC:-}" ] && rm -f "$MF_UPDATE_REEXEC"' EXIT
log "1/6 下载 ${SHA:0:12}"
download_src "$SHA" "$WORK/src.tar.gz" || die "下载更新包失败"
tar -xzf "$WORK/src.tar.gz" -C "$WORK"
SRC="$(echo "$WORK"/*/material-flow-backend)"
[ -f "$SRC/app/main.py" ] || die "更新包结构异常（找不到 material-flow-backend/app/main.py）"
"$PY" -m py_compile "$SRC/app/main.py" || die "新代码语法检查失败，已中止（未改动现有部署）"

set_status applying
BK="$BACKUPS/update-$(date +%Y%m%d-%H%M%S)-${SHA:0:12}"
log "2/6 备份数据库与代码 → $BK"
mkdir -p "$BK"
[ -n "$CURRENT" ] && echo "$CURRENT" > "$BK/DEPLOYED_SHA"
backup_db "$BK/db" || die "数据库备份失败，已中止"
backup_code "$BK"

log "3/6 替换代码"
OLD_REQ_HASH="$(sha256sum "$PREFIX/requirements.txt" 2>/dev/null | cut -d' ' -f1 || true)"
sync_code "$SRC"

log "4/6 依赖与 systemd 单元"
if [ "$OLD_REQ_HASH" != "$(sha256sum "$PREFIX/requirements.txt" | cut -d' ' -f1)" ]; then
  pip_install || { do_rollback "$BK"; die "依赖安装失败，已回滚"; }
else
  echo "  requirements.txt 未变，跳过"
fi
install_units

log "5/6 数据库迁移"
migrate || { do_rollback "$BK"; die "数据库迁移失败，已回滚代码（数据库备份在 $BK/db）"; }

log "6/6 重启并健康检查"
echo "$SHA" > "$PREFIX/DEPLOYED_SHA"
# 由网页触发时，本进程不属于 material-flow 服务，重启不会中断自己
systemctl restart "$SERVICE"
if ! health_ok; then
  do_rollback "$BK"
  die "新版本健康检查失败，已自动回滚（日志：journalctl -u $SERVICE -n 100）"
fi
prune_backups
set_status done
VER="$(cat "$PREFIX/VERSION" 2>/dev/null || true)"
log "更新完成：${CURRENT:0:12} → ${SHA:0:12}（后端 $VER）。备份：$BK"
