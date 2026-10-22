"""Pivot A re-plan: sold-out part + budget cut. Fixtures named LIVE_* are synthetic offers, NOT real shop data."""
import copy
import pytest
from fastapi.testclient import TestClient

from core import bill, compatibility_engine as ce, component_catalog as cat, optimizer as opt, pipeline, replan
from core.pagerules import trusted_price

PARTS = pipeline.select("line_follower", 3500, False, {})["best"]["parts"]
LIVE_N20 = {"mot_n20": [
    dict(url="https://robu.in/a", price=210.0, quote="Price: ₹210.00", name="N20", in_stock=None, checked_at="t", method="page rules"),
    dict(url="https://robocraze.com/b", price=240.0, quote="₹240", name="N20", in_stock=None, checked_at="t", method="page rules")]}


def run(**kw):
    kw.setdefault("budget_old", 3500)
    return replan.plan(copy.deepcopy(PARTS), kw.pop("live", {}), needs_bt=kw.pop("needs_bt", False), **kw)


# ---- 1. dry run: nothing is mutated
def test_plan_never_mutates_inputs():
    parts, live = copy.deepcopy(PARTS), copy.deepcopy(LIVE_N20)
    replan.plan(parts, live, budget_old=2085, cut_pct=40, sold_out={"mot_n20"}, needs_bt=False)
    assert parts == PARTS and live == LIVE_N20


# ---- 2. sold-out part is replaced, and the replacement is not the sold-out part
def test_sold_out_part_is_replaced_with_a_compatible_one():
    p = run(cut_pct=25, sold_out={"mot_n20"})
    ch = next(c for c in p["changes"] if c["role"] == "motor")
    assert ch["to_part"]["id"] != "mot_n20" and ch["reasons"][0]["code"] == "sold_out"
    assert p["guards"]["no_rule_worse"] and p["guards"]["compat_after"]["fail"] == 0


# ---- 3. listing-level sold out: same part, other shop, no part change
def test_sold_out_listing_switches_vendor_not_part():
    p = run(cut_pct=25, live=LIVE_N20, sold_out_urls={"mot_n20": ["https://robu.in/a"]})
    ch = p["changes"][0]
    assert ch["kind"] == "vendor_switch" and ch["source_url"] == "https://robocraze.com/b" and p["swaps"] == {}
    assert ch["cost_delta"] == pytest.approx((240 - 210) * 2)       # 2 motors


# ---- 4. a sold-out offer can never be priced (single choke point)
def test_sold_out_offer_is_not_a_trusted_price():
    o = dict(LIVE_N20["mot_n20"][0], in_stock=False)
    assert not trusted_price(o)
    live = replan.mark_sold_out(LIVE_N20, "mot_n20", {"https://robu.in/a"})
    assert [x["url"] for x in bill.offers_for("mot_n20", live)] == ["https://robocraze.com/b"]
    assert "Sold out" in bill.unverified_for("mot_n20", live)[0]["note"]


# ---- 5. budget cut: totals respect the new budget or the plan says it cannot
def test_budget_cut_fits_when_possible():
    p = run(budget_old=2085, budget_new=1900)
    assert p["status"] == "fits" and p["budget"]["after_total"] <= 1900
    assert all(c["reasons"][0]["code"] == "budget" for c in p["changes"])


def test_impossible_budget_is_reported_not_faked():
    p = run(budget_old=2085, budget_new=300)
    assert p["status"] == "over_budget" and p["budget"]["shortfall"] > 0
    assert p["budget"]["after_total"] > 300            # it did not "fit" by dropping parts or inventing prices
    assert p["guards"]["compat_after"]["fail"] == 0    # closest safe plan, still buildable


# ---- 6. no compatibility rule gets worse, in any scenario
@pytest.mark.parametrize("kw", [dict(budget_old=2085, budget_new=1500, sold_out={"drv_l298n"}),
                                dict(budget_old=2085, budget_new=1000, sold_out={"ctrl_nano", "bat_2s"}),
                                dict(budget_old=2085, cut_pct=40, sold_out={"sen_line5"})])
def test_no_rule_gets_worse(kw):
    p = run(**kw)
    assert p["guards"]["regressions"] == []
    new = replan.opt.snapshot(PARTS)
    for role, c in p["swaps"].items():
        new[role] = c
    recs = cat.load()
    before = {r["rule_id"]: replan.SEVERITY[r["status"]] for r in ce.check(opt.effective(PARTS, recs))}
    after = {r["rule_id"]: replan.SEVERITY[r["status"]] for r in ce.check(opt.effective(new, recs))}
    assert all(after[k] <= before.get(k, 0) for k in after)


