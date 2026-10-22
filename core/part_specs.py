"""Typed specs for the parts library (scripts/parts_library.py keys), so parts can be compared side by side and spec-checked.

HONESTY: every value here is a TYPICAL datasheet / listing value typed in by the team. It is a seed, not a verified fact:
  * for modules and breakouts it is the common hobby board (a different board of the same chip can differ),
  * ledger.py labels these rows status="seed",
  * the 'Check specs' tool compares a value with what the shop page states, quote included, and shows the difference.
Keys are stable ids from the library; names are looked up through project_boms.json ("library"). No network, no AI in this file."""
import datetime as dt
import json
import os
import re
import tempfile
import threading
from functools import lru_cache
from html import escape
from pathlib import Path

LIB_PATH = Path(__file__).with_name("project_data") / "project_boms.json"
_SPECS_LOCK = threading.Lock()


# key -> (label, unit, better). better: "high" / "low" = which way is better when comparing, None = just a property (no winner).
SPEC_DEFS = {
    # electrical
    "vin_min_v": ("Min input voltage", "V", None), "vin_max_v": ("Max input voltage", "V", "high"), "logic_v": ("Logic level", "V", None),
    "supply_min_v": ("Min supply voltage", "V", None), "supply_max_v": ("Max supply voltage", "V", "high"),
    "out_v": ("Output voltage", "V", None), "out_max_v": ("Max output voltage", "V", "high"), "out_a": ("Max output current", "A", "high"),
    "max_a": ("Max current", "A", "high"), "peak_a": ("Peak current", "A", "high"), "drop_v": ("Voltage drop", "V", "low"),
    "min_v": ("Min rated voltage", "V", None), "max_v": ("Max voltage", "V", "high"), "nominal_v": ("Nominal voltage", "V", None),
    "stall_a": ("Stall current", "A", "low"), "rds_on_mohm": ("On-resistance", "mΩ", "low"), "vf_v": ("Forward voltage", "V", "low"),
    "efficiency_pct": ("Efficiency", "%", "high"), "power_w": ("Power", "W", None), "meas_max_v": ("Max measured voltage", "V", "high"),
    "if_ma": ("Forward current", "mA", None), "current_ma": ("Current draw", "mA", "low"), "ma_per_led": ("Current per LED (white)", "mA", "low"),
    # power storage / conversion
    "capacity_mah": ("Capacity", "mAh", "high"), "c_rating": ("Discharge rating", "C", "high"), "cells_s": ("Cells in series", "S", None),
    "cells": ("Cells held", "", None), "charge_a": ("Charge current", "A", "high"), "capacitance_uf": ("Capacitance", "µF", None),
    "capacitance_pf": ("Capacitance", "pF", None), "resistance_ohm": ("Resistance", "Ω", None), "resistance_kohm": ("Resistance", "kΩ", None),
    # compute
    "clock_mhz": ("Clock", "MHz", "high"), "flash_kb": ("Flash", "KB", "high"), "ram_kb": ("RAM", "KB", "high"), "gpio": ("GPIO pins", "", "high"),
    "cpu_cores": ("CPU cores", "", "high"), "logic_cells": ("Logic cells (LUT4)", "", "high"), "wifi": ("Wi-Fi", "", None), "bluetooth": ("Bluetooth", "", None),
    # motion
    "rpm": ("No-load speed", "rpm", None), "torque_kgcm": ("Torque", "kg·cm", "high"), "kv": ("Motor constant", "KV", None),
    "steps_per_rev": ("Steps per revolution", "", "high"), "phase_a": ("Rated phase current", "A", None), "holding_torque_kgcm": ("Holding torque", "kg·cm", "high"),
    "speed_s60": ("Speed", "s/60°", "low"), "weight_g": ("Weight", "g", "low"), "encoder_ppr": ("Encoder resolution", "pulses/rev", "high"),
    "channels": ("Channels", "", "high"), "microsteps": ("Microsteps", "", "high"),
    # sensing
    "range_cm": ("Max range", "cm", "high"), "min_range_cm": ("Min range", "cm", "low"), "resolution_bits": ("Resolution", "bit", "high"),
    "temp_min_c": ("Min temperature", "°C", "low"), "temp_max_c": ("Max temperature", "°C", "high"), "temp_acc_c": ("Temperature accuracy", "±°C", "low"),
    "hum_acc_pct": ("Humidity accuracy", "±%RH", "low"), "press_acc_hpa": ("Pressure accuracy", "±hPa", "low"), "sample_hz": ("Max sample rate", "Hz", "high"),
    "gyro_dps": ("Gyro full scale", "±°/s", "high"), "accel_g": ("Accel full scale", "±g", "high"), "capacity_kg": ("Max load", "kg", "high"),
    "force_max_n": ("Max force", "N", "high"), "range_ppm": ("Measuring range", "ppm", "high"), "position_acc_m": ("Position accuracy", "m", "low"),
    "gain_db_max": ("Max gain", "dB", "high"), "drift_ppm": ("Clock drift", "±ppm", "low"), "wavelength_nm": ("Wavelength", "nm", None),
    "carrier_khz": ("Carrier", "kHz", None), "camera_mp": ("Resolution", "MP", "high"), "res_x": ("Width", "px", "high"), "res_y": ("Height", "px", "high"),
    # radio / io
    "freq_mhz": ("Frequency", "MHz", None), "range_m": ("Typical range (open air)", "m", "high"), "tx_dbm": ("TX power", "dBm", "high"),
    "data_kbps": ("Max data rate", "kbps", "high"), "interface": ("Interface", "", None), "band": ("Band", "", None),
    # displays / input
    "diag_in": ("Diagonal", "in", None), "cols": ("Columns", "", None), "rows": ("Rows", "", None), "digits": ("Digits", "", None),
    "refresh_s": ("Full refresh", "s", "low"), "keys": ("Keys", "", None), "travel_mm": ("Travel", "mm", None), "capacity_gb": ("Capacity", "GB", "high"),
    # mechanical
    "diameter_mm": ("Diameter", "mm", None), "thickness_mm": ("Thickness", "mm", None), "length_mm": ("Length", "mm", None), "width_mm": ("Width", "mm", None),
    "wheelbase_mm": ("Wheelbase", "mm", None), "prop_in": ("Prop size", "in", None), "pitch_in": ("Prop pitch", "in", None), "blades": ("Blades", "", None),
    "set_size": ("Wheels per set", "", None), "motor_mounts": ("Motor mounts", "", None), "scale": ("Scale (1:N)", "", None), "dof": ("Degrees of freedom", "", "high"),
    "legs": ("Legs", "", None), "rod_mm": ("Rod diameter", "mm", None), "belt_pitch_mm": ("Belt pitch", "mm", None), "shaft_mm": ("Shaft diameter", "mm", None),
    "thread_mm": ("Thread", "mm", None), "pitch_mm": ("Pin pitch", "mm", None), "weight_kg": ("Weight", "kg", None), "print_temp_c": ("Print temperature", "°C", None),
    "material": ("Material", "", None),
}

