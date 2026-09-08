from polybot.execution.eligibility import is_fast_resolution_market
from polybot.ingestion.gamma_discovery import MarketInfo

BASE_KWARGS = {
    "condition_id": "0xabc",
    "question": "¿Test?",
    "yes_token_id": "yes",
    "no_token_id": "no",
    "fee_rate": 0.0,
    "fee_exponent": 1.0,
    "fees_enabled": False,
    "cluster_id": "event-1",
}


def test_sports_market_is_eligible():
    market = MarketInfo(**BASE_KWARGS, sports_market_type="moneyline")
    assert is_fast_resolution_market(market) is True


def test_non_sports_market_is_not_eligible():
    market = MarketInfo(**BASE_KWARGS, sports_market_type=None)
    assert is_fast_resolution_market(market) is False


def test_default_sports_market_type_is_none_and_ineligible():
    market = MarketInfo(**BASE_KWARGS)
    assert is_fast_resolution_market(market) is False
