from __future__ import annotations

import json
import re
from datetime import date

import requests

from .models import Event, RawPost, Source
from .util import GENRES, find_region, guess_genre, log, normalize_region, now_kst, today_kst

CONCERT_KW = re.compile(r"버스킹|거리\s?공연|마술\s?(공연|쇼)|매직\s?쇼|서커스|콘서트|단독\s?공연|내한|투어|페스티벌|팬\s?콘서트|팬미팅|리사이틀|티켓\s?오픈|예매\s?오픈|선예매|공연\s?(개최|확정)")
NOISE_KW = re.compile(r"후기|직캠|양도|교환|구해요|구합니다|삽니다|팝니다|대리\s?티켓팅")

KNOWN_VENUES = [
    "KSPO DOME", "케이스포돔", "올림픽체조경기장", "핸드볼경기장", "올림픽홀", "잠실실내체육관", "잠실종합운동장",
    "잠실주경기장", "고척스카이돔", "인스파이어 아레나", "킨텍스", "블루스퀘어", "YES24 LIVE HALL", "예스24 라이브홀",
    "무신사 개러지", "롤링홀", "세종문화회관", "예술의전당", "롯데콘서트홀", "장충체육관", "벡스코", "BEXCO",
    "부산 사직실내체육관", "엑스코", "EXCO", "대구 엑스코", "김대중컨벤션센터", "광주 여자대학교 유니버시아드체육관",
    "대전 충무체육관", "창원 성산아트홀", "수원 경기아트센터", "고양종합운동장", "고양아람누리", "아시아드주경기장",
]

DATE_RE = re.compile(
    r"(?:(20\d{2})\s*[년.\-/]\s*)?(\d{1,2})\s*[월.\-/]\s*(\d{1,2})\s*일?"
    r"(?:\s*[~\-–]\s*(?:(\d{1,2})\s*[월.]\s*)?(\d{1,2})\s*일?)?"
)
TICKET_RE = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일[^.\n]{0,15}?(오후|오전)?\s*(\d{1,2})\s*시[^.\n]{0,20}?(?:티켓|예매)\s*(?:오픈|시작)")


def relevant(p: RawPost) -> bool:
    blob = f"{p.title} {p.text}"
    return bool(CONCERT_KW.search(blob)) and not NOISE_KW.search(p.title)


def _infer_year(m: int, d: int, y: int | None) -> date | None:
    t = today_kst()
    try:
        if y:
            return date(y, m, d)
        dt = date(t.year, m, d)
        if (t - dt).days > 30:
            dt = date(t.year + 1, m, d)
        return dt
    except ValueError:
        return None


# ---------------------------------------------------------------- rule based
def rule_extract(p: RawPost, artists: list[str]) -> Event | None:
    blob = f"{p.title} {p.text}"
    md = DATE_RE.search(blob)
    if not md:
        return None
    y, m, d, m2, d2 = md.groups()
    sd = _infer_year(int(m), int(d), int(y) if y else None)
    if not sd or sd < today_kst():
        return None
    ed = sd
    if d2:
        ed = _infer_year(int(m2 or m), int(d2), sd.year) or sd
    venue = next((v for v in KNOWN_VENUES if v.lower() in blob.lower()), "")
    if not venue:
        mv = re.search(r"([가-힣A-Za-z0-9 ]{2,20}(?:아트센터|문화회관|체육관|아레나|홀|돔|경기장|컨벤션센터|라이브홀|광장|공원|거리|역|해수욕장))에서", blob)
        venue = mv.group(1).strip() if mv else ""
    hit_artists = [a for a in artists if a.lower() in blob.lower()]
    mt = re.search(r"[「『‘'\"“《<]([^」』’'\"”》>]{3,40})[」』’'\"”》>]", p.title)
    title = mt.group(1) if mt else re.sub(r"\s*[-|]\s*[^-|]+$", "", p.title)[:40]
    if not venue and not hit_artists:
        return None
    ev = Event(title=title.strip(), start_date=sd.isoformat(), end_date=ed.isoformat(), venue=venue,
               genre=guess_genre(blob) or "대중음악",
               region=normalize_region(venue) or find_region(blob), artists=hit_artists,
               sources=[Source(p.source, p.url, now_kst().isoformat(timespec="seconds"))], confidence=0.4)
    mtk = TICKET_RE.search(blob)
    if mtk:
        ev.ticket_open = f"{mtk.group(1)}.{mtk.group(2)} {mtk.group(3) or ''}{mtk.group(4)}시".strip()
    return ev


