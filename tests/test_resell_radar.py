from datetime import datetime, timedelta, timezone

import pytest

from resell_radar.config import Settings
from resell_radar.models import DRAW, FCFS, Release
from resell_radar.pipeline import evaluate, notify
from resell_radar.scoring import MAYBE, PASS, RECOMMEND, ProfitSettings, score_release
from resell_radar.sources import manual, nike
from resell_radar.storage import Store
from resell_radar.tiers import DEFAULT_TIERS, find_collaborators, find_hot_silhouette

PROFIT = ProfitSettings()


def rel(name, price=200_000, **kw):
    return Release(source="test", source_id=name, name=name, retail_price=price, **kw)


def test_collaborator_aliases_korean_and_english():
    found = dict(find_collaborators("Fragment x 유니온 x Air Jordan 1 High", DEFAULT_TIERS))
    assert found == {"Fragment": "S", "Union LA": "S"}


def test_short_aliases_need_word_boundary():
    assert find_collaborators("Nike Baldwin Runner", DEFAULT_TIERS) == []


def test_silhouette_does_not_match_other_numbers():
    assert find_hot_silhouette("Air Jordan 11 Retro", DEFAULT_TIERS) is None
    assert find_hot_silhouette("조던 11 레트로", DEFAULT_TIERS) is None
    assert find_hot_silhouette("에어 조던 1 로우", DEFAULT_TIERS) == "Air Jordan 1"


def test_s_collab_on_hot_silhouette_is_recommended():
    s = score_release(rel("Union x Air Jordan 1 Low", method=DRAW), DEFAULT_TIERS, PROFIT)
    assert s.level == "S+" and s.verdict == RECOMMEND
    assert s.est_profit > 30_000


def test_general_release_is_passed():
    s = score_release(rel("Nike Air Max 90"), DEFAULT_TIERS, PROFIT)
    assert s.level == "C" and s.verdict == PASS


def test_penalties_stack():
    s = score_release(
        rel("Supreme x Nike Air Force 1", method=FCFS, is_restock=True, retailer_count=8),
        DEFAULT_TIERS,
        PROFIT,
    )
    assert s.level == "C-" and s.verdict == PASS  # A -2 -3 -2 = 7 notches down


def test_repeat_collab_drops_a_grade():
    s = score_release(rel("Travis Scott x Air Jordan 1 Low"), DEFAULT_TIERS, PROFIT, {"Travis Scott": 4})
    assert s.level == "B"  # B +2 (silhouette) -2 (repeat)


def test_real_market_price_overrides_estimate():
    s = score_release(rel("Fragment x Air Jordan 1", market_price=210_000), DEFAULT_TIERS, PROFIT)
    assert not s.price_is_estimate
    assert s.verdict == PASS  # S-grade name, but the actual market says it doesn't pay


def test_expensive_b_grade_fails_threshold():
    s = score_release(rel("Kith x New Balance 990", price=300_000), DEFAULT_TIERS, PROFIT)
    assert s.verdict == PASS


def test_b_grade_with_good_market_is_maybe():
    s = score_release(rel("Kith x New Balance 990", market_price=300_000), DEFAULT_TIERS, PROFIT)
    assert s.verdict == MAYBE


NIKE_FEED = {
    "objects": [
        {
            "publishedContent": {"properties": {"title": "AJ1", "seo": {"slug": "air-jordan-1-x-union"}}},
            "productInfo": [
                {
                    "merchProduct": {
                        "styleColor": "AB1234-100",
                        "productType": "FOOTWEAR",
                        "commerceStartDate": "2026-11-05T01:00:00.000Z",
                    },
                    "merchPrice": {"fullPrice": 219000, "currentPrice": 219000},
                    "productContent": {"fullTitle": "Air Jordan 1 Low x Union LA"},
                    "launchView": {
                        "method": "DAN",
                        "startEntryDate": "2026-11-04T01:00:00.000Z",
                        "stopEntryDate": "2026-11-04T02:00:00.000Z",
                    },
                },
                {"merchProduct": {"productType": "EQUIPMENT"}, "productContent": {"fullTitle": "Socks"}},
            ],
        },
        {"publishedContent": {}, "productInfo": [{}]},
    ]
}


def test_nike_feed_parsing():
    [r] = nike.parse_feed(NIKE_FEED)
    assert r.source_id == "AB1234-100"
    assert r.retail_price == 219000
    assert r.method == DRAW
    assert r.entry_end == datetime(2026, 11, 4, 2, tzinfo=timezone.utc)
    assert r.url.endswith("/air-jordan-1-x-union")


