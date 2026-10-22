"""Part-profile knowledge base for the optimizer.
  data/part_profiles.json  curated TYPICAL values (shipped, read-only; built by scripts/build_swap_dataset.py)
  data/swap_store.json     profiles + pair records added at run time (grows by itself; safe to commit or delete)
A profile is {id, role, name, price_inr, kind, attrs{field: value}, origin, confidence}. Unknown fields are simply absent (never 0).
Every value is validated against FIELDS before it is stored, whoever produced it."""
import json, os, pathlib, re, threading, time

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
CURATED = DATA / "part_profiles.json"
def store_path(): return pathlib.Path(os.getenv("FORGE_SWAP_STORE") or DATA / "swap_store.json")

# field -> (unit, label, min, max, kind, roles). kind: n=number, b=bool. Bounds reject absurd values from any source.
ALL_ROLES_STR = "controller driver motor battery sensor power actuator display comms camera input storage programmer chassis wheels mechanical wiring"
FIELDS = {
 "clock_mhz": ("MHz", "Clock", 0.1, 5000, "n", "controller"), "flash_kb": ("kB", "Flash", 1, 1e6, "n", "controller"),
 "sram_kb": ("kB", "RAM", 0.1, 1e5, "n", "controller"), "cores": ("", "CPU cores", 1, 64, "n", "controller"),
 "arch_bits": ("bit", "Architecture", 8, 64, "n", "controller"), "wifi": ("", "Wi-Fi", 0, 1, "b", "controller comms"),
 "bluetooth": ("", "Bluetooth", 0, 1, "b", "controller comms"), "bt_classic": ("", "Bluetooth Classic", 0, 1, "b", "controller comms"),
 "gpio": ("pins", "Usable GPIO", 1, 500, "n", "controller"), "adc_channels": ("ch", "ADC channels", 0, 100, "n", "controller"),
 "adc_bits": ("bit", "ADC resolution", 6, 24, "n", "controller"), "pwm_channels": ("ch", "PWM channels", 0, 200, "n", "controller"),
 "logic_v": ("V", "Logic voltage", 0.8, 6, "n", "controller driver sensor display comms input camera"), "vin_min_v": ("V", "Min input voltage", 0, 60, "n", "controller"),
 "vin_max_v": ("V", "Max input voltage", 0, 60, "n", "controller"),
 "supply_min_v": ("V", "Min supply", 0, 60, "n", "driver sensor power display comms camera input actuator"), "supply_max_v": ("V", "Max supply", 0, 60, "n", "driver sensor power display comms camera input actuator"),
 "out_v": ("V", "Output voltage", 0, 60, "n", "power"),
 "drop_v": ("V", "Voltage drop", 0, 6, "n", "driver power"), "max_a": ("A", "Continuous current", 0.01, 500, "n", "driver battery power"),
 "peak_a": ("A", "Peak current", 0.01, 1000, "n", "driver battery power"), "channels": ("", "Channels", 1, 64, "n", "driver"),
 "eff_pct": ("%", "Bridge efficiency", 10, 100, "n", "driver power"), "pins_needed": ("pins", "Control pins needed", 0, 64, "n", "driver sensor display comms input camera actuator"),
 "logic_min_v": ("V", "Min logic-high", 0.8, 6, "n", "driver display comms"),
 "min_v": ("V", "Min rated voltage", 0, 60, "n", "motor actuator"), "max_v": ("V", "Max voltage", 0, 100, "n", "motor battery power actuator"),
 "rated_v": ("V", "Rated voltage", 0.5, 60, "n", "motor actuator"), "no_load_rpm": ("rpm", "No-load speed", 1, 100000, "n", "motor actuator"),
 "stall_torque_kgcm": ("kg·cm", "Stall torque", 0.001, 500, "n", "motor actuator"), "stall_a": ("A", "Stall current", 0.01, 200, "n", "motor actuator"),
 "run_a": ("A", "Running current", 0.005, 100, "n", "motor actuator"), "encoder": ("", "Encoder", 0, 1, "b", "motor"),
 "nominal_v": ("V", "Nominal voltage", 0.8, 100, "n", "battery power"), "capacity_mah": ("mAh", "Capacity", 10, 1e6, "n", "battery power"),
 "current_ma": ("mA", "Typical current", 0.01, 5000, "n", "controller sensor display comms input camera driver actuator power"),
 "weight_g": ("g", "Weight", 0.1, 50000, "n", ALL_ROLES_STR),
 "range_min_cm": ("cm", "Min range", 0, 10000, "n", "sensor camera"), "range_max_cm": ("cm", "Max range", 0.1, 100000, "n", "sensor camera"),
 "rate_hz": ("Hz", "Update rate", 0.1, 1e5, "n", "sensor display comms camera"), "pins_provided": ("pins", "Pins", 1, 500, "n", "controller"),
}
UNIT = {k: v[0] for k, v in FIELDS.items()}; LABEL = {k: v[1] for k, v in FIELDS.items()}
ROLE_FIELDS = {r: [k for k, v in FIELDS.items() if r in v[5].split()] for r in ("controller", "driver", "motor", "battery", "sensor", "power", "actuator", "display", "comms", "input", "camera")}
CRITICAL = dict(controller=["clock_mhz", "logic_v", "wifi", "bluetooth"], driver=["supply_min_v", "supply_max_v", "max_a"],
                motor=["rated_v", "no_load_rpm", "stall_torque_kgcm"], battery=["nominal_v", "capacity_mah"], sensor=["supply_min_v", "supply_max_v"],
                power=["nominal_v"], actuator=["rated_v"])
