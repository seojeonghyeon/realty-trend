#!/bin/bash
# Mac 에서 직접 배포: 테스트 → 이미지 빌드/푸시(Docker Hub) → deploy/ 파일 전송 → NAS 에서 blue/green 배포
# GitHub Actions 없이 .github/workflows/deploy.yml 과 같은 일을 한다.
#
# 사용법:  ./scripts/deploy_manual.sh            (전체)
#          SKIP_TESTS=1 ./scripts/deploy_manual.sh
# 설정:    저장소 루트의 .deploy.env (git 에 올라가지 않음, .deploy.env.example 참고)
#          NAS 의 $NAS_DIR/deploy/.env 는 처음 한 번 직접 만들어 두고, 이 스크립트는 APP_VERSION 만 바꾼다.
set -euo pipefail
cd "$(dirname "$0")/.."

[[ -f .deploy.env ]] || { echo "!! .deploy.env 가 없습니다 (.deploy.env.example 복사 후 수정)"; exit 1; }
# shellcheck disable=SC1091
source .deploy.env
: "${DOCKER_USERNAME:?}" "${NAS_HOST:?}" "${NAS_USER:?}" "${NAS_DIR:?}"
NAS_PORT=${NAS_PORT:-22}
PLATFORM=${PLATFORM:-linux/amd64}
IMAGE="$DOCKER_USERNAME/realty-trend"

# 버전 태그: VERSION-커밋 (커밋 안 된 변경이 있으면 -dirty)
TAG="$(tr -d '[:space:]' < VERSION)-$(git rev-parse --short HEAD)"
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || TAG="$TAG-dirty-$(date +%m%d%H%M)"
echo ">>> 배포 태그: $TAG  →  $NAS_USER@$NAS_HOST:$NAS_PORT $NAS_DIR"

docker info >/dev/null 2>&1 || { echo "!! Docker 가 꺼져 있습니다 (Docker Desktop 실행)"; exit 1; }

# 1. 테스트
if [[ "${SKIP_TESTS:-}" != "1" ]]; then
    PY=.venv/bin/python; [[ -x $PY ]] || PY=python3
    $PY -m pytest -q
fi

# 2. 이미지 빌드·푸시 (docker login 은 미리 한 번)
docker build --platform "$PLATFORM" -t "$IMAGE:$TAG" -t "$IMAGE:latest" .
docker push "$IMAGE:$TAG"
docker push "$IMAGE:latest"

# 3. NAS 로 전송 + 배포 (ssh 연결 1개를 재사용해 비밀번호는 최대 한 번)
SOCK="$(mktemp -u /tmp/realty-ssh.XXXXXX)"
SSH=(ssh -p "$NAS_PORT" -o ControlMaster=auto -o ControlPath="$SOCK" -o ControlPersist=60)
trap '"${SSH[@]}" -O exit "$NAS_USER@$NAS_HOST" 2>/dev/null || true' EXIT

# scp/sftp 설정과 무관하게 tar 로 전송
tar czf - -C deploy docker-compose.yml deploy.sh switch.sh nginx.conf blue.conf green.conf \
  | "${SSH[@]}" "$NAS_USER@$NAS_HOST" "mkdir -p '$NAS_DIR/deploy' '$NAS_DIR/backup' && tar xzf - -C '$NAS_DIR/deploy'"

"${SSH[@]}" "$NAS_USER@$NAS_HOST" bash -s -- "$NAS_DIR" "$TAG" <<'REMOTE'
set -euo pipefail
export PATH=$PATH:/usr/local/bin
cd "$1/deploy"
[[ -f .env ]] || { echo "!! $1/deploy/.env 가 없습니다. README 의 .env 예시대로 먼저 만들어 주세요"; exit 1; }
chmod +x deploy.sh switch.sh
if grep -q '^APP_VERSION=' .env; then
    sed -i "s|^APP_VERSION=.*|APP_VERSION=$2|" .env
else
    echo "APP_VERSION=$2" >> .env
fi
./deploy.sh
docker ps --filter name=realty- --format '{{.Names}}\t{{.Status}}\t{{.Image}}'
REMOTE
echo ">>> 완료: $TAG"