def test_manual_csv_roundtrip(tmp_path):
    path = tmp_path / "releases.csv"
    manual.write_template(path)
    starter = manual.load(path)
    assert len(starter) == len(manual.STARTER_ROWS)  # the example row is ignored
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        f.write("CPFM x Nike Dunk,무신사,2026-11-01 10:00,2026-11-02 23:59,,\"159,000원\",선착순,1,Y,250000,\n")
    [r] = [x for x in manual.load(path) if x.name == "CPFM x Nike Dunk"]
    assert r.retail_price == 159_000 and r.market_price == 250_000
    assert r.method == FCFS and r.is_restock and r.retailer_count == 1
    assert r.entry_end.hour == 23


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    yield s
    s.close()


def test_notifications_are_sent_once_and_deadline_reminder(store):
    now = datetime.now(timezone.utc)
    soon = rel("Union x Air Jordan 1", entry_end=now + timedelta(hours=1))
    later = rel("Fragment x Air Jordan 1", entry_end=now + timedelta(days=2))
    results = evaluate([soon, later, rel("Nike Air Max 90")], DEFAULT_TIERS, Settings(), store)
    sent = []
    assert notify(results, Settings(), store, sent.append) == 3  # 2 new + 1 deadline
    assert notify(results, Settings(), store, sent.append) == 0


def test_history_feeds_repeat_penalty(store):
    base = datetime.now(timezone.utc) + timedelta(days=1)
    # Releases recorded by earlier runs, back when they were still upcoming.
    for i in range(1, 4):
        past = rel(f"Travis Scott x Dunk {i}", release_at=base - timedelta(days=30 * i))
        store.save(past, score_release(past, DEFAULT_TIERS, PROFIT))
    [result] = evaluate([rel("Travis Scott x Air Jordan 1", release_at=base)], DEFAULT_TIERS, Settings(), store)
    assert any("최근 1년 협업" in reason for reason in result.score.reasons)


def test_past_releases_are_skipped(store):
    old = rel("Union x Air Jordan 1", entry_end=datetime.now(timezone.utc) - timedelta(days=2))
    assert evaluate([old], DEFAULT_TIERS, Settings(), store) == []


def test_html_report_escapes_and_counts(store):
    from resell_radar.report import render_html

    now = datetime.now(timezone.utc)
    releases = [
        rel("Union x Air Jordan 1 <script>", entry_end=now + timedelta(hours=2), url="https://example.com/a"),
        rel("Nike Air Max 90"),
    ]
    results = evaluate(releases, DEFAULT_TIERS, Settings(), store)
    page = render_html(results, ["나이키 수집 실패"], now)
    assert "Air Jordan 1 <script>" not in page
    assert "Air Jordan 1 &lt;script&gt;" in page
    assert page.count('class="card ') == 2
    assert "나이키 수집 실패" in page


from resell_radar.learning import Estimator, Observation, load_price_log, write_price_log_template


def test_empty_history_reproduces_tier_table():
    est = Estimator([], DEFAULT_TIERS).estimate("Union x Air Jordan 1", "S")
    assert est.multiplier == pytest.approx(1.8)
    assert est.basis == "등급표 추정"


def test_estimate_moves_toward_records_as_they_accumulate():
    def union_mult(n):
        obs = [Observation(f"Union x Air Jordan 1 #{i}", "S", 100_000, 240_000) for i in range(n)]
        return Estimator(obs, DEFAULT_TIERS).estimate("Union x Air Jordan 1 Low", "S").multiplier

    m3, m10, m50 = union_mult(3), union_mult(10), union_mult(50)
    assert 1.8 < m3 < m10 < m50 < 2.4
    assert m50 == pytest.approx(2.4, rel=0.05)


def test_unseen_group_follows_market_wide_correction():
    obs = [Observation(f"Union x Dunk {i}", "S", 100_000, 90_000) for i in range(10)]  # market went cold
    est = Estimator(obs, DEFAULT_TIERS).estimate("Fragment x Air Jordan 1", "S")
    assert est.multiplier < 1.8
    assert est.count == 0 and "전체 기록 10건" in est.basis


def test_price_log_and_scoring_use_learned_basis(tmp_path):
    path = tmp_path / "prices.csv"
    write_price_log_template(path)
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        f.write('Kith x New Balance 990,"259,000",320000,2026-10-01,\n')
    obs = load_price_log(path, lambda name: "B")
    assert obs == [Observation("Kith x New Balance 990", "B", 259_000, 320_000)]
    s = score_release(rel("Kith x New Balance 991"), DEFAULT_TIERS, PROFIT, estimator=Estimator(obs, DEFAULT_TIERS))
    assert s.price_basis == "Kith 기록 1건 반영"


def test_recorded_market_prices_become_observations(store):
    evaluate([rel("Union x Air Jordan 1", market_price=400_000)], DEFAULT_TIERS, Settings(), store)
    [obs] = store.market_observations()
    assert (obs.retail, obs.market, obs.level) == (200_000, 400_000, "S+")


