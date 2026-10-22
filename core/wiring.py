"""Wiring and pinout generator with 1-click Wokwi simulation support.
Translates any build's component list into verified pin-to-pin connections,
generates interactive SVG/Mermaid circuit diagrams, and exports Wokwi diagram.json + starter firmware.
"""
import json
import re
from typing import Dict, List, Any, Tuple

# Standard Wire Color Conventions
COLOR_MAP = {
    "5V": "#EF4444",       # Red
    "3.3V": "#F59E0B",     # Amber
    "POWER": "#DC2626",    # Deep Red
    "GND": "#1E293B",      # Dark Slate/Black
    "I2C_SDA": "#10B981",  # Emerald Green
    "I2C_SCL": "#06B6D4",  # Cyan
    "SPI": "#8B5CF6",      # Purple
    "PWM": "#3B82F6",      # Blue
    "DIGITAL": "#F97316",  # Orange
    "ANALOG": "#EC4899",   # Pink
    "MOTOR": "#EAB308",    # Yellow
    "OTHER": "#94A3B8",    # Slate
}

# Microcontroller pin definitions and hardware peripherals
CONTROLLERS = {
    "nano": {
        "id": "nano",
        "name": "Arduino Nano",
        "wokwi_type": "wokwi-arduino-nano",
        "logic_v": 5.0,
        "pins": {
            "5V": "5V", "3V3": "3.3V", "GND": "GND.1", "GND2": "GND.2", "VIN": "VIN",
            "I2C_SDA": "A4", "I2C_SCL": "A5",
            "SPI_SS": "D10", "SPI_MOSI": "D11", "SPI_MISO": "D12", "SPI_SCK": "D13",
            "PWM": ["D3", "D5", "D6", "D9", "D10", "D11"],
            "ANALOG": ["A0", "A1", "A2", "A3", "A6", "A7"],
            "DIGITAL": ["D2", "D4", "D7", "D8", "D12", "D13"],
            "UART_TX": "TX", "UART_RX": "RX"
        }
    },
    "uno": {
        "id": "uno",
        "name": "Arduino Uno",
        "wokwi_type": "wokwi-arduino-uno",
        "logic_v": 5.0,
        "pins": {
            "5V": "5V", "3V3": "3.3V", "GND": "GND.1", "GND2": "GND.2", "VIN": "VIN",
            "I2C_SDA": "A4", "I2C_SCL": "A5",
            "SPI_SS": "D10", "SPI_MOSI": "D11", "SPI_MISO": "D12", "SPI_SCK": "D13",
            "PWM": ["D3", "D5", "D6", "D9", "D10", "D11"],
            "ANALOG": ["A0", "A1", "A2", "A3"],
            "DIGITAL": ["D2", "D4", "D7", "D8"],
            "UART_TX": "TX", "UART_RX": "RX"
        }
    },
    "esp32": {
        "id": "esp32",
        "name": "ESP32 DevKit",
        "wokwi_type": "board-esp32-devkit-c-v4",
        "logic_v": 3.3,
        "pins": {
            "5V": "5V", "3V3": "3V3", "GND": "GND.1", "GND2": "GND.2", "VIN": "5V",
            "I2C_SDA": "21", "I2C_SCL": "22",
            "SPI_SS": "5", "SPI_MOSI": "23", "SPI_MISO": "19", "SPI_SCK": "18",
            "PWM": ["14", "27", "26", "25", "33", "32", "12", "13"],
            "ANALOG": ["34", "35", "36", "39", "32", "33"],
            "DIGITAL": ["4", "15", "16", "17", "18", "19", "23"],
            "UART_TX": "1", "UART_RX": "3"
        }
    }
}