S = dict
SPECS = {
    # ---- controllers ----
    "nano": S(vin_min_v=7, vin_max_v=12, logic_v=5, clock_mhz=16, flash_kb=32, ram_kb=2, gpio=22, wifi=False, bluetooth=False),
    "uno": S(vin_min_v=7, vin_max_v=12, logic_v=5, clock_mhz=16, flash_kb=32, ram_kb=2, gpio=20, wifi=False, bluetooth=False),
    "esp32": S(vin_min_v=5, vin_max_v=12, logic_v=3.3, clock_mhz=240, flash_kb=4096, ram_kb=520, gpio=25, cpu_cores=2, wifi=True, bluetooth=True),
    "esp32cam": S(vin_min_v=5, vin_max_v=5, logic_v=3.3, clock_mhz=240, flash_kb=4096, ram_kb=520, camera_mp=2, wifi=True, bluetooth=True),
    "pico": S(vin_min_v=1.8, vin_max_v=5.5, logic_v=3.3, clock_mhz=133, flash_kb=2048, ram_kb=264, gpio=26, cpu_cores=2, wifi=False, bluetooth=False),
    "rpi": S(vin_min_v=5, vin_max_v=5, logic_v=3.3, clock_mhz=1500, ram_kb=4194304, gpio=28, cpu_cores=4, wifi=True, bluetooth=True),
    "jetson": S(logic_v=3.3, ram_kb=4194304, power_w=10),
    "stm32": S(logic_v=3.3, clock_mhz=72, flash_kb=128, ram_kb=20, wifi=False, bluetooth=False),
    "fpga": S(logic_v=3.3, logic_cells=8640, clock_mhz=27),
    "fc": S(vin_min_v=7, vin_max_v=26, logic_v=3.3, clock_mhz=168, flash_kb=1024, ram_kb=192),
    "nrf52": S(logic_v=3.3, clock_mhz=64, flash_kb=1024, ram_kb=256, wifi=False, bluetooth=True),
    "esp32_module": S(vin_min_v=3.0, vin_max_v=3.6, logic_v=3.3, clock_mhz=240, flash_kb=4096, ram_kb=520, cpu_cores=2, wifi=True, bluetooth=True),
    "atmega328p": S(vin_min_v=1.8, vin_max_v=5.5, logic_v=5, clock_mhz=20, flash_kb=32, ram_kb=2, gpio=23),
    # ---- batteries and power ----
    "bat1s": S(nominal_v=3.7, max_v=4.2, cells_s=1), "bat2s": S(nominal_v=7.4, max_v=8.4, cells_s=2), "bat3s": S(nominal_v=11.1, max_v=12.6, cells_s=3),
    "cell18650": S(nominal_v=3.7, max_v=4.2, cells_s=1, capacity_mah=2000), "holder18650": S(cells=1),
    "lipo_1s_micro": S(nominal_v=3.7, max_v=4.2, cells_s=1, capacity_mah=500), "lipo_1s_hd": S(nominal_v=3.7, max_v=4.2, cells_s=1, capacity_mah=450, c_rating=45),
    "lipo_hd": S(nominal_v=14.8, max_v=16.8, cells_s=4, capacity_mah=1500, c_rating=100), "lipo_3s_2200": S(nominal_v=11.1, max_v=12.6, cells_s=3, capacity_mah=2200, c_rating=30),
    "liion_longrange": S(nominal_v=14.4, max_v=16.8, cells_s=4, capacity_mah=4000), "bat_aa4": S(nominal_v=6.0, max_v=6.4, cells_s=4, capacity_mah=2500),
    "cr2032": S(nominal_v=3.0, max_v=3.3, capacity_mah=220),
    # Generic bank profiles only describe the nominal 5V default USB output; exact voltage/current limits vary by model.
    "power_bank": S(nominal_v=5.0, out_v=5.0),
    "tp4056": S(vin_min_v=4, vin_max_v=8, out_v=4.2, charge_a=1.0), "charger_2s": S(out_v=8.4, cells_s=2), "lipo_charger": S(charge_a=6),
    "bms2s": S(cells_s=2, max_v=8.4), "buck": S(vin_min_v=4, vin_max_v=35, out_a=3, efficiency_pct=80),
    "buck_5v_3a": S(out_v=5, out_a=3), "buck_5v_4a": S(out_v=5, out_a=4), "bec_high_current": S(out_v=5, out_a=10),
    "boost": S(vin_min_v=2, vin_max_v=24, out_max_v=28), "boost_hv": S(vin_min_v=5, vin_max_v=32, out_max_v=35), "boost_lowvolt": S(vin_min_v=0.9, out_v=5),
    "psu5v": S(out_v=5, out_a=2), "psu5v_5a": S(out_v=5, out_a=5), "psu12v": S(out_v=12, out_a=2), "psu_rpi": S(out_v=5, out_a=3),
    "solar_panel": S(out_v=6, power_w=2), "ldo33": S(vin_min_v=4.5, vin_max_v=15, out_v=3.3, out_a=1), "cap_bank": S(capacitance_uf=2200, max_v=35),
    "fuel_gauge": S(supply_min_v=2.5, supply_max_v=4.5, cells_s=1, interface="I2C"),
    "ina219": S(supply_min_v=3, supply_max_v=5.5, meas_max_v=26, max_a=3.2, resolution_bits=12, interface="I2C"),
    "current_hall_50a": S(supply_min_v=3, supply_max_v=5.5, max_a=50, interface="analog"), "inline_current": S(supply_min_v=2.7, supply_max_v=5.5, meas_max_v=80, interface="analog"),
    "ct_sct013": S(max_a=30, out_v=1, interface="analog"),
    # ---- drivers ----
    "l298n": S(supply_min_v=5, supply_max_v=35, drop_v=2.0, max_a=2.0, peak_a=3, channels=2),
    "tb6612": S(supply_min_v=4.5, supply_max_v=13.5, drop_v=0.5, max_a=1.2, peak_a=3.2, channels=2),
    "drv8833": S(supply_min_v=2.7, supply_max_v=10.8, max_a=1.5, peak_a=2, channels=2),
    "bts7960": S(supply_min_v=5.5, supply_max_v=27, max_a=43, channels=1, logic_v=5),
    "a4988": S(supply_min_v=8, supply_max_v=35, max_a=2, microsteps=16, logic_v=5, channels=1),
    "cnc_shield": S(supply_min_v=8, supply_max_v=35, max_a=2, microsteps=16, channels=4),
    "uln2003": S(supply_min_v=5, supply_max_v=12, max_a=0.5), "pca9685": S(supply_min_v=2.3, supply_max_v=5.5, channels=16, resolution_bits=12, interface="I2C"),
    "esc_30a": S(supply_min_v=7.4, supply_max_v=16.8, max_a=30, channels=1), "esc_4in1": S(supply_min_v=11.1, supply_max_v=25.2, max_a=30, channels=4),
    "esc_car": S(supply_min_v=7.4, supply_max_v=12.6, max_a=60, channels=1),
    "mosfet": S(supply_max_v=55, max_a=47, rds_on_mohm=22), "mosfet_smd": S(supply_max_v=30, max_a=5.7), "ir2104": S(supply_min_v=10, supply_max_v=20, logic_v=5),
    "bldc_driver_3ph": S(channels=3, interface="PWM"), "relay": S(supply_min_v=5, supply_max_v=5, max_a=10, channels=1), "relay4": S(supply_min_v=5, supply_max_v=5, max_a=10, channels=4),
    # ---- motors ----
    "n20": S(min_v=3, max_v=9, stall_a=0.8, nominal_v=6, rpm=100), "n20_enc": S(min_v=3, max_v=9, stall_a=0.8, nominal_v=6),
    "tt": S(min_v=3, max_v=6, stall_a=1.5, nominal_v=6, rpm=200), "dc_motor_enc": S(nominal_v=12), "dc_gear_12v": S(nominal_v=12, rpm=500),
    "dc_motor_12v_small": S(nominal_v=12), "coreless_716": S(nominal_v=3.7), "coreless_fast": S(nominal_v=3.7),
    "brushless_2207": S(kv=2400), "brushless_1404": S(kv=3600), "brushless_a2212": S(kv=1000, max_a=12), "brushless_car": S(kv=3900),
    "stepper_nema17": S(steps_per_rev=200, phase_a=1.7, holding_torque_kgcm=4.0), "stepper_28byj": S(nominal_v=5, steps_per_rev=2048, torque_kgcm=0.34, rpm=15),
    # ---- actuators ----
    "servo_sg90": S(supply_min_v=4.8, supply_max_v=6, torque_kgcm=1.8, speed_s60=0.1, weight_g=9),
    "servo_mg90s": S(supply_min_v=4.8, supply_max_v=6, torque_kgcm=1.8, speed_s60=0.1, weight_g=13),
    "servo_mg996": S(supply_min_v=4.8, supply_max_v=7.2, torque_kgcm=9.4, speed_s60=0.17, weight_g=55), "servo_steering": S(torque_kgcm=20),
    "solenoid_kicker": S(nominal_v=12), "solenoid_lock": S(nominal_v=12), "fan": S(nominal_v=12, diameter_mm=80), "pump_dc": S(min_v=2.5, max_v=6),
    "vibration_motor": S(nominal_v=3), "dc_gen": S(nominal_v=12), "buzzer": S(supply_min_v=3.3, supply_max_v=5), "speaker": S(supply_min_v=2.5, supply_max_v=5.5, power_w=3, interface="analog"),
    # ---- sensors ----
    "ir_array5": S(supply_min_v=3.3, supply_max_v=5, channels=5, interface="digital/analog"), "ir_array_highfreq": S(channels=16),
    "ir_edge": S(supply_min_v=3.3, supply_max_v=5, range_cm=3, interface="digital/analog"), "ir_obstacle": S(supply_min_v=3.3, supply_max_v=5, range_cm=30, min_range_cm=2, interface="digital"),
    "ir_pair": S(wavelength_nm=940), "ir_ball": S(supply_min_v=2.5, supply_max_v=5.5, carrier_khz=38),
    "hcsr04": S(supply_min_v=5, supply_max_v=5, range_cm=400, min_range_cm=2, current_ma=15, interface="digital (trig/echo)"),
    "tof": S(supply_min_v=3, supply_max_v=5, range_cm=200, min_range_cm=3, current_ma=19, interface="I2C"),
    "tf_luna": S(supply_min_v=3.7, supply_max_v=5.2, range_cm=800, min_range_cm=20, sample_hz=250, current_ma=70, interface="UART/I2C"),
    "lidar": S(supply_min_v=4.5, supply_max_v=5.5, range_cm=1200, min_range_cm=2, interface="UART"),
    "mpu6050": S(supply_min_v=3.3, supply_max_v=5, resolution_bits=16, gyro_dps=2000, accel_g=16, interface="I2C"),
    "dht22": S(supply_min_v=3.3, supply_max_v=6, temp_min_c=-40, temp_max_c=80, temp_acc_c=0.5, hum_acc_pct=2, sample_hz=0.5, interface="1-wire digital"),
    "bme280": S(supply_min_v=1.8, supply_max_v=3.6, temp_min_c=-40, temp_max_c=85, temp_acc_c=1.0, hum_acc_pct=3, press_acc_hpa=1.0, interface="I2C/SPI"),
    "bmp280": S(supply_min_v=1.8, supply_max_v=3.6, temp_min_c=-40, temp_max_c=85, temp_acc_c=1.0, press_acc_hpa=1.0, interface="I2C/SPI"),
    "ds18b20": S(supply_min_v=3, supply_max_v=5.5, temp_min_c=-55, temp_max_c=125, temp_acc_c=0.5, resolution_bits=12, interface="1-Wire"),
    "ldr": S(supply_min_v=3.3, supply_max_v=5, interface="digital/analog"), "pir": S(supply_min_v=4.5, supply_max_v=20, range_cm=700, interface="digital"),
    "rc522": S(supply_min_v=2.5, supply_max_v=3.3, logic_v=3.3, freq_mhz=13.56, range_cm=5, interface="SPI"),
    "pn532": S(supply_min_v=2.7, supply_max_v=5.5, freq_mhz=13.56, range_cm=5, interface="I2C/SPI/UART"),
    "gps": S(supply_min_v=2.7, supply_max_v=3.6, sample_hz=5, position_acc_m=2.5, interface="UART"),
    "max30102": S(supply_min_v=1.8, supply_max_v=5, resolution_bits=18, interface="I2C"),
    "mlx90614": S(supply_min_v=3, supply_max_v=5, temp_min_c=-70, temp_max_c=380, temp_acc_c=0.5, interface="I2C"),
    "flex": S(interface="analog"), "emg": S(interface="analog"),
    "load_cell": S(capacity_kg=5, resolution_bits=24, supply_min_v=2.6, supply_max_v=5.5, sample_hz=80), "fsr": S(force_max_n=10, interface="analog"),
    "soil": S(supply_min_v=3.3, supply_max_v=5.5, interface="analog"), "rain": S(supply_min_v=3.3, supply_max_v=5, interface="digital/analog"),
    "pms5003": S(supply_min_v=4.5, supply_max_v=5.5, logic_v=3.3, current_ma=100, interface="UART"), "mhz19": S(supply_min_v=4.5, supply_max_v=5.5, range_ppm=5000, interface="UART/PWM"),
    "sgp30": S(supply_min_v=3.3, supply_max_v=5, interface="I2C"), "ads1115": S(supply_min_v=2, supply_max_v=5.5, resolution_bits=16, channels=4, sample_hz=860, interface="I2C"),
    "adc_spi": S(supply_min_v=2.7, supply_max_v=5.5, resolution_bits=10, channels=8, sample_hz=200000, interface="SPI"),
    "mic_i2s": S(supply_min_v=1.8, supply_max_v=3.3, resolution_bits=24, interface="I2S"), "mic_analog": S(supply_min_v=2.7, supply_max_v=5.5, gain_db_max=60, interface="analog"),
    "tcs34725": S(supply_min_v=3.3, supply_max_v=5, resolution_bits=16, interface="I2C"), "photodiode": S(wavelength_nm=850, max_v=32),
    "hall": S(supply_min_v=4.5, supply_max_v=24, max_a=0.025, interface="digital"), "magnetic_encoder": S(supply_min_v=3.3, supply_max_v=5, resolution_bits=12, interface="I2C/analog/PWM"),
    "encoder_ab": S(encoder_ppr=600, supply_min_v=5, supply_max_v=24), "rotary_encoder": S(supply_min_v=3.3, supply_max_v=5, encoder_ppr=20, interface="digital"),
    "teg": S(max_v=15.4, max_a=6), "thermal_cam": S(supply_min_v=3.3, supply_max_v=3.3, res_x=32, res_y=24, sample_hz=64, temp_min_c=-40, temp_max_c=300, temp_acc_c=1.0, interface="I2C"),
    "rtc": S(supply_min_v=2.3, supply_max_v=5.5, drift_ppm=2, interface="I2C"), "dac_i2s": S(supply_min_v=3.3, supply_max_v=3.3, resolution_bits=32, sample_hz=384000, interface="I2S"),
    "opamp": S(supply_min_v=3, supply_max_v=32, channels=2),
    # ---- cameras ----
    "picam": S(camera_mp=12, interface="CSI"), "webcam_usb": S(res_x=1280, res_y=720, interface="USB"), "ov7670": S(res_x=640, res_y=480, camera_mp=0.3, interface="parallel+SCCB"),
    # ---- comms ----
    "hc05": S(supply_min_v=3.6, supply_max_v=6, freq_mhz=2400, range_m=10, tx_dbm=4, interface="UART", bluetooth=True),
    "elrs_rx": S(supply_min_v=4.5, supply_max_v=5.5, freq_mhz=2400), "radio_tx": S(freq_mhz=2400, channels=16), "rc_tx_rx": S(freq_mhz=2400, channels=6, range_m=500),
    "vtx": S(freq_mhz=5800), "lora": S(supply_min_v=1.8, supply_max_v=3.7, freq_mhz=865, tx_dbm=20, range_m=5000, interface="SPI"),
    "nrf24": S(supply_min_v=1.9, supply_max_v=3.6, freq_mhz=2400, tx_dbm=20, range_m=1000, interface="SPI"),
    "rf433": S(supply_min_v=3, supply_max_v=12, freq_mhz=433, range_m=100), "gsm": S(supply_min_v=3.4, supply_max_v=4.4, peak_a=2, logic_v=2.8, interface="UART"),
    "telemetry_radio": S(freq_mhz=433, tx_dbm=20, range_m=1000), "can_transceiver": S(supply_min_v=4.75, supply_max_v=5.25, data_kbps=1000, interface="CAN"),
    "fm_module": S(supply_min_v=2.5, supply_max_v=5, band="76-108 MHz", interface="I2C"), "ir_tx_rx": S(supply_min_v=2.7, supply_max_v=5.5, carrier_khz=38),
    # ---- displays, input, storage ----
    "oled": S(diag_in=0.96, res_x=128, res_y=64, supply_min_v=3.3, supply_max_v=5, interface="I2C"), "lcd16": S(cols=16, rows=2, supply_min_v=5, supply_max_v=5, interface="I2C"),
    "tft": S(diag_in=2.8, res_x=240, res_y=320, supply_min_v=3.3, supply_max_v=5, interface="SPI"), "eink": S(diag_in=2.9, res_x=296, res_y=128, supply_min_v=3.3, supply_max_v=3.3, refresh_s=2, interface="SPI"),
    "matrix": S(res_x=32, res_y=8, supply_min_v=5, supply_max_v=5, interface="SPI"), "ws2812": S(supply_min_v=4, supply_max_v=7, ma_per_led=60, interface="1-wire data"),
    "seg": S(digits=4, supply_min_v=3.3, supply_max_v=5, interface="2-wire"), "leds": S(if_ma=20), "buttons": S(max_a=0.05, max_v=12),
    "keypad": S(keys=16, interface="matrix"), "joystick": S(supply_min_v=3.3, supply_max_v=5, channels=2, interface="analog"), "pots": S(resistance_kohm=10),
    "keyswitch": S(travel_mm=4), "sd_module": S(supply_min_v=3.3, supply_max_v=5, interface="SPI"), "sd_card": S(capacity_gb=32, interface="SD"),
    "vga_pmod": S(interface="analog RGB"),
    # ---- programmers and cables ----
    "st_link": S(logic_v=3.3, interface="SWD/JTAG"), "usb_uart": S(interface="USB-UART", data_kbps=1000),
    "logic_analyzer": S(channels=8, sample_hz=24000000),
    # ---- passives and wiring ----
    "diodes": S(max_v=40, max_a=1, vf_v=0.6), "transistors": S(max_v=40, max_a=0.8), "header_pins": S(pitch_mm=2.54), "crystal": S(freq_mhz=16, capacitance_pf=22),
    "load_resistor": S(resistance_ohm=10, power_w=10), "switch": S(max_a=0.5, max_v=50), "perfboard": S(length_mm=90, width_mm=70),
    # ---- chassis, wheels, mechanical ----
    "chassis2wd": S(motor_mounts=2, diameter_mm=65), "chassis4wd": S(motor_mounts=4, diameter_mm=65), "rc_chassis": S(scale=10),
    "wheel_n20": S(diameter_mm=43), "wheel_65": S(diameter_mm=65), "wheel_traction": S(diameter_mm=80),
    "mecanum_set": S(diameter_mm=60, set_size=4), "omni_set": S(diameter_mm=48, set_size=3),
    "pan_tilt": S(dof=2), "hexapod_frame": S(legs=6, dof=18), "linear_kit": S(rod_mm=8, belt_pitch_mm=2), "turntable_bearing": S(diameter_mm=152),
    "pendulum_mech": S(shaft_mm=6), "beam_ball": S(diameter_mm=20), "frame_custom": S(thickness_mm=3, material="acrylic"),
    "filament_pla": S(diameter_mm=1.75, weight_kg=1, print_temp_c=210), "printed_parts": S(diameter_mm=1.75, weight_kg=0.5), "screws_kit": S(thread_mm=3),
    "frame_5in": S(prop_in=5, material="carbon fibre"), "frame_7in": S(prop_in=7, material="carbon fibre"), "frame_3in": S(prop_in=3, material="carbon fibre"),
    "frame_micro": S(wheelbase_mm=65, prop_in=1.2), "props_5in": S(prop_in=5, pitch_in=4.3, blades=3), "props_7in": S(prop_in=7, blades=3),
    "props_3in": S(prop_in=3, blades=3), "props_big": S(prop_in=10, pitch_in=4.5, blades=2), "ball_roller": S(diameter_mm=25), "sheet_blade": S(thickness_mm=1, material="aluminium"),
}

