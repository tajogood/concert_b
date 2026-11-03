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
DETAIL_V = 2
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
    max_detail = int(cfg.get("max_detail", 500))
    fetched = now_kst().isoformat(timespec="seconds")

    items = []
    failures = []
    for g in genres:
        page = 1
        while True:
            r = _get(s, f"{BASE}/pblprfr", {
                "service": key, "stdate": st.strftime("%Y%m%d"), "eddate": ed.strftime("%Y%m%d"),
                "cpage": page, "rows": 100, "shcate": g,
            })
            if r is None:
                failures.append(f"{g} p{page}")
                break
            dbs = ET.fromstring(r.content).findall("db")
            for db in dbs:
                items.append((g, db))
            if len(dbs) < 100 or page >= 30:
                break
            page += 1
            time.sleep(1.0)
    if failures:
        log.warning("[kopis] list pages failed: %s (수집된 %d건으로 계속)", ", ".join(failures), len(items))
    if not items:
        raise RuntimeError("KOPIS 목록을 하나도 받지 못함")

    cache = _load(CACHE)
    facilities = _load(FAC_CACHE)
    max_fac = int(cfg.get("max_facility", 300))
    pace = float(cfg.get("pace_sec", 0.8))
    deadline = time.time() + int(cfg.get("time_budget_sec", 900))
    fetched_detail = fetched_fac = fails = 0
    stop_lookups = False

    def budget_ok():
        return not stop_lookups and time.time() < deadline

    # 가까운 날짜 공연부터 상세·주소를 채운다
    items.sort(key=lambda x: max(parse_ymd(_txt(x[1], "prfpdfrom")) or "9", today_kst().isoformat()))
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
        if cache.get(mt20id, {}).get("v") != DETAIL_V and fetched_detail < max_detail and budget_ok():
            fetched_detail += 1
            try:
                cache[mt20id] = _fetch_detail(s, key, mt20id)
                fails = 0
            except Exception as e:  # noqa: BLE001
                log.debug("kopis detail %s failed: %s", mt20id, e)
                fails += 1
            time.sleep(pace)
        if mt20id in cache:
            _apply_detail(cache[mt20id], ev)
            fid = cache[mt20id].get("mt10id", "")
            if fid and fid not in facilities and fetched_fac < max_fac and budget_ok():
                fetched_fac += 1
                try:
                    facilities[fid] = _fetch_facility(s, key, fid)
                    fails = 0
                except Exception as e:  # noqa: BLE001
                    log.debug("kopis facility %s failed: %s", fid, e)
                    fails += 1
                time.sleep(pace)
            fac = facilities.get(fid, {})
            ev.city = fac.get("city", "")
            if not ev.region and fac.get("address"):
                ev.region = normalize_region(fac["address"])
        if fails >= 4 and not stop_lookups:
            stop_lookups = True
            log.warning("[kopis] 연속 차단돼 오늘 상세·주소 조회는 여기까지 (상세 %d, 주소 %d)", fetched_detail, fetched_fac)
        if ev.start_date:
            events.append(ev)
    # 상세를 아직 못 받은 공연도 같은 시·도의 같은 공연장이면 시·군·구를 재사용
    venue_city = {}
    for e in events:
        if e.city and e.venue:
            venue_city.setdefault((e.region, e.venue), e.city)
    for f in facilities.values():
        if f.get("name") and f.get("city"):
            venue_city.setdefault((normalize_region(f.get("address", "")), f["name"]), f["city"])
    for e in events:
        if not e.city and e.venue:
            e.city = venue_city.get((e.region, e.venue)) or next(
                (c for (r, v), c in venue_city.items() if r == e.region and len(v) >= 4 and (e.venue.startswith(v) or v.startswith(e.venue))), "")
    if not failures:  # 목록을 다 받았을 때만 끝난 공연의 캐시를 정리
        live = {_txt(db, "mt20id") for _, db in items}
        cache = {k: v for k, v in cache.items() if k in live}
    _save(CACHE, cache)
    _save(FAC_CACHE, facilities)
    log.info("[kopis] detail fetched=%d cached=%d, facility fetched=%d cached=%d",
             fetched_detail, len(cache), fetched_fac, len(facilities))
    return events, []


def _get(s, url, params, tries=4):
    """KOPIS는 짧은 시간에 호출이 몰리면 400/5xx를 돌려줄 때가 있어 간격을 늘려 재시도한다."""
    last = ""
    for n in range(tries):
        try:
            r = s.get(url, params=params, timeout=30)
            if r.status_code == 200 and r.content.lstrip().startswith(b"<"):
                return r
            last = f"HTTP {r.status_code} {r.text[:200]!r}"
        except Exception as e:  # noqa: BLE001
            last = str(e)
        if n < tries - 1:
            time.sleep(20 if "Blocked" in last else 3 * (n + 1) ** 2)
    log.warning("[kopis] %s failed: %s", url.rsplit("/", 1)[-1], last)
    return None


def _fetch_detail(s, key, mt20id) -> dict:
    r = _get(s, f"{BASE}/pblprfr/{mt20id}", {"service": key}, tries=2)
    if r is None:
        raise RuntimeError("detail failed")
    db = ET.fromstring(r.content).find("db")
    if db is None:
        return {}
    cast = _txt(db, "prfcast").replace(" 등", "")
    links = []
    for rel in db.findall("relates/relate"):
        name, url = _txt(rel, "relatenm"), _txt(rel, "relateurl")
        if url:
            links.append({"name": name or "예매", "url": url})
    company = next((_txt(db, t) for t in ("entrpsnmP", "entrpsnmA", "entrpsnmH", "entrpsnmS", "entrpsnm") if _txt(db, t)), "")
    return {
        "v": DETAIL_V,
        "story": _txt(db, "sty"),
        "intro_images": [(x.text or "").strip().replace("http://", "https://") for x in db.findall("styurls/styurl") if x.text][:10],
        "runtime": _txt(db, "prfruntime"),
        "age": _txt(db, "prfage"),
        "company": company,
        "artists": [c.strip() for c in cast.split(",") if c.strip()][:6],
        "price": _txt(db, "pcseguidance"),
        "times": _txt(db, "dtguidance"),
        "area": _txt(db, "area"),
        "mt10id": _txt(db, "mt10id"),
        "links": links[:4],
    }


def _fetch_facility(s, key, mt10id) -> dict:
    r = _get(s, f"{BASE}/prfplc/{mt10id}", {"service": key}, tries=2)
    if r is None:
        raise RuntimeError("facility failed")
    db = ET.fromstring(r.content).find("db")
    if db is None:
        return {}
    addr = _txt(db, "adres")
    return {"name": _txt(db, "fcltynm"), "address": addr, "city": parse_city(addr)}


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
    ev.story = d.get("story", "")
    ev.intro_images = d.get("intro_images", [])
    ev.runtime = d.get("runtime", "")
    ev.age = d.get("age", "")
    ev.company = d.get("company", "")
