import io
import json
import zipfile
from typing import Optional

from fastapi import APIRouter, Cookie, HTTPException
from fastapi.responses import Response

from core import firmware, wiring
from server.state import store

router = APIRouter(prefix="/api/wiring", tags=["wiring"])


@router.get("")
def get_wiring_data(forge_session: Optional[str] = Cookie(None)):
    session = store.get_or_create(forge_session)
    parts = session.get_active_parts()
    if not parts:
        raise HTTPException(status_code=400, detail="No active BOM build for wiring")

    label = session.get_build_label()
    parts_dict = parts if isinstance(parts, dict) else {p.get("role", f"part_{i}"): p for i, p in enumerate(parts)}

    data = wiring.generate_wiring(parts_dict, project_label=label)
    ctrl = data["controller"]
    conns = data["connections"]

    ard_res = firmware.generate_arduino_firmware(parts_dict, conns, ctrl, label)
    mpy_res = firmware.generate_micropython_firmware(parts_dict, conns, ctrl, label)
    component_tests = firmware.component_test_guides(parts_dict, conns, ctrl)

    arduino_code = ard_res.get("code", "") if isinstance(ard_res, dict) else str(ard_res)
    micropython_code = mpy_res.get("code", "") if isinstance(mpy_res, dict) else str(mpy_res)

    return {
        "controller": ctrl,
        "connections": conns,
        "wokwi_diagram": data.get("wokwi_diagram", {}),
        "wokwi_json_str": data.get("wokwi_json_str", "{}"),
        "mermaid_code": data.get("mermaid_code", ""),
        "svg_canvas": data.get("svg_canvas", ""),
        "arduino_code": arduino_code,
        "micropython_code": micropython_code,
        "component_tests": component_tests,
        "label": label,
    }


@router.get("/wokwi-project.zip")
def download_wokwi_project(forge_session: Optional[str] = Cookie(None)):
    session = store.get_or_create(forge_session)
    parts = session.get_active_parts()
    if not parts:
        raise HTTPException(status_code=400, detail="No active BOM build for Wokwi export")

    label = session.get_build_label()
    parts_dict = parts if isinstance(parts, dict) else {
        part.get("role", f"part_{index}"): part
        for index, part in enumerate(parts)
    }
    generated = wiring.generate_wiring(parts_dict, project_label=label)
    firmware_result = firmware.generate_arduino_firmware(
        parts_dict, generated["connections"], generated["controller"], label
    )
    arduino_code = (
        firmware_result.get("code", "")
        if isinstance(firmware_result, dict)
        else str(firmware_result)
    )

    readme = (
        f"FORGE Wokwi starter files for: {label}\n\n"
        "This bundle contains the generated diagram and starter Arduino firmware.\n"
        "To use it, create an Arduino project in Wokwi, replace its diagram.json and\n"
        "sketch.ino with the files in this bundle, then press Start Simulation.\n\n"
        "FORGE cannot import files into a Wokwi account or start a remote simulation\n"
        "from this browser. Verify every pin, voltage, library, and unsupported part\n"
        "in the Wokwi editor before treating the simulation as a validated build.\n"
    )
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("diagram.json", json.dumps(generated["wokwi_diagram"], indent=2))
        archive.writestr("sketch.ino", arduino_code)
        archive.writestr("wiring.mmd", generated["mermaid_code"])
        archive.writestr("README.txt", readme)

    return Response(
        content=bundle.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="forge-wokwi-project.zip"'},
    )
