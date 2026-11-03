import json
from email.utils import format_datetime

from datetime import timedelta

from concertbot.util import now_kst, today_kst

FUT = (today_kst() + timedelta(days=30)).isoformat()
from types import SimpleNamespace

from concertbot.collectors import kopis, naver, seoul

LIST = """<?xml version="1.0" encoding="UTF-8"?><dbs><db><mt20id>PF100001</mt20id><prfnm>테스트 밴드 콘서트</prfnm>
<prfpdfrom>2026.11.14</prfpdfrom><prfpdto>2026.11.15</prfpdto><fcltynm>올림픽공원 (올림픽홀)</fcltynm>
<poster>http://www.kopis.or.kr/upload/p.gif</poster><area>서울특별시</area><genrenm>대중음악</genrenm><prfstate>공연예정</prfstate></db></dbs>"""
DETAIL = """<?xml version="1.0" encoding="UTF-8"?><dbs><db><prfcast>테스트 밴드, 게스트 등</prfcast><pcseguidance>전석 99,000원</pcseguidance>
<dtguidance>토요일(18:00), 일요일(17:00)</dtguidance><area>서울특별시</area><mt10id>FC000001</mt10id>
<relates><relate><relatenm>인터파크</relatenm><relateurl>https://tickets.example.com/1</relateurl></relate></relates></db></dbs>"""


class FakeSession:
    def __init__(self, routes):
        self.routes, self.headers = routes, {}

    def get(self, url, params=None, timeout=None):
        for k, body in self.routes.items():
            if k in url:
                b = body.encode() if isinstance(body, str) else json.dumps(body).encode()
                return SimpleNamespace(status_code=200, content=b, text=b.decode(), raise_for_status=lambda: None,
                                       json=lambda: json.loads(b))
        raise AssertionError(url)


def test_kopis(monkeypatch, tmp_path):
    monkeypatch.setattr(kopis, "CACHE", str(tmp_path / "kopis_detail.json"))
    monkeypatch.setattr(kopis, "FAC_CACHE", str(tmp_path / "kopis_facility.json"))
    FAC = '<?xml version="1.0" encoding="UTF-8"?><dbs><db><fcltynm>올림픽공원</fcltynm><adres>서울특별시 송파구 올림픽로 424</adres></db></dbs>'
    monkeypatch.setattr(kopis, "session", lambda: FakeSession({"prfplc/FC": FAC, "pblprfr/PF": DETAIL, "pblprfr": LIST}))
    ev, _ = kopis.collect({"genres": ["CCCD"]}, {"KOPIS_API_KEY": "k"}, {})
    e = ev[0]
    assert e.start_date == "2026-11-14" and e.region == "서울" and e.artists == ["테스트 밴드", "게스트"]
    assert e.ticket_links[0]["url"].startswith("https://") and e.poster.startswith("https://")
    assert e.city == "송파구"


def test_naver(monkeypatch):
    body = {"items": [{"title": "<b>밴드</b> 콘서트 개최", "originallink": "https://n.example/1", "link": "x",
                       "description": "12월 1일 롤링홀", "pubDate": format_datetime(now_kst())}]}
    monkeypatch.setattr(naver, "session", lambda: FakeSession({"news": body}))
    _, posts = naver.collect({"kinds": ["news"], "queries": ["콘서트 개최"]},
                             {"NAVER_CLIENT_ID": "a", "NAVER_CLIENT_SECRET": "b"}, {})
    assert posts and posts[0].title == "밴드 콘서트 개최"


def test_seoul(monkeypatch):
    row = {"CODENAME": "콘서트", "TITLE": "서울 콘서트", "STRTDATE": FUT + " 00:00:00.0", "END_DATE": FUT + " 00:00:00.0",
           "PLACE": "세종문화회관", "GUNAME": "종로구", "ORG_LINK": "https://s.example"}
    monkeypatch.setattr(seoul, "session", lambda: FakeSession({"culturalEventInfo": {"culturalEventInfo": {"row": [row]}}}))
    ev, _ = seoul.collect({}, {"SEOUL_API_KEY": "k"}, {})
    assert ev[0].start_date == FUT and ev[0].region == "서울"
