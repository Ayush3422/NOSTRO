from nostro.policy.gate import CostModel, Decision, choose_tau, decide


def test_decide_is_a_simple_threshold():
    assert decide(0.99, 0.95) is Decision.AUTO_POST
    assert decide(0.94, 0.95) is Decision.REVIEW
    assert decide(0.95, 0.95) is Decision.AUTO_POST


def test_a_clean_separation_picks_a_threshold_between_the_groups():
    probs = [0.1] * 50 + [0.99] * 50
    labels = [0] * 50 + [1] * 50
    choice = choose_tau(probs, labels, CostModel())
    assert 0.1 < choice.tau <= 0.99
    assert choice.auto_post_count == 50
    assert choice.precision_at_tau == 1.0


def test_expensive_mistakes_push_the_threshold_up():
    probs = [0.4] * 50 + [0.8] * 50
    labels = [0] * 25 + [1] * 25 + [0] * 5 + [1] * 45
    cheap = choose_tau(probs, labels, CostModel(wrong_post_cost_paise=1000))
    dear = choose_tau(probs, labels, CostModel(wrong_post_cost_paise=100_000_00))
    assert dear.tau >= cheap.tau


def test_the_curve_is_returned_for_plotting():
    probs = [0.2, 0.4, 0.6, 0.8]
    labels = [0, 0, 1, 1]
    choice = choose_tau(probs, labels, CostModel())
    assert len(choice.curve) > 1
    assert {"tau", "expected_cost_paise", "auto_post_count", "precision"} <= set(choice.curve[0])


def test_chosen_tau_actually_minimises_the_curve():
    probs = [0.2, 0.4, 0.6, 0.8]
    labels = [0, 0, 1, 1]
    choice = choose_tau(probs, labels, CostModel())
    assert choice.expected_cost_paise == min(p["expected_cost_paise"] for p in choice.curve)


def test_no_data_falls_back_to_a_conservative_threshold():
    choice = choose_tau([], [], CostModel())
    assert choice.tau == 1.0            # auto-post nothing until we have evidence
    assert choice.auto_post_count == 0