ROLE_ALIASES = dict(power="battery", actuator="motor")
ROLE_MAP = dict(controller="controller", driver="driver", motor="motor", battery="battery", sensor="sensor", power="battery", actuator="motor")
# profile attrs -> names the compatibility rule engine (core/data.RULES) reads
RULE_KEYS = dict(controller=dict(vin_min_v="vin_min_v", vin_max_v="vin_max_v", logic_v="logic_v", bluetooth="bluetooth"),
                 driver=dict(supply_min_v="supply_min_v", supply_max_v="supply_max_v", drop_v="drop_v", max_a="max_a"),
                 motor=dict(min_v="min_v", max_v="max_v", stall_a="stall_a"), battery=dict(nominal_v="nominal_v", max_v="max_v"),
                 sensor=dict(supply_min_v="supply_min_v", supply_max_v="supply_max_v"),
                 power=dict(nominal_v="nominal_v", max_v="max_v"),
                 actuator=dict(min_v="min_v", max_v="max_v", stall_a="stall_a"),
                 display=dict(supply_min_v="supply_min_v", supply_max_v="supply_max_v", logic_v="logic_v"),
                 comms=dict(supply_min_v="supply_min_v", supply_max_v="supply_max_v", logic_v="logic_v"),
                 camera=dict(supply_min_v="supply_min_v", supply_max_v="supply_max_v", logic_v="logic_v"),
                 input=dict(supply_min_v="supply_min_v", supply_max_v="supply_max_v", logic_v="logic_v"))

_LOCK = threading.RLock(); _MEM = {"sig": None, "data": None}

def slug(s): return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:48]

def validate(role, attrs):
    """-> (clean, rejected). Keeps only fields that belong to the role, have the right type and sit inside plausible bounds."""
    clean, bad = {}, {}
    eff_role = ROLE_ALIASES.get(role, role)
    for k, v in (attrs or {}).items():
        spec = FIELDS.get(k)
        if v is None: continue
        allowed = spec[5].split() if spec else []
        if not spec or (role not in allowed and eff_role not in allowed): bad[k] = "not a field for this role"; continue
        if spec[4] == "b":
            if isinstance(v, bool): clean[k] = v
            else: bad[k] = "expected true/false"
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)): bad[k] = "expected a number"; continue
        if not spec[2] <= v <= spec[3]: bad[k] = f"{v} outside plausible {spec[2]}..{spec[3]} {spec[0]}"; continue
        clean[k] = v
    if clean.get("vin_min_v") is not None and clean.get("vin_max_v") is not None and clean["vin_min_v"] > clean["vin_max_v"]: bad["vin_min_v"] = "min above max"; clean.pop("vin_min_v")
    if clean.get("supply_min_v") is not None and clean.get("supply_max_v") is not None and clean["supply_min_v"] > clean["supply_max_v"]: bad["supply_min_v"] = "min above max"; clean.pop("supply_min_v")
    if clean.get("nominal_v") is not None and clean.get("max_v") is not None and eff_role == "battery" and clean["nominal_v"] > clean["max_v"]: bad["nominal_v"] = "nominal above max"; clean.pop("nominal_v")
    if clean.get("bluetooth") is False and clean.get("bt_classic"): clean["bt_classic"] = False
    return clean, bad


