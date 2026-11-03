from concertbot.models import Event, RawPost, Source
from concertbot import store, extract
from datetime import timedelta

from concertbot.util import normalize_region, today_kst


def d(n):
    return (today_kst() + timedelta(days=n)).isoformat()


def test_region():
    assert normalize_region("서울특별시") == "서울"
    assert normalize_region("경기도 고양시") == "경기"
    assert normalize_region("KSPO DOME") == "서울"


def test_dedupe_structured_and_news():
    a = Event("하이웨이브 2026 아시아투어 인 서울", d(20), d(21), "KSPO DOME", "서울",
              artists=["하이웨이브"], sources=[Source("kopis", "k1")], confidence=1.0)
    b = Event("하이웨이브 아시아 투어 서울 콘서트", d(20), "", "올림픽공원 KSPO DOME", "서울",
              artists=["하이웨이브"], ticket_open="11.5 20시", sources=[Source("naver_news", "n1")], confidence=0.6)
    evs, added = store.merge([], [b, a])
    assert len(evs) == 1 and evs[0].confidence == 1.0
    assert evs[0].ticket_open == "11.5 20시" and len(evs[0].sources) == 2


def test_corroboration_raises_confidence():
    a = Event("레드플랫 팬콘서트", d(5), venue="인스파이어 아레나", artists=["레드플랫"],
              sources=[Source("naver_news", "1")], confidence=0.6)
    b = Event("레드플랫 팬콘서트 우리의 계절", d(5), venue="인스파이어 아레나", artists=["레드플랫"],
              sources=[Source("x", "2")], confidence=0.6)
    evs, _ = store.merge([], [a, b])
    assert len(evs) == 1 and evs[0].confidence >= 0.7


def test_rule_extract():
    p = RawPost("naver_news", "u", "밴드 온도, 12월 8일 롤링홀서 단독 콘서트 개최",
                "밴드 온도가 12월 8일 서울 롤링홀에서 단독 콘서트를 연다. 티켓은 11월 3일 오후 8시 예매 오픈.")
    assert extract.relevant(p)
    e = extract.rule_extract(p, ["온도"])
    assert e and e.start_date.endswith("12-08") and e.venue == "롤링홀" and e.region == "서울"
    assert "8시" in e.ticket_open


def test_noise_filtered():
    assert not extract.relevant(RawPost("daum_cafe", "u", "콘서트 티켓 양도합니다", "11월 8일"))


def test_bootstrap_not_new():
    e = Event("첫 실행 공연", d(3), venue="롤링홀", sources=[Source("kopis", "k")])
    evs, _ = store.merge([], [e], bootstrap=True)
    assert evs[0].first_seen[:10] < today_kst().isoformat()


def test_parse_city():
    from concertbot.util import parse_city
    assert parse_city("경기도 수원시 팔달구 효원로 307") == "수원시"
    assert parse_city("서울특별시 송파구 올림픽로 424") == "송파구"
    assert parse_city("부산광역시 해운대구 APEC로 55") == "해운대구"
    assert parse_city("세종특별자치시 한누리대로 2130") == "세종시"
    assert parse_city("경상북도 칠곡군 왜관읍") == "칠곡군"
    assert parse_city("") == ""


def test_guess_genre():
    from concertbot.util import guess_genre
    assert guess_genre("홍대 버스킹 데이") == "버스킹"
    assert guess_genre("이은결 매직쇼") == "서커스·마술"
    assert guess_genre("단독 콘서트") == ""


def test_plan_posts_slots():
    from concertbot import render
    evs = [Event(f"서울 공연 {i}", d(i), venue="롤링홀", region="서울", sources=[Source("kopis", f"k{i}")]) for i in range(0, 21)]
    cfg = {"brand": "x", "instagram": {"regions": ["서울", "부산"]}}
    noon = render.plan_posts(evs, cfg, "noon")
    eve = render.plan_posts(evs, cfg, "evening")
    assert [p["region"] for p in noon] == ["서울"]  # 부산은 공연이 없어 건너뜀
    assert len(eve) == 2 and eve[0]["key"] != eve[1]["key"]
    assert noon[0]["start"] == today_kst().isoformat()
    assert noon[0]["slides"][0]["kind"] == "cover" and noon[0]["slides"][-1]["kind"] == "outro"
    assert "seoul" in noon[0]["key"]


def test_split_pages():
    from concertbot import render
    k = Event("콘서트", d(2), venue="롤링홀", region="서울", sources=[Source("kopis", "k")])
    f = Event("사과축제", d(2), venue="청송", region="경북", genre="축제", sources=[Source("tour", "t")])
    both = Event("국악 축제", d(3), venue="국악원", region="서울", sources=[Source("tour", "t2"), Source("kopis", "k2")])
    c, fs = render.split_pages([k, f, both])
    assert c == [k, both] and fs == [f]


def test_plan_festival_posts():
    from concertbot import render
    f1 = Event("사과축제", d(2), d(5), venue="청송", region="경북", genre="축제", poster="https://x/1.jpg",
               sources=[Source("tour", "t1")], ext_id="1")
    f2 = Event("연중 행사", d(-100), d(200), region="서울", genre="축제", poster="https://x/2.jpg",
               sources=[Source("tour", "t2")], ext_id="2")
    f3 = Event("포스터 없는 축제", d(1), d(2), region="서울", genre="축제", sources=[Source("tour", "t3")], ext_id="3")
    cfg = {"instagram": {"festival_posts": {"per_slot": 2}}}
    posts = render.plan_festival_posts([f1, f2, f3], cfg, set(), "")
    assert [p["key"] for p in posts] == ["fest-1"]
    assert render.plan_festival_posts([f1], cfg, {"fest-1"}, "") == []
    assert posts[0]["slides"][0]["kind"] == "fest_cover" and posts[0]["slides"][-1]["kind"] == "outro"
