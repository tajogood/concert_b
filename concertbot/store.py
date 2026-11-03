from __future__ import annotations

import json
import os
from datetime import date, timedelta

from rapidfuzz import fuzz

from .models import Event
from .util import log, norm_text, now_kst, today_kst

FIELDS_FILL = ["venue", "region", "city", "times", "price", "poster", "ticket_open", "status", "genre",
               "story", "intro_images", "runtime", "age", "company"]


def load(path: str) -> list[Event]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [Event.from_dict(d) for d in json.load(f)]


def save(path: str, events: list[Event]):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    events = sorted(events, key=lambda e: (e.start_date, e.region, e.title))
    with open(path, "w", encoding="utf-8") as f:
        json.dump([e.to_dict() for e in events], f, ensure_ascii=False, indent=1)


def _overlap(a: Event, b: Event, slack: int = 1) -> bool:
    try:
        a0, a1 = date.fromisoformat(a.start_date), date.fromisoformat(a.end_date or a.start_date)
        b0, b1 = date.fromisoformat(b.start_date), date.fromisoformat(b.end_date or b.start_date)
    except ValueError:
        return False
    return a0 <= b1 + timedelta(days=slack) and b0 <= a1 + timedelta(days=slack)


def same_event(a: Event, b: Event) -> bool:
    if a.id == b.id:
        return True
    if not _overlap(a, b):
        return False
    ta, tb = norm_text(a.title), norm_text(b.title)
    if ta and tb and (fuzz.token_set_ratio(ta, tb) >= 82 or (len(ta) > 4 and (ta in tb or tb in ta))):
        return True
    va, vb = norm_text(a.venue), norm_text(b.venue)
    venue_ok = va and vb and fuzz.partial_ratio(va, vb) >= 85
    arts_a = {norm_text(x) for x in a.artists}
    arts_b = {norm_text(x) for x in b.artists}
    return bool(venue_ok and arts_a & arts_b)


def merge_into(base: Event, new: Event):
    hi, lo = (new, base) if new.confidence >= base.confidence else (base, new)
    if hi is new:
        base.title, base.start_date, base.end_date = new.title, new.start_date, new.end_date
    for f in FIELDS_FILL:
        if not getattr(base, f) or (hi is new and getattr(new, f)):
            setattr(base, f, getattr(hi, f) or getattr(lo, f))
    if not base.artists or (hi is new and new.artists):
        base.artists = hi.artists or lo.artists
    urls = {s.url for s in base.sources}
    base.sources += [s for s in new.sources if s.url not in urls]
    links = {l["url"] for l in base.ticket_links}
    base.ticket_links += [l for l in new.ticket_links if l["url"] not in links]
    base.confidence = max(base.confidence, new.confidence)
    # 비정형 소스만 있을 때: 서로 다른 출처에서 반복 확인되면 신뢰도 상승
    if base.confidence < 0.9:
        kinds = {s.name.split(":")[0] for s in base.sources}
        base.confidence = min(0.85, max(base.confidence, 0.4 + 0.15 * len(kinds)))
    base.updated = now_kst().isoformat(timespec="seconds")


def merge(existing: list[Event], incoming: list[Event], bootstrap: bool = False) -> tuple[list[Event], list[Event]]:
    """returns (all_events, newly_added)"""
    today = today_kst().isoformat()
    stamp = now_kst().isoformat(timespec="seconds")
    # 첫 실행: 이미 공개돼 있던 공연을 전부 '새로 뜬 공연'으로 잡지 않도록 어제 날짜로 기록
    if bootstrap:
        stamp_new = (now_kst() - timedelta(days=1)).isoformat(timespec="seconds")
    else:
        stamp_new = stamp
    # 지난 공연 정리
    events = [e for e in existing if (e.end_date or e.start_date) >= today]
    # 정형 데이터를 먼저 넣어 기준점으로 삼는다
    incoming = sorted(incoming, key=lambda e: -e.confidence)
    added = []
    for ev in incoming:
        if (ev.end_date or ev.start_date) < today or not ev.title:
            continue
        match = next((e for e in events if same_event(e, ev)), None)
        if match:
            merge_into(match, ev)
        else:
            ev.first_seen = ev.first_seen or stamp_new
            ev.updated = stamp
            events.append(ev)
            added.append(ev)
    log.info("[store] total=%d new=%d", len(events), len(added))
    return events, added


def publishable(events: list[Event], min_conf: float) -> list[Event]:
    return [e for e in events if e.confidence >= min_conf]
