from __future__ import annotations

import json
import os
import shutil
from datetime import date, timedelta

from jinja2 import Environment, FileSystemLoader

from .models import Event
from .util import GENRES, REGION_ORDER, WEEKDAYS, kdate, now_kst, today_kst

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_LABELS = {"kopis": "KOPIS", "seoul": "서울시", "naver_news": "뉴스", "naver_cafe": "네이버 카페",
                 "naver_blog": "블로그", "daum_cafe": "다음 카페", "x": "X", "instagram": "인스타그램"}

env = Environment(loader=FileSystemLoader(os.path.join(ROOT, "templates")), autoescape=True)


def _base_css(font_base: str) -> str:
    with open(os.path.join(ROOT, "templates", "base.css"), encoding="utf-8") as f:
        return f.read().replace("{{ font_base }}", font_base)


def _is_new(e: Event, today: str) -> bool:
    return (e.first_seen or "")[:10] == today


def _view(e: Event, today: str) -> dict:
    start = max(e.start_date, today)
    d = date.fromisoformat(start)
    rng = ""
    if e.end_date and e.end_date != e.start_date:
        ed = date.fromisoformat(e.end_date)
        rng = f"{ed.month}.{ed.day}"
    return {
        "title": e.title, "who": ", ".join(e.artists[:3]), "venue": e.venue, "region": e.region,
        "md": f"{d.month}.{d.day}", "wd": WEEKDAYS[d.weekday()] + "요일", "range": rng,
        "ticket_open": e.ticket_open, "poster": e.poster, "is_new": _is_new(e, today),
    }


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
def plan_slides(events: list[Event], cfg: dict) -> list[dict]:
    today, new, week = summarize(events)
    icfg = cfg.get("instagram", {})
    max_slides = min(int(icfg.get("max_slides", 10)), 20)
    per_new, per_day = int(icfg.get("per_slide_new", 3)), int(icfg.get("per_slide_week", 3))
    content_budget = max_slides - 2  # cover + outro

    def by_start(lst):
        return sorted(lst, key=lambda e: (e.start_date, -e.confidence, e.region))

    slides = []
    new_v = [_view(e, today) for e in by_start(new)]
    new_slides_max = max(1, content_budget // 2) if week else content_budget
    for i in range(0, len(new_v), per_new):
        if len(slides) >= new_slides_max:
            break
        slides.append({"kind": "list", "is_new": True, "heading": "새로 뜬 공연",
                       "sub": "오늘 처음 소식이 올라온 공연", "events": new_v[i:i + per_new]})

    # 이번 주: 하루에 한 장씩, 그날 공연 중 우선순위 높은 순으로 per_day 개.
    # 날짜가 슬라이드 수보다 많으면 앞쪽 날짜부터 채운다.
    n_shown = sum(len(s["events"]) for s in slides)
    shown = {e.id for e in by_start(new)[:n_shown]}
    genre_rank = {g: i for i, g in enumerate(icfg.get("genre_priority", ["대중음악", "클래식", "국악"]))}

    def score(e):
        return (genre_rank.get(e.genre, 9), -bool(e.ticket_open), -bool(e.artists), -bool(e.ticket_links), e.title)

    by_day = {}
    for e in week:
        if e.id in shown:
            continue
        by_day.setdefault(max(e.start_date, today), []).append(e)
    for day in sorted(by_day):
        if len(slides) >= content_budget:
            break
        evs = sorted(by_day[day], key=score)
        chunk = [_view(e, today) for e in evs[:per_day]]
        more = len(evs) - len(chunk)
        sub = f"{chunk[0]['md']} {chunk[0]['wd']}" + (f", 이 밖에 {more}개 더" if more > 0 else "")
        slides.append({"kind": "list", "is_new": False, "heading": "오늘 공연" if day == today else "이번 주 공연",
                       "sub": sub, "events": chunk})
    return [{"kind": "cover"}] + slides + [{"kind": "outro"}]


def build_slides(events: list[Event], cfg: dict, out_dir: str) -> list[str]:
    today, new, week = summarize(events)
    os.makedirs(out_dir, exist_ok=True)
    font_base = "file://" + os.path.join(ROOT, "assets", "fonts")
    slides = plan_slides(events, cfg)
    url = cfg.get("site_url", "")
    t = env.get_template("slide.html.j2")
    paths = []
    for i, s in enumerate(slides, 1):
        html = t.render(slide=s, index=i, total=len(slides), brand=cfg["brand"], base_css=_base_css(font_base),
                        today_md=kdate(today, False), today_wd=WEEKDAYS[today_kst().weekday()],
                        counts={"new": len(new), "week": len(week)},
                        site_url_short=url.replace("https://", "").rstrip("/") or "프로필 링크",
                        logo="file://" + os.path.join(ROOT, "assets", "brand", "logo.png"),
                        data_credit=cfg.get("data_credit", ""),
                        show_posters=cfg.get("instagram", {}).get("show_posters", True))
        p = os.path.join(out_dir, f"slide-{i:02d}.html")
        with open(p, "w", encoding="utf-8") as f:
            f.write(html)
        paths.append(p)
    return paths


def build_caption(events: list[Event], cfg: dict) -> str:
    today, new, week = summarize(events)
    d = today_kst()
    lines = [f"{d.month}월 {d.day}일 전국 공연 소식", ""]
    if new:
        lines.append(f"새로 뜬 공연 {len(new)}개")
        for e in sorted(new, key=lambda x: x.start_date)[:8]:
            lines.append(f"- {e.title} | {kdate(max(e.start_date, today))} {e.region} {e.venue}".rstrip())
        lines.append("")
    if week:
        lines.append(f"7일 안에 열리는 공연 {len(week)}개")
        for e in sorted(week, key=lambda x: x.start_date)[:8]:
            lines.append(f"- {kdate(max(e.start_date, today))} {e.title} ({e.region})")
        lines.append("")
    lines.append("전체 일정과 예매 링크는 프로필 링크에서 확인하세요.")
    if cfg.get("data_credit"):
        lines.append(cfg["data_credit"])
    lines.append("")
    tags = list(cfg.get("instagram", {}).get("hashtags", []))
    regions = {e.region for e in new + week if e.region}
    tags += [f"{r}콘서트" for r in sorted(regions)][:5]
    lines.append(" ".join("#" + t.lstrip("#") for t in dict.fromkeys(tags))[:600])
    cap = "\n".join(lines)
    return cap[:2150]  # 인스타 캡션 2200자 제한
