"""
Hard-coded safety override rules that short-circuit LLM output.

Rules (applied in priority order):
  1. Chest pain + shortness of breath  →  EMERGENCY + CARDIAC_EVENT_RISK
  2. FAST stroke indicators             →  EMERGENCY + STROKE_RISK
  3. Infant (age < 1) + fever > 39 °C  →  URGENT    + PEDIATRIC_HIGH_FEVER
"""

from __future__ import annotations

import re
from typing import Optional

from models.triage_models import PatientInput, SafetyFlag, TriageLevel, TriageResult

_CHEST_PAIN_TERMS = {
    "chest pain", "chest tightness", "chest pressure",
    "chest discomfort", "chest heaviness", "chest ache",
}

_SHORTNESS_OF_BREATH_TERMS = {
    "shortness of breath", "difficulty breathing", "breathlessness",
    "can't breathe", "cannot breathe", "trouble breathing",
    "labored breathing", "respiratory distress", "dyspnea",
}

_FACE_DROOPING_TERMS = {
    "face drooping", "facial drooping", "face droop",
    "facial droop", "uneven smile", "face numbness", "facial numbness",
}

_ARM_WEAKNESS_TERMS = {
    "arm weakness", "arm numbness", "sudden arm weakness",
    "one arm weak", "limb weakness", "limb numbness",
}

_SPEECH_DIFFICULTY_TERMS = {
    "speech difficulty", "slurred speech", "trouble speaking",
    "difficulty speaking", "unable to speak", "speech problems",
    "confused speech", "garbled speech",
}

_TEMP_RE = re.compile(
    r"(?:fever|temp(?:erature)?)\s*[:\-]?\s*(\d{2,3}(?:\.\d+)?)\s*°?\s*[cC]",
    re.IGNORECASE,
)


def _symptom_text(symptoms: list[str]) -> str:
    return " | ".join(symptoms).lower()


def _any_term_in(text: str, term_set: set[str]) -> bool:
    return any(term in text for term in term_set)


def _has_fast_indicators(text: str) -> bool:
    present = sum([
        _any_term_in(text, _FACE_DROOPING_TERMS),
        _any_term_in(text, _ARM_WEAKNESS_TERMS),
        _any_term_in(text, _SPEECH_DIFFICULTY_TERMS),
    ])
    return present >= 2


def _pediatric_fever_over_39(patient: PatientInput) -> bool:
    if patient.age >= 12:
        return False
    for symptom in patient.symptoms:
        match = _TEMP_RE.search(symptom)
        if match and float(match.group(1)) > 39.0:
            return True
    combined = _symptom_text(patient.symptoms)
    return any(kw in combined for kw in ["high fever", "very high fever", "dangerously high fever"])


def apply_safety_overrides(patient: PatientInput) -> Optional[TriageResult]:
    """
    Check hard-coded safety rules. Returns a TriageResult with
    override_applied=True if a rule fires, else None.
    """
    text = _symptom_text(patient.symptoms)

    if _any_term_in(text, _CHEST_PAIN_TERMS) and _any_term_in(text, _SHORTNESS_OF_BREATH_TERMS):
        return TriageResult(
            triage_level=TriageLevel.EMERGENCY,
            confidence_score=1.0,
            reasoning=(
                "Safety override: chest pain combined with shortness of breath "
                "is a recognised cardiac event indicator requiring immediate emergency care."
            ),
            red_flags=[SafetyFlag.CARDIAC_EVENT_RISK],
            override_applied=True,
        )

    if _has_fast_indicators(text):
        return TriageResult(
            triage_level=TriageLevel.EMERGENCY,
            confidence_score=1.0,
            reasoning=(
                "Safety override: two or more FAST stroke indicators detected "
                "(face drooping, arm weakness, speech difficulty). "
                "Immediate neurological emergency response required."
            ),
            red_flags=[SafetyFlag.STROKE_RISK],
            override_applied=True,
        )

    if _pediatric_fever_over_39(patient):
        return TriageResult(
            triage_level=TriageLevel.EMERGENCY,
            confidence_score=1.0,
            reasoning=(
                f"Safety override: pediatric patient (age {patient.age}) with fever exceeding 39 °C. "
                "Immediate emergency pediatric evaluation required."
            ),
            red_flags=[SafetyFlag.PEDIATRIC_HIGH_FEVER],
            override_applied=True,
        )

    return None