# Library parts with nothing meaningful to compare: the reason is shown instead of an empty table.
NO_SPECS = {
    "reed": "simple switch; rating depends on the exact part", "tilt": "simple switch; rating depends on the exact part", "electrodes": "consumable",
    "float_switch": "simple switch; rating depends on the exact part", "usb_cable": "cable; length and gauge vary", "usb_hub": "ports and power vary by model",
    "resistor_kit": "assortment", "cap_kit": "assortment", "inductor_kit": "assortment", "smd_kit": "assortment", "fuse": "rating chosen per project",
    "usbc": "connector; variants differ", "wires": "kit contents vary", "enclosure": "size chosen per project", "foam_plane": "wingspan and weight vary by kit",
    "foam_board": "thickness varies", "heatsink": "size and thermal resistance vary", "limit_switch": "ratings vary by model", "glove": "clothing",
    "arm_frame": "degrees of freedom and servo size vary by kit", "gripper": "opening and servo size vary by kit", "caster": "size varies",
    "bldc_motor": "KV and size vary widely by listing",
}


ROLE_PLURAL = dict(controller="microcontrollers", driver="motor drivers", motor="motors", battery="batteries", sensor="sensors", power="power modules",
                   comms="communication modules", display="displays", actuator="actuators", input="input parts", camera="cameras", wheels="wheels",
                   chassis="chassis kits", mechanical="mechanical parts", wiring="wiring parts", programmer="programmers", storage="storage parts")

