"""
Department routing: matches a TriageResult to the best available department.

Selection logic:
  1. Find all departments that accept the patient's triage_level.
  2. If a safety flag is present, prefer the matching specialty.
  3. Fall back to the department with the most available_slots.
  4. If matched_department.available_slots == 0:
       - Set capacity_flag = True
       - Find next_best_department (same triage level, slots > 0, different dept)
"""

from __future__ import annotations

import json
from pathlib import Path

from models.triage_models import Department, PatientInput, RoutingResult, SafetyFlag, TriageResult

_PROVIDERS_PATH = Path(__file__).parent.parent / "providers.json"

_FLAG_SPECIALTY_PREFERENCE: dict[SafetyFlag, list[str]] = {
    SafetyFlag.CARDIAC_EVENT_RISK: ["Cardiovascular", "Emergency Medicine"],
    SafetyFlag.STROKE_RISK: ["Emergency Medicine"],
    SafetyFlag.PEDIATRIC_HIGH_FEVER: ["Child Health", "Emergency Medicine"],
}


def _load_departments() -> list[Department]:
    with _PROVIDERS_PATH.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    return [Department(**d) for d in raw["departments"]]


def route_patient(patient: PatientInput, triage: TriageResult) -> RoutingResult:
    """Match a triaged patient to the best available department."""
    departments = _load_departments()

    # All departments that accept this triage level (regardless of slots)
    accepting = [d for d in departments if triage.triage_level in d.accepts_triage_levels]

    if not accepting:
        return RoutingResult(
            patient=patient,
            triage=triage,
            matched_department=None,
            capacity_flag=False,
            next_best_department=None,
            routing_notes=(
                f"No department accepts {triage.triage_level.value} cases. "
                "Manual escalation required."
            ),
        )

    # Apply safety flag specialty preference
    primary: Department | None = None
    active_flag = triage.red_flags[0] if triage.red_flags else None

    if active_flag and active_flag in _FLAG_SPECIALTY_PREFERENCE:
        for specialty in _FLAG_SPECIALTY_PREFERENCE[active_flag]:
            match = next((d for d in accepting if d.specialty == specialty), None)
            if match:
                primary = match
                break

    if primary is None:
        primary = max(accepting, key=lambda d: d.available_slots)

    # Capacity check
    capacity_flag = primary.available_slots == 0
    next_best: Department | None = None

    if capacity_flag:
        alternatives = [
            d for d in accepting
            if d.department_id != primary.department_id and d.available_slots > 0
        ]
        next_best = max(alternatives, key=lambda d: d.available_slots) if alternatives else None

    flag_note = (
        f" Safety flag [{active_flag.value}] directed to {primary.specialty}."
        if active_flag else ""
    )
    capacity_note = (
        f" WARNING: {primary.name} has no available slots. "
        f"Next best: {next_best.name}." if capacity_flag and next_best
        else " WARNING: No alternative departments available." if capacity_flag
        else ""
    )
    override_note = (
        " LLM output was overridden by a hard-coded safety rule."
        if triage.override_applied else ""
    )

    return RoutingResult(
        patient=patient,
        triage=triage,
        matched_department=primary,
        capacity_flag=capacity_flag,
        next_best_department=next_best,
        routing_notes=(
            f"Patient routed to '{primary.name}' "
            f"({primary.specialty}, {primary.available_slots} slot(s) available)."
            f"{flag_note}{capacity_note}{override_note}"
        ),
    )
