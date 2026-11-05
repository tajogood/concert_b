#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

import yaml

from concertbot import capture, render, store
from concertbot.collectors import run_all
from concertbot.extract import extract_events
from concertbot.models import Event
from concertbot.util import log, now_kst, today_kst

ROOT = os.path.dirname(os.path.abspath(__file__))
SECRET_KEYS = ["KOPIS_API_KEY", "TOUR_API_KEY", "SEOUL_API_KEY", "DATA_GO_KR_KEY", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET",
               "KAKAO_REST_KEY", "X_BEARER_TOKEN", "X_STORAGE_STATE_B64", "IG_ACCESS_TOKEN", "IG_USER_ID",
               "ANTHROPIC_API_KEY"]


def load_config():
    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("SITE_URL"):
        cfg["site_url"] = os.environ["SITE_URL"]
    return cfg


def secrets():
    return {k: os.environ.get(k, "") for k in SECRET_KEYS}


def resolve_slot(args) -> str:
    if getattr(args, "slot", "auto") in ("noon", "evening"):
        return args.slot
    return "noon" if now_kst().hour < 15 else "evening"


def paths(cfg, slot="noon"):
    d = today_kst().isoformat()
    site = os.path.join(ROOT, cfg.get("site_dir", "site"))
    return {
        "events": os.path.join(ROOT, "data", "events.json"),
        "posted": os.path.join(ROOT, "data", "posted.json"),
        "report": os.path.join(ROOT, "data", "last_report.json"),
        "site": site,
        "cards": os.path.join(site, "cards", f"{d}-{slot}"),
        "cards_rel": f"cards/{d}-{slot}",
        "work": os.path.join(ROOT, "build", f"{d}-{slot}"),
        "date": d,
        "run": f"{d}-{slot}",
    }


