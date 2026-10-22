"""Compatibility results with rule id, status, components, actual/limit, explanation and action.
Wraps the existing rule engine (core/rules.py, core/data.RULES) and adds catalog-aware rules. PASS / WARN / FAIL / UNKNOWN; missing data is never a pass."""
from .data import QTY, RULES
from .rules import evaluate

ACTION = dict(batt_driver_min="Use a battery with a higher voltage or a driver with a lower minimum supply.",
              batt_driver_max="Use a lower-voltage battery or a driver that accepts the charged voltage.",
              motor_voltage="Pick a motor rated for the voltage left after the driver drop, or change battery/driver.",
              driver_stall="Use a driver rated above the motor's stall current.",
              ctrl_vin="Add a regulator or choose a battery within the controller's input range.",
              logic_level="Add a level shifter or choose a sensor that works at the controller's logic voltage.",
              bluetooth="Choose a controller with Bluetooth.")
INVOLVES = dict(batt_driver_min="battery, driver", batt_driver_max="battery, driver", motor_voltage="battery, driver, motor", driver_stall="driver, motor",
                ctrl_vin="controller, battery", logic_level="controller, sensor", bluetooth="controller")

def _row(rule_id, title, status, comps, msg, action="", actual=None, limit=None, quality="seed"):
    return dict(rule_id=rule_id, title=title, status=status, components=comps, explanation=msg, action=action if status in ("FAIL", "WARN") else "", actual=actual, limit=limit, quality=quality)

def check(parts, catalog_by_legacy=None, needs_bt=False):
    """parts: role -> part dict whose specs may already be merged with verified catalog values (see optimizer.merge_specs)."""
    out = []
    for c in evaluate(parts, RULES, needs_bt):
        out.append(_row(c["id"], c["title"], c["status"], INVOLVES.get(c["id"], ""), c["msg"], ACTION.get(c["id"], "")))
    # extra rule: battery voltage vs a controller whose docs give only a fixed supply (e.g. 5 V pin) -> cannot confirm direct connection
    ctrl = parts.get("controller") or next((p for p in parts.values() if isinstance(p, dict) and p.get("role") == "controller"), None)
    bat = parts.get("battery") or parts.get("power") or next((p for p in parts.values() if isinstance(p, dict) and p.get("role") in ("battery", "power")), None)
    if ctrl and bat and not bat.get("none"):
        sup, bmax = ctrl.get("_verified", {}).get("supply_nominal_v"), (bat.get("specs") or {}).get("max_v")
        if sup is not None and (ctrl.get("specs") or {}).get("vin_max_v") is not None and ctrl.get("_provenance", {}).get("vin_max_v") != "verified":
            if bmax is None: out.append(_row("ctrl_fixed_supply", "Controller supply documented for this battery", "UNKNOWN", "controller, battery", "Battery voltage unknown.", quality="verified"))
            elif bmax > sup + 0.5:
                out.append(_row("ctrl_fixed_supply", "Controller supply documented for this battery", "WARN", "controller, battery",
                                f"Official docs only describe a {sup:g} V supply for {ctrl['name']}; the battery reaches {bmax:g} V and no wider input range is documented.",
                                "Add a regulator (e.g. buck converter) to 5 V instead of connecting the battery directly.", actual=bmax, limit=sup, quality="verified"))
            else: out.append(_row("ctrl_fixed_supply", "Controller supply documented for this battery", "PASS", "controller, battery", "Battery within the documented supply.", actual=bmax, limit=sup, quality="verified"))
    if bat and not bat.get("none") and "power bank" in f"{bat.get('id', '')} {bat.get('name', '')}".lower():
        out.append(_row("power_bank_mode", "Power bank output mode and board input", "WARN", "battery, controller",
                        "This option models only the bank's nominal 5V default USB mode. The exact output current, negotiated USB-PD mode, cable, and controller input port are not verified; check the labels and use the board's documented 5V input.",
                        "Use USB input on Nano/Uno, the exact board's marked 5V input on ESP32, and never use a 9V/12V output mode.", quality="unknown"))
        motor = parts.get("motor") or next((p for p in parts.values() if isinstance(p, dict) and p.get("role") == "motor"), None)
        if motor and not motor.get("none"):
            bank_a = (bat.get("specs") or {}).get("max_a")
            bank_quality = "verified" if bat.get("_provenance", {}).get("max_a") == "verified" else "seed"
            stall_a = (motor.get("specs") or {}).get("stall_a")
            required_a = stall_a * (motor.get("qty") or QTY.get("motor", 1)) if stall_a is not None else None
            if bank_a is None or required_a is None:
                out.append(_row("power_bank_load", "Power bank output current vs motor load", "FAIL", "battery, motor",
                                "This generic 5V power bank has no verified output-current limit, or the motor stall current is unknown. It cannot be approved as a motor supply. Keep a separately rated motor battery or provide verified ratings for both parts.",
                                "Use a motor-rated battery or verify the exact power-bank output and motor stall current before considering this swap.", quality="seed"))
            elif bank_a < required_a:
                out.append(_row("power_bank_load", "Power bank output current vs motor load", "FAIL", "battery, motor",
                                f"Recorded bank rating {bank_a:g} A is below the motors' combined stall current estimate {required_a:g} A.",
                                "Use a motor-rated battery or a power source with a verified current rating above the combined load.", actual=bank_a, limit=required_a, quality=bank_quality))
            else:
                out.append(_row("power_bank_load", "Power bank output current vs motor load", "WARN", "battery, motor",
                                f"The recorded bank rating ({bank_a:g} A) exceeds the estimated combined motor stall current ({required_a:g} A), but controller/sensor demand, motor startup transients, cable limits, and current limiting still need checking before Stage B.",
                                actual=bank_a, limit=required_a, quality=bank_quality))
    return out

def summary(rows):
    c = lambda s: sum(r["status"] == s for r in rows)
    return dict(fail=c("FAIL"), warn=c("WARN"), unknown=c("UNKNOWN"), passed=c("PASS"),
                ready=c("FAIL") == 0, state="incompatible" if c("FAIL") else "caution" if c("WARN") or c("UNKNOWN") else "compatible")
