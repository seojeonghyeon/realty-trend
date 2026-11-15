"""부가 소스: 카카오 로컬(역·학교 거리, A6), 텔레그램 알림(A1)."""
from __future__ import annotations

import httpx

from collector.util import log

KAKAO_CATEGORY = "https://dapi.kakao.com/v2/local/search/category.json"


def nearest_place(rest_key: str, lat: float, lng: float, category: str, name_suffix: str = "") -> tuple[str, int] | None:
    """category: SW8(지하철역), SC4(학교). 가장 가까운 장소 (이름, 거리m)."""
    try:
        r = httpx.get(
            KAKAO_CATEGORY,
            params={"category_group_code": category, "x": lng, "y": lat, "radius": 3000, "sort": "distance"},
            headers={"Authorization": f"KakaoAK {rest_key}"},
            timeout=10,
        )
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("카카오 로컬 실패: %s", e)
        return None
    for doc in r.json().get("documents", []):
        if name_suffix and not doc.get("place_name", "").endswith(name_suffix):
            continue
        return doc.get("place_name", ""), int(doc.get("distance") or 0)
    return None


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    if not token or not chat_id:
        return False
    chunks = [text[i : i + 3900] for i in range(0, len(text), 3900)] or [""]
    ok = True
    for chunk in chunks:
        try:
            r = httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=15,
            )
            ok = ok and r.status_code == 200
        except httpx.HTTPError as e:
            log.warning("텔레그램 전송 실패: %s", e)
            ok = False
    return ok
