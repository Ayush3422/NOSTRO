import json

import pytest
from fastapi.testclient import TestClient

from nostro.generator.config import GeneratorConfig
from nostro.generator.engine import generate


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("api")
    ds = generate(GeneratorConfig(cycles=6, payments_per_cycle=10), tmp / "data")
    from nostro.api.main import build_app
    return TestClient(build_app(data_dir=ds.razorpay_csv.parent,
                                audit_path=tmp / "audit.jsonl", use_model=False))


@pytest.fixture(scope="module")
def holdout_client(tmp_path_factory):
    """A dataset with a persisted meta.json holdout split, same as `nostro generate`
    writes -- so build_app resolves holdout mode the same way the CLI does."""
    tmp = tmp_path_factory.mktemp("api_holdout")
    ds = generate(GeneratorConfig(cycles=6, payments_per_cycle=10, seed=99), tmp / "data")
    data_dir = ds.razorpay_csv.parent
    (data_dir / "meta.json").write_text(
        json.dumps({"holdout_cycles": list(ds.holdout_cycles)}), encoding="utf-8")
    from nostro.api.main import build_app
    return TestClient(build_app(data_dir=data_dir,
                                audit_path=tmp / "audit.jsonl", use_model=False))


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_summary_carries_the_headline_numbers(client):
    body = client.get("/api/close/summary").json()
    for key in ("match_rate", "precision", "recall", "f1", "exceptions",
                "auto_posted", "tau", "degraded", "result_hash", "rows_per_second"):
        assert key in body, key


def test_summary_in_sample_mode_is_labelled(client):
    body = client.get("/api/close/summary").json()
    assert body["evaluation_mode"] == "in_sample"
    assert body["holdout_razorpay_match_rate"] is None


def test_summary_holdout_mode_does_not_surface_biased_match_rate(holdout_client):
    body = holdout_client.get("/api/close/summary").json()
    assert body["evaluation_mode"] == "holdout"
    assert body["holdout_razorpay_match_rate"] is not None
    # The whole-population match_rate is tautologically inflated in holdout mode
    # (see CloseResult.holdout_razorpay_match_rate docstring): it must not be
    # exposed under the plain `match_rate` key, or a dashboard consumer will read
    # it as if it were a normal, comparable figure.
    assert body["match_rate"] is None
    assert body["match_rate"] != body["holdout_razorpay_match_rate"]


def test_matches_paginate_and_filter(client):
    first = client.get("/api/close/matches?limit=5").json()
    assert len(first["items"]) <= 5
    assert first["total"] >= len(first["items"])
    exact = client.get("/api/close/matches?method=exact&limit=5").json()
    assert all(item["method"] == "exact" for item in exact["items"])


def test_exceptions_list_is_filterable(client):
    body = client.get("/api/close/exceptions").json()
    assert body["total"] >= 0
    if body["items"]:
        cls = body["items"][0]["exception_class"]
        filtered = client.get(f"/api/close/exceptions?exception_class={cls}").json()
        assert all(i["exception_class"] == cls for i in filtered["items"])


def test_drill_down_resolves_a_real_row(client):
    match = client.get("/api/close/matches?limit=1").json()["items"][0]
    row_id = (match["razorpay_ids"] + match["bank_ids"] + match["erp_ids"])[0]
    body = client.get(f"/api/close/row/{row_id}").json()
    assert body["row"]["row_id"] == row_id
    assert body["matches"]
    assert body["match"] is not None  # deprecated alias, kept for old clients


def test_drill_down_on_an_unknown_row_is_a_404(client):
    assert client.get("/api/close/row/does_not_exist").status_code == 404


def test_drill_down_probability_agrees_with_the_matches_endpoint(client):
    # Match.probability defaults to 0.0 on the model; the real calibrated value
    # lives in the parallel result.probabilities list. /matches injects it
    # correctly -- this test pins /row to do the same, so the two endpoints
    # never disagree about a match's confidence (a wrong confidence on the
    # drill-down screen is worse than showing none at all).
    listed = client.get("/api/close/matches?limit=1").json()["items"][0]
    row_id = (listed["razorpay_ids"] + listed["bank_ids"] + listed["erp_ids"])[0]
    body = client.get(f"/api/close/row/{row_id}").json()
    match = next(m for m in body["matches"] if m["match_id"] == listed["match_id"])
    assert match["probability"] == listed["probability"]


def test_drill_down_returns_every_match_touching_a_row(client):
    """R45: under per-axis consumption a row can legitimately be in two
    matches (one ERP-axis, one bank-axis). The endpoint must not silently
    drop the second one by returning only the first match found."""
    body_all = client.get("/api/close/matches?limit=500").json()["items"]
    counts: dict[str, int] = {}
    for m in body_all:
        for rid in (*m["razorpay_ids"], *m["bank_ids"], *m["erp_ids"]):
            counts[rid] = counts.get(rid, 0) + 1
    multi = [rid for rid, c in counts.items() if c > 1]
    if not multi:
        return  # nothing in this dataset exercises the two-match case
    row_id = multi[0]
    body = client.get(f"/api/close/row/{row_id}").json()
    assert len(body["matches"]) == counts[row_id]


def test_threshold_returns_the_curve(client):
    body = client.get("/api/close/threshold").json()
    assert "tau" in body
    assert isinstance(body["curve"], list)


def test_audit_endpoint_reports_integrity(client):
    body = client.get("/api/close/audit?limit=5").json()
    assert body["intact"] is True
    assert len(body["entries"]) <= 5


def test_no_endpoint_exposes_a_mutating_route(client):
    routes = {getattr(r, "path", None) for r in client.app.routes}
    methods = {
        m for r in client.app.routes for m in getattr(r, "methods", set()) or set()
    }
    assert methods <= {"GET", "HEAD", "OPTIONS"}
    assert routes  # sanity: routes were actually collected
