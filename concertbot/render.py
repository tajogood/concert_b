from __future__ import annotations

import json
import os
import shutil
from datetime import date, timedelta

from jinja2 import Environment, FileSystemLoader

from .models import Event
from .util import GENRES, REGION_FULL, REGION_SLUG, REGION_ORDER, WEEKDAYS, kdate, now_kst, today_kst, week_info

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_LABELS = {"kopis": "KOPIS", "seoul": "서울시", "naver_news": "뉴스", "naver_cafe": "네이버 카페",
                 "naver_blog": "블로그", "daum_cafe": "다음 카페", "x": "X", "instagram": "인스타그램"}

env = Environment(loader=FileSystemLoader(os.path.join(ROOT, "templates")), autoescape=True)


def _base_css(font_base: str) -> str:
    with open(os.path.join(ROOT, "templates", "base.css"), encoding="utf-8") as f:
        return f.read().replace("{{ font_base }}", font_base)


def _is_new(e: Event, today: str) -> bool:
    return (e.first_seen or "")[:10] == today


LONG_RUN_DAYS = 7


def is_long_run(e: Event) -> bool:
    """상설·오픈런처럼 기간이 긴 공연. 날짜별 목록과 캐러셀에서는 따로 다룬다."""
    try:
        return (date.fromisoformat(e.end_date or e.start_date) - date.fromisoformat(e.start_date)).days > LONG_RUN_DAYS
    except ValueError:
        return False


def summarize(events: list[Event]):
    today = today_kst().isoformat()
    week_end = (today_kst() + timedelta(days=6)).isoformat()
    short = [e for e in events if not is_long_run(e)]
    new = [e for e in short if _is_new(e, today)]
    week = [e for e in short if e.start_date <= week_end and (e.end_date or e.start_date) >= today]
    return today, new, week


