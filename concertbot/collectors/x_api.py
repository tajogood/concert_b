from __future__ import annotations

from ..models import RawPost
from ..util import session
from .base import need


def collect(cfg, secrets, config):
    (token,) = need(secrets, "X_BEARER_TOKEN")
    s = session()
    s.headers["Authorization"] = f"Bearer {token}"
    posts = []
    budget = int(cfg.get("max_requests", 5))
    for q in cfg.get("queries", [])[:budget]:
        r = s.get("https://api.x.com/2/tweets/search/recent", params={
            "query": f"({q}) lang:ko -is:retweet",
            "max_results": 100,
            "tweet.fields": "created_at,author_id",
            "expansions": "author_id", "user.fields": "username",
        }, timeout=20)
        r.raise_for_status()
        data = r.json()
        users = {u["id"]: u["username"] for u in data.get("includes", {}).get("users", [])}
        for t in data.get("data", []):
            uname = users.get(t.get("author_id"), "i")
            posts.append(RawPost("x", f"https://x.com/{uname}/status/{t['id']}",
                                 t["text"][:80], t["text"], t.get("created_at", "")))
    return [], posts