def plural(role):
    """'driver' -> 'motor drivers', for the 'Compare with other ...' switch."""
    return ROLE_PLURAL.get(role, f"{role} parts")


def meta(key):
    """(label, unit, better) for a spec key; unknown keys fall back to the key itself."""
    return SPEC_DEFS.get(key, (key.replace("_", " "), "", None))


def unit(key):
    return meta(key)[1]


def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)  # True == 1 in Python: keep booleans out of numeric checks


def numeric_specs(specs):
    return {k: v for k, v in (specs or {}).items() if is_number(v) and k in SPEC_DEFS}


def fmt(key, v):
    """Human text for one value: 'Yes'/'No', '1,200 cm', '3.3 V'."""
    if v is None: return "—"
    if isinstance(v, bool): return "Yes" if v else "No"
    if is_number(v):
        s = f"{v:,.0f}" if float(v).is_integer() else f"{v:,.3g}"
        u = unit(key)
        return f"{s} {u}".strip()
    return str(v)


@lru_cache(maxsize=1)
def _lib():
    """library key -> {role, name}; read from the same JSON the BOMs use, so names always match."""
    try:
        return json.loads(LIB_PATH.read_text(encoding="utf-8")).get("library", {})
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=1)
def _by_name():
    return {v["name"].casefold(): k for k, v in _lib().items()}


