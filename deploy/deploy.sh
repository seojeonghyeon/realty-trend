#!/bin/bash
# blue/green 무중단 배포. 현재 연결된 색은 nginx 의 active.conf 링크로 판별한다.
set -euo pipefail
cd "$(dirname "$0")"
COMPOSE="docker compose -f ./docker-compose.yml --env-file ./.env"
NGINX=realty-nginx

# 1. 현재 활성 색 확인 → 반대 색에 배포
CURRENT=""
if docker ps --format '{{.Names}}' | grep -q "^${NGINX}$"; then
    LINK=$(docker exec "$NGINX" readlink /etc/nginx/conf.d/active.conf || true)
    case "$LINK" in *blue.conf) CURRENT=blue;; *green.conf) CURRENT=green;; esac
fi
if [[ "$CURRENT" == "blue" ]]; then IDLE=green; else IDLE=blue; fi
echo ">>> current: ${CURRENT:-none} → deploy to: $IDLE"

# 2. 이미지 받고 새 색 기동
$COMPOSE pull "$IDLE" collector
$COMPOSE up -d --force-recreate "$IDLE"

# 3. 헬스 체크 (컨테이너 내부에서 /healthz) — 실패하면 전환하지 않고 중단
echo ">>> health checking $IDLE..."
OK=""
for i in $(seq 1 24); do
    if docker exec "realty-$IDLE" curl -fsS http://localhost:8000/healthz >/dev/null 2>&1; then
        OK=1; echo ">>> $IDLE is UP"; break
    fi
    sleep 5
done
if [[ -z "$OK" ]]; then
    echo "!!! $IDLE health check failed — 기존 버전 유지"
    docker logs --tail 80 "realty-$IDLE" || true
    $COMPOSE stop "$IDLE"
    exit 1
fi

# 4. nginx 가 없으면 새 색으로 기동, 있으면 스위칭
if [[ -z "$CURRENT" ]]; then
    INITIAL_COLOR="$IDLE" $COMPOSE up -d realty-nginx
else
    ./switch.sh "$IDLE"
    sleep 5
    $COMPOSE stop "$CURRENT"   # 이전 버전 정리 (롤백: ./switch.sh 이전색 전에 start)
fi

# 5. 수집기 교체 — 수집/백필이 돌고 있으면 끊지 않고 다음 배포로 미룬다
if docker ps --format '{{.Names}}' | grep -q '^realty-collector$' \
   && docker exec realty-collector sh -c 'cat /proc/[0-9]*/cmdline 2>/dev/null | tr "\0" " " | grep -qE "collector\.(run_daily|backfill|legal_dong)"'; then
    echo ">>> collector 작업 실행 중 — 수집기 교체는 건너뜀 (다음 배포 때 반영)"
else
    $COMPOSE up -d --force-recreate collector
fi

docker image prune -f >/dev/null 2>&1 || true
echo ">>> deployed $(grep '^APP_VERSION=' .env | cut -d= -f2) on $IDLE"
