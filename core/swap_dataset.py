"""Pair dataset: 'what changes if I swap A for B' for same-role parts.
Lookup order: 1) data/part_profiles.json pairs (shipped)  2) data/swap_store.json pairs (added at run time)
              3) computed from the two profiles, then saved to the run-time store so the next lookup is a plain read.
If a profile is missing the caller may ask core.profile_fill for it first (see optimizer.profile_for)."""
from . import impact, profiles

def pair_key(role, a, b): return f"{role}|{a}>{b}"

def build_record(role, pa, pb):
    return dict(role=role, a=pa["id"], b=pb["id"], chips=impact.part_chips(role, pa, pb),
                deltas=[[r["key"], r["a"], r["b"], r["pct"]] for r in impact.intrinsic(role, pa, pb)],
                quality="typical" if pa.get("origin", "curated") == "curated" and pb.get("origin", "curated") == "curated" else "estimated")

def lookup(role, pa, pb, persist=True):
    """pa/pb: profiles (not None). Returns (record, how) with how in {'dataset','computed'}."""
    if pa["id"] == pb["id"]: return dict(role=role, a=pa["id"], b=pb["id"], chips=[], deltas=[], quality="typical"), "dataset"
    key = pair_key(role, pa["id"], pb["id"])
    # The generic owned power-bank profile intentionally has no assumed current, capacity, or weight.
    # Old curated pair rows contain made-up generic values, so always recompute these comparisons.
    generic_bank_pair = "pow_power_bank" in (pa["id"], pb["id"])
    hit = None if generic_bank_pair else profiles.load()["pairs"].get(key)
    if hit: return hit, "dataset"
    rec = build_record(role, pa, pb)
    if persist and not generic_bank_pair:
        try: profiles.add_pair(key, rec)
        except OSError: pass          # read-only deployment: still return the computed answer
    return rec, "computed"
