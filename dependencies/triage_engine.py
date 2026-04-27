"""
Triage engine: runs safety overrides first, then calls OpenAI via instructor.
"""

from __future__ import annotations

import os

import instructor
from openai import OpenAI

from dependencies.safety_overrides import apply_safety_overrides
from models.triage_models import LLMTriageAssessment, PatientInput, TriageResult

_client: instructor.Instructor | None = None

_SYSTEM_PROMPT = """You are a clinical triage assistant. Assess patient-reported
symptoms and classify the urgency of care required.

Triage levels:
- EMERGENCY  : Life-threatening; immediate intervention required.
- URGENT     : Serious but stable; seen within 1-2 hours.
- STANDARD   : Non-urgent; routine appointment appropriate.
- SELF_CARE  : Mild; manageable at home with OTC treatment or rest.

Guidelines:
- Be conservative: when in doubt, escalate rather than downgrade.
- Consider patient age and medical history when available.
- Provide concise reasoning (2-4 sentences).
- Express confidence as a float between 0.0 and 1.0.
"""


def _get_client() -> instructor.Instructor:
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY is not set in the environment or .env file.")
        _client = instructor.from_openai(OpenAI(api_key=api_key))
    return _client


def _build_user_prompt(patient: PatientInput) -> str:
    symptoms_list = "\n".join(f"  - {s}" for s in patient.symptoms)
    history = patient.medical_history_notes or "None provided."
    return (
        f"Patient: {patient.patient_name}\n"
        f"Age: {patient.age}  |  Gender: {patient.gender}\n\n"
        f"Reported symptoms:\n{symptoms_list}\n\n"
        f"Medical history: {history}\n\n"
        "Classify the triage level, confidence score, and provide your reasoning."
    )


def assess_patient(patient: PatientInput) -> TriageResult:
    """
    Assess a patient. Safety overrides are checked first;
    the LLM is only called when no rule applies.
    """
    override = apply_safety_overrides(patient)
    if override is not None:
        return override

    client = _get_client()
    assessment: LLMTriageAssessment = client.chat.completions.create(
        model="gpt-4o-mini",
        response_model=LLMTriageAssessment,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(patient)},
        ],
        max_retries=2,
    )

    return TriageResult(
        triage_level=assessment.triage_level,
        confidence_score=assessment.confidence_score,
        reasoning=assessment.reasoning,
        red_flags=[],
        override_applied=False,
    )
