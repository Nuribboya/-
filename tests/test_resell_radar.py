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
    assert s.level == "S" and s.verdict == RECOMMEND
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
    assert s.level == "C" and s.verdict == PASS


def test_repeat_collab_drops_a_grade():
    s = score_release(rel("Travis Scott x Air Jordan 1 Low"), DEFAULT_TIERS, PROFIT, {"Travis Scott": 4})
    assert s.level == "B"


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
    assert manual.load(path) == []  # the example row is ignored
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        f.write("CPFM x Nike Dunk,무신사,2026-11-01 10:00,2026-11-02 23:59,,\"159,000원\",선착순,1,Y,250000,\n")
    [r] = manual.load(path)
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
    assert (obs.retail, obs.market, obs.level) == (200_000, 400_000, "S")
