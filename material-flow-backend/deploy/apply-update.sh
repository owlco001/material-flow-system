#!/bin/bash
# 以 root 执行：应用 GitHub 更新包。
# 用法：apply-update.sh <sha>
# 前置：API 已下载 tarball 到 /srv/material-flow/backups/update-<sha>.tar.gz
# 由 /etc/sudoers.d/material-flow 授权 material-flow 用户免密调用。
set -euo pipefail

SHA="${1:?用法：apply-update.sh <sha>}"
PREFIX="/srv/material-flow"
TARBALL="$PREFIX/backups/update-$SHA.tar.gz"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [[ ! "$SHA" =~ ^[0-9a-f]{40}$ ]]; then
    echo "SHA 格式无效" >&2; exit 2
fi
if [[ ! -f "$TARBALL" ]]; then
    echo "更新包不存在：$TARBALL" >&2; exit 2
fi

# 1. 解包，定位 material-flow-backend/
tar -xzf "$TARBALL" -C "$WORK"
SRC="$(echo "$WORK"/*/material-flow-backend)"
if [[ ! -d "$SRC" ]]; then
    echo "更新包结构异常（找不到 material-flow-backend/）" >&2; exit 2
fi

# 2. 备份当前代码（不含数据/环境/上传/发布包）
STAMP="$(date +%Y%m%d-%H%M%S)"
BK="$PREFIX/backups/code-$STAMP-${SHA:0:12}"
mkdir -p "$BK"
for d in app docs deploy scripts tasks; do
    [[ -d "$PREFIX/$d" ]] && cp -r "$PREFIX/$d" "$BK"/
done
for f in VERSION requirements.txt requirements-test.txt README.md NEXT.md; do
    [[ -f "$PREFIX/$f" ]] && cp "$PREFIX/$f" "$BK"/
done

# 3. 同步新代码（排除数据、上传、虚拟环境、发布包、备份）
rsync -a --delete \
    --exclude 'data/' --exclude 'uploads/' --exclude '.venv/' \
    --exclude 'app-releases/' --exclude 'backups/' \
    "$SRC/" "$PREFIX/"

# 4. 数据库迁移（以 material-flow 身份，避免文件属主错乱）
sudo -u material-flow bash -c "cd '$PREFIX' && '$PREFIX/.venv/bin/python' -m app.migrate"

# 5. 记录部署 SHA
echo "$SHA" > "$PREFIX/DEPLOYED_SHA"

# 6. 延迟重启（让 API 先把响应发出去）
nohup bash -c "sleep 5; systemctl restart material-flow" >/dev/null 2>&1 &

echo "OK $SHA (backup: $BK)"
