from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TriageLevel(str, Enum):
    EMERGENCY = "EMERGENCY"
    URGENT = "URGENT"
    STANDARD = "STANDARD"
    SELF_CARE = "SELF_CARE"


class SafetyFlag(str, Enum):
    CARDIAC_EVENT_RISK = "CARDIAC_EVENT_RISK"
    STROKE_RISK = "STROKE_RISK"
    PEDIATRIC_HIGH_FEVER = "PEDIATRIC_HIGH_FEVER"


class PatientInput(BaseModel):
    patient_name: str
    age: int = Field(ge=0)
    gender: str
    symptoms: list[str] = Field(min_length=1)
    medical_history_notes: Optional[str] = None


class LLMTriageAssessment(BaseModel):
    """Structured response returned by the LLM via instructor."""

    triage_level: TriageLevel = Field(description="Clinical urgency classification.")
    confidence_score: float = Field(
        ge=0.0, le=1.0,
        description="Model confidence in the classification (0–1).",
    )
    reasoning: str = Field(
        description="Brief natural-language explanation of the classification."
    )


class TriageResult(BaseModel):
    triage_level: TriageLevel
    confidence_score: float
    reasoning: str
    red_flags: list[SafetyFlag] = []
    override_applied: bool = False


class Department(BaseModel):
    department_id: str
    name: str
    specialty: str
    available_slots: int
    accepts_triage_levels: list[TriageLevel]
    contact_ext: str
    hours: str


class RoutingResult(BaseModel):
    patient: PatientInput
    triage: TriageResult
    matched_department: Optional[Department] = None
    capacity_flag: bool = False
    next_best_department: Optional[Department] = None
    routing_notes: str


class TriageReport(BaseModel):
    patient_id: str
    created_at: str
    routing: RoutingResult
    escalated: bool = False
    escalation_notes: Optional[str] = None
