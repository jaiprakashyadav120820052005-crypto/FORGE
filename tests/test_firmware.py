"""Tests for core.firmware: compilable Arduino C++ and MicroPython boilerplate generation with self-tests."""
import pytest
from core import firmware, wiring

def test_arduino_motor_driver_and_self_test():
    parts = {
        "controller": {"role": "controller", "name": "Arduino Nano", "id": "ctrl_nano"},
        "driver": {"role": "driver", "name": "TB6612FNG driver", "id": "drv_tb6612"},
        "motor": {"role": "motor", "name": "N20 gear motor", "id": "mot_n20"},
        "battery": {"role": "battery", "name": "2S Li-ion (7.4V)", "id": "bat_2s"},
    }
    w = wiring.generate_wiring(parts, "Line Follower")
    ctrl = w["controller"]
    fw = firmware.generate_arduino_firmware(parts, w["connections"], ctrl, "Line Follower")
    code = fw["code"]

    # Motor pin definitions & controls
    assert "#define PIN_PWMA" in code
    assert "#define PIN_PWMB" in code
    assert "void setMotors(" in code

    # Power-On Self-Test (POST)
    assert "[POST] Power-On Self-Test" in code
    assert "Testing Motors (1 sec forward @ 40%)..." in code
    assert "setMotors(100, 100);" in code
    assert "delay(1000);" in code
    assert "setMotors(0, 0);" in code
    assert "STATUS_LED" in code

def test_arduino_ultrasonic_obstacle_avoidance():
    parts = {
        "controller": {"role": "controller", "name": "ESP32 DevKit", "id": "ctrl_esp32"},
        "driver": {"role": "driver", "name": "L298N driver", "id": "drv_l298n"},
        "sensor": {"role": "sensor", "name": "HC-SR04 ultrasonic", "id": "sen_hcsr04"},
    }
    w = wiring.generate_wiring(parts, "Obstacle Avoider")
    ctrl = w["controller"]
    fw = firmware.generate_arduino_firmware(parts, w["connections"], ctrl, "Obstacle Avoider")
    code = fw["code"]

    # Ultrasonic pins & logic
    assert "#define PIN_TRIG 5" in code
    assert "#define PIN_ECHO 18" in code
    assert "float readDistanceCM()" in code
    assert "Testing Ultrasonic Sensor..." in code
    assert "distance < 20.0" in code

def test_arduino_display_and_sensor_libraries():
    parts = {
        "controller": {"role": "controller", "name": "Arduino Nano", "id": "ctrl_nano"},
        "display": {"role": "display", "name": "SSD1306 OLED", "id": "disp_oled"},
        "sensor": {"role": "sensor", "name": "DHT11 sensor", "id": "sen_dht11"},
    }
    w = wiring.generate_wiring(parts, "Weather Station")
    ctrl = w["controller"]
    fw = firmware.generate_arduino_firmware(parts, w["connections"], ctrl, "Weather Station")
    code = fw["code"]

    # Library includes
    assert "#include <Wire.h>" in code
    assert "#include <Adafruit_SSD1306.h>" in code
    assert "#include <DHT.h>" in code
    assert "Adafruit SSD1306" in fw["libraries"]
    assert "DHT sensor library" in fw["libraries"]

def test_micropython_firmware():
    parts = {
        "controller": {"role": "controller", "name": "ESP32 DevKit", "id": "ctrl_esp32"},
        "driver": {"role": "driver", "name": "TB6612FNG driver", "id": "drv_tb6612"},
        "sensor": {"role": "sensor", "name": "HC-SR04 ultrasonic", "id": "sen_hcsr04"},
    }
    w = wiring.generate_wiring(parts, "Smart Bot")
    ctrl = w["controller"]
    mpy = firmware.generate_micropython_firmware(parts, w["connections"], ctrl, "Smart Bot")
    code = mpy["code"]

    assert "import machine, time" in code
    assert "from machine import Pin, PWM" in code
    assert "def set_motors" in code
    assert "def get_distance_cm" in code
    assert "[POST] Power-On Self-Test" in code
    assert "time.sleep(1.0)" in code


