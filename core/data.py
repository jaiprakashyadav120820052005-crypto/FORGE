"""Seed catalog + rules. Seed specs are TYPICAL datasheet values (verify against your exact part).
Live TinyFish sourcing adds price/stock/links on top; it never overwrites a spec without a quote."""
def P(id, role, name, price, query, kind=None, **specs):
    return dict(id=id, role=role, name=name, seed_price=price, query=query, kind=kind, specs=specs)

CATALOG = [
 P("ctrl_nano","controller","Arduino Nano",450,"Arduino Nano",vin_min_v=7,vin_max_v=12,logic_v=5,bluetooth=False),
 P("ctrl_esp32","controller","ESP32 DevKit",500,"ESP32 DevKit V1",vin_min_v=5,vin_max_v=12,logic_v=3.3,bluetooth=True),
 P("drv_l298n","driver","L298N driver",150,"L298N motor driver module",supply_min_v=5,supply_max_v=35,drop_v=2.0,max_a=2.0),
 P("drv_tb6612","driver","TB6612FNG driver",250,"TB6612FNG motor driver",supply_min_v=4.5,supply_max_v=13.5,drop_v=0.5,max_a=1.2),
 P("mot_n20","motor","N20 6V gear motor",200,"N20 6V 100RPM gear motor",min_v=3,max_v=9,stall_a=0.8),
 P("mot_tt","motor","TT gear motor",90,"TT gear motor 3-6V",min_v=3,max_v=6,stall_a=1.5),
 P("bat_1s","battery","1S Li-ion (3.7V)",200,"18650 battery 3.7V",nominal_v=3.7,max_v=4.2),
 P("bat_2s","battery","2S Li-ion (7.4V)",450,"2S 7.4V 18650 battery pack",nominal_v=7.4,max_v=8.4),
 P("bat_3s","battery","3S Li-ion (11.1V)",700,"3S 11.1V 18650 battery pack",nominal_v=11.1,max_v=12.6),
 dict(P("pow_power_bank","battery","Existing 5V USB power bank (owned; regulated 5V mode)",0,"",kind="power_bank",nominal_v=5.0,out_v=5.0), owned=True),
 P("sen_line5","sensor","5-channel line array",150,"5 channel IR line follower sensor array","line",supply_min_v=3.3,supply_max_v=5),
 P("sen_hcsr04","sensor","HC-SR04 ultrasonic",90,"HC-SR04 ultrasonic sensor","distance",supply_min_v=5,supply_max_v=5,output_high_v=5),
]
# Non-electronic extras every build needs. Prices are rough ESTIMATES until live sourcing finds a verified price.
# Caution: pick chassis kits WITHOUT motors, or the motors get counted twice. Chassis-to-motor fit is not rule-checked yet.
EXTRAS = [
 P("x_chassis","chassis","Chassis plate + wheels + caster (no motors)",350,"2WD robot car chassis wheels caster without motor"),
 P("x_wires","wires","Jumper wires + breadboard",120,"jumper wires 40 pcs breadboard"),
 P("x_switch","switch","Power switch",15,"SPDT slide switch"),
]
def J(label, desc, sensor, bt, diff, days, cost, ai, parts, demo, hack, tags):
    """Picker scores (1-10, days, INR) are my ESTIMATES. sensor = kind the build needs (None = no sensor); bt = Bluetooth needed."""
    return dict(label=label, desc=desc, sensor=sensor, bt=bt, diff=diff, days=days, cost=cost, ai=ai, parts=parts, demo=demo, hack=hack, tags=tags)

PROJECTS = {
 "line_follower": J("Line follower", "Follows a black line on the floor using IR sensors.", "line", False, 3, 4, 2500, 2, 10, 7, 5, ["sensors", "mechanics"]),
 "obstacle_avoider": J("Obstacle avoider", "Detects obstacles with ultrasonic and steers around them.", "distance", False, 3, 4, 3000, 3, 10, 7, 5, ["sensors"]),
 "bluetooth_car": J("Bluetooth car", "A small car you drive from your phone over Bluetooth.", None, True, 2, 3, 2800, 1, 10, 6, 3, ["wireless"]),
}

