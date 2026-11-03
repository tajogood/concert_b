from __future__ import annotations

from ..models import RawPost
from ..util import log, session
from .base import need


def collect(cfg, secrets, config):
    token, ig_id = need(secrets, "IG_ACCESS_TOKEN", "IG_USER_ID")
    ver = config.get("instagram", {}).get("api_version", "v26.0")
    base = f"https://graph.facebook.com/{ver}"
    s = session()
    posts = []

    for tag in cfg.get("hashtags", [])[:30]:
        try:
            r = s.get(f"{base}/ig_hashtag_search", params={"user_id": ig_id, "q": tag, "access_token": token}, timeout=20)
            r.raise_for_status()
            hid = r.json()["data"][0]["id"]
            r = s.get(f"{base}/{hid}/recent_media", params={
                "user_id": ig_id, "fields": "caption,permalink,timestamp", "limit": 50, "access_token": token}, timeout=20)
            r.raise_for_status()
            for m in r.json().get("data", []):
                cap = m.get("caption") or ""
                posts.append(RawPost("instagram", m["permalink"], cap[:80], cap, m.get("timestamp", "")))
        except Exception as e:  # noqa: BLE001
            log.warning("instagram #%s: %s", tag, e)

    n = int(cfg.get("account_media_limit", 8))
    for handle in cfg.get("accounts", []):
        h = handle.lstrip("@")
        try:
            r = s.get(f"{base}/{ig_id}", params={
                "fields": f"business_discovery.username({h}){{media.limit({n}){{caption,permalink,timestamp}}}}",
                "access_token": token}, timeout=20)
            r.raise_for_status()
            for m in r.json().get("business_discovery", {}).get("media", {}).get("data", []):
                cap = m.get("caption") or ""
                posts.append(RawPost("instagram", m["permalink"], f"@{h} " + cap[:60], cap, m.get("timestamp", "")))
        except Exception as e:  # noqa: BLE001
            log.warning("instagram @%s: %s", h, e)
    return [], posts
