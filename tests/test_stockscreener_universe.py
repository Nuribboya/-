from stockscreener.universe import load_sp500_tickers


def test_load_sp500_tickers_returns_hundreds_of_unique_tickers():
    tickers = load_sp500_tickers()
    assert len(tickers) > 400
    assert len(set(tickers)) == len(tickers)
    assert "AAPL" in tickers
    # 야후 파이낸스 형식으로 점(.)이 아니라 하이픈(-)을 써야 한다 (예: BRK.B -> BRK-B)
    assert "BRK-B" in tickers
    assert all("." not in t for t in tickers)
    assert all(not t.startswith("#") for t in tickers)
