"""Optimizer, dataset and calculation tests. Fixtures named FIXTURE_* are synthetic and NOT real component data."""
import copy, json, math, pytest
from core import component_catalog as cat, compatibility_engine as ce, engineering_calculations as ec, optimizer as opt, pipeline, specification_validator as sv
from core.data import CATALOG

RECS = cat.load()
P = {p["id"]: p for p in CATALOG}
def base(): return pipeline.select("line_follower", 3500, False, {})["best"]["parts"]
def fx(**kw):  # FIXTURE record: synthetic
    r = dict(id="fx1", name="FIXTURE", category="battery", description="synthetic", notes="synthetic", verification_status="unverified", specification_sources=[],
             specifications=dict(nominal_v=dict(value=None, unit="V"), full_charge_v=dict(value=None, unit="V"), capacity_mah=dict(value=None, unit="mAh")))
    r.update(kw); return r

# ---- dataset
def test_real_dataset_validates_with_no_errors():
    assert [x for x in sv.validate(RECS) if x["level"] == "error"] == []
def test_dataset_counts_and_status_honest():
    st = [r["verification_status"] for r in RECS]
    assert len(RECS) == 12 and st.count("partially_verified") == 2 and st.count("verified") == 0
def test_every_legacy_part_has_a_record():
    assert set(cat.by_legacy(RECS)) == {p["id"] for p in CATALOG}
def test_duplicate_ids_detected():
    assert any("duplicate" in e["msg"] for e in sv.validate([fx(), fx()]))
def test_missing_required_spec_and_bad_category():
    r = fx(category="spaceship"); r["specifications"].pop("capacity_mah")
    msgs = " ".join(e["msg"] for e in sv.validate([r])); assert "invalid category" in msgs
    r2 = fx(); r2["specifications"].pop("capacity_mah"); assert "capacity_mah" in " ".join(e["msg"] for e in sv.validate([r2]))
def test_invalid_numbers_and_units():
    r = fx(); r["specifications"]["nominal_v"] = dict(value="7.4", unit="V"); r["specifications"]["full_charge_v"] = dict(value=9, unit="furlongs")
    m = " ".join(e["msg"] for e in sv.validate([r])); assert "not a number" in m and "unknown unit" in m
def test_out_of_range_and_zero_for_unknown():
    r = fx(); r["specifications"]["nominal_v"] = dict(value=900, unit="V"); r["specifications"]["capacity_mah"] = dict(value=0, unit="mAh")
    out = sv.validate([r]); assert any("outside plausible" in e["msg"] for e in out) and any("0 is suspicious" in e["msg"] for e in out)
def test_battery_nominal_not_above_full_charge():
    r = fx(); r["specifications"]["nominal_v"] = dict(value=8.4, unit="V"); r["specifications"]["full_charge_v"] = dict(value=7.4, unit="V")
    assert any("nominal voltage must be below" in e["msg"] for e in sv.validate([r]))
def test_verified_status_requires_evidence():
    r = fx(verification_status="verified"); r["specifications"]["nominal_v"] = dict(value=7.4, unit="V")
    assert any("lack evidence" in e["msg"] for e in sv.validate([r]))
def test_evidence_must_match_value_and_have_good_url():
    r = fx(verification_status="partially_verified", last_verified="2026-10-17"); r["specifications"]["nominal_v"] = dict(value=7.4, unit="V")
    r["specification_sources"] = [dict(field="nominal_v", value=3.7, unit="V", source_url="ftp://x", retrieved="today", status="verified", note="n")]
    m = " ".join(e["msg"] for e in sv.validate([r])); assert "differs" in m and "bad source_url" in m and "bad retrieved" in m
def test_unknown_stays_null_not_zero():
    esp = cat.by_id(RECS)["ctrl_esp32_devkitc_v4"]; assert cat.spec_value(esp, "logic_v") is None and cat.spec_value(esp, "typical_current_ma") is None

# ---- calculations
def test_bom_total_and_missing_price():
    assert ec.bom_total([(100, 2), (50, 1)]).value == 250 and ec.bom_total([(100, 1), (None, 1)]).status == "insufficient"
def test_runtime_valid_and_missing_and_invalid():
    r = ec.runtime_hours(2000, 500); assert r.status == "ok" and math.isclose(r.value, 4.0) and "derating" in r.assumptions[0]
    assert math.isclose(ec.runtime_hours(2000, 500, 0.8).value, 3.2)
    m = ec.runtime_hours(None, 500); assert m.status == "insufficient" and "capacity_mah" in m.missing
    assert ec.runtime_hours(2000, 0).status == "invalid" and ec.runtime_hours(2000, 500, 1.5).status == "invalid"
