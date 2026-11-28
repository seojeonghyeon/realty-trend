# 부동산 동향

`TalkFile_매수후보고르기.pdf` 의 4단계 프로세스(예산 산정 → 지역별 단지 선정 → 우선순위 → 매물 보기)를
관심 지역에 대해 매일 자동으로 재현하는 개인용 사이트입니다. 매수 추천이 아닙니다.

```
[Synology NAS · docker compose]
  db (MariaDB) ─┬─ app        FastAPI 웹 (:8000 → 리버스 프록시 HTTPS)
                └─ collector  supercronic
                     01:30 실거래 백필 · 04:00 DB 백업 · 04:30 일일 수집 · 매월 1일 법정동코드 갱신
[Claude Code 클라우드 routine · 매일 07:07]
  GET /api/v1/digest → 수리상태 분류·단지 평가·지역 분석·금융 뉴스 → POST /api/v1/*
```

## 화면
| 메뉴 | 내용 (PDF 대응) |
|---|---|
| 대시보드 | 예산 필터 범위·부대비용·대출 한도 추정, Claude 요약, 지역별 평단가/신규/인하, 뉴스 |
| 관심 지역 | 시·도 / 시·군·구 / 읍·면·동 아무 단위나 검색해 추가, 그룹명으로 묶기 (p14) |
| 지역 상세 | 가격대(0.1억)×동 매트릭스 `단지명(연식^/전용/가격)` (p15~27), 전수조사 표 + CSV, 지역 분석 기록 |
| 우선순위 | 지역(그룹) × 가격대 통합 매트릭스, 번호 입력으로 순위 지정 (p30) |
| 단지 | 정성평가(교통·균질성·학군·세대수, 탈락 사유), 호가·실거래 추이 차트, 매물별 수리상태·인테리어비·실투입 총액 (p32~34) |
| 매물 | 예산/남길 단지 매물 모아보기, 신규, 의심 매물 숨기기 |
| 지도 | 단지 위치를 가격대 색으로 (OpenStreetMap) |
| 임장 | 전화할 날(방문 이틀 전 = 목요일) 알림, 통화 스크립트, 하자 체크리스트 (p32~33) |
| 뉴스 | 날짜별 금융·부동산 뉴스 요약과 영향 태그 |
| 설정 | 예산, 필터, 규제지역 코드, 대출 한도(LTV/DSR), 인테리어 비용표 |
| 수집 | 작업 기록, 국토부↔네이버 단지 수동 매칭 |

## 1. 준비물
1. **공공데이터포털(data.go.kr) 인증키** — 아래 3개를 활용신청 (보통 즉시~1일 승인)
   - 국토교통부_아파트 매매 실거래가 상세 자료
   - 국토교통부_아파트 전월세 실거래가 자료
   - 행정안전부_법정동코드
2. (선택) 텔레그램 봇 토큰과 chat id — 매일 아침 신규·인하 매물, 수집 실패, 전화 알림
3. (선택) 카카오 REST API 키 — 역·초등학교 거리 자동 계산 → 교통/학군 초안 평가
4. 도메인을 외부에서 HTTPS로 접속할 수 있어야 함 (Claude routine 이 클라우드에서 접속)

