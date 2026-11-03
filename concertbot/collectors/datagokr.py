from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import timedelta

from ..models import Event, Source
from ..util import normalize_region, now_kst, parse_ymd, session, today_kst, strip_html
from .base import need, SkipSource


def _get(d, path):
    for p in path.split("."):
        if isinstance(d, list):
            d = d[int(p)] if p.isdigit() and int(p) < len(d) else None
        elif isinstance(d, dict):
            d = d.get(p)
        else:
            return None
    return d


def _xml_items(content, item_tag):
    root = ET.fromstring(content)
    return [{c.tag: (c.text or "").strip() for c in it} for it in root.iter(item_tag)]


def collect(cfg, secrets, config):
    endpoints = [e for e in cfg.get("endpoints", []) if e.get("enabled", True)]
    if not endpoints:
        raise SkipSource("no endpoints configured")
    s = session()
    fetched = now_kst().isoformat(timespec="seconds")
    st, ed = today_kst(), today_kst() + timedelta(days=int(cfg.get("days_ahead", 90)))
    events = []
    for ep in endpoints:
        (key,) = need(secrets, ep.get("key_env", "DATA_GO_KR_KEY"))
        params = {k: str(v).replace("{KEY}", key).replace("{FROM}", st.strftime("%Y%m%d"))
                  .replace("{TO}", ed.strftime("%Y%m%d")) for k, v in ep.get("params", {}).items()}
        r = s.get(ep["url"], params=params, timeout=30)
        r.raise_for_status()
        if ep.get("format", "xml") == "xml":
            rows = _xml_items(r.content, ep.get("item_tag", "item"))
        else:
            rows = _get(r.json(), ep.get("items_path", "response.body.items.item")) or []
            if isinstance(rows, dict):
                rows = [rows]
        m = ep["fields"]
        kw = ep.get("include_keywords", [])
        for row in rows:
            g = lambda f: strip_html(str(row.get(m[f], "") or "")) if m.get(f) else ""  # noqa: E731
            blob = " ".join(str(v) for v in row.values())
            if kw and not any(k in blob for k in kw):
                continue
            sd = parse_ymd(g("start_date"))
            if not sd:
                continue
            events.append(Event(
                title=g("title"), start_date=sd, end_date=parse_ymd(g("end_date")),
                venue=g("venue"), region=normalize_region(g("region") or g("venue") or ep.get("default_region", "")),
                price=g("price"), poster=g("poster"),
                ticket_links=[{"name": "상세", "url": g("url")}] if g("url") else [],
                sources=[Source(ep.get("name", "datagokr"), g("url"), fetched)],
                confidence=0.9,
            ))
    return events, []
