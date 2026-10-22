"""Verified-component knowledge base (data/components.json). Unknown values are None, never 0."""
import json, pathlib
PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "components.json"
# catalog spec name -> name used by the existing rule engine (core/data.py)
KEYMAP = dict(vin_min_v="vin_min_v", vin_max_v="vin_max_v", logic_v="logic_v", bluetooth="bluetooth",
              supply_min_v="supply_min_v", supply_max_v="supply_max_v", drop_v="drop_v", continuous_current_a_per_channel="max_a",
              rated_voltage_min_v="min_v", rated_voltage_max_v="max_v", stall_current_a="stall_a",
              nominal_v="nominal_v", full_charge_v="max_v", max_v="max_v",
              max_continuous_discharge_a="max_a")
CATEGORY_ROLE = dict(controller="controller", driver="driver", motor="motor", battery="battery", sensor="sensor")

def load(path=PATH):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))["components"]

def by_id(records): return {r["id"]: r for r in records}
def by_legacy(records): return {r["legacy_id"]: r for r in records if r.get("legacy_id")}
def in_category(records, category): return [r for r in records if r["category"] == category]
def search(records, text):
    t = text.lower(); return [r for r in records if t in (r["name"] + " " + (r.get("model_variant") or "") + " " + r["description"]).lower()]

def spec_value(rec, key):
    """Value of a spec or None (unknown)."""
    s = (rec or {}).get("specifications", {}).get(key)
    return s.get("value") if isinstance(s, dict) else None

def verified_fields(rec):
    return {e["field"] for e in rec.get("specification_sources", []) if e.get("status") == "verified"}

def evidence_for(rec, key):
    return [e for e in rec.get("specification_sources", []) if e["field"] == key]

def coverage(records):
    """{id: (verified_fields, total_fields)} - how much of each record is evidence-backed."""
    return {r["id"]: (len(verified_fields(r)), len(r["specifications"])) for r in records}