def test_speed_does_not_apply_ratio_twice():
    d = 0.065; v = ec.wheel_speed_mps(100, d, gear_ratio=48, rpm_is_output_shaft=True)
    assert math.isclose(v.value, 100 * math.pi * d / 60)
    assert math.isclose(ec.wheel_speed_mps(4800, d, 48, rpm_is_output_shaft=False).value, v.value)
    assert ec.wheel_speed_mps(100, None).status == "insufficient" and ec.wheel_speed_mps(100, 0).status == "invalid"
def test_torque_output_vs_motor_side():
    assert ec.wheel_torque_nm(0.5).value == 0.5
    assert ec.wheel_torque_nm(0.01, 50, None, torque_is_output=False).status == "insufficient"   # efficiency never defaulted
    assert math.isclose(ec.wheel_torque_nm(0.01, 50, 0.6, torque_is_output=False).value, 0.3)
def test_efficiency_valid_and_invalid():
    assert math.isclose(ec.efficiency(3, 6).value, 50) and ec.efficiency(None, 6).status == "insufficient"
    assert ec.efficiency(7, 6).status == "invalid" and ec.efficiency(1, 0).status == "invalid"
def test_mechanical_power():
    assert math.isclose(ec.mechanical_power_w(1, 60).value, 2 * math.pi)
def test_total_current_incomplete_budget_reported():
    c = ec.total_current_ma([("mcu", 50, 1), ("motor", None, 2)]); assert c.status == "insufficient" and c.missing == ["motor"]
def test_compare_pct_and_zero_original():
    assert ec.compare(100, 150)["pct"] == 50 and ec.compare(0, 5)["pct"] is None and ec.compare(None, 5)["status"] == "unknown"
    assert ec.compare(100, 80, higher_is_better=False)["status"] == "improved"
def test_gpio_unknown_is_never_pass():
    assert ec.gpio_check({"pwm": 4}, {"pwm": None})[0]["status"] == "UNKNOWN" and ec.gpio_check({"pwm": 4}, {"pwm": 6})[0]["status"] == "PASS"

# ---- optimizer
def test_unsupported_replacement_category_rejected():
    with pytest.raises(ValueError): opt.replace(base(), "controller", P["bat_2s"])
    with pytest.raises(ValueError): opt.replace(base(), "chassis", P["ctrl_nano"])
def test_candidates_are_same_category():
    assert {c["role"] for c in opt.candidates(base(), "controller")} == {"controller"}
    assert all(c["kind"] == "line" for c in opt.candidates(base(), "sensor", "line"))
def test_original_is_never_mutated_and_baseline_shared():
    b = base(); before = copy.deepcopy(b)
    r1 = opt.compare_builds(b, opt.replace(b, "controller", P["ctrl_esp32"]), {"controller"}, RECS)
    r2 = opt.compare_builds(b, opt.replace(b, "driver", P["drv_tb6612"]), {"driver"}, RECS)
    assert b == before and r1["rows"][0]["original"] == r2["rows"][0]["original"]
def test_controller_swap_rows_are_honest():
    b = base(); res = opt.compare_builds(b, opt.replace(b, "controller", P["ctrl_esp32"]), {"controller"}, RECS)
    k = {r["key"]: r for r in res["rows"]}
    assert k["speed"]["status"] == "not_affected" and k["torque"]["status"] == "not_affected"      # no assumed mechanical gain
    assert k["wifi"]["status"] == "improved" and k["runtime"]["status"] == "reduced" and k["power"]["status"] == "reduced"   # from the dataset's typical values
    assert k["bluetooth"]["status"] == "improved" and k["bluetooth"]["quality"] == "typical"
def test_cost_difference_matches_part_prices():
    b = base(); res = opt.compare_builds(b, opt.replace(b, "controller", P["ctrl_esp32"]), {"controller"}, RECS)
    assert math.isclose(res["rows"][0]["diff"], P["ctrl_esp32"]["seed_price"] - P["ctrl_nano"]["seed_price"])
def test_live_price_changes_cost_and_quality():
    b = base(); live = {"ctrl_esp32": [dict(url="https://x.in", price=400, quote="Rs. 400")]}
    row = opt.compare_builds(b, opt.replace(b, "controller", P["ctrl_esp32"]), {"controller"}, RECS, live)["rows"][0]
    assert math.isclose(row["diff"], -50) and "mixed" in row["quality"]
