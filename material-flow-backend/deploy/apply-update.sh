#!/bin/bash
# 已废弃：保留给旧调用方。请改用 deploy/update.sh。
exec bash "$(dirname "$0")/update.sh" --sha "${1:?用法：apply-update.sh <sha>}"
