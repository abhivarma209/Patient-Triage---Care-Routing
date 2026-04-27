"""
Acceptance criteria tests for the Patient Triage & Care Routing Agent.

AC-1: Valid patient JSON → POST /triage → 201 → response has patient_id,
      triage_level, and matched_department.

AC-2: Chest pain + shortness of breath → triage_level EMERGENCY,
      red_flags contains CARDIAC_EVENT_RISK.

AC-3: Matched department has 0 available_slots → capacity_flag: true,
      next_best_department is present.

AC-4: FAST stroke indicators → triage_level EMERGENCY override,
      red_flags contains STROKE_RISK (regardless of LLM output).

AC-5: Patient age 8, fever > 39°C → triage_level EMERGENCY override,
      red_flags contains PEDIATRIC_HIGH_FEVER.

AC-6: symptoms field missing or empty → 422 with structured validation error.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from main import app
from models.triage_models import (
    Department, TriageLevel, TriageResult, SafetyFlag
)

client = TestClient(app)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

VALID_PATIENT = {
    "patient_name": "Jane Doe",
    "age": 35,
    "gender": "Female",
    "symptoms": ["mild headache", "fatigue"],
    "medical_history_notes": "No significant history.",
}

MOCK_STANDARD_TRIAGE = TriageResult(
    triage_level=TriageLevel.STANDARD,
    confidence_score=0.82,
    reasoning="Symptoms are consistent with a non-urgent condition.",
    red_flags=[],
    override_applied=False,
)


# ---------------------------------------------------------------------------
# AC-1: Valid patient → 201, patient_id + triage_level + matched_department
# ---------------------------------------------------------------------------

class TestAC1ValidPatientTriage:
    def test_returns_201(self):
        with patch("routes.triage_router.assess_patient", return_value=MOCK_STANDARD_TRIAGE):
            response = client.post("/triage", json=VALID_PATIENT)
        assert response.status_code == 201

    def test_response_contains_patient_id(self):
        with patch("routes.triage_router.assess_patient", return_value=MOCK_STANDARD_TRIAGE):
            response = client.post("/triage", json=VALID_PATIENT)
        data = response.json()
        assert "patient_id" in data
        assert isinstance(data["patient_id"], str)
        assert len(data["patient_id"]) > 0

    def test_response_contains_triage_level(self):
        with patch("routes.triage_router.assess_patient", return_value=MOCK_STANDARD_TRIAGE):
            response = client.post("/triage", json=VALID_PATIENT)
        data = response.json()
        assert data["routing"]["triage"]["triage_level"] == "STANDARD"

    def test_response_contains_matched_department(self):
        with patch("routes.triage_router.assess_patient", return_value=MOCK_STANDARD_TRIAGE):
            response = client.post("/triage", json=VALID_PATIENT)
        data = response.json()
        dept = data["routing"]["matched_department"]
        assert dept is not None
        assert "department_id" in dept
        assert "name" in dept


# ---------------------------------------------------------------------------
# AC-2: Chest pain + shortness of breath → EMERGENCY + CARDIAC_EVENT_RISK
# ---------------------------------------------------------------------------

class TestAC2CardiacOverride:
    CARDIAC_PATIENT = {
        "patient_name": "Alice Monroe",
        "age": 58,
        "gender": "Female",
        "symptoms": ["chest pain", "shortness of breath", "sweating"],
    }

    def test_triage_level_is_emergency(self):
        response = client.post("/triage", json=self.CARDIAC_PATIENT)
        assert response.status_code == 201
        assert response.json()["routing"]["triage"]["triage_level"] == "EMERGENCY"

    def test_red_flags_contains_cardiac_event_risk(self):
        response = client.post("/triage", json=self.CARDIAC_PATIENT)
        red_flags = response.json()["routing"]["triage"]["red_flags"]
        assert "CARDIAC_EVENT_RISK" in red_flags

    def test_override_applied_is_true(self):
        response = client.post("/triage", json=self.CARDIAC_PATIENT)
        assert response.json()["routing"]["triage"]["override_applied"] is True

    def test_routed_to_cardiovascular_or_emergency(self):
        response = client.post("/triage", json=self.CARDIAC_PATIENT)
        specialty = response.json()["routing"]["matched_department"]["specialty"]
        assert specialty in ("Cardiovascular", "Emergency Medicine")


# ---------------------------------------------------------------------------
# AC-3: Matched department has 0 slots → capacity_flag True + next_best_department
# ---------------------------------------------------------------------------

class TestAC3CapacityFlag:
    """
    Mocks _load_departments to force a scenario where the preferred department
    (Cardiovascular, selected via CARDIAC_EVENT_RISK flag) has 0 available
    slots but a fallback department exists.
    """

    # Preferred dept via CARDIAC_EVENT_RISK flag — deliberately 0 slots
    ZERO_SLOT_DEPT = Department(
        department_id="DEPT-T01",
        name="Full Cardiology",
        specialty="Cardiovascular",
        available_slots=0,
        accepts_triage_levels=[TriageLevel.EMERGENCY, TriageLevel.URGENT],
        contact_ext="999",
        hours="24/7",
    )
    FALLBACK_DEPT = Department(
        department_id="DEPT-T02",
        name="Overflow Emergency",
        specialty="Emergency Medicine",
        available_slots=4,
        accepts_triage_levels=[TriageLevel.EMERGENCY, TriageLevel.URGENT],
        contact_ext="888",
        hours="24/7",
    )

    URGENT_PATIENT = {
        "patient_name": "Bob Smith",
        "age": 45,
        "gender": "Male",
        "symptoms": ["severe abdominal pain", "nausea"],
    }

    # Simulate LLM returning URGENT with a CARDIAC flag so routing prefers
    # Cardiovascular specialty (ZERO_SLOT_DEPT) before falling back.
    MOCK_URGENT_TRIAGE = TriageResult(
        triage_level=TriageLevel.URGENT,
        confidence_score=0.78,
        reasoning="Severe abdominal pain warrants urgent assessment.",
        red_flags=[SafetyFlag.CARDIAC_EVENT_RISK],
        override_applied=False,
    )

    def test_capacity_flag_true_when_matched_dept_has_no_slots(self):
        with (
            patch("routes.triage_router.assess_patient", return_value=self.MOCK_URGENT_TRIAGE),
            patch("dependencies.routing._load_departments", return_value=[self.ZERO_SLOT_DEPT, self.FALLBACK_DEPT]),
        ):
            response = client.post("/triage", json=self.URGENT_PATIENT)
        assert response.status_code == 201
        assert response.json()["routing"]["capacity_flag"] is True

    def test_next_best_department_present_when_capacity_flag_true(self):
        with (
            patch("routes.triage_router.assess_patient", return_value=self.MOCK_URGENT_TRIAGE),
            patch("dependencies.routing._load_departments", return_value=[self.ZERO_SLOT_DEPT, self.FALLBACK_DEPT]),
        ):
            response = client.post("/triage", json=self.URGENT_PATIENT)
        next_best = response.json()["routing"]["next_best_department"]
        assert next_best is not None
        assert next_best["available_slots"] > 0

    def test_matched_department_is_zero_slot_dept(self):
        with (
            patch("routes.triage_router.assess_patient", return_value=self.MOCK_URGENT_TRIAGE),
            patch("dependencies.routing._load_departments", return_value=[self.ZERO_SLOT_DEPT, self.FALLBACK_DEPT]),
        ):
            response = client.post("/triage", json=self.URGENT_PATIENT)
        matched = response.json()["routing"]["matched_department"]
        assert matched["available_slots"] == 0

    def test_capacity_flag_false_when_slots_available(self):
        with patch("routes.triage_router.assess_patient", return_value=self.MOCK_URGENT_TRIAGE):
            response = client.post("/triage", json=self.URGENT_PATIENT)
        assert response.json()["routing"]["capacity_flag"] is False


# ---------------------------------------------------------------------------
# AC-4: FAST stroke indicators → EMERGENCY override + STROKE_RISK
# ---------------------------------------------------------------------------

class TestAC4StrokeOverride:
    STROKE_PATIENT = {
        "patient_name": "Robert Chen",
        "age": 71,
        "gender": "Male",
        "symptoms": ["face drooping", "arm weakness", "slurred speech"],
    }

    def test_triage_level_is_emergency(self):
        response = client.post("/triage", json=self.STROKE_PATIENT)
        assert response.json()["routing"]["triage"]["triage_level"] == "EMERGENCY"

    def test_red_flags_contains_stroke_risk(self):
        response = client.post("/triage", json=self.STROKE_PATIENT)
        red_flags = response.json()["routing"]["triage"]["red_flags"]
        assert "STROKE_RISK" in red_flags

    def test_override_applied_is_true(self):
        response = client.post("/triage", json=self.STROKE_PATIENT)
        assert response.json()["routing"]["triage"]["override_applied"] is True

    def test_llm_is_not_called(self):
        """Safety override must fire before the LLM is reached."""
        with patch("dependencies.triage_engine._get_client") as mock_llm:
            client.post("/triage", json=self.STROKE_PATIENT)
        mock_llm.assert_not_called()


# ---------------------------------------------------------------------------
# AC-5: Age 8 + fever > 39°C → EMERGENCY override + PEDIATRIC_HIGH_FEVER
# ---------------------------------------------------------------------------

class TestAC5PediatricFeverOverride:
    PEDIATRIC_PATIENT = {
        "patient_name": "Sam Taylor",
        "age": 8,
        "gender": "Male",
        "symptoms": ["fever 40.2°C", "irritability", "reduced appetite"],
    }

    def test_triage_level_is_emergency(self):
        response = client.post("/triage", json=self.PEDIATRIC_PATIENT)
        assert response.json()["routing"]["triage"]["triage_level"] == "EMERGENCY"

    def test_red_flags_contains_pediatric_high_fever(self):
        response = client.post("/triage", json=self.PEDIATRIC_PATIENT)
        red_flags = response.json()["routing"]["triage"]["red_flags"]
        assert "PEDIATRIC_HIGH_FEVER" in red_flags

    def test_override_applied_is_true(self):
        response = client.post("/triage", json=self.PEDIATRIC_PATIENT)
        assert response.json()["routing"]["triage"]["override_applied"] is True

    def test_llm_is_not_called(self):
        with patch("dependencies.triage_engine._get_client") as mock_llm:
            client.post("/triage", json=self.PEDIATRIC_PATIENT)
        mock_llm.assert_not_called()

    def test_fever_below_threshold_does_not_trigger(self):
        patient = {**self.PEDIATRIC_PATIENT, "symptoms": ["fever 38.5°C", "mild cough"]}
        with patch("routes.triage_router.assess_patient", return_value=MOCK_STANDARD_TRIAGE):
            response = client.post("/triage", json=patient)
        red_flags = response.json()["routing"]["triage"]["red_flags"]
        assert "PEDIATRIC_HIGH_FEVER" not in red_flags


# ---------------------------------------------------------------------------
# AC-6: Missing or empty symptoms → 422 with structured validation error
# ---------------------------------------------------------------------------

class TestAC6ValidationErrors:
    def test_missing_symptoms_field_returns_422(self):
        payload = {
            "patient_name": "No Symptoms",
            "age": 30,
            "gender": "Female",
        }
        response = client.post("/triage", json=payload)
        assert response.status_code == 422

    def test_empty_symptoms_list_returns_422(self):
        payload = {
            "patient_name": "Empty Symptoms",
            "age": 30,
            "gender": "Female",
            "symptoms": [],
        }
        response = client.post("/triage", json=payload)
        assert response.status_code == 422

    def test_validation_error_body_lists_field(self):
        payload = {"patient_name": "Test", "age": 30, "gender": "Male"}
        response = client.post("/triage", json=payload)
        detail = response.json()["detail"]
        # FastAPI returns a list of error objects; verify symptoms is flagged
        fields = [err["loc"] for err in detail]
        assert any("symptoms" in loc for loc in fields)

    def test_missing_patient_name_returns_422(self):
        payload = {"age": 30, "gender": "Male", "symptoms": ["headache"]}
        response = client.post("/triage", json=payload)
        assert response.status_code == 422