## 2. NAS 설치 (Synology Container Manager)
```bash
# NAS 의 /volume1/docker/realty 에 이 저장소를 둔다
cp .env.example .env    # 값 채우기 (비밀번호는 영문+숫자만: DB URL 에 들어감)
docker compose up -d --build
docker compose exec collector python -m collector.legal_dong --api   # 법정동코드 최초 적재
```
- Container Manager → 프로젝트 → 생성 → 경로 지정 → `docker-compose.yml` 사용으로도 가능
- **리버스 프록시**: 제어판 → 로그인 포털 → 고급 → 리버스 프록시 → `https://내도메인:443` → `http://localhost:8000`
  인증서는 제어판 → 보안 → 인증서(Let's Encrypt). 포트포워딩 대신 **Cloudflare Tunnel** 을 쓰면 공유기 포트를 열지 않아도 됩니다.
- HTTPS 로 운영하면 `.env` 에 `COOKIE_SECURE=true`
- `./backup` 폴더를 Hyper Backup 대상에 넣어 두세요.

### 첫 수집
1. 사이트 로그인 → 설정에서 예산 입력 → 관심 지역 추가
2. 수동 실행으로 확인:
   ```bash
   docker compose exec collector python -m collector.run_daily
   docker compose exec collector python -m collector.backfill   # 전고점용 과거 실거래 (여러 날에 걸쳐 진행)
   ```
3. "수집" 메뉴에서 상태·오류 확인, 매칭 안 된 단지는 수동 연결

## 2-0. Mac 에서 직접 배포 (현재 기본)
GitHub Actions 를 쓸 수 없을 때 `scripts/deploy_manual.sh` 가 아래 2-1 과 같은 일을 합니다.
```bash
cp .deploy.env.example .deploy.env   # 처음 한 번 (NAS 주소·계정·Docker Hub ID)
docker login                         # 처음 한 번, Docker Desktop 실행 상태에서
./scripts/deploy_manual.sh           # 테스트 → 빌드/푸시 → deploy/ 전송 → NAS 의 .env APP_VERSION 갱신 → deploy.sh
```
- NAS 의 `$NAS_DIR/deploy/.env`(비밀값)는 처음에 직접 만들어 두고, 스크립트는 `APP_VERSION` 만 바꿉니다.
- 커밋 안 된 변경이 있으면 태그에 `-dirty-<시각>` 이 붙습니다.

## 2-1. GitHub Actions 배포 (현재 수동 실행만, time-page-api 와 같은 방식)
Actions 탭에서 Run workflow 로 `.github/workflows/deploy.yml` 을 실행합니다 (push 자동 실행은 꺼 둠).

1. **build-and-push**: pytest → Docker 이미지 빌드 → Docker Hub 푸시 (`<DOCKER_USERNAME>/realty-trend:<VERSION>-<sha7>`, `latest`)
2. **copy-files**: `deploy/` 의 compose·nginx·스크립트를 NAS 의 `DEPLOY_DIR` 로 scp
3. **deploy**: SSH 로 `.env` 생성(권한 600) → `deploy/deploy.sh`
   - nginx `active.conf` 링크로 현재 색 판별 → 반대 색(blue/green) 기동 → `/healthz` 확인
   - 헬스체크 실패 시 **전환하지 않고 중단**(기존 버전 유지), 성공 시 nginx reload 후 이전 색 정지
   - 수집/백필이 돌고 있으면 수집기 교체는 다음 배포로 미룸
   - 수동 롤백: `docker start realty-<이전색> && ./switch.sh <이전색>`

운영 배포는 `deploy/docker-compose.yml` 을 쓰고, DB 는 **NAS 의 MariaDB 패키지**(`host.docker.internal`)에 붙습니다.
루트의 `docker-compose.yml` 은 로컬/단독 실행용(MariaDB 컨테이너 포함)입니다.

### GitHub 저장소 설정
**Secrets** (Settings → Secrets and variables → Actions)
| 이름 | 값 |
|---|---|
| `DOCKER_USER`, `DOCKER_PASSWORD` | Docker Hub 로그인 (비밀번호 대신 Access Token 권장) |
| `DOCKER_USERNAME` | 이미지 네임스페이스 |
| `EC2_HOST`, `EC2_PORT`, `EC2_USERNAME`, `EC2_SSH_KEY` | NAS SSH 접속 정보 (time-page 와 같은 이름) |
| `DATABASE_URL` | `mysql+pymysql://realty:<비밀번호>@host.docker.internal:3326/realty?charset=utf8mb4` (포트는 NAS MariaDB 설정값. 이 NAS 는 3326 = time-page 와 동일) |
| `SECRET_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ROUTINE_TOKEN` | 앱 비밀값 |
| `MOLIT_SERVICE_KEY` | 공공데이터포털 인증키 |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `KAKAO_REST_KEY` | 선택 |

**Variables**: `DEPLOY_DIR`(예: `/var/services/homes/<SSH사용자>/realty`), `PUBLIC_BASE_URL`(예: `https://api.time-page.kr/realty`), `REALTY_PORT`(기본 8000)

> `PUBLIC_BASE_URL` 에 경로가 있으면(`/realty`) 앱이 링크·리다이렉트·세션 쿠키 경로에 그 prefix 를 붙입니다.
> 앞단 프록시는 prefix 를 **떼고** 넘겨야 합니다 (아래 time-page 연동 참고).

> 비밀값은 `.env` 한 줄에 그대로 들어가므로 줄바꿈·공백·`#` 은 피하세요. DB 비밀번호에 `@ : / ?` 가 있으면 URL 인코딩이 필요합니다.

### NAS 1회 준비
```sql
-- Synology MariaDB 10 (phpMyAdmin 또는 mysql -P 3326)
CREATE DATABASE realty CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'realty'@'172.%' IDENTIFIED BY '<비밀번호>';   -- 도커 브리지 대역에서 접속
GRANT ALL PRIVILEGES ON realty.* TO 'realty'@'172.%';
```
- MariaDB 10 패키지 설정에서 **TCP/IP 연결 허용**
- SSH 사용자(`EC2_USERNAME`)가 `sudo` 없이 `docker` 를 실행할 수 있어야 함 (time-page 와 같은 계정을 쓰면 그대로 동작)
- Docker Hub 이미지를 private 으로 두면 NAS 에서 한 번 `docker login` 필요
- 첫 배포 후 한 번: `docker exec realty-collector python -m collector.legal_dong --api`
- 접속 경로: 아래 "time-page 와 443 공유" 참고

### time-page 와 443 공유 (`https://api.time-page.kr/realty`)
공유기는 443 만 열려 있고 Synology 리버스 프록시가 `api.time-page.kr` → `localhost:9080`(time-page-nginx)으로 보냅니다.
time-page-api 저장소의 `deploy/blue.conf`·`green.conf` 에 `location /realty/ { proxy_pass http://172.17.0.1:8000/; ... }` 를
추가해 두었고, time-page 를 한 번 배포하면(또는 conf 복사 후 `docker exec time-page-nginx nginx -s reload`) 적용됩니다.
```
인터넷 :443 → Synology 리버스 프록시 → :9080 time-page-nginx ─┬ /realty/ → 172.17.0.1:8000 realty-nginx → realty-blue|green
                                                              └ /        → time-page blue|green
```
- 확인: `docker exec time-page-nginx curl -s http://172.17.0.1:8000/healthz` → `{"ok":true}`
  (안 되면 `docker network inspect bridge` 의 Gateway 주소로 conf 를 바꾸세요)
- Synology 리버스 프록시·인증서는 추가 작업이 없습니다.

## 3. Claude 예약 작업 (routine)
1. 이 저장소를 **private GitHub 저장소**로 올립니다 (routine 에 저장소가 연결돼야 셸·curl 을 쓸 수 있음).
2. claude.ai/code 의 클라우드 환경 설정에서
   - Network access: **Limited** + Allowed domains 에 `api.time-page.kr` 추가 (첫 실행에서 WebSearch 가 막히면 Full 로)
   - **Network secret** 추가: 타입 Bearer, 호스트 = `api.time-page.kr`, 값 = `.env` 의 `ROUTINE_TOKEN`
     (프롬프트나 환경변수에 토큰을 넣지 마세요. 프록시가 Authorization 헤더를 붙여 줍니다.)
3. Claude Code 에서 `/schedule` 로 routine 생성
   - 저장소: 위 저장소
   - 스케줄: `CRON_TZ=Asia/Seoul 7 7 * * *` (정각은 지연될 수 있어 07:07)
   - 프롬프트: `scripts/claude_routine_prompt.md 를 읽고 그대로 수행해. SITE=https://api.time-page.kr/realty`
4. "Run now" 로 한 번 돌려 대시보드에 요약·뉴스가 들어오는지 확인합니다. routine 실행은 구독 사용량에서 차감됩니다.

## 주의할 점
- **네이버 부동산은 비공식 API** 입니다. 구조가 바뀌거나 차단될 수 있고 이용약관상 자동 수집이 제한될 수 있습니다.
  요청 간 1.5~2.4초 지연, 하루 상한(`NAVER_DAILY_LIMIT`), 예산 ±1억 단지만 상세 수집, 다른 단지는 주 1회로 줄였습니다.
  수집이 실패하면 텔레그램으로 알리고 국토부 데이터만으로 계속 동작합니다. 어댑터는 `collector/sources/naver_land.py` 하나라 교체가 쉽습니다.
- 시 단위처럼 넓게 등록하면 단지 수가 많아 수집이 1~2시간 걸릴 수 있습니다. "수집" 메뉴에서 소요 시간을 보고 04:30 시작이 늦으면 crontab 을 앞당기세요.
- 실거래는 신고까지 최대 30일 걸려 최근 1~2개월은 미확정입니다 (매일 최근 3개월을 다시 조회).
- 세금·중개보수·대출 한도는 일반 규정을 단순화한 추정치입니다. 규제지역·토지거래허가구역은 정책이 자주 바뀌므로 설정에서 직접 관리하세요.
- 행정구역 개편으로 법정동코드가 폐지되면 관심 지역 화면과 텔레그램으로 알려 줍니다. 지역을 다시 선택하세요.

## 개발
```bash
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest -q          # 계산 로직 + 가짜 수집기로 전체 흐름 테스트 (SQLite)
.venv/bin/uvicorn app.main:app --reload  # .env 없으면 SQLite(realty.db), admin/admin
```