# ---- 7. Bluetooth requirement is a hard constraint: Nano sold out + BT needed -> a Bluetooth-capable controller
def test_bluetooth_requirement_survives_the_replan():
    p = run(budget_old=3500, budget_new=1500, sold_out={"ctrl_nano"}, needs_bt=True)
    ctrl = next(c for c in p["changes"] if c["role"] == "controller")
    assert "esp32" in ctrl["to_part"]["id"] and p["guards"]["compat_after"]["fail"] == 0
    assert not any("Loses Bluetooth" in i for i in ctrl["impact"])


# ---- 8. wiring guard: a controller FORGE cannot wire is never proposed
def test_controller_without_pin_map_is_rejected():
    digispark = next(c for c in opt.candidates(PARTS, "controller") if c["id"] == "ctrl_attiny85_digispark")
    assert "no pin map" in replan._wiring_problem(PARTS, opt.replace(PARTS, "controller", digispark))
    p = run(budget_old=2085, budget_new=1400)
    assert "ctrl_attiny85_digispark" not in {c["to_part"]["id"] for c in p["changes"]}


def test_wireable_controller_passes_guard():
    assert replan._wiring_problem(PARTS, copy.deepcopy(PARTS)) is None


# ---- 9. a sold-out part is either replaced safely or reported as unresolved, never silently dropped
def test_sold_out_part_is_never_silently_dropped():
    p = run(cut_pct=10, sold_out={"sen_line5"})
    swapped = {c["role"] for c in p["changes"]}
    unresolved = {u["role"] for u in p["unresolved"]}
    assert "sensor" in swapped | unresolved


# ---- 10. unpriced parts are surfaced, not counted as free
def test_unpriced_parts_are_reported():
    parts = copy.deepcopy(PARTS)
    parts["sensor"] = dict(parts["sensor"], seed_price=None, price_inr=None, price=None, id="zz_unpriced", name="Unpriced sensor")
    p = replan.plan(parts, {}, budget_old=3500, budget_new=3000, needs_bt=False)
    assert "Unpriced sensor" in p["guards"]["unpriced_before"]


# ---- 11. public view is JSON-safe and hides internals
def test_public_view_strips_internal_part_dicts():
    import json
    pub = replan.public(run(cut_pct=25, sold_out={"mot_n20"}))
    json.dumps(pub)
    assert all("_part" not in c for c in pub["changes"])


# ---- 12. API round trip: preview does not change the bill; accept does; undo restores it
@pytest.fixture()
def client():
    from server.main import app
    c = TestClient(app)
    r = c.post("/api/bom/generate", json={"project": "line_follower", "budget": 3500})
    assert r.status_code == 200
    return c


def test_api_preview_accept_undo(client):
    before = client.get("/api/bom").json()
    prev = client.post("/api/replan/preview", json={"cut_pct": 25, "sold_out_part_ids": ["mot_n20"]})
    assert prev.status_code == 200, prev.text
    assert client.get("/api/bom").json()["bill"]["grand_total"] == before["bill"]["grand_total"]   # preview changed nothing
    acc = client.post("/api/replan/accept").json()
    assert acc["status"] == "ok" and acc["bom"]["budget"] == round(before["bill"]["grand_total"] * 0.75)
    assert "mot_n20" not in {p["id"] for p in acc["bom"]["parts"].values() if isinstance(p, dict)}
    assert client.get("/api/replan/log").json()[0]["changes"]
    und = client.post("/api/replan/undo").json()
    assert und["bom"]["budget"] == 3500 and "mot_n20" in {p["id"] for p in und["bom"]["parts"].values() if isinstance(p, dict)}


def test_api_rejects_unknown_part_and_missing_preview(client):
    assert client.post("/api/replan/preview", json={"cut_pct": 25, "sold_out_part_ids": ["nope"]}).status_code == 404
    assert client.post("/api/replan/accept").status_code == 409
    assert client.post("/api/replan/preview", json={"sold_out_part_ids": ["mot_n20"]}).status_code == 400


# ---- 13. Regression test for newer code: wiring studio, ledger, bill.pdf after accept
def test_regression_wiring_ledger_bill_after_accept(client):
    prev = client.post("/api/replan/preview", json={"cut_pct": 25, "sold_out_part_ids": ["mot_n20"]})
    assert prev.status_code == 200
    acc = client.post("/api/replan/accept")
    assert acc.status_code == 200
    w = client.get("/api/wiring")
    assert w.status_code == 200
    data = w.json()
    assert "component_tests" in data
    assert len(data["component_tests"]) > 0
    ledger_res = client.get("/api/bom/ledger")
    assert ledger_res.status_code == 200
    pdf_res = client.get("/api/bom/bill.pdf")
    assert pdf_res.status_code == 200
    assert pdf_res.headers.get("content-type") == "application/pdf"
