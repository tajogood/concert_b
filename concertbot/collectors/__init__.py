from __future__ import annotations

import traceback

from ..util import log
from .base import SkipSource
from . import kopis, tour, seoul, datagokr, naver, daum, x_api, x_scrape, instagram_tags, web_board

REGISTRY = {
    "kopis": kopis.collect,
    "tour": tour.collect,
    "seoul": seoul.collect,
    "datagokr": datagokr.collect,
    "naver": naver.collect,
    "daum": daum.collect,
    "x_api": x_api.collect,
    "x_scrape": x_scrape.collect,
    "instagram": instagram_tags.collect,
    "web_board": web_board.collect,
}


def run_all(config: dict, secrets: dict):
    events, posts, report = [], [], {}
    for name, fn in REGISTRY.items():
        scfg = config.get("sources", {}).get(name, {})
        if not scfg.get("enabled", False):
            report[name] = "disabled"
            continue
        try:
            ev, ps = fn(scfg, secrets, config)
            events += ev
            posts += ps
            report[name] = f"ok events={len(ev)} posts={len(ps)}"
        except SkipSource as e:
            report[name] = f"skipped: {e}"
        except Exception as e:  # noqa: BLE001
            report[name] = f"error: {e}"
            log.debug(traceback.format_exc())
        log.info("[collect] %-10s %s", name, report[name])
    return events, posts, report

