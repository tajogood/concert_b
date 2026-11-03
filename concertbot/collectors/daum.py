from __future__ import annotations

import time
from datetime import datetime, timedelta

from ..models import RawPost
from ..util import now_kst, session, strip_html
from .base import need
from .naver import build_queries


def collect(cfg, secrets, config):
    (key,) = need(secrets, "KAKAO_REST_KEY")
    s = session()
    s.headers["Authorization"] = f"KakaoAK {key}"
    cutoff = now_kst() - timedelta(hours=int(cfg.get("lookback_hours", 36)))
    posts = []
    for q in build_queries(cfg, config):
        for page in (1, 2):
            r = s.get("https://dapi.kakao.com/v2/search/cafe",
                      params={"query": q, "sort": "recency", "size": 50, "page": page}, timeout=20)
            r.raise_for_status()
            docs = r.json().get("documents", [])
            stop = False
            for d in docs:
                try:
                    if datetime.fromisoformat(d["datetime"]) < cutoff:
                        stop = True
                        continue
                except Exception:  # noqa: BLE001
                    pass
                posts.append(RawPost("daum_cafe", d["url"], strip_html(d["title"]),
                                     strip_html(d["contents"]), d.get("datetime", ""), d.get("thumbnail") or None))
            if stop or len(docs) < 50:
                break
            time.sleep(0.1)
    return [], posts
