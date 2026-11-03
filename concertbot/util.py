from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta, timezone, date

import requests

KST = timezone(timedelta(hours=9))
log = logging.getLogger("concertbot")

REGIONS = {
    "서울": ["서울특별시", "서울시", "서울"],
    "부산": ["부산광역시", "부산"],
    "대구": ["대구광역시", "대구"],
    "인천": ["인천광역시", "인천"],
    "광주": ["광주광역시", "광주"],
    "대전": ["대전광역시", "대전"],
    "울산": ["울산광역시", "울산"],
    "세종": ["세종특별자치시", "세종"],
    "경기": ["경기도", "경기"],
    "강원": ["강원특별자치도", "강원도", "강원"],
    "충북": ["충청북도", "충북"],
    "충남": ["충청남도", "충남"],
    "전북": ["전북특별자치도", "전라북도", "전북"],
    "전남": ["전라남도", "전남"],
    "경북": ["경상북도", "경북"],
    "경남": ["경상남도", "경남"],
    "제주": ["제주특별자치도", "제주도", "제주"],
}
REGION_ORDER = list(REGIONS)

# 시군구/공연장 → 시도 추정용 (자주 나오는 것만)
CITY_HINTS = {
    "고양": "경기", "일산": "경기", "킨텍스": "경기", "수원": "경기", "성남": "경기", "용인": "경기",
    "안산": "경기", "부천": "경기", "화성": "경기", "파주": "경기", "인스파이어": "인천", "송도": "인천",
    "잠실": "서울", "올림픽공원": "서울", "KSPO": "서울", "핸드볼경기장": "서울", "고척": "서울",
    "블루스퀘어": "서울", "롤링홀": "서울", "무신사 개러지": "서울", "롯데콘서트홀": "서울", "장충체육관": "서울", "예스24라이브홀": "서울", "세종문화회관": "서울", "예술의전당": "서울",
    "벡스코": "부산", "BEXCO": "부산", "해운대": "부산", "엑스코": "대구", "EXCO": "대구",
    "김대중컨벤션": "광주", "창원": "경남", "김해": "경남", "포항": "경북", "경주": "경북",
    "전주": "전북", "청주": "충북", "천안": "충남", "춘천": "강원", "강릉": "강원", "원주": "강원",
    "여수": "전남", "목포": "전남", "서귀포": "제주",
}


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> date:
    return now_kst().date()


def normalize_region(text: str) -> str:
    if not text:
        return ""
    t = text.strip()
    for short, names in REGIONS.items():
        for n in names:
            if t.startswith(n) or n in t[:8]:
                return short
    for hint, short in CITY_HINTS.items():
        if hint in t:
            return short
    return ""


def find_region(text: str) -> str:
    """본문 아무 곳에서나 시도명/공연장 힌트를 찾는다 (가장 먼저 나온 것)."""
    best, pos = "", 10**9
    for short, names in REGIONS.items():
        for n in names + [short]:
            i = (text or "").find(n)
            if 0 <= i < pos and not (short == "광주" and "경기 광주" in text):
                best, pos = short, i
    if best:
        return best
    return normalize_region(text)


GENRES = ["대중음악", "클래식", "국악", "서커스·마술", "버스킹"]


def guess_genre(text: str) -> str:
    t = text or ""
    if re.search(r"버스킹|거리\s?공연|길거리\s?공연", t):
        return "버스킹"
    if re.search(r"마술|매직\s?쇼|매지션|서커스|저글링", t):
        return "서커스·마술"
    return ""


def parse_city(address: str) -> str:
    """도로명/지번 주소 → 시군구. '경기도 수원시 팔달구 …' → '수원시', '서울특별시 송파구 …' → '송파구'."""
    toks = (address or "").replace("(", " ").split()
    if len(toks) < 2:
        return ""
    region = normalize_region(toks[0])
    if region == "세종":
        return "세종시"
    t = toks[1]
    if t.endswith(("시", "군", "구")) and len(t) >= 2:
        return t
    return ""


def norm_text(s: str) -> str:
    """중복 비교용 정규화: 괄호/공백/특수문자 제거, 소문자."""
    s = html.unescape(s or "").lower()
    s = re.sub(r"\[[^\]]*\]|\([^)]*\)|〈|〉|<|>|《|》|「|」|『|』", " ", s)
    s = re.sub(r"(콘서트|concert|tour|투어|in\s|공연|단독|내한)", " ", s)
    s = re.sub(r"[^0-9a-z가-힣]", "", s)
    return s


def strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", html.unescape(s or "")).strip()


def parse_ymd(s: str) -> str:
    """'2026.10.27' / '20261027' / '2026-10-27 ...' → '2026-10-27'"""
    if not s:
        return ""
    m = re.search(r"(20\d{2})[.\-/년\s]*(\d{1,2})[.\-/월\s]*(\d{1,2})", s)
    if not m:
        return ""
    y, mo, d = map(int, m.groups())
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return ""


def session(user_agent: str | None = None) -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = user_agent or (
        "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0 Mobile Safari/537.36 concert-bot/1.0"
    )
    return s


WEEKDAYS = "월화수목금토일"


def kdate(iso: str, with_wd: bool = True) -> str:
    """'2026-10-31' → '10.31(토)'"""
    try:
        d = date.fromisoformat(iso)
    except Exception:
        return iso
    return f"{d.month}.{d.day}" + (f"({WEEKDAYS[d.weekday()]})" if with_wd else "")


# ---------------------------------------------------------------- 주차
REGION_SLUG = {"서울": "seoul", "부산": "busan", "대구": "daegu", "인천": "incheon", "광주": "gwangju", "대전": "daejeon",
               "울산": "ulsan", "세종": "sejong", "경기": "gyeonggi", "강원": "gangwon", "충북": "chungbuk", "충남": "chungnam",
               "전북": "jeonbuk", "전남": "jeonnam", "경북": "gyeongbuk", "경남": "gyeongnam", "제주": "jeju"}
ORDINALS = ["첫째", "둘째", "셋째", "넷째", "다섯째"]
REGION_FULL = {"서울": "서울시", "부산": "부산시", "대구": "대구시", "인천": "인천시", "광주": "광주시", "대전": "대전시",
               "울산": "울산시", "세종": "세종시", "경기": "경기도", "강원": "강원도", "충북": "충청북도", "충남": "충청남도",
               "전북": "전북", "전남": "전라남도", "경북": "경상북도", "경남": "경상남도", "제주": "제주도"}


def week_info(d: date) -> dict:
    """월~일 한 주. 목요일이 속한 달의 몇째 주인지로 '11월 첫째 주'를 정한다."""
    mon = d - timedelta(days=d.weekday())
    thu = mon + timedelta(days=3)
    n = (thu.day - 1) // 7 + 1
    return {"monday": mon, "sunday": mon + timedelta(days=6), "year": thu.year, "month": thu.month, "n": n,
            "key": f"{thu.year}-{thu.month:02d}-w{n}", "label": f"{thu.month}월 {n}주차",
            "label_long": f"{thu.month}월 {ORDINALS[n - 1]} 주"}
