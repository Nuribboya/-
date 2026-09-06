from unittest.mock import patch

import pandas as pd

from stock_valuation.notify import format_notification_message, send_ntfy_notification, strong_buy_tickers


def test_strong_buy_tickers_filters_to_the_strongest_tier_only():
    result = pd.DataFrame(
        {
            "ticker": ["AAA", "BBB", "CCC"],
            "buy_tier": ["3차 매수 (강한 저평가)", "1차 매수", "관망"],
        }
    )
    assert strong_buy_tickers(result) == ["AAA"]


def test_strong_buy_tickers_empty_when_none_hit_strongest_tier():
    result = pd.DataFrame({"ticker": ["AAA"], "buy_tier": ["관망"]})
    assert strong_buy_tickers(result) == []


def test_format_notification_message_lists_tickers():
    message = format_notification_message(["AAA", "BBB"])
    assert "AAA" in message
    assert "BBB" in message


@patch("stock_valuation.notify.requests.post")
def test_send_ntfy_notification_posts_to_correct_topic_url(mock_post):
    send_ntfy_notification("my-secret-topic", "hello world", title="Test")
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "https://ntfy.sh/my-secret-topic"
    assert kwargs["data"] == "hello world".encode("utf-8")
    assert kwargs["headers"]["Title"] == "Test"
