"""NetLab P4-16 Data Plane va In-Band Network Telemetry (INT) to'plami.

Ushbu paket quyidagi funksiyalarni taqdim etadi:
- INT telemetriya modeli: INTHeader, INTHopMetadata, INTReport
- INT kollektor va simulyatori: INTTelemetryCollector, INTSimulator, int_telemetry_collector
- P4_16 v1model kompilyatsiya va pipeline tahlili: p4_compile_standalone, p4_pipeline_from_json, parse_p4_outline
- Standart P4-16 shablonlari (l2_switch.p4, ipv4_router.p4)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from lab.telemetry.int_collector import (
    INTHeader,
    INTHopMetadata,
    INTReport,
    INTTelemetryCollector,
    INTSimulator,
    int_telemetry_collector,
    int_simulator,
)
from lab.drivers.base import (
    p4_compile_standalone,
    p4_pipeline_from_json,
)
from lab.drivers.demo_driver import parse_p4_outline

P4_TEMPLATES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "templates", "p4"))


def list_p4_templates() -> List[str]:
    """Mavjud standart P4-16 shablonlari ro'yxatini qaytaradi."""
    if not os.path.exists(P4_TEMPLATES_DIR):
        return []
    return [
        f for f in os.listdir(P4_TEMPLATES_DIR)
        if f.endswith(".p4")
    ]


def get_p4_template(name: str) -> str:
    """Nomlangan P4-16 shablon matnini o'qiydi (masalan 'l2_switch.p4' yoki 'l2_switch')."""
    clean_name = name if name.endswith(".p4") else f"{name}.p4"
    path = os.path.join(P4_TEMPLATES_DIR, clean_name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"P4 shablon topilmadi: {clean_name}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


__all__ = [
    "INTHeader",
    "INTHopMetadata",
    "INTReport",
    "INTTelemetryCollector",
    "INTSimulator",
    "int_telemetry_collector",
    "int_simulator",
    "p4_compile_standalone",
    "p4_pipeline_from_json",
    "parse_p4_outline",
    "P4_TEMPLATES_DIR",
    "list_p4_templates",
    "get_p4_template",
]
