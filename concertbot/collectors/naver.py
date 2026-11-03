from __future__ import annotations

import time
from datetime import timedelta
from email.utils import parsedate_to_datetime

from ..models import RawPost
from ..util import now_kst, session, strip_html
from .base import need

API = "https://openapi.naver.com/v1/search/{kind}.json"


def build_queries(cfg, config):
    qs = list(cfg.get("queries", []))
    for a in config.get("watch_artists", []):
        qs.append(f"{a} 콘서트")
    return list(dict.fromkeys(qs))


def collect(cfg, secrets, config):
    cid, csec = need(secrets, "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET")
    s = session()
    s.headers.update({"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec})
    kinds = cfg.get("kinds", ["news", "cafearticle"])
    hours = int(cfg.get("lookback_hours", 36))
    cutoff = now_kst() - timedelta(hours=hours)
    posts, seen = [], set()
    for kind in kinds:
        for q in build_queries(cfg, config):
            r = s.get(API.format(kind=kind), params={"query": q, "display": 100, "sort": "date"}, timeout=20)
            if r.status_code == 429:
                time.sleep(1)
                continue
            r.raise_for_status()
            for it in r.json().get("items", []):
                url = it.get("originallink") or it.get("link")
                if not url or url in seen:
                    continue
                pub = it.get("pubDate") or it.get("postdate")
                if pub and kind == "news":
                    try:
                        if parsedate_to_datetime(pub) < cutoff:
                            continue
                    except Exception:  # noqa: BLE001
                        pass
                seen.add(url)
                posts.append(RawPost(
                    source=f"naver_{'cafe' if kind == 'cafearticle' else kind}",
                    url=url, title=strip_html(it.get("title", "")),
                    text=strip_html(it.get("description", "")),
                    posted_at=pub or "",
                ))
            time.sleep(0.12)
    return [], posts
