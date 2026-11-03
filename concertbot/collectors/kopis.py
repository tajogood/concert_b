from __future__ import annotations

import json
import os
import time
import xml.etree.ElementTree as ET
from datetime import timedelta

from ..models import Event, Source
from ..util import guess_genre, log, normalize_region, now_kst, parse_city, parse_ymd, session, today_kst
from .base import need

BASE = "https://kopis.or.kr/openApi/restful"
CACHE = os.path.join("data", "kopis_detail.json")
FAC_CACHE = os.path.join("data", "kopis_facility.json")
GENRE_NAMES = {"CCCD": "대중음악", "CCCA": "클래식", "CCCC": "국악", "EEEB": "서커스·마술", "GGGA": "뮤지컬"}


def _txt(el, tag):
    x = el.find(tag)
    return (x.text or "").strip() if x is not None and x.text else ""


def collect(cfg, secrets, config):
    (key,) = need(secrets, "KOPIS_API_KEY")
    s = session()
    days = int(cfg.get("days_ahead", 90))
    st = today_kst()
    ed = st + timedelta(days=days)
    genres = cfg.get("genres", ["CCCD"])
    max_detail = int(cfg.get("max_detail", 300))
    fetched = now_kst().isoformat(timespec="seconds")

    items = []
    for g in genres:
        page = 1
        while True:
            r = s.get(f"{BASE}/pblprfr", params={
                "service": key, "stdate": st.strftime("%Y%m%d"), "eddate": ed.strftime("%Y%m%d"),
                "cpage": page, "rows": 100, "shcate": g,
            }, timeout=30)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            dbs = root.findall("db")
            for db in dbs:
                items.append((g, db))
            if len(dbs) < 100 or page >= 30:
                break
            page += 1
            time.sleep(0.2)

    cache = _load(CACHE)
    facilities = _load(FAC_CACHE)
    max_fac = int(cfg.get("max_facility", 800))
    fetched_detail = fetched_fac = 0
    events = []
    for g, db in items:
        mt20id = _txt(db, "mt20id")
        ev = Event(
            title=_txt(db, "prfnm"),
            start_date=parse_ymd(_txt(db, "prfpdfrom")),
            end_date=parse_ymd(_txt(db, "prfpdto")),
            venue=_txt(db, "fcltynm"),
            region=normalize_region(_txt(db, "area")),
            genre=guess_genre(_txt(db, "prfnm")) or GENRE_NAMES.get(g, _txt(db, "genrenm")),
            poster=_txt(db, "poster").replace("http://", "https://"),
            status=_txt(db, "prfstate"),
            sources=[Source("kopis", f"https://www.kopis.or.kr/por/db/pblprfr/pblprfrView.do?menuId=MNU_00020&mt20Id={mt20id}", fetched)],
            confidence=1.0,
        )
        if ("mt10id" not in cache.get(mt20id, {})) and fetched_detail < max_detail:
            try:
                cache[mt20id] = _fetch_detail(s, key, mt20id)
            except Exception as e:  # noqa: BLE001
                log.debug("kopis detail %s failed: %s", mt20id, e)
            fetched_detail += 1
            time.sleep(0.1)
        if mt20id in cache:
            _apply_detail(cache[mt20id], ev)
            fid = cache[mt20id].get("mt10id", "")
            if fid and fid not in facilities and fetched_fac < max_fac:
                try:
                    facilities[fid] = _fetch_facility(s, key, fid)
                except Exception as e:  # noqa: BLE001
                    log.debug("kopis facility %s failed: %s", fid, e)
                fetched_fac += 1
                time.sleep(0.1)
            fac = facilities.get(fid, {})
            ev.city = fac.get("city", "")
            if not ev.region and fac.get("address"):
                ev.region = normalize_region(fac["address"])
        if ev.start_date:
            events.append(ev)
    live = {_txt(db, "mt20id") for _, db in items}
    cache = {k: v for k, v in cache.items() if k in live}
    _save(CACHE, cache)
    _save(FAC_CACHE, facilities)
    log.info("[kopis] detail fetched=%d cached=%d, facility fetched=%d cached=%d",
             fetched_detail, len(cache), fetched_fac, len(facilities))
    return events, []


def _fetch_detail(s, key, mt20id) -> dict:
    r = s.get(f"{BASE}/pblprfr/{mt20id}", params={"service": key}, timeout=30)
    r.raise_for_status()
    db = ET.fromstring(r.content).find("db")
    if db is None:
        return {}
    cast = _txt(db, "prfcast").replace(" 등", "")
    links = []
    for rel in db.findall("relates/relate"):
        name, url = _txt(rel, "relatenm"), _txt(rel, "relateurl")
        if url:
            links.append({"name": name or "예매", "url": url})
    return {
        "artists": [c.strip() for c in cast.split(",") if c.strip()][:6],
        "price": _txt(db, "pcseguidance"),
        "times": _txt(db, "dtguidance"),
        "area": _txt(db, "area"),
        "mt10id": _txt(db, "mt10id"),
        "links": links[:4],
    }


def _fetch_facility(s, key, mt10id) -> dict:
    r = s.get(f"{BASE}/prfplc/{mt10id}", params={"service": key}, timeout=30)
    r.raise_for_status()
    db = ET.fromstring(r.content).find("db")
    if db is None:
        return {}
    addr = _txt(db, "adres")
    return {"address": addr, "city": parse_city(addr)}


def _load(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _apply_detail(d: dict, ev: Event):
    ev.artists = d.get("artists", [])
    ev.price = d.get("price", "")
    ev.times = d.get("times", "")
    if d.get("area") and not ev.region:
        ev.region = normalize_region(d["area"])
    ev.ticket_links = d.get("links", [])
