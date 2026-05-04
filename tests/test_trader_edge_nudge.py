from trader_research.trader_edge import trader_edge_prob_nudge


def _features(**overrides):
    data = {
        "active_top_traders": True,
        "net_yes_minus_no_score": 0.0,
    }
    data.update(overrides)
    return data


def test_strength_zero_returns_probability_unchanged():
    assert trader_edge_prob_nudge(
        0.42,
        _features(net_yes_minus_no_score=100.0),
        strength=0,
    ) == 0.42


def test_inactive_top_traders_returns_probability_unchanged():
    assert trader_edge_prob_nudge(
        0.42,
        _features(active_top_traders=False, net_yes_minus_no_score=100.0),
        strength=0.05,
    ) == 0.42


def test_positive_score_nudges_probability_up():
    nudged = trader_edge_prob_nudge(
        0.42,
        _features(net_yes_minus_no_score=50.0),
        strength=0.05,
    )

    assert 0.42 < nudged <= 0.99


def test_negative_score_nudges_probability_down():
    nudged = trader_edge_prob_nudge(
        0.42,
        _features(net_yes_minus_no_score=-50.0),
        strength=0.05,
    )

    assert 0.01 <= nudged < 0.42


def test_extreme_scores_are_bounded():
    up = trader_edge_prob_nudge(
        0.98,
        _features(net_yes_minus_no_score=1_000_000.0),
        strength=0.50,
    )
    down = trader_edge_prob_nudge(
        0.02,
        _features(net_yes_minus_no_score=-1_000_000.0),
        strength=0.50,
    )

    assert up == 0.99
    assert down == 0.01
