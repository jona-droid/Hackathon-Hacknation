from __future__ import annotations

import threading
from pathlib import Path
from backend.core.config import KNOWLEDGE_FILE

_lock = threading.RLock()

DEFAULT_KNOWLEDGE = """# Power Line Drone Maintenance Knowledge Base

## 1. Flight Safety Margins & Operational Limits
- **Cable Standoff Distance**: Maintain a minimum distance of 2.5 meters when traveling along the span. During static inspection, do not close within 1.5 meters under any circumstances.
- **Maximum Approach Speed**: Cap velocity at 1.0 m/s when within 4.0 meters of high-voltage cables and 0.5 m/s within 2.0 meters.
- **Altitude Range**: Maintain clearance between 15.0m and 35.0m above ground. Do not descend below 20.0m when traversing the mid-span road crossing.
- **Wind Accommodation**: High-voltage corridors experience vortex shedding around pylons; reduce speed by 30% when crossing the windward side of tower arms.

## 2. Component Inspection Protocols
- **Insulator Strings (Pylon Top)**:
  - Approach horizontally from the lateral side at an elevation matching the insulator bracket.
  - Hover stable for at least 3 seconds at a standoff distance of 2.0m to 2.5m to capture clear high-resolution imagery.
  - Check for ceramic disk cracking, flashover burn marks, and cotter pin displacement.
- **Conductor Cables & Mid-Span Sag**:
  - The cable catenary dips by approximately 4 meters at mid-span between 60m and 120m spans.
  - Adjust flight altitude downwards progressively to follow cable sag while keeping a constant 2.5m standoff.
- **Vegetation Clearance (Tree Zones)**:
  - Maintain a 4.0m radial exclusion zone around any mature trees adjacent to the right-of-way.
  - Be alert for bird nests and branch intrusion near Span 1 (x = 30m).

## 3. Anomaly Response Procedures
- **Unplanned Drift**: If gusts exceed compensation authority, immediately gain 3.0m altitude to clear the top shield wire and hold position.
- **Sensor Glare**: If morning sun creates blinding reflections off conductors, yaw +30° to maintain oblique visual contrast against the ground.

## 4. Operator Insights Log
- *[Benchmark Flight]*: Initial baseline established for Pylons 1, 2, and 3 spans.
"""


def get_knowledge() -> str:
    with _lock:
        if not KNOWLEDGE_FILE.exists():
            KNOWLEDGE_FILE.parent.mkdir(parents=True, exist_ok=True)
            KNOWLEDGE_FILE.write_text(DEFAULT_KNOWLEDGE, encoding="utf-8")
        return KNOWLEDGE_FILE.read_text(encoding="utf-8")


def save_knowledge(content: str) -> None:
    with _lock:
        KNOWLEDGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        KNOWLEDGE_FILE.write_text(content, encoding="utf-8")


def append_insight(insight_entry: str) -> None:
    with _lock:
        current = get_knowledge()
        cleaned_entry = insight_entry.strip()
        if not cleaned_entry.startswith("-"):
            cleaned_entry = f"- {cleaned_entry}"

        if "## 4. Operator Insights Log" in current:
            parts = current.split("## 4. Operator Insights Log")
            updated = f"{parts[0]}## 4. Operator Insights Log\n{cleaned_entry}\n{parts[1].lstrip()}"
        else:
            updated = f"{current}\n\n## 4. Operator Insights Log\n{cleaned_entry}\n"

        KNOWLEDGE_FILE.write_text(updated, encoding="utf-8")