def test_component_test_guides_follow_generated_pins_and_flag_esp32_echo():
    parts = {
        "controller": {"role": "controller", "name": "ESP32 DevKit", "id": "ctrl_esp32"},
        "sensor": {"role": "sensor", "name": "HC-SR04 ultrasonic", "id": "sen_hcsr04"},
        "display": {"role": "display", "name": "SSD1306 OLED", "id": "disp_oled"},
    }
    generated = wiring.generate_wiring(parts, "Distance Meter")
    guides = firmware.component_test_guides(parts, generated["connections"], generated["controller"])
    ultrasonic = next(guide for guide in guides if guide["id"] == "sen_hcsr04")
    oled = next(guide for guide in guides if guide["id"] == "disp_oled")

    assert ultrasonic["supported"]
    assert "#define TRIG_PIN 5" in ultrasonic["code"]
    assert "#define ECHO_PIN 18" in ultrasonic["code"]
    assert "5 V" in ultrasonic["safety"] and "3.3 V GPIO" in ultrasonic["safety"]
    assert "Distance:" in ultrasonic["expected"]
    assert oled["supported"]
    assert "#define SDA_PIN 21" in oled["code"]
    assert "#define SCL_PIN 22" in oled["code"]
    assert "Adafruit SSD1306" in oled["library"]
    assert "FORGE display test" in oled["expected"]


def test_component_test_guide_fails_closed_for_unsupported_parts():
    parts = {
        "controller": {"role": "controller", "name": "Arduino Nano", "id": "ctrl_nano"},
        "camera": {"role": "camera", "name": "Unknown camera module", "id": "camera_unknown"},
    }
    generated = wiring.generate_wiring(parts, "Camera Build")
    guides = firmware.component_test_guides(parts, generated["connections"], generated["controller"])
    camera = next(guide for guide in guides if guide["id"] == "camera_unknown")

    assert not camera["supported"]
    assert camera["code"] == ""
    assert "No isolated test sketch is available" in camera["expected"]


def test_line_sensor_and_motor_guides_use_bom_wiring_and_safe_test():
    parts = {
        "controller": {"role": "controller", "name": "Arduino Nano", "id": "ctrl_nano"},
        "driver": {"role": "driver", "name": "L298N driver", "id": "drv_l298n"},
        "motor": {"role": "motor", "name": "N20 6V gear motor", "id": "mot_n20"},
        "sensor": {"role": "sensor", "name": "5-channel line array", "id": "sen_line5"},
    }
    generated = wiring.generate_wiring(parts, "Line Follower")
    guides = firmware.component_test_guides(parts, generated["connections"], generated["controller"])
    motor = next(guide for guide in guides if guide["id"] == "mot_n20")
    sensor = next(guide for guide in guides if guide["id"] == "sen_line5")

    assert motor["supported"]
    assert "const int MOTOR_PWM = 5" in motor["code"]
    assert "const int MOTOR_IN_A = 7" in motor["code"]
    assert "analogWrite(MOTOR_PWM, 64)" in motor["code"]
    assert "never connect a motor directly to an MCU pin" in motor["safety"]
    assert sensor["supported"]
    assert "const int sensorPins[] = {A0, A1, A2, A3, A6}" in sensor["code"]
    assert "S1...S5" in sensor["expected"]


def test_oled_guide_uses_board_specific_i2c_init():
    for controller, expected in (("ctrl_nano", "Wire.begin();"), ("ctrl_esp32", "Wire.begin(21, 22);")):
        parts = {
            "controller": {"role": "controller", "name": "Arduino Nano" if controller == "ctrl_nano" else "ESP32 DevKit", "id": controller},
            "display": {"role": "display", "name": "SSD1306 OLED", "id": "disp_oled"},
        }
        generated = wiring.generate_wiring(parts, "Display Test")
        guide = firmware.component_test_guides(parts, generated["connections"], generated["controller"])[0]
        assert expected in guide["code"]