# ------------------------------------------------------------------ site
def build_site(events: list[Event], cfg: dict, out_dir: str) -> str:
    today, new, week = summarize(events)
    os.makedirs(out_dir, exist_ok=True)
    shutil.copytree(os.path.join(ROOT, "assets", "fonts"), os.path.join(out_dir, "fonts"), dirs_exist_ok=True)
    shutil.copytree(os.path.join(ROOT, "assets", "brand"), os.path.join(out_dir, "brand"), dirs_exist_ok=True)
    rows = []
    for e in sorted(events, key=lambda x: (max(x.start_date, today), x.region, x.title)):
        d = e.to_dict()
        d["is_new"] = _is_new(e, today)
        d["long_run"] = is_long_run(e)
        d["sources"] = [{"label": SOURCE_LABELS.get(s.name.split(":")[0], s.name.split(":")[-1] or "출처"), "url": s.url}
                        for s in e.sources]
        rows.append({k: d[k] for k in ("title", "artists", "start_date", "end_date", "venue", "region", "city", "genre",
                                       "times", "price", "poster", "ticket_links", "ticket_open", "sources",
                                       "confidence", "is_new", "long_run")})
    from collections import Counter
    cnt = Counter(e.region for e in events if e.region)
    regions = sorted(cnt, key=lambda r: (-cnt[r], REGION_ORDER.index(r) if r in REGION_ORDER else 99))
    last_day = max(((e.end_date or e.start_date) for e in events if not is_long_run(e)), default=today)
    horizon = (today_kst() + timedelta(days=int(cfg.get("sources", {}).get("kopis", {}).get("days_ahead", 90)))).isoformat()
    range_end = min(last_day, horizon)
    html = env.get_template("index.html.j2").render(
        brand=cfg["brand"], base_css=_base_css("fonts"), today_md=kdate(today, False), today_iso=today,
        range_end_iso=range_end, genres=GENRES, range_end_label=f"{date.fromisoformat(range_end).year}년 {kdate(range_end)}",
        counts={"new": len(new), "week": len(week), "total": len(events)}, regions=regions,
        events_json=json.dumps(rows, ensure_ascii=False).replace("</", "<\\/"),
        updated=now_kst().strftime("%Y-%m-%d %H:%M"), og_image=(cfg.get("site_url", "").rstrip("/") + "/brand/og.png") if cfg.get("site_url") else "",
        company=cfg.get("company", ""), contact_email=cfg.get("contact_email", ""), data_credit=cfg.get("data_credit", ""),
    )
    path = os.path.join(out_dir, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(out_dir, "data.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    return path


# ------------------------------------------------------------------ carousel
# 게시물 하나 = (지역, 주차). 점심: 이번 주(오늘~일요일), 저녁: 다음 주·다다음 주.
SLOT_WEEKS = {"noon": [0], "evening": [1, 2]}


def _view(e: Event, day: str) -> dict:
    d = date.fromisoformat(day)
    rng = ""
    if e.end_date and e.end_date != e.start_date:
        ed = date.fromisoformat(e.end_date)
        rng = f"{ed.month}.{ed.day}"
    place = " ".join(x for x in (e.city, e.venue) if x)
    return {
        "title": e.title, "who": ", ".join(e.artists[:3]), "venue": place or e.venue, "region": e.region,
        "md": f"{d.month}.{d.day}", "wd": WEEKDAYS[d.weekday()] + "요일", "range": rng,
        "ticket_open": e.ticket_open, "poster": e.poster, "is_new": False,
    }


def plan_posts(events: list[Event], cfg: dict, slot: str) -> list[dict]:
    icfg = cfg.get("instagram", {})
    today = today_kst()
    regions = icfg.get("regions", ["서울", "경기"])
    per_day = int(icfg.get("per_slide_week", 3))
    max_slides = min(int(icfg.get("max_slides", 10)), 20)
    genre_rank = {g: i for i, g in enumerate(icfg.get("genre_priority", GENRES))}
    short = [e for e in events if not is_long_run(e) and e.confidence >= float(cfg.get("min_confidence_instagram", 0.7))]

    def score(e):
        return (genre_rank.get(e.genre, 9), -bool(e.ticket_open), -bool(e.artists), -bool(e.ticket_links), e.title)

    posts = []
    for offset in SLOT_WEEKS[slot]:
        w = week_info(today + timedelta(days=7 * offset))
        start = max(w["monday"], today).isoformat()
        end = w["sunday"].isoformat()
        for region in regions:
            evs = [e for e in short if e.region == region and e.start_date <= end and (e.end_date or e.start_date) >= start]
            if not evs:
                continue
            by_day = {}
            for e in evs:
                by_day.setdefault(max(e.start_date, start), []).append(e)
            slides = []
            for day in sorted(by_day):
                if len(slides) >= max_slides - 2:
                    break
                ds = sorted(by_day[day], key=score)
                chunk = [_view(e, day) for e in ds[:per_day]]
                more = len(ds) - len(chunk)
                d = date.fromisoformat(day)
                slides.append({"kind": "list", "heading": f"{d.month}.{d.day} {WEEKDAYS[d.weekday()]}요일",
                               "sub": f"{REGION_FULL.get(region, region)} 공연 {len(ds)}개" + (f" 중 {len(chunk)}개" if more > 0 else ""),
                               "events": chunk})
            venues = {e.venue for e in evs if e.venue}
            posts.append({
                "key": f"{w['key']}-{REGION_SLUG.get(region, region)}", "region": region, "region_full": REGION_FULL.get(region, region),
                "week_label": w["label_long"], "start": start, "end": end,
                "count": len(evs), "venues": len(venues), "events": sorted(evs, key=lambda e: (max(e.start_date, start), score(e))),
                "slides": [{"kind": "cover"}] + slides + [{"kind": "outro"}],
            })
    return posts


def build_post_slides(post: dict, cfg: dict, out_dir: str) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    font_base = "file://" + os.path.join(ROOT, "assets", "fonts")
    url = cfg.get("site_url", "")
    t = env.get_template("slide.html.j2")
    s0, s1 = date.fromisoformat(post["start"]), date.fromisoformat(post["end"])
    paths = []
    for i, s in enumerate(post["slides"], 1):
        html = t.render(slide=s, index=i, total=len(post["slides"]), brand=cfg["brand"], base_css=_base_css(font_base),
                        post=post, range_label=f"{s0.month}.{s0.day} {WEEKDAYS[s0.weekday()]} – {s1.month}.{s1.day} {WEEKDAYS[s1.weekday()]}",
                        site_url_short=url.replace("https://", "").rstrip("/") or "프로필 링크",
                        logo="file://" + os.path.join(ROOT, "assets", "brand", "logo.png"),
                        data_credit=cfg.get("data_credit", ""),
                        show_posters=cfg.get("instagram", {}).get("show_posters", True))
        p = os.path.join(out_dir, f"slide-{i:02d}.html")
        with open(p, "w", encoding="utf-8") as f:
            f.write(html)
        paths.append(p)
    return paths


def build_post_caption(post: dict, cfg: dict) -> str:
    s0, s1 = post["start"], post["end"]
    lines = [f"{post['region_full']} {post['week_label']} 공연 정보", "",
             f"{kdate(s0)} ~ {kdate(s1)}, 공연 {post['count']}개", ""]
    for e in post["events"][:15]:
        place = " ".join(x for x in (e.city, e.venue) if x)
        lines.append(f"- {kdate(max(e.start_date, s0))} {e.title}" + (f" | {place}" if place else ""))
    if post["count"] > 15:
        lines.append(f"외 {post['count'] - 15}개")
    lines += ["", "전체 일정과 예매 링크는 프로필 링크에서 확인하세요."]
    if cfg.get("data_credit"):
        lines.append(cfg["data_credit"])
    lines.append("")
    r = post["region"]
    tags = [f"{r}공연", f"{r}콘서트", f"{r}가볼만한곳"] + list(cfg.get("instagram", {}).get("hashtags", []))
    lines.append(" ".join("#" + t.lstrip("#") for t in dict.fromkeys(tags))[:600])
    return "\n".join(lines)[:2150]
