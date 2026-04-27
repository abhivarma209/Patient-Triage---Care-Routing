from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from dependencies.triage_engine import assess_patient
from dependencies.routing import route_patient
from models.triage_models import (
    Department, PatientInput, TriageLevel, TriageReport, TriageResult
)

router = APIRouter(tags=["Triage"])

# In-memory store: patient_id -> TriageReport
_reports: dict[str, TriageReport] = {}


@router.post("/triage", response_model=TriageReport, status_code=201)
def submit_triage(patient: PatientInput) -> TriageReport:
    """
    Submit patient symptoms for AI triage assessment.

    Returns a full triage report including:
    - Generated **patient_id** for future lookups
    - Triage level (EMERGENCY / URGENT / STANDARD / SELF_CARE)
    - Confidence score and reasoning
    - Safety flags if any hard-coded override was triggered
    - Assigned department from the provider network
    """
    try:
        triage_result = assess_patient(patient)
        routing_result = route_patient(patient, triage_result)
    except EnvironmentError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Triage assessment failed: {str(e)}")

    report = TriageReport(
        patient_id=str(uuid4()),
        created_at=datetime.now(timezone.utc).isoformat(),
        routing=routing_result,
    )
    _reports[report.patient_id] = report
    return report


@router.get("/report/{patient_id}", response_model=TriageReport)
def get_report(patient_id: str) -> TriageReport:
    """Retrieve the full triage report for a patient by their patient ID."""
    report = _reports.get(patient_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"No report found for patient_id '{patient_id}'")
    return report


@router.post("/escalate/{patient_id}", response_model=TriageReport)
def escalate_patient(patient_id: str) -> TriageReport:
    """
    Manually escalate a patient to EMERGENCY level.

    Overrides the existing triage level regardless of the original AI assessment.
    No-op if the patient is already classified as EMERGENCY.
    """
    report = _reports.get(patient_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"No report found for patient_id '{patient_id}'")

    if report.routing.triage.triage_level == TriageLevel.EMERGENCY:
        raise HTTPException(status_code=409, detail="Patient is already classified as EMERGENCY.")

    original_level = report.routing.triage.triage_level.value

    # Escalate triage result
    escalated_triage = TriageResult(
        triage_level=TriageLevel.EMERGENCY,
        confidence_score=1.0,
        reasoning=(
            f"Manually escalated to EMERGENCY from {original_level} by clinical staff. "
            f"Original reasoning: {report.routing.triage.reasoning}"
        ),
        red_flags=report.routing.triage.red_flags,
        override_applied=True,
    )

    # Re-route to an emergency-capable department
    escalated_routing = route_patient(report.routing.patient, escalated_triage)

    updated_report = TriageReport(
        patient_id=report.patient_id,
        created_at=report.created_at,
        routing=escalated_routing,
        escalated=True,
        escalation_notes=f"Escalated from {original_level} to EMERGENCY by clinical staff.",
    )
    _reports[patient_id] = updated_report
    return updated_report