# ---------------------------------------------------------------- LLM based
LLM_PROMPT = """다음은 한국 뉴스/카페/SNS 게시물 목록이다. 각 게시물에서 '앞으로 열릴 국내 공연(콘서트, 페스티벌, 팬콘서트, 내한공연, 클래식·국악 공연, 마술·서커스 공연, 버스킹·거리공연)'의 확정 정보만 추출하라.
오늘 날짜: {today}
규칙:
- 해외 공연, 이미 끝난 공연, 추측/루머, 양도·후기 글은 제외.
- 날짜는 YYYY-MM-DD. 연도가 없으면 오늘 이후 가장 가까운 날짜로.
- 모르는 필드는 빈 문자열. 지어내지 말 것.
- region 은 서울/부산/대구/인천/광주/대전/울산/세종/경기/강원/충북/충남/전북/전남/경북/경남/제주 중 하나.
- city 는 시·군·구 이름(예: 수원시, 마포구, 해운대구). 모르면 빈 문자열.
- genre 는 대중음악/클래식/국악/서커스·마술/버스킹 중 하나.
- 버스킹은 날짜와 장소(공원·광장·거리 이름 등)가 정해진 일정만 추출.
JSON 배열만 출력: [{{"post": 게시물번호, "title": "", "artists": [""], "start_date": "", "end_date": "", "venue": "", "region": "", "city": "", "genre": "", "ticket_open": "", "price": ""}}]

게시물:
{posts}"""


def llm_extract(posts: list[RawPost], api_key: str, model: str) -> list[Event]:
    out = []
    fetched = now_kst().isoformat(timespec="seconds")
    for i in range(0, len(posts), 25):
        batch = posts[i:i + 25]
        listing = "\n".join(f"[{j}] ({p.source}) {p.title}\n{p.text[:600]}" for j, p in enumerate(batch))
        try:
            r = requests.post("https://api.anthropic.com/v1/messages", headers={
                "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json",
            }, json={"model": model, "max_tokens": 4000, "messages": [{"role": "user", "content":
                     LLM_PROMPT.format(today=today_kst().isoformat(), posts=listing)}]}, timeout=120)
            r.raise_for_status()
            txt = "".join(b.get("text", "") for b in r.json()["content"])
            items = json.loads(txt[txt.index("["): txt.rindex("]") + 1])
        except Exception as e:  # noqa: BLE001
            log.warning("llm extract batch %d failed: %s", i, e)
            continue
        for it in items:
            try:
                p = batch[int(it["post"])]
                if not it.get("title") or not it.get("start_date") or it["start_date"] < today_kst().isoformat():
                    continue
                out.append(Event(
                    title=it["title"], start_date=it["start_date"], end_date=it.get("end_date") or "",
                    venue=it.get("venue", ""), region=it.get("region") or normalize_region(it.get("venue", "")),
                    city=it.get("city", ""),
                    genre=it.get("genre") if it.get("genre") in GENRES else (guess_genre(it["title"]) or "대중음악"),
                    artists=[a for a in it.get("artists", []) if a][:6], ticket_open=it.get("ticket_open", ""),
                    price=it.get("price", ""), sources=[Source(p.source, p.url, fetched)], confidence=0.6,
                ))
            except Exception:  # noqa: BLE001
                continue
    return out


def extract_events(posts: list[RawPost], config: dict, secrets: dict) -> list[Event]:
    rel = [p for p in posts if relevant(p)]
    log.info("[extract] %d posts → %d relevant", len(posts), len(rel))
    if not rel:
        return []
    ecfg = config.get("extract", {})
    key = secrets.get("ANTHROPIC_API_KEY")
    if key and ecfg.get("use_llm", True):
        evs = llm_extract(rel[: int(ecfg.get("max_posts", 300))], key, ecfg.get("model", "claude-haiku-5-5"))
        log.info("[extract] LLM → %d events", len(evs))
        return evs
    artists = config.get("watch_artists", [])
    evs = [e for e in (rule_extract(p, artists) for p in rel) if e]
    log.info("[extract] rules → %d events", len(evs))
    return evs