def learned_specs_path():
    return Path(os.getenv("FORGE_LEARNED_SPECS_PATH") or LIB_PATH.parent / "learned_specs.json")


def normalize_part_name(name):
    """Normalize part name for consistent caching/lookup across variations."""
    s = str(name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def clean_specs_dict(raw):
    """Clean and validate raw spec dictionary."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        k = str(k).strip().lower()
        if not k or len(k) > 40:
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = float(v) if not float(v).is_integer() else int(v)
        elif isinstance(v, bool):
            out[k] = v
        elif isinstance(v, str):
            s = v.strip()
            cleaned_num = re.sub(r"[^\d.-]", "", s)
            if cleaned_num and cleaned_num.replace(".", "", 1).replace("-", "", 1).isdigit():
                try:
                    num = float(cleaned_num)
                    out[k] = int(num) if num.is_integer() else num
                    continue
                except ValueError:
                    pass
            if len(s) <= 50:
                out[k] = s
    return out


def _read_learned_specs():
    try:
        p = learned_specs_path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("specs"), dict):
                return data
    except (OSError, ValueError):
        pass
    return {"specs": {}}


def find_learned_spec(name):
    """Find specs in the dynamically learned specs dataset."""
    key = normalize_part_name(name)
    if not key:
        return None
    data = _read_learned_specs()
    entry = data["specs"].get(key)
    if entry and isinstance(entry.get("specs"), dict) and entry["specs"]:
        return dict(entry["specs"])
    return None


def save_learned_spec(name, specs, source="gemini"):
    """Store specs in the dynamic specs dataset using memoization / persistent cache."""
    key = normalize_part_name(name)
    clean = clean_specs_dict(specs)
    if not key or not clean:
        return False
    with _SPECS_LOCK:
        data = _read_learned_specs()
        data["specs"][key] = {
            "name": str(name).strip(),
            "specs": clean,
            "source": source,
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        }
        path = learned_specs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    return True


def save_learned_specs_batch(items, source="gemini"):
    """Save multiple part specs in a single atomic write."""
    valid_items = {}
    for name, sp in (items or {}).items():
        k = normalize_part_name(name)
        c = clean_specs_dict(sp)
        if k and c:
            valid_items[k] = {
                "name": str(name).strip(),
                "specs": c,
                "source": source,
                "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            }
    if not valid_items:
        return False
    with _SPECS_LOCK:
        data = _read_learned_specs()
        data["specs"].update(valid_items)
        path = learned_specs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    return True


def learned_specs_count():
    return len(_read_learned_specs().get("specs", {}))


def match_library_key(name):
    """Smart lookup for library parts when exact casefold doesn't match."""
    if not name:
        return None
    raw = str(name).strip().casefold()
    k = _by_name().get(raw)
    if k:
        return k
    norm = normalize_part_name(name)
    tokens = set(norm.split())

    ALIASES = {
        "uno": {"arduino", "uno"},
        "nano": {"arduino", "nano"},
        "esp32cam": {"esp32", "cam"},
        "esp32": {"esp32"},
        "pico": {"raspberry", "pi", "pico"} if {"raspberry", "pi", "pico"} <= tokens else {"pico"},
        "rpi": {"raspberry", "pi"},
        "l298n": {"l298n"},
        "tb6612": {"tb6612"},
        "drv8833": {"drv8833"},
        "bts7960": {"bts7960"},
        "a4988": {"a4988"},
        "hcsr04": {"hc", "sr04"} if {"hc", "sr04"} <= tokens else {"hcsr04"},
        "mpu6050": {"mpu6050"} if "mpu6050" in tokens else {"mpu", "6050"},
        "dht22": {"dht22"} if "dht22" in tokens else {"dht", "22"},
        "bme280": {"bme280"} if "bme280" in tokens else {"bme", "280"},
        "bmp280": {"bmp280"} if "bmp280" in tokens else {"bmp", "280"},
        "ds18b20": {"ds18b20"},
        "servo_sg90": {"sg90"},
        "servo_mg90s": {"mg90s"},
        "servo_mg996": {"mg996"} if "mg996" in tokens else {"mg996r"},
        "tp4056": {"tp4056"},
        "ina219": {"ina219"},
        "pca9685": {"pca9685"},
        "soil": {"soil", "moisture"},
        "relay": {"1", "channel", "relay"} if {"1", "channel", "relay"} <= tokens else {"relay"},
        "cell18650": {"18650", "battery"} if {"18650", "battery"} <= tokens else {"18650"},
    }
    for lk, req in ALIASES.items():
        if req <= tokens and lk in SPECS:
            return lk
    return None


def find_specs_in_dataset(name):
    """Check whether a part's specs are already present in our dataset (static library or dynamic learned dataset).
    Returns specs dict if found, or None if not present in the dataset."""
    if not name:
        return None
    # 1. Exact library name
    k = key_for_name(name)
    if k and k in SPECS and SPECS[k]:
        return specs_for(k)
    # 2. Smart alias match to library
    lk = match_library_key(name)
    if lk and lk in SPECS and SPECS[lk]:
        return specs_for(lk)
    # 3. Dynamic learned specs dataset
    learned = find_learned_spec(name)
    if learned:
        return learned
    return None


def has_specs(name):
    """Check if the part has specs present in the dataset."""
    return find_specs_in_dataset(name) is not None


def resolve_specs(name, fallback=None):
    """Resolve specs for a part name.
    1. Checks dataset (library + dynamic learned store).
    2. If found, returns specs.
    3. If not found and fallback specs are given, saves fallback specs to dynamic store and returns them.
    4. Otherwise returns {}."""
    specs = find_specs_in_dataset(name)
    if specs:
        return specs
    clean_fb = clean_specs_dict(fallback)
    if clean_fb:
        save_learned_spec(name, clean_fb, source="fallback")
        return clean_fb
    return {}


def specs_for(key):
    """Typed specs for a library key or learned spec key (a fresh copy; {} if none)."""
    if key in SPECS:
        return dict(SPECS[key])
    learned = find_learned_spec(key)
    if learned:
        return learned
    return {}


def key_for_name(name):
    return _by_name().get(str(name or "").strip().casefold())


def specs_for_name(name):
    """Specs by exact library part name (case-insensitive). Used for learned or Gemini-named parts. {} when the name is not in the library."""
    k = key_for_name(name)
    return specs_for(k) if k else {}


def why_none(key):
    return NO_SPECS.get(key)


def alternatives(role, specs, exclude_name=None, limit=4, min_shared=1):
    """Library parts of the same role that share spec keys with `specs`, best overlap first: [(key, name, shared_keys)].
    Roles in the library are coarse ('sensor' covers 50+ parts), so overlap on numeric spec keys is what makes two parts comparable."""
    mine = set(numeric_specs(specs))
    out = []
    for k, v in _lib().items():
        if v["role"] != role or (exclude_name and v["name"].casefold() == str(exclude_name).casefold()):
            continue
        shared = mine & set(numeric_specs(SPECS.get(k, {})))
        if len(shared) >= min_shared:
            out.append((k, v["name"], sorted(shared)))
    learned = _read_learned_specs().get("specs", {})
    for lk, lentry in learned.items():
        lname = lentry.get("name", lk)
        if exclude_name and lname.casefold() == str(exclude_name).casefold():
            continue
        lspecs = lentry.get("specs", {})
        shared = mine & set(numeric_specs(lspecs))
        if len(shared) >= min_shared:
            if not any(item[1].casefold() == lname.casefold() for item in out):
                out.append((lk, lname, sorted(shared)))
    return sorted(out, key=lambda r: (-len(r[2]), r[1].casefold()))[:limit]



def compare(items):
    """items: [(name, specs_dict)]. Returns {columns:[name], rows:[{key,label,unit,values,best:[column index]}]}.
    Rows with no value anywhere are dropped. 'best' is only set when the spec has a direction, at least two numbers differ, and the numbers are comparable."""
    cols = [n for n, _ in items]
    rows = []
    for key in sorted({k for _, s in items for k in (s or {})}, key=lambda k: (meta(k)[2] is None, k)):
        vals = [(s or {}).get(key) for _, s in items]
        if all(v is None for v in vals): continue
        label, u, better = meta(key)
        nums = [(i, v) for i, v in enumerate(vals) if is_number(v)]
        best = []
        if better and len(nums) >= 2 and len({v for _, v in nums}) > 1:
            target = (max if better == "high" else min)(v for _, v in nums)
            best = [i for i, v in nums if v == target]
        rows.append(dict(key=key, label=label, unit=u, values=vals, best=best))
    return dict(columns=cols, rows=rows)


CSS = ("<style>.cmp{width:100%;border-collapse:collapse;font-size:14px}.cmp th{font:600 11px ui-monospace,monospace;letter-spacing:.1em;text-transform:uppercase;color:#93A1BA;"
       "text-align:left;padding:8px;border-bottom:1px solid #27385A}.cmp td{padding:8px;border-bottom:1px solid #1D2C48}.cmp td.l{color:#93A1BA}"
       ".cmp td.best{color:#19E3B1;font-weight:700}.cmp td.na{color:#5B6B87}</style>")


def compare_html(table):
    """Side-by-side table for the app. Every string is escaped; the winner of each row (where one exists) is green."""
    head = "".join(f"<th>{escape(str(c))}</th>" for c in table["columns"])
    body = ""
    for r in table["rows"]:
        cells = "".join(f'<td class="{"best" if i in r["best"] else "na" if v is None else ""}">{escape(fmt(r["key"], v))}</td>' for i, v in enumerate(r["values"]))
        body += f'<tr><td class="l">{escape(r["label"])}</td>{cells}</tr>'
    return f'{CSS}<table class="cmp"><thead><tr><th>Spec</th>{head}</tr></thead><tbody>{body}</tbody></table>'