# ------------------------------------------------------------------ commands
def cmd_collect(cfg, args):
    P = paths(cfg)
    existing = store.load(P["events"])
    if args.sample:
        with open(os.path.join(ROOT, "sample_data", "sample_events.json"), encoding="utf-8") as f:
            incoming = [Event.from_dict(d) for d in json.load(f)]
        # 샘플은 2026-10-27 기준으로 만들어져 있어 오늘 날짜로 평행이동
        from datetime import date as _date, timedelta as _td
        shift = (today_kst() - _date(2026, 10, 27)).days
        mv = lambda s: (_date.fromisoformat(s[:10]) + _td(days=shift)).isoformat() + s[10:] if s else s  # noqa: E731
        for e in incoming:
            e.start_date, e.end_date, e.first_seen = mv(e.start_date), mv(e.end_date), mv(e.first_seen)
        report = {"sample": f"{len(incoming)} events"}
    else:
        sec = secrets()
        structured, posts, report = run_all(cfg, sec)
        extracted = extract_events(posts, cfg, sec)
        incoming = structured + extracted
        report["extract"] = f"posts={len(posts)} events={len(extracted)}"
    events, added = store.merge(existing, incoming, bootstrap=not existing and not args.sample)
    store.save(P["events"], events)
    report.update({"total": len(events), "new": len(added), "at": now_kst().isoformat(timespec="seconds")})
    with open(P["report"], "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    log.info("[collect] done: %s", report)


def cmd_build(cfg, args):
    """사이트 + 게시물 이미지. --slot all 이면 점심·저녁 게시물을 한 번에 만들어 둔다 (아침 수집 직후)."""
    slots = ["noon", "evening"] if args.slot == "all" else [resolve_slot(args)]
    P0 = paths(cfg, slots[0])
    events = store.publishable(store.load(P0["events"]), float(cfg.get("min_confidence_site", 0.5)))
    idx = render.build_site(events, cfg, P0["site"])
    log.info("[build] site → %s (%d events)", idx, len(events))

    posted = json.load(open(P0["posted"], encoding="utf-8")) if os.path.exists(P0["posted"]) else {}
    posted_keys = {k for run in posted.values() if isinstance(run, dict) for k in run}
    for slot in slots:
        P = paths(cfg, slot)
        manifest = {"run": P["run"], "slot": slot, "posts": []}
        for post in render.plan_posts(events, cfg, slot):
            htmls = render.build_post_slides(post, cfg, os.path.join(P["work"], post["key"]))
            jpgs = capture.capture_slides(htmls, os.path.join(P["cards"], post["key"]))
            manifest["posts"].append({
                "key": post["key"], "title": f"{post['region_full']} {post['week_label']}",
                "images": [f"{post['key']}/{os.path.basename(j)}" for j in jpgs],
                "caption": render.build_post_caption(post, cfg),
            })
            log.info("[build] %s post %s: %d slides, %d events", slot, post["key"], len(jpgs), post["count"])
        # 축제 게시물: 이미 올렸거나 앞 회차에 배정한 축제는 고르지 않는다
        for post in render.plan_festival_posts(events, cfg, posted_keys, os.environ.get("TOUR_API_KEY", "")):
            posted_keys.add(post["key"])
            htmls = render.build_festival_slides(post, cfg, os.path.join(P["work"], post["key"]))
            jpgs = capture.capture_slides(htmls, os.path.join(P["cards"], post["key"]))
            manifest["posts"].append({
                "key": post["key"], "title": post["event"].title,
                "images": [f"{post['key']}/{os.path.basename(j)}" for j in jpgs],
                "caption": render.build_festival_caption(post, cfg),
            })
            log.info("[build] %s festival %s: %d slides, %d spots", slot, post["event"].title, len(jpgs), len(post["spots"]))
        os.makedirs(P["cards"], exist_ok=True)
        with open(os.path.join(P["cards"], "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
        log.info("[build] slot=%s posts=%d", slot, len(manifest["posts"]))
    if args.preview:
        capture.capture_page(idx, os.path.join(P0["work"], "mobile-preview.jpg"))


def cmd_publish(cfg, args):
    from concertbot.instagram import Instagram, wait_public

    slot = resolve_slot(args)
    P = paths(cfg, slot)
    posted = json.load(open(P["posted"], encoding="utf-8")) if os.path.exists(P["posted"]) else {}
    done = posted.setdefault(P["run"], {})
    mpath = os.path.join(P["cards"], "manifest.json")
    if os.path.exists(mpath):
        man = json.load(open(mpath, encoding="utf-8"))
    else:  # 아침에 만들어 배포해 둔 것을 사이트에서 받는다
        import requests
        murl = cfg["site_url"].rstrip("/") + "/" + P["cards_rel"] + "/manifest.json"
        r = requests.get(murl, timeout=30)
        if r.status_code != 200:
            sys.exit(f"오늘 {slot} 게시물이 준비돼 있지 않습니다 ({murl} → {r.status_code}). 아침 수집 실행을 확인하세요.")
        man = r.json()
    base = cfg["site_url"].rstrip("/") + "/" + P["cards_rel"] + "/"
    todo = [p for p in man["posts"] if args.force or p["key"] not in done]
    if args.only == "festival":
        todo = [p for p in todo if p["key"].startswith("fest-")]
    elif args.only == "concert":
        todo = [p for p in todo if not p["key"].startswith("fest-")]
    if args.max_posts:
        todo = todo[: args.max_posts]
    if not todo:
        log.info("[publish] nothing to post for %s", P["run"])
        return
    sec = secrets()
    icfg = cfg.get("instagram", {})
    host, ver = icfg.get("api_host", "graph.facebook.com"), icfg.get("api_version", "v26.0")
    if sec["IG_ACCESS_TOKEN"] and not sec["IG_USER_ID"]:
        from concertbot.instagram import discover_account
        try:
            sec["IG_USER_ID"], uname = discover_account(sec["IG_ACCESS_TOKEN"], host, ver)
            log.info("[publish] 인스타 계정 @%s (id %s)", uname, sec["IG_USER_ID"])
        except Exception as e:  # noqa: BLE001
            log.error("[publish] 인스타 계정 확인 실패: %s", e)
            if not args.dry_run:
                sys.exit(1)
    if args.dry_run:
        for p in todo:
            log.info("[publish] dry-run %s: %d images\n%s\n---\n%s", p["key"], len(p["images"]),
                     "\n".join(base + i for i in p["images"]), p["caption"])
        return
    if not (sec["IG_ACCESS_TOKEN"] and sec["IG_USER_ID"]):
        log.warning("[publish] IG_ACCESS_TOKEN / IG_USER_ID 가 아직 없어 업로드를 건너뜁니다 (%d개 게시물 준비됨)", len(todo))
        return
    ig = Instagram(sec["IG_ACCESS_TOKEN"], sec["IG_USER_ID"], host, ver)
    log.info("[publish] quota: %s", ig.publishing_quota())
    os.makedirs(os.path.dirname(P["posted"]), exist_ok=True)
    failed = 0
    for p in todo:
        urls = [base + i for i in p["images"]]
        try:
            wait_public(urls)
            media_id = ig.publish_carousel(urls, p["caption"])
        except Exception as e:  # noqa: BLE001
            failed += 1
            log.error("[publish] %s failed: %s", p["key"], e)
            continue
        done[p["key"]] = {"media_id": media_id, "permalink": ig.permalink(media_id),
                          "at": now_kst().isoformat(timespec="seconds"), "images": len(urls)}
        json.dump(posted, open(P["posted"], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        log.info("[publish] %s done %s", p["key"], done[p["key"]])
    if failed:
        sys.exit(f"{failed} post(s) failed")


def cmd_x_login(cfg, args):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False)
        ctx = b.new_context(locale="ko-KR")
        pg = ctx.new_page()
        pg.goto("https://x.com/login")
        input("브라우저에서 수집용 부계정으로 로그인한 뒤 Enter 를 누르세요… ")
        ctx.storage_state(path="x_state.json")
        b.close()
    print("x_state.json 저장 완료. GitHub Secret 용:  base64 -w0 x_state.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["collect", "build", "publish", "all", "x-login"])
    ap.add_argument("--sample", action="store_true", help="샘플 데이터로 수집 단계 대체")
    ap.add_argument("--dry-run", action="store_true", help="인스타 업로드 생략")
    ap.add_argument("--force", action="store_true", help="이미 게시한 게시물도 다시 게시")
    ap.add_argument("--only", choices=["all", "concert", "festival"], default="all", help="올릴 게시물 종류")
    ap.add_argument("--max-posts", type=int, default=0, help="이번에 올릴 최대 게시물 수 (0: 전부)")
    ap.add_argument("--slot", choices=["auto", "noon", "evening", "all"], default="auto",
                    help="noon: 이번 주, evening: 다음 주·다다음 주 (auto: 15시 전이면 noon)")
    ap.add_argument("--preview", action="store_true", help="모바일 페이지 미리보기 캡처")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    cfg = load_config()
    if args.command == "x-login":
        return cmd_x_login(cfg, args)
    if args.command in ("collect", "all"):
        cmd_collect(cfg, args)
    if args.command in ("build", "all"):
        cmd_build(cfg, args)
    if args.command in ("publish", "all"):
        cmd_publish(cfg, args)


if __name__ == "__main__":
    main()