def test_verified_catalog_overrides_seed_and_reports_conflict():
    b = base(); res = opt.compare_builds(b, b, set(), RECS)
    assert ("original", "controller", "Arduino Nano", ("vin_max_v", 12, 15)) in res["conflicts"]
def test_3s_battery_over_volts_motor_through_tb6612():
    b = base(); res = opt.compare_builds(b, opt.replace(b, "driver", P["drv_tb6612"]), {"driver"}, RECS)   # TB6612 max 13.5 V
    b3 = opt.replace(opt.replace(b, "driver", P["drv_tb6612"]), "battery", P["bat_3s"])
    r = opt.compare_builds(b, b3, {"driver", "battery"}, RECS); assert r["summary_proposed"]["state"] == "incompatible"
    st = {c["rule_id"]: c["status"] for c in r["compat_proposed"]}
    assert st["batt_driver_max"] == "PASS" and st["motor_voltage"] == "FAIL"   # 12.6 V is within the driver's limit, but the motors would be over-volted
def test_single_cell_incompatible_with_actions():
    b = base(); r = opt.compare_builds(b, opt.replace(b, "battery", P["bat_1s"]), {"battery"}, RECS)
    fails = [c for c in r["compat_proposed"] if c["status"] == "FAIL"]
    assert fails and all(c["action"] and c["components"] for c in fails) and r["rows"][1]["status"] == "incompatible"
def test_logic_level_failure_for_3v3_controller_with_5v_sensor():
    parts = dict(base()); parts = opt.replace(parts, "sensor", P["sen_hcsr04"])
    r = opt.compare_builds(parts, opt.replace(parts, "controller", P["ctrl_esp32"]), {"controller"}, RECS)
    assert {c["rule_id"]: c["status"] for c in r["compat_proposed"]}["logic_level"] == "FAIL"
def test_esp32_direct_battery_warns_not_passes():
    b = base(); r = opt.compare_builds(b, opt.replace(b, "controller", P["ctrl_esp32"]), {"controller"}, RECS)
    w = [c for c in r["compat_proposed"] if c["rule_id"] == "ctrl_fixed_supply"][0]; assert w["status"] == "WARN" and w["action"]
def test_missing_spec_is_unknown_in_engine():
    b = base(); b["motor"] = dict(b["motor"], specs={}); assert {c["rule_id"]: c["status"] for c in ce.check(opt.effective(b, RECS))}["driver_stall"] == "UNKNOWN"
def test_runtime_is_capacity_over_total_current():
    b = base(); k = {r["key"]: r for r in opt.compare_builds(b, opt.replace(b, "controller", P["ctrl_esp32"]), {"controller"}, RECS)["rows"]}["runtime"]
    cur0 = 19 + 40 + 2 * 0.2 * 1000; cur1 = 80 + 40 + 2 * 0.2 * 1000       # controller + sensor + 2 motors (profile values)
    assert k["status"] == "reduced" and math.isclose(k["original"], 0.8 * 2500 / cur0) and math.isclose(k["proposed"], 0.8 * 2500 / cur1)
def test_goal_ranking_rankable_and_not():
    b = base(); r = opt.rank_candidates(b, "controller", "lowest_cost", RECS); assert r["rankable"] and r["ranked"][0]["cost"] <= r["ranked"][-1]["cost"]
    assert opt.rank_candidates(b, "controller", "higher_speed", RECS)["rankable"] is False and opt.rank_candidates(b, "motor", "higher_speed", RECS)["rankable"]

# ---- network resilience
def test_explanation_is_deterministic_text_from_rows():
    b = base(); res = opt.compare_builds(b, opt.replace(b, "controller", P["ctrl_esp32"]), {"controller"}, RECS)
    t = opt.plain_explanation(res); assert "Wi-Fi" in t and t == opt.plain_explanation(res)
def test_tinyfish_unavailable_does_not_break_comparison(monkeypatch):
    from core import tf
    monkeypatch.setattr(tf, "search", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(RuntimeError): pipeline.source_part(P["ctrl_esp32"], log=lambda m: None)
    b = base(); assert opt.compare_builds(b, opt.replace(b, "controller", P["ctrl_esp32"]), {"controller"}, RECS)["rows"]   # core works without it

# ---- existing features still work through the optimizer path
def test_existing_pdf_export_for_optimized_bom():
    from core import bill
    b = opt.replace(base(), "controller", P["ctrl_esp32"]); pdf = bill.make(b, {}, {}, 5000, "Line follower (optimized)")["pdf"]
    assert pdf[:5] == b"%PDF-" and len(pdf) > 2000