def test_single_soft_penalty_keeps_s_collab_recommended():
    # Under whole-letter grading FCFS alone dropped an S collab straight to A.
    s = score_release(rel("Fragment x Nike Air Force 1", method=FCFS), DEFAULT_TIERS, PROFIT)
    assert s.level == "A+" and s.verdict == RECOMMEND  # S -2 notches, still recommended
    assert "선착순 판매 -2칸" in s.reasons


def test_b_plus_is_still_not_recommended():
    s = score_release(rel("Kith x Nike Kobe 6", market_price=400_000), DEFAULT_TIERS, PROFIT)
    assert s.level == "A-"  # B +2
    s = score_release(rel("Kith x New Balance 990", market_price=400_000), DEFAULT_TIERS, PROFIT)
    assert s.level == "B" and s.verdict == MAYBE


def test_old_tiers_file_is_upgraded(tmp_path):
    import json

    from resell_radar.tiers import load_tiers

    old = {k: v for k, v in DEFAULT_TIERS.items() if k not in ("adjust",)}
    old["price_multiplier"] = {"S": 2.0, "A": 1.4, "B": 1.15, "C": 1.0}
    path = tmp_path / "tiers.json"
    path.write_text(json.dumps(old), encoding="utf-8")
    tiers = load_tiers(path)
    assert tiers["price_multiplier"]["S"] == 2.0  # user's edit kept
    assert tiers["price_multiplier"]["A+"] == DEFAULT_TIERS["price_multiplier"]["A+"]
    assert tiers["adjust"] == DEFAULT_TIERS["adjust"]


def test_pharrell_is_a_default_collaborator():
    s = score_release(rel("Pharrell x adidas Adistar Jellyfish", price=345_000), DEFAULT_TIERS, PROFIT)
    assert s.level == "A" and s.verdict == RECOMMEND


def test_apparel_sits_two_notches_lower():
    shoe = score_release(rel("Supreme x Nike SB Dunk Low"), DEFAULT_TIERS, PROFIT)
    hoodie = score_release(rel("Supreme x Nike Hooded Sweatshirt"), DEFAULT_TIERS, PROFIT)
    feed = score_release(rel("Supreme x Nike Top", is_apparel=True), DEFAULT_TIERS, PROFIT)
    assert shoe.level == "S-"  # A +2 (SB Dunk)
    assert hoodie.level == feed.level == "B+"  # A -2
    assert "의류 -2칸" in hoodie.reasons


def test_untouched_template_from_older_version_gets_starter_rows(tmp_path):
    path = tmp_path / "releases.csv"
    path.write_text("상품명,발매처\n(예시) Fragment x Union,무신사\n", encoding="utf-8-sig")
    manual.ensure_template(path)
    assert len(manual.load(path)) == len(manual.STARTER_ROWS)


def test_user_schedule_is_never_overwritten(tmp_path):
    path = tmp_path / "releases.csv"
    path.write_text("상품명,발매처\nUnion x Air Jordan 1,무신사\n", encoding="utf-8-sig")
    manual.ensure_template(path)
    assert [r.name for r in manual.load(path)] == ["Union x Air Jordan 1"]


def test_old_tiers_file_gains_new_default_collaborators_but_keeps_edits(tmp_path):
    import json

    from resell_radar.tiers import load_tiers

    old = json.loads(json.dumps(DEFAULT_TIERS))
    del old["collaborators"]["Pharrell"]
    old["collaborators"]["Kith"]["grade"] = "A"
    path = tmp_path / "tiers.json"
    path.write_text(json.dumps(old), encoding="utf-8")
    tiers = load_tiers(path)
    assert tiers["collaborators"]["Pharrell"]["grade"] == "A"
    assert tiers["collaborators"]["Kith"]["grade"] == "A"


