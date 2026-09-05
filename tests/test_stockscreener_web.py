from fastapi.testclient import TestClient

from stockscreener.data.provider import DataUnavailableError
from stockscreener.models import PriceSnapshot, YearlyFinancials
from stockscreener.web import main as webmain

client = TestClient(webmain.app)


class _FakeProvider:
    def __init__(self, tickers_that_fail=()):
        self.tickers_that_fail = set(tickers_that_fail)

    def get_price_snapshot(self, ticker):
        if ticker in self.tickers_that_fail:
            raise DataUnavailableError(f"{ticker}: 테스트용 실패")
        return PriceSnapshot(ticker=ticker, price=10.0, currency="USD")

    def get_annual_financials(self, ticker, max_years=10):
        return [
            YearlyFinancials(
                fiscal_year=2020 + i,
                revenue=1000.0,
                net_income=100.0,
                eps=1.0,
                book_value_per_share=10.0,
                total_current_assets=500.0,
                total_current_liabilities=200.0,
                total_liabilities=600.0,
                long_term_debt=50.0,
                dividend_per_share=0.5,
            )
            for i in range(5)
        ]


class _FakeNews:
    def get_market_news(self):
        return []

    def get_ticker_news(self, ticker, company_name=None):
        return []


def _reset_state():
    with webmain._state_lock:
        webmain._state.update(
            {
                "payload": None,
                "last_refreshed_at": None,
                "is_refreshing": False,
                "last_error": None,
                "ticker_count": 0,
                "processed_count": 0,
            }
        )


def test_index_serves_dashboard_html():
    response = client.get("/")
    assert response.status_code == 200
    assert "그레이엄 밸류 스크리너" in response.text


def test_status_and_data_before_any_refresh():
    _reset_state()
    status = client.get("/api/status").json()
    assert status["last_refreshed_at"] is None
    assert status["is_refreshing"] is False

    data = client.get("/api/data").json()
    assert data == {"reports": [], "market_news": [], "generated_at": None}


def test_run_cycle_populates_state_on_success(monkeypatch):
    _reset_state()
    monkeypatch.setattr(webmain, "_load_configured_tickers", lambda: ["AAA", "BBB"])

    def fake_screener(*args, **kwargs):
        kwargs["data_provider"] = _FakeProvider()
        kwargs["news_provider"] = _FakeNews()
        from stockscreener.screener import Screener

        return Screener(*args, **kwargs)

    monkeypatch.setattr(webmain, "Screener", fake_screener)

    webmain._run_cycle()

    status = client.get("/api/status").json()
    assert status["is_refreshing"] is False
    assert status["last_error"] is None
    assert status["ticker_count"] == 2
    assert status["processed_count"] == 2
    assert status["last_refreshed_at"] is not None

    data = client.get("/api/data").json()
    assert len(data["reports"]) == 2
    assert {r["ticker"] for r in data["reports"]} == {"AAA", "BBB"}


def test_run_cycle_records_error_without_crashing(monkeypatch):
    _reset_state()
    monkeypatch.setattr(webmain, "_load_configured_tickers", lambda: ["X"])

    class ExplodingScreener:
        def __init__(self, *args, **kwargs):
            pass

        def get_market_news(self):
            raise RuntimeError("network exploded")

        def analyze(self, tickers):
            return []

    monkeypatch.setattr(webmain, "Screener", ExplodingScreener)

    webmain._run_cycle()  # 예외가 밖으로 새어나가면 안 된다

    status = client.get("/api/status").json()
    assert status["is_refreshing"] is False
    assert "network exploded" in status["last_error"]


def test_manual_refresh_rejects_when_already_running():
    _reset_state()
    with webmain._state_lock:
        webmain._state["is_refreshing"] = True

    response = client.post("/api/refresh")
    assert response.json() == {"started": False, "reason": "already_running"}
    _reset_state()
