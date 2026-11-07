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


def test_plan_posts_one_per_run_forward_only():
    from concertbot import render
    from concertbot.util import week_info
    evs = [Event(f"공연 {i}", d(i), venue="롤링홀", region=r, sources=[Source("kopis", f"k{i}{r}")])
           for i in range(0, 28) for r in ("서울", "경기")]
    cfg = {"brand": "x", "instagram": {"regions": ["서울", "경기"], "weeks_ahead": 3}}
    posted, seq = set(), []
    for _ in range(4):
        ps = render.plan_posts(evs, cfg, "noon", posted)
        assert len(ps) == 1  # 회차당 1개
        posted.add(ps[0]["key"]); seq.append(ps[0]["key"])
    assert len(set(seq)) == 4
    order = [render.concert_key(w, r) for _, w, r in render.concert_queue(cfg)]
    assert [order.index(k) for k in seq] == sorted(order.index(k) for k in seq)  # 앞으로만 진행
    # 다음 주를 올렸으면 이번 주 남은 지역으로 되돌아가지 않는다
    nxt = render.concert_key(week_info(today_kst() + timedelta(days=7)), "서울")
    ps = render.plan_posts(evs, cfg, "noon", {nxt})
    assert order.index(ps[0]["key"]) > order.index(nxt)
    assert ps[0]["slides"][0]["kind"] == "cover" and ps[0]["slides"][-1]["kind"] == "outro"


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


def test_festival_weekend_first():
    from datetime import timedelta as td
    from concertbot import render
    t = today_kst()
    fri = t + td(days=(4 - t.weekday()) % 7 if t.weekday() <= 4 else 0)
    sun = fri + td(days=6 - fri.weekday())
    nxt = sun + td(days=3)  # 주말 이후 시작
    early = Event("주말 지나 시작", nxt.isoformat(), (nxt + td(days=1)).isoformat(), region="경북", genre="축제",
                  poster="https://x/1.jpg", sources=[Source("tour", "a")], ext_id="a")
    wknd = Event("주말 축제", sun.isoformat(), sun.isoformat(), region="부산", genre="축제",
                 poster="https://x/2.jpg", sources=[Source("tour", "b")], ext_id="b")
    cfg = {"instagram": {"festival_posts": {"per_slot": 1, "days_ahead": 14}}}
    assert render.plan_festival_posts([early, wknd], cfg, set(), "")[0]["key"] == "fest-b"