from resell_radar import krify
from resell_radar.sources import us_calendar

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<item><title>Union x Air Jordan 1 Low &#8220;Sail&#8221; Release Date</title>
<link>https://example.com/union-aj1</link>
<content:encoded><![CDATA[<p>Look for it soon.</p><p><strong>Union x Air Jordan 1 Low</strong><br>
Release Date: November 21st, 2026<br>Price: $160</p>]]></content:encoded></item>
<item><title>Nike Air Max 1 Official Images</title><link>https://example.com/am1</link>
<description>Release Date: Saturday, December 5, 2026 ($150)</description></item>
<item><title>Opinion: the best shoes of the year</title><link>https://example.com/op</link>
<description>No date here, $200 though.</description></item>
</channel></rss>"""


def test_rss_feed_parsing():
    items = us_calendar.parse_feed(RSS, "Sneaker News")
    assert [(r.name, r.us_price) for r in items] == [
        ("Union x Air Jordan 1 Low “Sail”", 160),
        ("Nike Air Max 1", 150),
    ]
    assert items[0].release_at.date().isoformat() == "2026-11-21"
    assert items[1].release_at.date().isoformat() == "2026-12-05"


def us_item(name, usd, days=10):
    return Release(source="us_calendar", source_id=name, name=name, us_price=usd,
                   release_at=datetime.now(timezone.utc) + timedelta(days=days))


def test_reconcile_estimates_unmatched_us_items(store):
    out, factor, n = krify.reconcile([us_item("Union x Air Jordan 1 Low Sail", 160)], store)
    [r] = out
    assert factor == krify.DEFAULT_FACTOR and n == 0
    assert r.retail_price == 184_000
    assert "나이키 SNKRS 한국" in r.channels and "Union 공식몰" in r.channels
    assert "추정" in r.note


def test_reconcile_learns_factor_from_matched_korean_prices(store):
    pairs = [("Kobe 6 Protro Reverse Grinch", 190, 239_000), ("Air Jordan 4 Bred", 215, 269_000),
             ("Nike SB Dunk Low Concepts Lobster", 135, 169_000)]
    releases = []
    for name, usd, krw in pairs:
        releases.append(us_item(name, usd))
        releases.append(rel(name, price=krw))
    out, factor, n = krify.reconcile(releases + [us_item("Travis Scott x AF1 Ice Blue", 160)], store)
    assert n == 3 and 1240 < factor < 1260  # median of 1258 / 1251 / 1252
    assert len(out) == 4  # matched US duplicates dropped
    ts = [r for r in out if r.source == "us_calendar"][0]
    assert ts.retail_price == round(160 * factor, -2)
    assert "실제 3건 학습" in ts.note


def test_reconcile_fills_missing_korean_price_and_dedupes_feeds(store):
    kr = rel("JJJJound x New Balance 990v3", price=None)
    out, _, _ = krify.reconcile(
        [kr, us_item("JJJJound x New Balance 990v3 Brown", 230), us_item("JJJJound x New Balance 990v3", 230)], store)
    assert out == [kr] and kr.retail_price == 264_500
    assert store.price_pairs() == []  # an estimated price never becomes training data


def test_keep_filter_drops_plain_colorways(store):
    out, _, _ = krify.reconcile([us_item("Nike Air Max 1 Triple White", 150)], store, keep=lambda r: " x " in r.name)
    assert out == []


def test_calendar_items_are_remembered_until_release(store):
    soon = us_item("Union x Air Jordan 1 Low Sail", 160, days=20)
    past = us_item("Old x Shoe", 100, days=-5)
    assert {r.name for r in store.remember_calendar([soon, past])} == {soon.name}
    # Next run: the article has dropped off the feed, the item is still tracked.
    [kept] = store.remember_calendar([])
    assert kept.name == soon.name and kept.us_price == 160


from resell_radar.sources import musinsa


def musinsa_page(no, state="진행중", end="2099-01-01T00:00:00", price=259000, normal=259000):
    import json

    data = {"props": {"pageProps": {"raffleData": {
        "raffleNo": no, "title": f"[미즈노] 와디 웨이브 {no}", "brandName": "미즈노",
        "normalPrice": normal, "price": price,
        "entryStartTime": "2026-10-20T11:00:00", "entryEndTime": end,
        "entryCount": 34434, "winnerCount": 1, "raffleStateMessage": state,
    }}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data, ensure_ascii=False)}</script></html>'


def test_musinsa_detail_parsing():
    r = musinsa.to_release(musinsa.parse_detail(musinsa_page(1279, price=1000, normal=139000)))
    assert r.source_id == "1279" and r.retail_price == 1000
    assert (r.entries, r.winners) == (34434, 1)
    assert r.entry_start.utcoffset() == timedelta(hours=9) and r.entry_start.hour == 11
    assert "이벤트가 1,000원" in r.note and r.url.endswith("/1279")


def test_musinsa_scan_finds_new_open_raffles_and_stops_past_newest():
    pages = {1276: musinsa_page(1276, state="래플 종료"), 1278: musinsa_page(1278, state="래플 종료"),
             1280: musinsa_page(1280), 1281: musinsa_page(1281, end="2020-01-01T00:00:00")}
    asked = []

    def get(no):
        asked.append(no)
        return pages.get(no)

    found, highest = musinsa.fetch(1278, get=get)
    assert [r.source_id for r in found] == ["1280"]  # ended / past-deadline raffles skipped
    assert highest == 1281
    assert max(asked) == 1285  # four misses after the newest raffle, then stop


def test_raffle_odds_show_in_reasons_and_card(store):
    from resell_radar.report import render_html

    r = musinsa.to_release(musinsa.parse_detail(musinsa_page(1280)))
    results = evaluate([r], DEFAULT_TIERS, Settings(), store)
    assert "당첨 1명 / 응모 34,434명" in results[0].score.reasons
    assert "확률 0.0029%" in render_html(results, [], datetime.now(timezone.utc))
