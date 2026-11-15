#!/bin/bash
# 사용법: ./switch.sh [blue|green]   (생략하면 현재의 반대 색으로)
set -euo pipefail
NGINX=realty-nginx
TARGET="${1:-}"
if [[ "$TARGET" != "blue" && "$TARGET" != "green" ]]; then
    LINK=$(docker exec "$NGINX" readlink /etc/nginx/conf.d/active.conf || true)
    [[ "$LINK" == *blue.conf ]] && TARGET=green || TARGET=blue
fi
echo ">>> TARGET : $TARGET"
docker exec "$NGINX" ln -sfn "/etc/nginx/${TARGET}.conf" /etc/nginx/conf.d/active.conf
docker exec "$NGINX" nginx -t
docker exec "$NGINX" nginx -s reload
echo ">>> switched to ${TARGET}.conf"