# Explicit recommendation factors: eight 1-5 ratings plus measured units and tool requirements.
# Estimates describe the currently supported builds; BOM selection still uses its own compatibility rules.
PROJECT_FACTORS = {
 "line_follower": dict(domain="sensing", difficulty=2, electronics=3, programming=2, fabrication=2,
                       control_math=3, ai_vision=1, wireless_iot=1, portfolio_impact=3,
                       build_hours=18, cost_inr=2500, parts_availability_india=5,
                       tools_required={"laptop", "soldering_iron"},
                       required_skills={"sensor_read", "motor_driver"},
                       prerequisites={"basic_programming", "basic_circuits"}),
 "obstacle_avoider": dict(domain="robotics", difficulty=3, electronics=3, programming=3, fabrication=2,
                          control_math=3, ai_vision=1, wireless_iot=1, portfolio_impact=3,
                          build_hours=24, cost_inr=3000, parts_availability_india=5,
                          tools_required={"laptop", "soldering_iron", "multimeter"},
                          required_skills={"sensor_read", "motor_driver", "breadboard"},
                          prerequisites={"basic_programming", "basic_circuits"}),
 "bluetooth_car": dict(domain="iot", difficulty=2, electronics=2, programming=3, fabrication=2,
                       control_math=1, ai_vision=1, wireless_iot=4, portfolio_impact=3,
                       build_hours=16, cost_inr=2800, parts_availability_india=4,
                       tools_required={"laptop"},
                       required_skills={"motor_driver", "c_cpp"},
                       prerequisites={"basic_programming"}),
}
ROBOTS = PROJECTS
# Planned, NOT built: they would need their own parts lists (e.g. edge sensors, several distance sensors), catalog parts and rules.
# Until then they are hidden, because with today's catalog they would just duplicate the obstacle avoider's BOM.
PLANNED = {"sumo_bot": "Sumo bot", "maze_solver": "Maze solver"}
NONE_SENSOR = dict(id="sen_none", role="sensor", name="No sensor needed", seed_price=0, query="", kind=None, none=True, specs={})
QTY = {"motor": 2}
# Each rule: needs (role.spec values required), check (formula), fail message. Missing value -> UNKNOWN, never guessed.
# Roles: c=controller d=driver m=motor b=battery s=sensor. Authored by us only (never from users or an LLM).
RULES = [
 dict(id="batt_driver_min", title="Battery meets driver minimum supply", needs=["b.nominal_v","d.supply_min_v"], check="b.nominal_v >= d.supply_min_v", fail="Battery {b.nominal_v} V is below the driver's minimum {d.supply_min_v} V"),
 dict(id="batt_driver_max", title="Charged battery within driver maximum", needs=["b.max_v","d.supply_max_v"], check="b.max_v <= d.supply_max_v", fail="Charged battery {b.max_v} V exceeds driver maximum {d.supply_max_v} V"),
 dict(id="motor_voltage", title="Motor voltage after driver drop is in range", needs=["b.nominal_v","b.max_v","d.drop_v","m.min_v","m.max_v"], check="b.nominal_v - d.drop_v >= m.min_v and b.max_v - d.drop_v <= m.max_v", fail="Motors would see {b.nominal_v}-{b.max_v} V minus {d.drop_v} V drop; rated {m.min_v}-{m.max_v} V"),
 dict(id="driver_stall", title="Driver covers motor stall current", needs=["d.max_a","m.stall_a"], check="d.max_a >= m.stall_a", fail="Driver {d.max_a} A is below motor stall current {m.stall_a} A"),
 dict(id="ctrl_vin", title="Battery within controller input range", needs=["c.vin_min_v","c.vin_max_v","b.nominal_v","b.max_v"], check="c.vin_min_v <= b.nominal_v and b.max_v <= c.vin_max_v", fail="Battery {b.nominal_v}-{b.max_v} V is outside controller input {c.vin_min_v}-{c.vin_max_v} V"),
 dict(id="logic_level", uses_sensor=True, title="Sensor signal voltage is safe for controller GPIO", needs=["s.output_high_v","c.logic_v"], check="s.output_high_v <= c.logic_v", fail="Sensor output can reach {s.output_high_v} V, above the controller's {c.logic_v} V GPIO logic level"),
]
