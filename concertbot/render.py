from __future__ import annotations

import json
import os
import shutil
from datetime import date, timedelta

from jinja2 import Environment, FileSystemLoader

from .models import Event
from .util import REGION_ORDER, WEEKDAYS, kdate, now_kst, today_kst

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


def summarize(events: list[Event]):
    today = today_kst().isoformat()
    week_end = (today_kst() + timedelta(days=6)).isoformat()
    new = [e for e in events if _is_new(e, today)]
    week = [e for e in events if e.start_date <= week_end and (e.end_date or e.start_date) >= today]
    return today, new, week


# ------------------------------------------------------------------ site
def build_site(events: list[Event], cfg: dict, out_dir: str) -> str:
    today, new, week = summarize(events)
    os.makedirs(out_dir, exist_ok=True)
    shutil.copytree(os.path.join(ROOT, "assets", "fonts"), os.path.join(out_dir, "fonts"), dirs_exist_ok=True)
    rows = []
    for e in sorted(events, key=lambda x: (max(x.start_date, today), x.region, x.title)):
        d = e.to_dict()
        d["is_new"] = _is_new(e, today)
        d["sources"] = [{"label": SOURCE_LABELS.get(s.name.split(":")[0], s.name.split(":")[-1] or "출처"), "url": s.url}
                        for s in e.sources]
        rows.append({k: d[k] for k in ("title", "artists", "start_date", "end_date", "venue", "region", "genre",
                                       "times", "price", "poster", "ticket_links", "ticket_open", "sources",
                                       "confidence", "is_new")})
    regions = [r for r in REGION_ORDER if any(e.region == r for e in events)]
    html = env.get_template("index.html.j2").render(
        brand=cfg["brand"], base_css=_base_css("fonts"), today_md=kdate(today, False), today_iso=today,
        counts={"new": len(new), "week": len(week), "total": len(events)}, regions=regions,
        events_json=json.dumps(rows, ensure_ascii=False).replace("</", "<\\/"),
        updated=now_kst().strftime("%Y-%m-%d %H:%M"), og_image=cfg.get("og_image", ""),
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

    # 이번 주: 날짜순으로 채우되 슬라이드 제목은 그 슬라이드의 날짜 범위
    n_shown = sum(len(s["events"]) for s in slides)
    shown = {e.id for e in by_start(new)[:n_shown]}
    week_v = [_view(e, today) for e in by_start(week) if e.id not in shown]
    for i in range(0, len(week_v), per_day):
        if len(slides) >= content_budget:
            break
        chunk = week_v[i:i + per_day]
        a, b = chunk[0]["md"], chunk[-1]["md"]
        slides.append({"kind": "list", "is_new": False, "heading": "이번 주 공연",
                       "sub": f"{a} ~ {b}" if a != b else f"{a} {chunk[0]['wd']}", "events": chunk})
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
                        show_posters=cfg.get("instagram", {}).get("show_posters", True))
        p = os.path.join(out_dir, f"slide-{i:02d}.html")
        with open(p, "w", encoding="utf-8") as f:
            f.write(html)
        paths.append(p)
    return paths


def build_caption(events: list[Event], cfg: dict) -> str:
    today, new, week = summarize(events)
    d = today_kst()
    lines = [f"{d.month}월 {d.day}일 전국 음악 공연 소식", ""]
    if new:
        lines.append(f"새로 뜬 공연 {len(new)}개")
        for e in sorted(new, key=lambda x: x.start_date)[:8]:
            lines.append(f"- {e.title} | {kdate(e.start_date)} {e.region} {e.venue}".rstrip())
        lines.append("")
    if week:
        lines.append(f"7일 안에 열리는 공연 {len(week)}개")
        for e in sorted(week, key=lambda x: x.start_date)[:8]:
            lines.append(f"- {kdate(max(e.start_date, today))} {e.title} ({e.region})")
        lines.append("")
    lines.append("전체 일정과 예매 링크는 프로필 링크에서 확인하세요.")
    lines.append("예매 전 공식 공지를 꼭 확인해 주세요.")
    lines.append("")
    tags = list(cfg.get("instagram", {}).get("hashtags", []))
    regions = {e.region for e in new + week if e.region}
    tags += [f"{r}콘서트" for r in sorted(regions)][:5]
    lines.append(" ".join("#" + t.lstrip("#") for t in dict.fromkeys(tags))[:600])
    cap = "\n".join(lines)
    return cap[:2150]  # 인스타 캡션 2200자 제한
