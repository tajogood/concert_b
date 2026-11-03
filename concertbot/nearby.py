"""축제장 근처 관광지 — 한국관광공사 위치기반 관광정보(locationBasedList2)."""
from __future__ import annotations

import json
import os
import time
from urllib.parse import unquote

from .util import log, session, strip_html

BASE = "https://apis.data.go.kr/B551011/KorService2"
CACHE = os.path.join("data", "nearby_cache.json")


def _items(r):
    body = r.json().get("response", {}).get("body", {})
    items = (body.get("items") or {}).get("item", [])
    return [items] if isinstance(items, dict) else items


def find_nearby(lat: float, lng: float, key: str, cfg: dict, exclude_title: str = "") -> list[dict]:
    """반경 안 관광지 중 사진 있는 곳을 가까운 순으로. 결과는 좌표별로 캐시."""
    if not (lat and lng and key):
        return []
    n = int(cfg.get("count", 4))
    radius = int(cfg.get("radius_m", 15000))
    ck = f"{lat:.4f},{lng:.4f},{radius},{n}"
    cache = _load()
    if ck in cache:
        return cache[ck]
    s = session()
    common = {"serviceKey": unquote(key), "MobileOS": "ETC", "MobileApp": "onstage", "_type": "json"}
    out = []
    try:
        r = s.get(f"{BASE}/locationBasedList2", params={
            **common, "mapX": lng, "mapY": lat, "radius": radius, "contentTypeId": cfg.get("content_type", 12),
            "arrange": "S", "numOfRows": 30, "pageNo": 1}, timeout=20)
        for it in _items(r):
            if not it.get("firstimage") or (exclude_title and it.get("title", "") in exclude_title):
                continue
            out.append({
                "id": str(it.get("contentid")), "title": it.get("title", "").strip(),
                "addr": it.get("addr1", ""), "image": it["firstimage"].replace("http://", "https://"),
                "dist_km": round(float(it.get("dist") or 0) / 1000, 1),
                "license": it.get("cpyrhtDivCd", ""),
            })
            if len(out) >= n:
                break
        for a in out:
            time.sleep(0.2)
            try:
                r = s.get(f"{BASE}/detailCommon2", params={**common, "contentId": a["id"]}, timeout=20)
                a["overview"] = strip_html((_items(r) or [{}])[0].get("overview", ""))[:400]
            except Exception:  # noqa: BLE001
                a["overview"] = ""
    except Exception as e:  # noqa: BLE001
        log.warning("[nearby] %s,%s: %s", lat, lng, e)
        return []
    cache[ck] = out
    _save(cache)
    return out


def _load():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def _save(c):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    if len(c) > 300:  # 오래된 것부터 정리
        c = dict(list(c.items())[-300:])
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False)