def _detect_controller(parts: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Identify which microcontroller is in the build."""
    for p in parts.values():
        if not isinstance(p, dict): continue
        pid = str(p.get("id", "")).lower()
        name = str(p.get("name", "")).lower()
        role = str(p.get("role", "")).lower()
        if role == "controller" or "arduino" in name or "esp32" in name or "nano" in name:
            if "esp32" in pid or "esp32" in name:
                return "esp32", CONTROLLERS["esp32"]
            if "uno" in pid or "uno" in name:
                return "uno", CONTROLLERS["uno"]
            return "nano", CONTROLLERS["nano"]
    return "nano", CONTROLLERS["nano"]


def _mermaid_label(value: Any) -> str:
    """Keep generated labels on one line and prevent Mermaid syntax injection."""
    label = str(value).replace("&", " and ").replace('"', "'")
    label = re.sub(r"[\[\]{}()|<>]", " ", label)
    return re.sub(r"\s+", " ", label).strip()


def generate_wiring(parts: Dict[str, Any], project_label: str = "Robot Build") -> Dict[str, Any]:
    """Generate pin-to-pin wiring connections, Wokwi diagram.json, and starter Arduino code."""
    ctrl_key, ctrl = _detect_controller(parts)
    has_level_shifter = any(
        isinstance(part, dict) and "shifter" in f"{part.get('id', '')} {part.get('name', '')}".lower()
        for part in parts.values()
    )
    protect_5v_echo = has_level_shifter and ctrl["logic_v"] <= 3.3
    connections: List[Dict[str, Any]] = []
    wokwi_parts: List[Dict[str, Any]] = []
    wokwi_connections: List[List[Any]] = []
    pin_alloc = {
        "pwm_idx": 0,
        "analog_idx": 0,
        "digital_idx": 0,
    }

    # Add Controller to Wokwi
    wokwi_parts.append({
        "type": ctrl["wokwi_type"],
        "id": ctrl_key,
        "top": 150,
        "left": 250,
        "attrs": {}
    })
    if protect_5v_echo:
        wokwi_parts.append({"type": "wokwi-logic-level-shifter", "id": "logic-shifter", "top": 40, "left": 410, "attrs": {}})

    # Keep track of assigned pins for starter code generation
    firmware_defines: List[Tuple[str, str, str]] = []
    firmware_setups: List[str] = []
    firmware_loops: List[str] = []
    firmware_includes: List[str] = []

    pos_x = 20
    pos_y = 50

    for role, part in parts.items():
        if not isinstance(part, dict) or part.get("none"): continue
        pid = str(part.get("id", "")).lower()
        pname = str(part.get("name", "")).lower()
        prole = str(part.get("role", role)).lower()

        # ---------------- 1. MOTOR DRIVERS ----------------
        if prole in ("driver", "actuator") and ("tb6612" in pid or "tb6612" in pname):
            # TB6612FNG Dual DC Motor Driver
            d_id = "tb6612"
            wokwi_parts.append({"type": "wokwi-logic-level-shifter", "id": d_id, "top": pos_y, "left": pos_x, "attrs": {}})
            pwma = ctrl["pins"]["PWM"][0] if ctrl_key == "nano" else "14"
            ain1 = "D7" if ctrl_key == "nano" else "27"
            ain2 = "D8" if ctrl_key == "nano" else "26"
            pwmb = ctrl["pins"]["PWM"][1] if ctrl_key == "nano" else "12"
            bin1 = "D9" if ctrl_key == "nano" else "33"
            bin2 = "D10" if ctrl_key == "nano" else "32"
            stby = "5V" if ctrl_key == "nano" else "3V3"

            connections.extend([
                {"from_part": "TB6612 Driver", "from_pin": "VM (Motor Power)", "to_part": "Battery", "to_pin": "(+) Terminal", "type": "POWER", "color": COLOR_MAP["POWER"], "note": "Direct motor voltage (e.g. 7.4V)"},
                {"from_part": "TB6612 Driver", "from_pin": "VCC (Logic)", "to_part": ctrl["name"], "to_pin": ctrl["pins"]["5V"] if ctrl["logic_v"]==5 else ctrl["pins"]["3V3"], "type": "5V", "color": COLOR_MAP["5V"], "note": "Logic level power"},
                {"from_part": "TB6612 Driver", "from_pin": "GND", "to_part": ctrl["name"], "to_pin": ctrl["pins"]["GND"], "type": "GND", "color": COLOR_MAP["GND"], "note": "Common ground with battery & MCU"},
                {"from_part": "TB6612 Driver", "from_pin": "PWMA (Left Speed)", "to_part": ctrl["name"], "to_pin": pwma, "type": "PWM", "color": COLOR_MAP["PWM"], "note": "Left motor PWM duty cycle"},
                {"from_part": "TB6612 Driver", "from_pin": "AIN1", "to_part": ctrl["name"], "to_pin": ain1, "type": "DIGITAL", "color": COLOR_MAP["DIGITAL"], "note": "Left motor direction pin 1"},
                {"from_part": "TB6612 Driver", "from_pin": "AIN2", "to_part": ctrl["name"], "to_pin": ain2, "type": "DIGITAL", "color": COLOR_MAP["DIGITAL"], "note": "Left motor direction pin 2"},
                {"from_part": "TB6612 Driver", "from_pin": "STBY (Standby)", "to_part": ctrl["name"], "to_pin": stby, "type": "5V", "color": COLOR_MAP["5V"], "note": "Must be HIGH to enable driver bridge"},
                {"from_part": "TB6612 Driver", "from_pin": "PWMB (Right Speed)", "to_part": ctrl["name"], "to_pin": pwmb, "type": "PWM", "color": COLOR_MAP["PWM"], "note": "Right motor PWM duty cycle"},
                {"from_part": "TB6612 Driver", "from_pin": "BIN1", "to_part": ctrl["name"], "to_pin": bin1, "type": "DIGITAL", "color": COLOR_MAP["DIGITAL"], "note": "Right motor direction pin 1"},
                {"from_part": "TB6612 Driver", "from_pin": "BIN2", "to_part": ctrl["name"], "to_pin": bin2, "type": "DIGITAL", "color": COLOR_MAP["DIGITAL"], "note": "Right motor direction pin 2"},
                {"from_part": "TB6612 Driver", "from_pin": "A01, A02", "to_part": "Left Motor", "to_pin": "Motor terminals", "type": "MOTOR", "color": COLOR_MAP["MOTOR"], "note": "DC Motor left channel"},
                {"from_part": "TB6612 Driver", "from_pin": "B01, B02", "to_part": "Right Motor", "to_pin": "Motor terminals", "type": "MOTOR", "color": COLOR_MAP["MOTOR"], "note": "DC Motor right channel"},
            ])
            wokwi_connections.extend([
                [f"{ctrl_key}:{ctrl['pins']['GND']}", f"{d_id}:GND", "black", ["v0"]],
                [f"{ctrl_key}:{ctrl['pins']['5V']}", f"{d_id}:HV", "red", ["v0"]],
            ])
            firmware_defines.extend([
                ("PWMA_PIN", pwma.replace("D", ""), "Left motor PWM speed"),
                ("AIN1_PIN", ain1.replace("D", ""), "Left motor direction 1"),
                ("AIN2_PIN", ain2.replace("D", ""), "Left motor direction 2"),
                ("PWMB_PIN", pwmb.replace("D", ""), "Right motor PWM speed"),
                ("BIN1_PIN", bin1.replace("D", ""), "Right motor direction 1"),
                ("BIN2_PIN", bin2.replace("D", ""), "Right motor direction 2"),
            ])
            firmware_setups.extend([
                "pinMode(PWMA_PIN, OUTPUT);",
                "pinMode(AIN1_PIN, OUTPUT);",
                "pinMode(AIN2_PIN, OUTPUT);",
                "pinMode(PWMB_PIN, OUTPUT);",
                "pinMode(BIN1_PIN, OUTPUT);",
                "pinMode(BIN2_PIN, OUTPUT);",
            ])
            firmware_loops.append("// Drive Forward (TB6612)\n  digitalWrite(AIN1_PIN, HIGH); digitalWrite(AIN2_PIN, LOW); analogWrite(PWMA_PIN, 180);\n  digitalWrite(BIN1_PIN, HIGH); digitalWrite(BIN2_PIN, LOW); analogWrite(PWMB_PIN, 180);")
            pos_y += 180

        elif prole in ("driver", "actuator") and ("l298" in pid or "l298" in pname or "driver" in prole):
            # L298N Dual H-Bridge Driver
            ena = "D5" if ctrl_key == "nano" else "14"
            in1 = "D7" if ctrl_key == "nano" else "27"
            in2 = "D8" if ctrl_key == "nano" else "26"
            in3 = "D9" if ctrl_key == "nano" else "33"
            in4 = "D10" if ctrl_key == "nano" else "32"
            enb = "D6" if ctrl_key == "nano" else "12"

            connections.extend([
                {"from_part": "L298N Driver", "from_pin": "12V Terminal", "to_part": "Battery", "to_pin": "(+) Terminal", "type": "POWER", "color": COLOR_MAP["POWER"], "note": "High current motor supply"},
                {"from_part": "L298N Driver", "from_pin": "GND Terminal", "to_part": ctrl["name"], "to_pin": ctrl["pins"]["GND"], "type": "GND", "color": COLOR_MAP["GND"], "note": "Common system ground"},
                {"from_part": "L298N Driver", "from_pin": "5V Terminal", "to_part": ctrl["name"], "to_pin": ctrl["pins"]["5V"], "type": "5V", "color": COLOR_MAP["5V"], "note": "5V logic input or 5V regulator output"},
                {"from_part": "L298N Driver", "from_pin": "ENA (Jumper Removed)", "to_part": ctrl["name"], "to_pin": ena, "type": "PWM", "color": COLOR_MAP["PWM"], "note": "Left motor speed PWM"},
                {"from_part": "L298N Driver", "from_pin": "IN1", "to_part": ctrl["name"], "to_pin": in1, "type": "DIGITAL", "color": COLOR_MAP["DIGITAL"], "note": "Left motor forward"},
                {"from_part": "L298N Driver", "from_pin": "IN2", "to_part": ctrl["name"], "to_pin": in2, "type": "DIGITAL", "color": COLOR_MAP["DIGITAL"], "note": "Left motor reverse"},
                {"from_part": "L298N Driver", "from_pin": "IN3", "to_part": ctrl["name"], "to_pin": in3, "type": "DIGITAL", "color": COLOR_MAP["DIGITAL"], "note": "Right motor forward"},
                {"from_part": "L298N Driver", "from_pin": "IN4", "to_part": ctrl["name"], "to_pin": in4, "type": "DIGITAL", "color": COLOR_MAP["DIGITAL"], "note": "Right motor reverse"},
                {"from_part": "L298N Driver", "from_pin": "ENB (Jumper Removed)", "to_part": ctrl["name"], "to_pin": enb, "type": "PWM", "color": COLOR_MAP["PWM"], "note": "Right motor speed PWM"},
                {"from_part": "L298N Driver", "from_pin": "OUT1, OUT2", "to_part": "Left Motor", "to_pin": "Motor leads", "type": "MOTOR", "color": COLOR_MAP["MOTOR"], "note": "Left motor drive terminals"},
                {"from_part": "L298N Driver", "from_pin": "OUT3, OUT4", "to_part": "Right Motor", "to_pin": "Motor leads", "type": "MOTOR", "color": COLOR_MAP["MOTOR"], "note": "Right motor drive terminals"},
            ])
            firmware_defines.extend([
                ("ENA_PIN", ena.replace("D", ""), "Left motor PWM enable"),
                ("IN1_PIN", in1.replace("D", ""), "Left motor IN1"),
                ("IN2_PIN", in2.replace("D", ""), "Left motor IN2"),
                ("IN3_PIN", in3.replace("D", ""), "Right motor IN3"),
                ("IN4_PIN", in4.replace("D", ""), "Right motor IN4"),
                ("ENB_PIN", enb.replace("D", ""), "Right motor PWM enable"),
            ])
            firmware_setups.extend([
                "pinMode(ENA_PIN, OUTPUT);", "pinMode(IN1_PIN, OUTPUT);", "pinMode(IN2_PIN, OUTPUT);",
                "pinMode(ENB_PIN, OUTPUT);", "pinMode(IN3_PIN, OUTPUT);", "pinMode(IN4_PIN, OUTPUT);",
            ])
            firmware_loops.append("// Drive Forward (L298N)\n  digitalWrite(IN1_PIN, HIGH); digitalWrite(IN2_PIN, LOW); analogWrite(ENA_PIN, 200);\n  digitalWrite(IN3_PIN, HIGH); digitalWrite(IN4_PIN, LOW); analogWrite(ENB_PIN, 200);")

        # ---------------- 2. SENSORS ----------------
        elif prole == "sensor" and ("ultrasonic" in pid or "hcsr04" in pid or "ultrasonic" in pname or "hcsr04" in pname):
            # Ultrasonic HC-SR04
            u_id = "sonar"
            wokwi_parts.append({"type": "wokwi-hc-sr04", "id": u_id, "top": 50, "left": 480, "attrs": {}})
            trig_pin = "D11" if ctrl_key == "nano" else "5"
            echo_pin = "D12" if ctrl_key == "nano" else "18"

            echo_connections = ([
                {"from_part": "HC-SR04 Ultrasonic", "from_pin": "ECHO", "to_part": "Logic Level Shifter", "to_pin": "HV1", "type": "DIGITAL", "color": COLOR_MAP["DIGITAL"], "note": "5V ECHO enters the high-voltage side"},
                {"from_part": "Logic Level Shifter", "from_pin": "LV1", "to_part": ctrl["name"], "to_pin": echo_pin, "type": "DIGITAL", "color": COLOR_MAP["DIGITAL"], "note": "Translated to the controller's 3.3V logic level"},
            ] if protect_5v_echo else [
                {"from_part": "HC-SR04 Ultrasonic", "from_pin": "ECHO", "to_part": ctrl["name"], "to_pin": echo_pin, "type": "DIGITAL", "color": COLOR_MAP["DIGITAL"], "note": "Echo response pulse" + (" (use a divider or level shifter for 3.3V ESP32)" if ctrl["logic_v"] == 3.3 else "")},
            ])
            connections.extend([
                {"from_part": "HC-SR04 Ultrasonic", "from_pin": "VCC", "to_part": ctrl["name"], "to_pin": ctrl["pins"]["5V"], "type": "5V", "color": COLOR_MAP["5V"], "note": "Requires 5V operating voltage"},
                {"from_part": "HC-SR04 Ultrasonic", "from_pin": "GND", "to_part": ctrl["name"], "to_pin": ctrl["pins"]["GND"], "type": "GND", "color": COLOR_MAP["GND"], "note": "Common ground"},
                {"from_part": "HC-SR04 Ultrasonic", "from_pin": "TRIG", "to_part": ctrl["name"], "to_pin": trig_pin, "type": "DIGITAL", "color": COLOR_MAP["DIGITAL"], "note": "Trigger 10µs pulse output; verify the exact module accepts 3.3V logic"},
                *echo_connections,
            ])
            if protect_5v_echo:
                connections.extend([
                    {"from_part": "Logic Level Shifter", "from_pin": "HV", "to_part": ctrl["name"], "to_pin": ctrl["pins"]["5V"], "type": "5V", "color": COLOR_MAP["5V"], "note": "High-side reference; verify this matches the module"},
                    {"from_part": "Logic Level Shifter", "from_pin": "LV", "to_part": ctrl["name"], "to_pin": ctrl["pins"]["3V3"], "type": "3.3V", "color": COLOR_MAP["3.3V"], "note": "Low-side reference for the 3.3V controller"},
                    {"from_part": "Logic Level Shifter", "from_pin": "GND", "to_part": ctrl["name"], "to_pin": ctrl["pins"]["GND"], "type": "GND", "color": COLOR_MAP["GND"], "note": "Common ground"},
                ])
            wokwi_connections.extend([
                [f"{ctrl_key}:{ctrl['pins']['5V']}", f"{u_id}:VCC", "red", ["v0"]],
                [f"{ctrl_key}:{ctrl['pins']['GND']}", f"{u_id}:GND", "black", ["v0"]],
                [f"{ctrl_key}:{trig_pin}", f"{u_id}:TRIG", "orange", ["v0"]],
            ])
            if protect_5v_echo:
                wokwi_connections.extend([
                    [f"{ctrl_key}:{ctrl['pins']['5V']}", "logic-shifter:HV", "red", ["v0"]],
                    [f"{ctrl_key}:{ctrl['pins']['3V3']}", "logic-shifter:LV", "red", ["v0"]],
                    [f"{ctrl_key}:{ctrl['pins']['GND']}", "logic-shifter:GND1", "black", ["v0"]],
                    [f"{ctrl_key}:{ctrl['pins']['GND']}", "logic-shifter:GND2", "black", ["v0"]],
                    [f"{u_id}:ECHO", "logic-shifter:HV1", "green", ["v0"]],
                    ["logic-shifter:LV1", f"{ctrl_key}:{echo_pin}", "green", ["v0"]],
                ])
            else:
                wokwi_connections.append([f"{ctrl_key}:{echo_pin}", f"{u_id}:ECHO", "green", ["v0"]])
            firmware_defines.extend([
                ("TRIG_PIN", trig_pin.replace("D", ""), "Ultrasonic Trigger Pin"),
                ("ECHO_PIN", echo_pin.replace("D", ""), "Ultrasonic Echo Pin"),
            ])
            firmware_setups.extend(["pinMode(TRIG_PIN, OUTPUT);", "pinMode(ECHO_PIN, INPUT);"])
            firmware_loops.append("// Read Ultrasonic Distance (cm)\n  digitalWrite(TRIG_PIN, LOW); delayMicroseconds(2);\n  digitalWrite(TRIG_PIN, HIGH); delayMicroseconds(10); digitalWrite(TRIG_PIN, LOW);\n  long duration = pulseIn(ECHO_PIN, HIGH);\n  float distance_cm = duration * 0.034 / 2;\n  Serial.print(\"Distance: \"); Serial.print(distance_cm); Serial.println(\" cm\");")

        elif prole == "sensor" and ("line" in pid or "ir" in pid or "line" in pname):
            # IR Line Follower Array (e.g. 5-Channel)
            pins_used = ["A0", "A1", "A2", "A3", "A6"] if ctrl_key == "nano" else ["34", "35", "32", "33", "25"]
            connections.extend([
                {"from_part": "IR Line Sensor Array", "from_pin": "VCC", "to_part": ctrl["name"], "to_pin": ctrl["pins"]["5V"], "type": "5V", "color": COLOR_MAP["5V"], "note": "Power for IR emitters and photodiodes"},
                {"from_part": "IR Line Sensor Array", "from_pin": "GND", "to_part": ctrl["name"], "to_pin": ctrl["pins"]["GND"], "type": "GND", "color": COLOR_MAP["GND"], "note": "System ground"},
            ])
            for i, p_out in enumerate(pins_used):
                connections.append({
                    "from_part": "IR Line Sensor Array", "from_pin": f"OUT {i+1} (Sensor {i+1})", "to_part": ctrl["name"], "to_pin": p_out, "type": "ANALOG", "color": COLOR_MAP["ANALOG"], "note": f"Channel {i+1} reflect reading"
                })
            firmware_defines.append(("LINE_SENSORS_COUNT", "5", "Number of line tracking IR channels"))
            firmware_setups.append("// IR sensors are read via analogRead() / digitalRead()")
            firmware_loops.append("// Line tracking check\n  int left_val = analogRead(A0); int mid_val = analogRead(A2); int right_val = analogRead(A4);")

        elif prole == "sensor" and ("dht" in pid or "dht" in pname or "temp" in pname):
            # DHT11 / DHT22 Sensor
            dht_id = "dht1"
            wokwi_parts.append({"type": "wokwi-dht22", "id": dht_id, "top": 240, "left": 480, "attrs": {}})
            data_pin = "D4" if ctrl_key == "nano" else "4"
            connections.extend([
                {"from_part": "DHT Sensor", "from_pin": "VCC", "to_part": ctrl["name"], "to_pin": ctrl["pins"]["5V"], "type": "5V", "color": COLOR_MAP["5V"], "note": "3.3V - 5V power"},
                {"from_part": "DHT Sensor", "from_pin": "GND", "to_part": ctrl["name"], "to_pin": ctrl["pins"]["GND"], "type": "GND", "color": COLOR_MAP["GND"], "note": "Ground"},
                {"from_part": "DHT Sensor", "from_pin": "DATA", "to_part": ctrl["name"], "to_pin": data_pin, "type": "DIGITAL", "color": COLOR_MAP["DIGITAL"], "note": "Single-bus communication pin"},
            ])
            wokwi_connections.extend([
                [f"{ctrl_key}:{ctrl['pins']['5V']}", f"{dht_id}:VCC", "red", ["v0"]],
                [f"{ctrl_key}:{ctrl['pins']['GND']}", f"{dht_id}:GND", "black", ["v0"]],
                [f"{ctrl_key}:{data_pin}", f"{dht_id}:SDA", "green", ["v0"]],
            ])
            firmware_includes.append("#include <DHT.h>")
            firmware_defines.extend([("DHTPIN", data_pin.replace("D", ""), "DHT Data Pin"), ("DHTTYPE", "DHT11", "DHT sensor type")])
            firmware_setups.append("dht.begin();")
            firmware_loops.append("// Read DHT\n  float h = dht.readHumidity(); float t = dht.readTemperature();\n  Serial.print(\"Temp: \"); Serial.print(t); Serial.println(\" *C\");")

        # ---------------- 3. DISPLAYS ----------------
        elif prole == "display" or "oled" in pid or "oled" in pname or "ssd1306" in pname:
            # OLED SSD1306 I2C Display
            oled_id = "oled1"
            wokwi_parts.append({"type": "board-ssd1306", "id": oled_id, "top": 360, "left": 480, "attrs": {}})
            sda = ctrl["pins"]["I2C_SDA"]
            scl = ctrl["pins"]["I2C_SCL"]
            connections.extend([
                {"from_part": "OLED Display (SSD1306)", "from_pin": "VCC", "to_part": ctrl["name"], "to_pin": ctrl["pins"]["3V3"], "type": "3.3V", "color": COLOR_MAP["3.3V"], "note": "3.3V / 5V tolerant"},
                {"from_part": "OLED Display (SSD1306)", "from_pin": "GND", "to_part": ctrl["name"], "to_pin": ctrl["pins"]["GND"], "type": "GND", "color": COLOR_MAP["GND"], "note": "System ground"},
                {"from_part": "OLED Display (SSD1306)", "from_pin": "SDA", "to_part": ctrl["name"], "to_pin": sda, "type": "I2C_SDA", "color": COLOR_MAP["I2C_SDA"], "note": "Hardware I2C Data line"},
                {"from_part": "OLED Display (SSD1306)", "from_pin": "SCL", "to_part": ctrl["name"], "to_pin": scl, "type": "I2C_SCL", "color": COLOR_MAP["I2C_SCL"], "note": "Hardware I2C Clock line"},
            ])
            wokwi_connections.extend([
                [f"{ctrl_key}:{ctrl['pins']['3V3']}", f"{oled_id}:VCC", "red", ["v0"]],
                [f"{ctrl_key}:{ctrl['pins']['GND']}", f"{oled_id}:GND", "black", ["v0"]],
                [f"{ctrl_key}:{sda}", f"{oled_id}:SDA", "green", ["v0"]],
                [f"{ctrl_key}:{scl}", f"{oled_id}:SCL", "blue", ["v0"]],
            ])
            firmware_includes.extend(["#include <Wire.h>", "#include <Adafruit_GFX.h>", "#include <Adafruit_SSD1306.h>"])
            firmware_setups.append("display.begin(SSD1306_SWITCHCAPVCC, 0x3C);\ndisplay.clearDisplay();\ndisplay.setTextSize(1);\ndisplay.setTextColor(SSD1306_WHITE);")
            firmware_loops.append("// OLED Status Update\n  display.clearDisplay(); display.setCursor(0,0); display.println(\"FORGE Active\"); display.display();")

        # ---------------- 4. SERVOS ----------------
        elif "servo" in pid or "servo" in pname:
            servo_id = f"servo_{len(wokwi_parts)}"
            wokwi_parts.append({"type": "wokwi-servo", "id": servo_id, "top": 100, "left": 50, "attrs": {}})
            pwm_pin = "D9" if ctrl_key == "nano" else "13"
            connections.extend([
                {"from_part": part.get("name", "Servo Motor"), "from_pin": "V+ (Red Wire)", "to_part": ctrl["name"], "to_pin": ctrl["pins"]["5V"], "type": "5V", "color": COLOR_MAP["5V"], "note": "5V - 6V operating power"},
                {"from_part": part.get("name", "Servo Motor"), "from_pin": "GND (Brown Wire)", "to_part": ctrl["name"], "to_pin": ctrl["pins"]["GND"], "type": "GND", "color": COLOR_MAP["GND"], "note": "Common ground"},
                {"from_part": part.get("name", "Servo Motor"), "from_pin": "PWM Signal (Orange)", "to_part": ctrl["name"], "to_pin": pwm_pin, "type": "PWM", "color": COLOR_MAP["PWM"], "note": "50Hz Servo control pulse"},
            ])
            wokwi_connections.extend([
                [f"{ctrl_key}:{ctrl['pins']['5V']}", f"{servo_id}:V+", "red", ["v0"]],
                [f"{ctrl_key}:{ctrl['pins']['GND']}", f"{servo_id}:GND", "black", ["v0"]],
                [f"{ctrl_key}:{pwm_pin}", f"{servo_id}:PWM", "orange", ["v0"]],
            ])
            firmware_includes.append("#include <Servo.h>")
            firmware_defines.append(("SERVO_PIN", pwm_pin.replace("D", ""), "Servo PWM signal pin"))
            firmware_setups.append("myServo.attach(SERVO_PIN);")
            firmware_loops.append("// Sweep servo\n  myServo.write(90); delay(500); myServo.write(180); delay(500);")

        # ---------------- 5. POWER & BATTERY ----------------
        elif prole == "battery" or "bat" in pid or "battery" in pname:
            if re.search(r"power.?bank", f"{pid} {pname}"):
                power_input = "USB input (5V; do not use VIN)" if ctrl_key in ("nano", "uno") else "5V/VIN input (verify exact board)"
                connections.extend([
                    {"from_part": part.get("name", "5V USB Power Bank"), "from_pin": "USB 5V output", "to_part": ctrl["name"], "to_pin": power_input, "type": "5V", "color": COLOR_MAP["5V"], "note": "Use documented 5V default mode only; verify the exact board input. Never connect 5V to Nano/Uno VIN."},
                    {"from_part": part.get("name", "5V USB Power Bank"), "from_pin": "USB ground", "to_part": ctrl["name"], "to_pin": ctrl["pins"]["GND"], "type": "GND", "color": COLOR_MAP["GND"], "note": "Common ground; use a suitable USB cable/adapter and preserve connector polarity."},
                ])
            else:
                connections.extend([
                    {"from_part": part.get("name", "Battery Pack"), "from_pin": "(+) Positive Lead", "to_part": ctrl["name"], "to_pin": ctrl["pins"]["VIN"], "type": "POWER", "color": COLOR_MAP["POWER"], "note": "Raw battery voltage to onboard VIN regulator"},
                    {"from_part": part.get("name", "Battery Pack"), "from_pin": "(-) Ground Lead", "to_part": ctrl["name"], "to_pin": ctrl["pins"]["GND2"] if "GND2" in ctrl["pins"] else ctrl["pins"]["GND"], "type": "GND", "color": COLOR_MAP["GND"], "note": "Common battery ground rail"},
                ])

    # If no specific modules matched, ensure standard basic power rails are documented
    if len(connections) <= 2:
        connections.append({"from_part": "Microcontroller", "from_pin": "5V", "to_part": "Breadboard Rail", "to_pin": "5V Positive Rail", "type": "5V", "color": COLOR_MAP["5V"], "note": "System 5V power bus"})
        connections.append({"from_part": "Microcontroller", "from_pin": "GND", "to_part": "Breadboard Rail", "to_pin": "Ground Rail", "type": "GND", "color": COLOR_MAP["GND"], "note": "System common ground bus"})

    # Assemble Wokwi Diagram JSON
    wokwi_diagram = {
        "version": 1,
        "author": "FORGE Robot BOM Agent",
        "editor": "wokwi",
        "parts": wokwi_parts,
        "connections": wokwi_connections
    }

    # Assemble Starter Firmware Code
    code_lines = [
        "// -------------------------------------------------------------",
        f"// FORGE Auto-Generated Starter Firmware: {project_label}",
        f"// Target Controller: {ctrl['name']}",
        "// Hardware pins automatically mapped to match the Wiring Schematic",
        "// -------------------------------------------------------------\n"
    ]
    for inc in sorted(set(firmware_includes)):
        code_lines.append(inc)
    if firmware_includes: code_lines.append("")

    for name, val, desc in firmware_defines:
        code_lines.append(f"#define {name} {val}  // {desc}")
    code_lines.append("")

    # Global declarations
    if "#include <DHT.h>" in firmware_includes:
        code_lines.append("DHT dht(DHTPIN, DHTTYPE);")
    if "#include <Adafruit_SSD1306.h>" in firmware_includes:
        code_lines.append("#define SCREEN_WIDTH 128\n#define SCREEN_HEIGHT 64\nAdafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);")
    if "#include <Servo.h>" in firmware_includes:
        code_lines.append("Servo myServo;")

    code_lines.append("\nvoid setup() {")
    code_lines.append("  Serial.begin(115200);")
    code_lines.append("  Serial.println(F(\"FORGE System Initializing...\"));\n")
    for s in firmware_setups:
        code_lines.append(f"  {s}")
    code_lines.append("  Serial.println(F(\"Setup Complete. Running main loop.\"));")
    code_lines.append("}\n")

    code_lines.append("void loop() {")
    if firmware_loops:
        for l in firmware_loops:
            code_lines.append(f"  {l}\n")
    else:
        code_lines.append("  // System active heartbeat\n  Serial.println(\"System running...\");")
    code_lines.append("  delay(200);")
    code_lines.append("}")

    firmware_code = "\n".join(code_lines)

    # Generate a device-level Mermaid diagram with unique IDs and readable signal labels.
    mermaid_lines = [
        "graph LR",
        "  classDef mcu fill:#351D0B,stroke:#FF7A1A,stroke-width:3px,color:#FFF7ED;",
        "  classDef pwr fill:#3B1515,stroke:#EF4444,stroke-width:2px,color:#FEE2E2;",
        "  classDef drv fill:#33250B,stroke:#EAB308,stroke-width:2px,color:#FEF9C3;",
        "  classDef sen fill:#102B35,stroke:#06B6D4,stroke-width:2px,color:#CFFAFE;",
        "  classDef component fill:#171A22,stroke:#64748B,stroke-width:1px,color:#E2E8F0;",
        "  classDef note fill:#21170F,stroke:#FF7A1A,stroke-dasharray:4 3,color:#FED7AA;",
    ]
    node_ids: Dict[str, str] = {}
    link_styles = []

    def add_node(name: str) -> str:
        if name in node_ids:
            return node_ids[name]

        node_id = f"device{len(node_ids)}"
        node_ids[name] = node_id
        safe_name = _mermaid_label(name)
        if name == ctrl["name"]:
            node_class = "mcu"
        elif re.search(r"battery|power|rail", name, re.IGNORECASE):
            node_class = "pwr"
        elif re.search(r"driver|motor", name, re.IGNORECASE):
            node_class = "drv"
        elif re.search(r"sensor|ultrasonic|oled|dht|encoder", name, re.IGNORECASE):
            node_class = "sen"
        else:
            node_class = "component"
        mermaid_lines.append(f'  {node_id}["{safe_name}"]:::{node_class}')
        return node_id

    add_node(ctrl["name"])
    for connection in connections:
        source_id = add_node(connection["from_part"])
        target_id = add_node(connection["to_part"])
        pin_label = _mermaid_label(
            f"{connection['type']}: {connection['from_pin']} -> {connection['to_pin']}"
        )
        mermaid_lines.append(f'  {source_id} -->|"{pin_label}"| {target_id}')
        link_styles.append(
            f"  linkStyle {len(link_styles)} stroke:{connection['color']},stroke-width:2px"
        )

    mermaid_lines.extend(link_styles)
    mermaid_lines.append(
        '  legend["Read each edge as signal type: source pin -> destination pin. '
        'Confirm voltage and current limits before building."]:::note'
    )

    mermaid_code = "\n".join(mermaid_lines)

    # Generate SVG Graphical Canvas
    svg_canvas = _render_svg_schematic(ctrl, connections, project_label)

    return {
        "controller": ctrl,
        "connections": connections,
        "wokwi_diagram": wokwi_diagram,
        "wokwi_json_str": json.dumps(wokwi_diagram, indent=2),
        "firmware_code": firmware_code,
        "mermaid_code": mermaid_code,
        "svg_canvas": svg_canvas,
    }


def _render_svg_schematic(ctrl: Dict[str, Any], connections: List[Dict[str, Any]], title: str) -> str:
    """Generate a clean SVG schematic showing the controller with pin traces."""
    svg_w = 900
    svg_h = max(520, 100 + len(connections) * 32)
    
    lines = [
        f'<svg viewBox="0 0 {svg_w} {svg_h}" width="100%" height="{svg_h}" xmlns="http://www.w3.org/2000/svg" style="background:#0D1528; border-radius:16px; border:1px solid rgba(124,58,237,0.25); font-family:Inter,system-ui,sans-serif;">',
        '  <defs>',
        '    <linearGradient id="mcuGrad" x1="0%" y1="0%" x2="100%" y2="100%">',
        '      <stop offset="0%" stop-color="#1E1B4B" />',
        '      <stop offset="100%" stop-color="#312E81" />',
        '    </linearGradient>',
        '    <linearGradient id="cardGrad" x1="0%" y1="0%" x2="100%" y2="100%">',
        '      <stop offset="0%" stop-color="#0F172A" />',
        '      <stop offset="100%" stop-color="#1E293B" />',
        '    </linearGradient>',
        '    <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">',
        '      <feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="#7C3AED" flood-opacity="0.35"/>',
        '    </filter>',
        '  </defs>',
        f'  <text x="30" y="42" fill="#E2E8F0" font-size="18" font-weight="700">⚡ {title} — Wiring Schematic</text>',
        f'  <text x="30" y="66" fill="#64748B" font-size="12">Verified pin connections &amp; color-coded signal rails for {ctrl["name"]}</text>',
    ]

    # Draw Central MCU
    mcu_x, mcu_y, mcu_w, mcu_h = 320, 100, 260, 360
    lines.append(f'  <rect x="{mcu_x}" y="{mcu_y}" width="{mcu_w}" height="{mcu_h}" rx="14" fill="url(#mcuGrad)" stroke="#7C3AED" stroke-width="2" filter="url(#glow)"/>')
    lines.append(f'  <text x="{mcu_x + mcu_w//2}" y="{mcu_y + 36}" text-anchor="middle" fill="#FFFFFF" font-size="16" font-weight="800">🧠 {ctrl["name"]}</text>')
    lines.append(f'  <text x="{mcu_x + mcu_w//2}" y="{mcu_y + 58}" text-anchor="middle" fill="#A5B4FC" font-size="11" font-weight="500">Logic Level: {ctrl["logic_v"]}V</text>')

    # MCU Pin Indicators
    pin_y_start = mcu_y + 85
    step_y = 26
    for i in range(10):
        py = pin_y_start + i * step_y
        lines.append(f'  <circle cx="{mcu_x + 12}" cy="{py}" r="4" fill="#6366F1"/>')
        lines.append(f'  <circle cx="{mcu_x + mcu_w - 12}" cy="{py}" r="4" fill="#6366F1"/>')

    # Draw Connection Blocks and Wires
    left_y = 120
    right_y = 120
    
    for idx, c in enumerate(connections[:14]):  # Show up to 14 main connections visually
        is_left = (idx % 2 == 0)
        c_color = c["color"]
        sig_type = c["type"]

        if is_left:
            card_x = 30
            card_y = left_y
            left_y += 58
            target_cx = mcu_x
            target_cy = card_y + 20
            wire_start_x = card_x + 230
            wire_start_y = card_y + 20
        else:
            card_x = 640
            card_y = right_y
            right_y += 58
            target_cx = mcu_x + mcu_w
            target_cy = card_y + 20
            wire_start_x = card_x
            wire_start_y = card_y + 20

        # Wire curved path
        mid_x = (wire_start_x + target_cx) // 2
        path_d = f"M {wire_start_x} {wire_start_y} C {mid_x} {wire_start_y}, {mid_x} {target_cy}, {target_cx} {target_cy}"
        lines.append(f'  <path d="{path_d}" fill="none" stroke="{c_color}" stroke-width="2.2" stroke-linecap="round" opacity="0.85"/>')

        # Connection Card Box
        lines.append(f'  <rect x="{card_x}" y="{card_y}" width="230" height="46" rx="8" fill="url(#cardGrad)" stroke="{c_color}" stroke-width="1.2" opacity="0.95"/>')
        lines.append(f'  <text x="{card_x + 12}" y="{card_y + 19}" fill="#F8FAFC" font-size="12" font-weight="600">{c["from_part"][:22]}</text>')
        lines.append(f'  <text x="{card_x + 12}" y="{card_y + 36}" fill="{c_color}" font-size="11" font-weight="700">{c["from_pin"]} ➔ {c["to_pin"]}</text>')

    lines.append('</svg>')
    return "\n".join(lines)
