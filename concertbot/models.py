from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Source:
    name: str          # kopis / seoul / naver_news / naver_cafe / daum_cafe / x / instagram / web
    url: str = ""
    fetched_at: str = ""


@dataclass
class Event:
    title: str
    start_date: str                      # YYYY-MM-DD
    end_date: str = ""                   # YYYY-MM-DD
    venue: str = ""
    region: str = ""                     # 서울/부산/경기 ... (짧은 시도명)
    city: str = ""                       # 시군구
    artists: list[str] = field(default_factory=list)
    genre: str = "대중음악"
    times: str = ""                      # "토 18:00, 일 17:00"
    price: str = ""
    poster: str = ""
    ticket_links: list[dict] = field(default_factory=list)   # [{"name":..,"url":..}]
    ticket_open: str = ""                # 티켓 오픈 일시(뉴스/SNS에서 추출)
    status: str = ""                     # 공연예정/공연중/공연완료
    sources: list[Source] = field(default_factory=list)
    confidence: float = 1.0              # 정형 API=1.0, 비정형 추출은 낮게
    first_seen: str = ""
    updated: str = ""
    id: str = ""

    def __post_init__(self):
        if not self.end_date:
            self.end_date = self.start_date
        if not self.id:
            self.id = make_id(self.title, self.start_date, self.venue)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        d = dict(d)
        d["sources"] = [Source(**s) for s in d.get("sources", [])]
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})

    @property
    def is_structured(self) -> bool:
        return self.confidence >= 0.9


def make_id(title: str, start_date: str, venue: str) -> str:
    from .util import norm_text
    raw = f"{norm_text(title)}|{start_date}|{norm_text(venue)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


@dataclass
class RawPost:
    """비정형 소스(뉴스·카페·SNS)에서 가져온 원문. extract 단계에서 Event로 변환."""
    source: str
    url: str
    title: str
    text: str
    posted_at: str = ""
    image: Optional[str] = None
