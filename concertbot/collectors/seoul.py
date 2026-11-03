from __future__ import annotations

from ..models import Event, Source
from ..util import guess_genre, now_kst, parse_ymd, today_kst, session
from .base import need

URL = "http://openapi.seoul.go.kr:8088/{key}/json/culturalEventInfo/{a}/{b}/"


def collect(cfg, secrets, config):
    (key,) = need(secrets, "SEOUL_API_KEY")
    codes = set(cfg.get("codenames", ["콘서트", "클래식", "국악"]))
    kw = cfg.get("keywords_any_code", [])
    s = session()
    fetched = now_kst().isoformat(timespec="seconds")
    today = today_kst().isoformat()
    events = []
    for a in range(1, int(cfg.get("max_rows", 3000)), 1000):
        r = s.get(URL.format(key=key, a=a, b=a + 999), timeout=30)
        r.raise_for_status()
        rows = r.json().get("culturalEventInfo", {}).get("row", [])
        for row in rows:
            title = row.get("TITLE", "")
            hit_kw = any(k in title for k in kw)
            if row.get("CODENAME") not in codes and not hit_kw:
                continue
            ed = parse_ymd(row.get("END_DATE", ""))
            if ed and ed < today:
                continue
            events.append(Event(
                title=row.get("TITLE", "").strip(),
                start_date=parse_ymd(row.get("STRTDATE", "")),
                end_date=ed,
                venue=row.get("PLACE", "").strip(),
                region="서울",
                city=row.get("GUNAME", ""),
                artists=[p.strip() for p in (row.get("PLAYER") or "").split(",") if p.strip()][:6],
                genre=guess_genre(title) or {"콘서트": "대중음악"}.get(row.get("CODENAME"), row.get("CODENAME")),
                times=row.get("DATE", ""),
                price=row.get("USE_FEE", "") or ("무료" if row.get("IS_FREE") == "무료" else ""),
                poster=row.get("MAIN_IMG", ""),
                ticket_links=[{"name": "상세", "url": row["ORG_LINK"]}] if row.get("ORG_LINK") else [],
                sources=[Source("seoul", row.get("HMPG_ADDR") or row.get("ORG_LINK", ""), fetched)],
                confidence=0.95,
            ))
        if len(rows) < 1000:
            break
    return [e for e in events if e.start_date], []
