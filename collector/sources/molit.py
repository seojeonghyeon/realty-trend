"""국토교통부 실거래가 공공 API (data.go.kr).

- 아파트 매매 실거래가 상세: RTMSDataSvcAptTradeDev
- 아파트 전월세 실거래가:   RTMSDataSvcAptRent
- 행정안전부 법정동코드:     StanReginCd
"""
from __future__ import annotations

import datetime as dt
import time
import xml.etree.ElementTree as ET
from urllib.parse import unquote
from collections.abc import Iterator

import httpx

from collector.util import Quota, log

SALE_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
RENT_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"
LEGAL_DONG_URL = "https://apis.data.go.kr/1741000/StanReginCd/getStanReginCdList"

OK_CODES = {"00", "000"}


class MolitError(RuntimeError):
    pass


class MolitClient:
    def __init__(self, service_key: str, quota: Quota | None = None, timeout: float = 30):
        if not service_key:
            raise MolitError("MOLIT_SERVICE_KEY 가 설정되지 않았습니다")
        # 포털의 Encoding 키(%2B 등)를 넣어도 httpx 가 다시 인코딩하지 않도록 Decoding 형태로 통일
        self.key = unquote(service_key.strip()) if "%" in service_key else service_key.strip()
        self.quota = quota
        self.http = httpx.Client(timeout=timeout)

    def _get(self, url: str, params: dict) -> httpx.Response:
        for attempt in range(3):
            if self.quota:
                self.quota.take()
            try:
                r = self.http.get(url, params={"serviceKey": self.key, **params})
                if r.status_code == 200:
                    return r
                log.warning("MOLIT HTTP %s %s", r.status_code, r.text[:200])
            except httpx.HTTPError as e:
                log.warning("MOLIT 요청 오류: %s", e)
            time.sleep(2 * (attempt + 1))
        raise MolitError(f"MOLIT 요청 실패: {url} {params}")

    def _items(self, url: str, lawd_cd: str, ym: str) -> Iterator[dict]:
        page = 1
        while True:
            r = self._get(url, {"LAWD_CD": lawd_cd, "DEAL_YMD": ym, "pageNo": page, "numOfRows": 1000})
            root = ET.fromstring(r.content)
            code = (root.findtext(".//resultCode") or "").strip()
            if code not in OK_CODES:
                raise MolitError(f"MOLIT 오류 {code}: {root.findtext('.//resultMsg')}")
            items = root.findall(".//items/item")
            for it in items:
                yield {child.tag: (child.text or "").strip() for child in it}
            total = int(root.findtext(".//totalCount") or 0)
            if page * 1000 >= total or not items:
                break
            page += 1

    def sales(self, lawd_cd: str, ym: str) -> Iterator[dict]:
        yield from self._items(SALE_URL, lawd_cd, ym)

    def rents(self, lawd_cd: str, ym: str) -> Iterator[dict]:
        yield from self._items(RENT_URL, lawd_cd, ym)

    def legal_dongs(self) -> Iterator[dict]:
        """법정동코드 전체 (현존만). JSON 응답."""
        page = 1
        while True:
            if self.quota:
                self.quota.take()
            r = self.http.get(
                LEGAL_DONG_URL,
                params={"ServiceKey": self.key, "type": "json", "pageNo": page, "numOfRows": 1000, "flag": "Y"},
            )
            r.raise_for_status()
            data = r.json()
            block = data.get("StanReginCd")
            if not block:
                raise MolitError(f"법정동코드 응답 오류: {str(data)[:200]}")
            rows = next((b["row"] for b in block if "row" in b), [])
            yield from rows
            total = next((b["head"][0]["totalCount"] for b in block if "head" in b), 0)
            if page * 1000 >= int(total) or not rows:
                break
            page += 1


# ---------------------------------------------------------------- 변환
def _int(v: str | None) -> int | None:
    v = (v or "").replace(",", "").strip()
    try:
        return int(float(v))
    except ValueError:
        return None


def _date(item: dict) -> dt.date:
    return dt.date(int(item["dealYear"]), int(item["dealMonth"]), int(item["dealDay"]))


def sale_record(item: dict, lawd_cd: str) -> dict:
    excl = float(item.get("excluUseAr") or 0)
    sgg = item.get("sggCd") or lawd_cd
    umd = item.get("umdCd") or ""
    apt_seq = item.get("aptSeq") or ""
    d = _date(item)
    price = _int(item.get("dealAmount")) or 0
    floor = _int(item.get("floor"))
    key = f"S|{apt_seq or sgg + item.get('aptNm', '')}|{d}|{excl}|{floor}|{price}|{item.get('aptDong', '')}"
    return {
        "dedupe_key": key[:160],
        "kind": "sale",
        "apt_seq": apt_seq,
        "lawd_cd": sgg,
        "dong_code": (sgg + umd) if umd else "",
        "dong_name": item.get("umdNm", ""),
        "apt_name": item.get("aptNm", ""),
        "jibun": item.get("jibun", ""),
        "built_year": _int(item.get("buildYear")),
        "exclusive_m2": excl,
        "deal_date": d,
        "price": price,
        "monthly": 0,
        "floor": floor,
        "cancelled": (item.get("cdealType") or "").strip().upper() == "O",
    }


def rent_record(item: dict, lawd_cd: str) -> dict:
    excl = float(item.get("excluUseAr") or 0)
    sgg = item.get("sggCd") or lawd_cd
    d = _date(item)
    deposit = _int(item.get("deposit")) or 0
    monthly = _int(item.get("monthlyRent")) or 0
    floor = _int(item.get("floor"))
    name = item.get("aptNm", "")
    key = f"R|{sgg}|{name}|{item.get('jibun', '')}|{d}|{excl}|{floor}|{deposit}|{monthly}"
    umd = item.get("umdCd") or ""
    return {
        "dedupe_key": key[:160],
        "kind": "jeonse" if monthly == 0 else "monthly",
        "apt_seq": item.get("aptSeq") or "",
        "lawd_cd": sgg,
        "dong_code": (sgg + umd) if umd else "",
        "dong_name": item.get("umdNm", ""),
        "apt_name": name,
        "jibun": item.get("jibun", ""),
        "built_year": _int(item.get("buildYear")),
        "exclusive_m2": excl,
        "deal_date": d,
        "price": deposit,
        "monthly": monthly,
        "floor": floor,
        "cancelled": False,
    }
