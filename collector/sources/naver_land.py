"""네이버 부동산 (new.land.naver.com) 비공식 API 어댑터.

⚠️ 공식 API 가 아니므로 구조가 바뀌거나 차단될 수 있다. 개인용·저빈도로만 사용한다.
- 요청 간 지연 + 지터, 일일 상한(Quota), 401 시 토큰 재발급, 429/403 시 백오프 후 중단
- 응답에서 필요한 필드만 꺼내고, 형식이 다르면 NaverSchemaError 로 해당 단계만 실패 처리
- 헤드리스 브라우저를 쓰지 않는다 (NAS 메모리·ARM 호환)

이 파일만 교체하면 다른 소스로 바꿀 수 있도록 dict 형태로 반환한다.
"""
from __future__ import annotations

import random
import re
import time
from collections.abc import Iterator

import httpx

from collector.util import Quota, log, parse_price, parse_ymd

BASE = "https://new.land.naver.com"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
JWT_RE = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")

TRADE_TYPES = {"sale": "A1", "jeonse": "B1"}


class NaverError(RuntimeError):
    pass


class NaverBlocked(NaverError):
    """403/429 반복 — 오늘은 더 요청하지 않는다."""


class NaverSchemaError(NaverError):
    pass


class NaverClient:
    def __init__(self, quota: Quota | None = None, delay: float = 1.5, timeout: float = 20):
        self.quota = quota
        self.delay = delay
        self.http = httpx.Client(
            timeout=timeout,
            headers={
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "ko-KR,ko;q=0.9",
                "Referer": f"{BASE}/complexes",
            },
            follow_redirects=True,
        )
        self.token: str | None = None
        self._last = 0.0

    # ------------------------------------------------------------ 저수준
    def _sleep(self) -> None:
        wait = self.delay + random.uniform(0, self.delay * 0.6) - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _refresh_token(self) -> None:
        self._sleep()
        if self.quota:
            self.quota.take()
        r = self.http.get(f"{BASE}/complexes")
        m = JWT_RE.search(r.text)
        self.token = m.group(0) if m else None
        if not self.token:
            log.warning("네이버 토큰을 찾지 못함 — 토큰 없이 시도")

    def _get(self, path: str, params: dict | None = None) -> dict:
        if self.token is None:
            self._refresh_token()
        backoff = 10
        for attempt in range(4):
            self._sleep()
            if self.quota:
                self.quota.take()
            headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
            try:
                r = self.http.get(f"{BASE}{path}", params=params, headers=headers)
            except httpx.HTTPError as e:
                log.warning("네이버 요청 오류 %s: %s", path, e)
                time.sleep(backoff)
                continue
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError as e:
                    raise NaverSchemaError(f"JSON 아님: {path}") from e
            if r.status_code == 401:
                self._refresh_token()
                continue
            if r.status_code in (403, 429):
                log.warning("네이버 %s (%s) — %ss 대기", r.status_code, path, backoff)
                time.sleep(backoff)
                backoff *= 3
                if attempt >= 2:
                    raise NaverBlocked(f"네이버 접근 제한 {r.status_code}")
                continue
            if r.status_code == 404:
                return {}
            log.warning("네이버 HTTP %s %s", r.status_code, path)
        raise NaverError(f"네이버 요청 실패: {path}")

    # ------------------------------------------------------------ 단지 목록 (읍면동 단위)
    def complexes_in_dong(self, cortar_no: str) -> list[dict]:
        data = self._get("/api/regions/complexes", {"cortarNo": cortar_no, "realEstateType": "APT", "order": ""})
        if not data:
            return []
        if "complexList" not in data:
            raise NaverSchemaError(f"complexList 없음: {list(data)[:10]}")
        out = []
        for c in data["complexList"] or []:
            if c.get("realEstateTypeCode", "APT") != "APT":
                continue
            out.append(
                {
                    "naver_no": str(c.get("complexNo")),
                    "name": c.get("complexName", ""),
                    "dong_code": str(c.get("cortarNo") or cortar_no),
                    "households": _int(c.get("totalHouseholdCount")),
                    "built_year": _year(c.get("useApproveYmd")),
                    "lat": _float(c.get("latitude")),
                    "lng": _float(c.get("longitude")),
                    "deal_count": _int(c.get("dealCount")) or 0,
                    "lease_count": _int(c.get("leaseCount")) or 0,
                }
            )
        return out

    # ------------------------------------------------------------ 단지 상세 + 평형
    def complex_detail(self, naver_no: str) -> dict:
        data = self._get(f"/api/complexes/{naver_no}", {"sameAddressGroup": "false"})
        d = data.get("complexDetail")
        if not d:
            raise NaverSchemaError(f"complexDetail 없음: {naver_no}")
        areas = []
        for p in data.get("complexPyeongDetailList") or []:
            areas.append(
                {
                    "pyeong_no": str(p.get("pyeongNo", "")),
                    "area_name": p.get("pyeongName") or p.get("pyeongName2") or "",
                    "supply_m2": _float(p.get("supplyArea")) or 0.0,
                    "exclusive_m2": _float(p.get("exclusiveArea")) or 0.0,
                    "rooms": _int(p.get("roomCnt")),
                    "baths": _int(p.get("bathroomCnt")),
                    "entrance": p.get("entranceType") or "",
                    "households": _int(p.get("householdCountByPyeong")),
                }
            )
        entrances = {a["entrance"] for a in areas if a["entrance"]}
        return {
            "name": d.get("complexName", ""),
            "address": " ".join(x for x in (d.get("address"), d.get("detailAddress")) if x),
            "jibun": d.get("detailAddress") or "",
            "households": _int(d.get("totalHouseholdCount")),
            "built_year": _year(d.get("useApproveYmd")),
            "lat": _float(d.get("latitude")),
            "lng": _float(d.get("longitude")),
            "corridor": "혼합" if len(entrances) > 1 else (entrances.pop() if entrances else ""),
            "areas": areas,
        }

    # ------------------------------------------------------------ 매물
    def articles(self, naver_no: str, trade: str = "sale", max_pages: int = 10) -> Iterator[dict]:
        code = TRADE_TYPES[trade]
        for page in range(1, max_pages + 1):
            data = self._get(
                f"/api/articles/complex/{naver_no}",
                {
                    "realEstateType": "APT",
                    "tradeType": code,
                    "page": page,
                    "complexNo": naver_no,
                    "sameAddressGroup": "true",
                    "order": "prc",
                    "type": "list",
                },
            )
            if not data:
                return
            if "articleList" not in data:
                raise NaverSchemaError(f"articleList 없음: {list(data)[:10]}")
            for a in data["articleList"] or []:
                price = parse_price(a.get("dealOrWarrantPrc"))
                if price is None:
                    continue
                yield {
                    "article_no": str(a.get("articleNo")),
                    "trade_type": trade,
                    "area_name": a.get("areaName") or "",
                    "supply_m2": _float(a.get("area1")) or 0.0,
                    "exclusive_m2": _float(a.get("area2")) or 0.0,
                    "building": a.get("buildingName") or "",
                    "floor_text": a.get("floorInfo") or "",
                    "direction": a.get("direction") or "",
                    "price": price,
                    "description": " ".join(
                        x for x in [a.get("articleFeatureDesc") or "", " ".join(a.get("tagList") or [])] if x
                    ),
                    "realtor": a.get("realtorName") or "",
                    "realtor_count": _int(a.get("sameAddrCnt")) or 1,
                    "confirmed_at": parse_ymd(a.get("articleConfirmYmd")),
                }
            if not data.get("isMoreData"):
                return


def article_url(article_no: str) -> str:
    return f"https://new.land.naver.com/complexes?articleNo={article_no}"


def complex_url(naver_no: str) -> str:
    return f"https://new.land.naver.com/complexes/{naver_no}"


def _int(v) -> int | None:
    try:
        return int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _float(v) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _year(ymd) -> int | None:
    s = str(ymd or "")
    return int(s[:4]) if len(s) >= 4 and s[:4].isdigit() else None
