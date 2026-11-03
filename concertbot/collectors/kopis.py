from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import timedelta

from ..models import Event, Source
from ..util import log, normalize_region, now_kst, parse_ymd, session, today_kst
from .base import need

BASE = "https://kopis.or.kr/openApi/restful"
GENRE_NAMES = {"CCCD": "대중음악", "CCCA": "클래식", "CCCC": "국악", "GGGA": "뮤지컬"}


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

    events = []
    for i, (g, db) in enumerate(items):
        mt20id = _txt(db, "mt20id")
        ev = Event(
            title=_txt(db, "prfnm"),
            start_date=parse_ymd(_txt(db, "prfpdfrom")),
            end_date=parse_ymd(_txt(db, "prfpdto")),
            venue=_txt(db, "fcltynm"),
            region=normalize_region(_txt(db, "area")),
            genre=GENRE_NAMES.get(g, _txt(db, "genrenm")),
            poster=_txt(db, "poster").replace("http://", "https://"),
            status=_txt(db, "prfstate"),
            sources=[Source("kopis", f"https://www.kopis.or.kr/por/db/pblprfr/pblprfrView.do?menuId=MNU_00020&mt20Id={mt20id}", fetched)],
            confidence=1.0,
        )
        if i < max_detail:
            try:
                _enrich_detail(s, key, mt20id, ev)
            except Exception as e:  # noqa: BLE001
                log.debug("kopis detail %s failed: %s", mt20id, e)
            time.sleep(0.1)
        if ev.start_date:
            events.append(ev)
    return events, []


def _enrich_detail(s, key, mt20id, ev: Event):
    r = s.get(f"{BASE}/pblprfr/{mt20id}", params={"service": key}, timeout=30)
    r.raise_for_status()
    db = ET.fromstring(r.content).find("db")
    if db is None:
        return
    cast = _txt(db, "prfcast").replace(" 등", "")
    ev.artists = [c.strip() for c in cast.split(",") if c.strip()][:6]
    ev.price = _txt(db, "pcseguidance")
    ev.times = _txt(db, "dtguidance")
    area = _txt(db, "area")
    if area and not ev.region:
        ev.region = normalize_region(area)
    links = []
    for rel in db.findall("relates/relate"):
        name, url = _txt(rel, "relatenm"), _txt(rel, "relateurl")
        if url:
            links.append({"name": name or "예매", "url": url})
    ev.ticket_links = links[:4]
