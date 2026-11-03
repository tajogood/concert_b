from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import RawPost
from ..util import log, session, strip_html
from .base import SkipSource


def _rss(s, site):
    r = s.get(site["url"], timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for it in root.iter("item"):
        g = lambda t: (it.findtext(t) or "").strip()  # noqa: E731
        out.append(RawPost("web", g("link"), strip_html(g("title")), strip_html(g("description")), g("pubDate")))
    return out


def _html_from(html, site):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for el in soup.select(site["item"])[: int(site.get("max_items", 30))]:
        t = el.select_one(site["title"]) if site.get("title") else el
        a = el.select_one(site.get("link", "a"))
        x = el.select_one(site["text"]) if site.get("text") else None
        if not t or not a or not a.get("href"):
            continue
        out.append(RawPost("web", urljoin(site["url"], a["href"]), t.get_text(" ", strip=True),
                           x.get_text(" ", strip=True) if x else ""))
    return out


def _browser_html(url):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(locale="ko-KR")
        pg.goto(url, wait_until="networkidle", timeout=40000)
        html = pg.content()
        b.close()
    return html


def collect(cfg, secrets, config):
    sites = cfg.get("sites", [])
    if not sites:
        raise SkipSource("no sites configured")
    s = session()
    delay = float(cfg.get("delay_sec", 2))
    posts = []
    for site in sites:
        try:
            typ = site.get("type", "html")
            if typ == "rss":
                got = _rss(s, site)
            else:
                html = _browser_html(site["url"]) if typ == "browser" else s.get(site["url"], timeout=20).text
                got = _html_from(html, site)
                if site.get("follow") and site.get("body"):
                    for p in got:
                        time.sleep(delay)
                        try:
                            body = BeautifulSoup(s.get(p.url, timeout=20).text, "html.parser").select_one(site["body"])
                            if body:
                                p.text = body.get_text(" ", strip=True)[:3000]
                        except Exception:  # noqa: BLE001
                            pass
            for p in got:
                p.source = f"web:{site.get('name', '')}"
            posts += got
        except Exception as e:  # noqa: BLE001
            log.warning("web_board %s: %s", site.get("name"), e)
        time.sleep(delay)
    return [], posts
