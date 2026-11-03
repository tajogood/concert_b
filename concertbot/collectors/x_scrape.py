from __future__ import annotations

import base64
import json
import os
import random
import tempfile
from urllib.parse import quote

from ..models import RawPost
from ..util import log
from .base import need


def _storage_state(secrets):
    if os.path.exists("x_state.json"):
        return "x_state.json"
    (b64,) = need(secrets, "X_STORAGE_STATE_B64")
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(json.loads(base64.b64decode(b64)), f)
    f.close()
    return f.name


def collect(cfg, secrets, config):
    from playwright.sync_api import sync_playwright

    state = _storage_state(secrets)
    targets = [f"https://x.com/search?q={quote(q + ' lang:ko')}&f=live" for q in cfg.get("queries", [])]
    targets += [f"https://x.com/{h.lstrip('@')}" for h in cfg.get("accounts", [])]
    per_page = int(cfg.get("max_per_page", 30))
    posts = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(storage_state=state, locale="ko-KR", viewport={"width": 1280, "height": 2000})
        page = ctx.new_page()
        for url in targets:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_selector('article[data-testid="tweet"]', timeout=15000)
                for _ in range(3):
                    page.mouse.wheel(0, 2500)
                    page.wait_for_timeout(1200 + random.randint(0, 800))
                rows = page.eval_on_selector_all('article[data-testid="tweet"]', """els => els.map(e => {
                    const t = e.querySelector('[data-testid="tweetText"]');
                    const a = [...e.querySelectorAll('a[href*="/status/"]')].map(x => x.href)[0];
                    const tm = e.querySelector('time');
                    return {text: t ? t.innerText : '', url: a || '', time: tm ? tm.dateTime : ''};
                })""")
                for r in rows[:per_page]:
                    if r["text"] and r["url"]:
                        posts.append(RawPost("x", r["url"].split("?")[0], r["text"][:80], r["text"], r["time"]))
            except Exception as e:  # noqa: BLE001
                log.warning("x_scrape %s: %s", url, e)
            page.wait_for_timeout(2000 + random.randint(0, 2000))
        b.close()
    # 중복 제거
    uniq = {p.url: p for p in posts}
    return [], list(uniq.values())
