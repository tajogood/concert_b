from __future__ import annotations

import time
from datetime import timedelta
from urllib.parse import unquote

from ..models import Event, Source
from ..util import guess_genre, log, normalize_region, now_kst, parse_city, parse_ymd, session, strip_html, today_kst
from .base import need

BASE = "https://apis.data.go.kr/B551011/KorService2"
LDONG = {"12": "전남광주", "11": "서울", "26": "부산", "27": "대구", "28": "인천", "29": "광주", "30": "대전", "31": "울산", "36": "세종",
         "41": "경기", "42": "강원", "51": "강원", "43": "충북", "44": "충남", "45": "전북", "52": "전북", "46": "전남",
         "47": "경북", "48": "경남", "50": "제주"}
BUSKING_WORDS = ("버스킹", "거리공연", "거리 공연", "길거리공연", "청춘마이크")
MUSIC_WORDS = ("음악", "뮤직", "콘서트", "재즈", "락", "록 ", "밴드", "힙합", "페스티벌", "가요", "국악", "클래식", "오케스트라", "합창")


def _items(r):
    body = r.json().get("response", {}).get("body", {})
    items = (body.get("items") or {}).get("item", [])
    return [items] if isinstance(items, dict) else items, int(body.get("totalCount") or 0)


def _classify(it: dict, overview: str = "") -> str:
    """축제 / 버스킹 / 음악 공연이면 장르를, 아니면 '' (수집 제외)."""
    title = it.get("title", "")
    blob = f"{title} {overview}"
    code = it.get("lclsSystm2", "") or ""
    if any(w in blob for w in BUSKING_WORDS):
        return "버스킹"
    g = guess_genre(title)
    if g:
        return g
    if code == "EV01":
        return "축제"
    if code == "EV02":
        if it.get("lclsSystm3", "").startswith("EV0201"):
            return "국악"
        if any(w in blob for w in MUSIC_WORDS):
            return "대중음악"
    return ""


def collect(cfg, secrets, config):
    (key,) = need(secrets, "TOUR_API_KEY")
    key = unquote(key)  # 포털의 '인코딩' 키를 넣어도 동작하도록
    s = session()
    common = {"serviceKey": key, "MobileOS": "ETC", "MobileApp": "onstage", "_type": "json"}
    today = today_kst()
    horizon = (today + timedelta(days=int(cfg.get("days_ahead", 90)))).isoformat()
    fetched = now_kst().isoformat(timespec="seconds")

    raw, page = [], 1
    while page <= 30:
        r = s.get(f"{BASE}/searchFestival2", params={**common, "eventStartDate": today.strftime("%Y%m%d"),
                                                       "arrange": "A", "numOfRows": 100, "pageNo": page}, timeout=30)
        if not r.headers.get("content-type", "").startswith("application/json"):
            raise RuntimeError(f"TourAPI 응답 오류: {r.text[:200]}")
        items, total = _items(r)
        raw += items
        if len(raw) >= total or not items:
            break
        page += 1
        time.sleep(0.3)

    events = []
    for it in raw:
        sd, ed = parse_ymd(it.get("eventstartdate", "")), parse_ymd(it.get("eventenddate", ""))
        if not sd or sd > horizon or (ed or sd) < today.isoformat():
            continue
        genre = _classify(it)
        detail = {}
        # 상세(소개글·장소·요금)는 넣을 행사와, 공연 분류(EV02) 중 소개글을 봐야 판단되는 것만 조회
        if genre or (cfg.get("check_overview", True) and it.get("lclsSystm2") == "EV02"):
            detail = _detail(s, common, it["contentid"]) if cfg.get("fetch_detail", True) else {}
            genre = genre or _classify(it, detail.get("overview", ""))
        if not genre:
            continue
        addr = f"{it.get('addr1', '')} {it.get('addr2', '')}".strip()
        # 주소의 시·도를 우선 (원본 코드가 주소와 어긋나는 경우가 있음)
        region = normalize_region(it.get("addr1", "")) or LDONG.get(str(it.get("lDongRegnCd", "")), "")
        link = detail.get("homepage") or f"https://korean.visitkorea.or.kr/detail/fes_detail.do?cotid={it['contentid']}"
        events.append(Event(
            title=it.get("title", "").strip(),
            start_date=sd, end_date=ed,
            venue=detail.get("eventplace") or it.get("addr1", ""),
            region=region, city=parse_city(it.get("addr1", "")),
            genre=genre,
            times=detail.get("playtime", ""),
            price=detail.get("fee", ""),
            poster=(it.get("firstimage") or "").replace("http://", "https://"),
            story=detail.get("overview", ""),
            company=detail.get("sponsor", ""),
            ticket_links=[{"name": "행사 정보", "url": link}],
            sources=[Source("tour", f"https://korean.visitkorea.or.kr/detail/fes_detail.do?cotid={it['contentid']}", fetched)],
            confidence=0.95,
        ))
        time.sleep(0.2)
    log.info("[tour] 행사 %d건 중 축제·버스킹·음악 %d건", len(raw), len(events))
    return events, []


def _detail(s, common, cid) -> dict:
    out = {}
    try:
        r = s.get(f"{BASE}/detailCommon2", params={**common, "contentId": cid}, timeout=20)
        it = (_items(r)[0] or [{}])[0]
        out["overview"] = strip_html(it.get("overview", ""))[:1500]
        hp = it.get("homepage", "")
        if "href=" in hp:
            hp = hp.split('href="', 1)[-1].split('"', 1)[0]
        out["homepage"] = hp if hp.startswith("http") else ""
        r = s.get(f"{BASE}/detailIntro2", params={**common, "contentId": cid, "contentTypeId": 15}, timeout=20)
        it = (_items(r)[0] or [{}])[0]
        out["eventplace"] = strip_html(it.get("eventplace", ""))
        out["playtime"] = strip_html(it.get("playtime", ""))[:200]
        out["fee"] = strip_html(it.get("usetimefestival", ""))[:200]
        out["sponsor"] = strip_html(it.get("sponsor1", ""))
    except Exception as e:  # noqa: BLE001
        log.debug("tour detail %s: %s", cid, e)
    return out