def complete_enough(role, attrs):
    if not attrs: return False
    if role == "controller":
        return all(k in attrs for k in CRITICAL["controller"])
    if role == "driver":
        return all(k in attrs for k in CRITICAL["driver"])
    if role == "sensor":
        return all(k in attrs for k in CRITICAL["sensor"])
    if role == "motor":
        return "rated_v" in attrs or ("no_load_rpm" in attrs and "stall_torque_kgcm" in attrs)
    if role == "battery":
        return "nominal_v" in attrs
    if role == "power":
        return any(k in attrs for k in ("nominal_v", "supply_min_v", "max_v", "vin_min_v"))
    if role == "actuator":
        return any(k in attrs for k in ("rated_v", "min_v", "supply_min_v"))
    reqs = CRITICAL.get(role) or CRITICAL.get(ROLE_ALIASES.get(role, role))
    if not reqs: return bool(attrs)
    return any(k in attrs for k in reqs)

def _read(p):
    try: return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError): return {}

def _sig():
    out = []
    for p in (CURATED, store_path()):
        try: st = pathlib.Path(p).stat(); out.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError: out.append((str(p), 0, 0))
    return tuple(out)

def load():
    """{'profiles': {id: profile}, 'pairs': {...}} = curated + run-time store. Re-read only when a file changed."""
    with _LOCK:
        sig = _sig()
        if _MEM["sig"] != sig:
            cur, sto = _read(CURATED), _read(store_path())
            profs = {**cur.get("profiles", {}), **sto.get("profiles", {})}
            for p in profs.values(): p.setdefault("origin", "curated"); p.setdefault("confidence", "typical")
            _MEM.update(sig=sig, data=dict(profiles=profs, pairs={**cur.get("pairs", {}), **sto.get("pairs", {})}, curated_ids=set(cur.get("profiles", {}))))
        return _MEM["data"]

def get(pid):
    if not pid: return None
    d = load()["profiles"]
    if pid in d: return d[pid]
    for p in d.values():                       # components.json id or legacy id
        if pid in (p.get("component_id"), p.get("legacy_id")): return p
    if pid.startswith(("d_", "c_")):
        sub = pid[2:]
        if sub in d: return d[sub]
        for p in d.values():
            if sub in (p.get("component_id"), p.get("legacy_id")): return p
            if p["id"].endswith("_" + sub): return p
    return None

def get_by_name(name, role=None):
    if not name: return None
    target = name.strip().lower()
    d = load()["profiles"]
    for p in d.values():
        if role:
            eff_p = ROLE_ALIASES.get(p.get("role"), p.get("role"))
            eff_r = ROLE_ALIASES.get(role, role)
            if eff_p != eff_r and p.get("role") != role: continue
        if p["name"].strip().lower() == target: return p
    return None

def for_role(role, kind=None):
    eff_role = ROLE_ALIASES.get(role, role)
    return sorted((p for p in load()["profiles"].values() if (p["role"] == role or p["role"] == eff_role) and (kind is None or p.get("kind") == kind)), key=lambda p: p["name"])

def _save_store(mutator):
    """Read-modify-write the run-time store atomically. Never touches the curated file."""
    with _LOCK:
        path = store_path(); path.parent.mkdir(parents=True, exist_ok=True)
        data = _read(path) or {}
        data.setdefault("profiles", {}); data.setdefault("pairs", {})
        mutator(data)
        tmp = path.with_suffix(f".tmp{os.getpid()}"); tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8"); os.replace(tmp, path)
        _MEM["sig"] = None

def add_profile(profile):
    """Validate and persist a profile. Returns the stored profile or None if it is not usable."""
    clean, _bad = validate(profile["role"], profile.get("attrs"))
    if not complete_enough(profile["role"], clean): return None
    p = dict(profile, attrs=clean)
    p.setdefault("created", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    _save_store(lambda d: d["profiles"].__setitem__(p["id"], p))
    return get(p["id"])

def add_pair(key, rec): _save_store(lambda d: d["pairs"].__setitem__(key, rec))

def as_part(profile):
    """Profile -> part dict in the shape core.data / the rule engine / the BOM use."""
    role = profile.get("role", "part")
    eff = ROLE_ALIASES.get(role, role)
    rk = RULE_KEYS.get(role) or RULE_KEYS.get(eff) or {}
    a = profile.get("attrs", {})
    specs = {dst: a[src] for src, dst in rk.items() if a.get(src) is not None}
    return dict(id=profile["id"], role=role, name=profile["name"], seed_price=profile.get("price_inr"),
                query=profile["name"] + " buy India", kind=profile.get("kind"), specs=specs, _estimated=profile.get("origin") != "curated")

