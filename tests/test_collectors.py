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
    monkeypatch.setattr(kopis.time, "sleep", lambda *_: None)
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


def test_kopis_partial_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(kopis, "CACHE", str(tmp_path / "c.json"))
    monkeypatch.setattr(kopis, "FAC_CACHE", str(tmp_path / "f.json"))
    monkeypatch.setattr(kopis.time, "sleep", lambda *_: None)

    class Flaky(FakeSession):
        def get(self, url, params=None, timeout=None):
            if params and params.get("shcate") == "CCCA":
                return SimpleNamespace(status_code=400, content=b"bad", text="bad")
            return super().get(url, params, timeout)

    monkeypatch.setattr(kopis, "session", lambda: Flaky({"pblprfr/PF": DETAIL, "pblprfr": LIST}))
    ev, _ = kopis.collect({"genres": ["CCCD", "CCCA"]}, {"KOPIS_API_KEY": "k"}, {})
    assert len(ev) == 1  # CCCA 실패해도 CCCD 결과는 살린다


def test_tour(monkeypatch):
    from concertbot.collectors import tour
    monkeypatch.setattr(tour.time, "sleep", lambda *_: None)
    d = lambda n: (today_kst() + timedelta(days=n)).strftime("%Y%m%d")  # noqa: E731
    items = [
        {"contentid": "1", "title": "2026 서울거리공연 구석구석라이브", "eventstartdate": d(-100), "eventenddate": d(50),
         "addr1": "서울특별시 중구 세종대로 110", "lDongRegnCd": "11", "lclsSystm2": "EV02", "lclsSystm3": "EV020900", "firstimage": "http://x/1.jpg"},
        {"contentid": "2", "title": "청송사과축제", "eventstartdate": d(1), "eventenddate": d(5),
         "addr1": "경상북도 청송군 청송읍", "lDongRegnCd": "47", "lclsSystm2": "EV01", "lclsSystm3": "EV010100"},
        {"contentid": "3", "title": "부산브릿지마라톤", "eventstartdate": d(3), "eventenddate": d(3),
         "addr1": "부산광역시 남구", "lDongRegnCd": "26", "lclsSystm2": "EV03", "lclsSystm3": "EV030400"},
        {"contentid": "4", "title": "문화가 흐르는 서울광장", "eventstartdate": d(1), "eventenddate": d(60),
         "addr1": "서울특별시 중구 세종대로 110", "lDongRegnCd": "11", "lclsSystm2": "EV02", "lclsSystm3": "EV020900"},
    ]

    class S:
        headers = {}

        def get(self, url, params=None, timeout=None):
            if url.endswith("searchFestival2"):
                body = {"items": {"item": items}, "totalCount": len(items)}
            elif url.endswith("detailCommon2"):
                ov = "점심시간 버스킹 공연이 열립니다" if params["contentId"] == "4" else "소개"
                body = {"items": {"item": [{"overview": ov, "homepage": '<a href="https://h.example">x</a>'}]}, "totalCount": 1}
            else:
                body = {"items": {"item": [{"eventplace": "서울광장", "playtime": "12:00", "usetimefestival": "무료"}]}, "totalCount": 1}
            b = json.dumps({"response": {"body": body}}).encode()
            return SimpleNamespace(status_code=200, headers={"content-type": "application/json"}, content=b, text=b.decode(),
                                   json=lambda: json.loads(b))

    monkeypatch.setattr(tour, "session", lambda: S())
    ev, _ = tour.collect({}, {"TOUR_API_KEY": "a%2Bb"}, {})
    got = {e.title: e for e in ev}
    assert set(got) == {"2026 서울거리공연 구석구석라이브", "청송사과축제", "문화가 흐르는 서울광장"}
    assert got["2026 서울거리공연 구석구석라이브"].genre == "버스킹"
    assert got["청송사과축제"].genre == "축제" and got["청송사과축제"].city == "청송군"
    assert got["문화가 흐르는 서울광장"].genre == "버스킹"
    assert got["청송사과축제"].ticket_links[0]["url"] == "https://h.example"
