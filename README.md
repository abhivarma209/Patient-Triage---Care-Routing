# AI-Powered Patient Triage & Care Routing Agent

A FastAPI service that assesses patient-reported symptoms, classifies clinical urgency, detects life-threatening patterns via hard-coded safety rules, and routes patients to the most appropriate available department.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        FastAPI Application                       │
│                                                                  │
│  POST /triage          GET /report/{id}    POST /escalate/{id}  │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌────────────────────────┐
│     Triage Engine       │  dependencies/triage_engine.py
│                         │
│  1. Safety Overrides    │──── fires? ──► TriageResult (override=True)
│     (deterministic)     │                confidence_score = 1.0
│                         │
│  2. LLM Assessment      │──── no override ──► OpenAI gpt-4o-mini
│     (instructor)        │                     structured JSON response
└────────────┬────────────┘
             │  TriageResult
             ▼
┌────────────────────────┐
│    Routing Engine       │  dependencies/routing.py
│                         │
│  - Match triage level   │
│  - Apply flag preference│──► matched_department
│  - Check capacity       │──► capacity_flag + next_best_department
└────────────┬────────────┘
             │  RoutingResult
             ▼
┌────────────────────────┐
│    In-Memory Store      │  routes/triage_router.py
│    patient_id → Report  │
└─────────────────────────┘
```

---

## Project Structure

```
C:\Wip\
├── main.py                       FastAPI app entry point
├── providers.json                Mock department dataset
├── requirements.txt
│
├── models/
│   └── triage_models.py          All Pydantic schemas
│
├── dependencies/
│   ├── safety_overrides.py       Hard-coded clinical safety rules
│   ├── triage_engine.py          Orchestrates overrides + LLM call
│   └── routing.py                Department matching & capacity logic
│
├── routes/
│   ├── triage_router.py          API endpoints + in-memory store
│   └── test_router.py            Existing health-check route
│
└── tests/
    └── test_triage.py            Acceptance criteria test suite (25 tests)
```

---

## Request & Response Flow

### POST /triage

```
Client sends patient JSON
        │
        ▼
[1] Safety Override Check  ──────────────────────────────────────┐
    Evaluate symptoms against hard-coded rules.                   │
    Rules are evaluated in strict priority order.                 │
    If a rule fires, the LLM is never called.                    │
        │ no rule fired                                           │
        ▼                                                         │
[2] LLM Assessment                                               │
    Patient data is sent to gpt-4o-mini via instructor.          │
    The model returns a validated, typed TriageResult.            │
        │                                                         │
        └──────────────────── TriageResult ◄──────────────────────┘
                                   │
                                   ▼
[3] Department Routing
    Find all departments that accept the triage level.
    If a safety flag is present, prefer the matching specialty.
    Otherwise, pick the department with the most available slots.
    If matched department has 0 slots → capacity_flag = true,
    find next_best_department as alternative.
                                   │
                                   ▼
[4] Store & Return
    Generate patient_id (UUID), store report in memory.
    Return full TriageReport to client.
```

### GET /report/{patient_id}

Retrieves a previously stored `TriageReport` by its generated `patient_id`. Returns `404` if not found.

### POST /escalate/{patient_id}

Looks up an existing report and overrides the triage level to `EMERGENCY`. Re-runs the department routing step with the new level. Returns `409` if the patient is already at `EMERGENCY`.

---

## Safety Override Rules

Safety rules are evaluated **before** any LLM call and cannot be overridden by the model. They fire deterministically with `confidence_score = 1.0`.

| Trigger | Result | Flag |
|---|---|---|
| `chest pain` + `shortness of breath` in symptoms | `EMERGENCY` | `CARDIAC_EVENT_RISK` |
| ≥ 2 of: face drooping / arm weakness / slurred speech (FAST) | `EMERGENCY` | `STROKE_RISK` |
| Age < 12 + fever > 39 °C detected in symptoms | `EMERGENCY` | `PEDIATRIC_HIGH_FEVER` |

When a rule fires, the `TriageResult` carries `override_applied: true` and the matched flag appears in `red_flags[]`.

---

## Department Routing Logic

Routing uses the mock provider dataset (`providers.json`) and follows this priority:

1. **Filter by triage level** — only departments whose `accepts_triage_levels` includes the patient's level are considered.
2. **Safety flag preference** — if a `red_flags` entry is present, the routing engine looks for a department whose specialty matches a preferred list for that flag (e.g. `CARDIAC_EVENT_RISK` → Cardiovascular first, then Emergency Medicine).
3. **Slot maximisation** — if no flag preference applies, the department with the most `available_slots` is selected.
4. **Capacity check** — if the selected department has `available_slots = 0`, the response includes `capacity_flag: true` and a `next_best_department` suggestion (next highest-slot department for the same triage level).

---

## Data Models

```
PatientInput
  patient_name, age, gender, symptoms[], medical_history_notes?

TriageResult
  triage_level      EMERGENCY | URGENT | STANDARD | SELF_CARE
  confidence_score  0.0 – 1.0
  reasoning         Natural language explanation
  red_flags[]       CARDIAC_EVENT_RISK | STROKE_RISK | PEDIATRIC_HIGH_FEVER
  override_applied  true if a safety rule fired

RoutingResult
  patient             PatientInput
  triage              TriageResult
  matched_department  Department (may have 0 slots)
  capacity_flag       true when matched_department.available_slots == 0
  next_best_department  Alternative department, or null
  routing_notes       Human-readable routing summary

TriageReport  (stored + returned by the API)
  patient_id          UUID generated at triage time
  created_at          ISO 8601 timestamp
  routing             RoutingResult
  escalated           true if manually escalated
  escalation_notes    Populated on escalation
```

---

## LLM Integration

The service uses **OpenAI `gpt-4o-mini`** via the **instructor** library, which enforces structured JSON output validated against the `LLMTriageAssessment` Pydantic schema. This guarantees the model always returns a valid `triage_level` enum, a numeric `confidence_score`, and a `reasoning` string — no post-processing or regex parsing required.

The LLM is only invoked when no safety override rule applies, keeping deterministic safety logic fully independent of the model.

### LLM Prompts

**System Prompt**
```
You are a clinical triage assistant. Your role is to assess patient-reported symptoms
and classify the urgency of care required. You must be conservative — when in doubt,
assign a higher urgency level. Never downgrade urgency based on patient reassurances.

Respond strictly in the required JSON schema. Do not add commentary outside the schema fields.
```

**User Prompt** (populated at runtime)
```
Assess the following patient and return a structured triage classification.

Patient: {patient_name}, Age: {age}, Gender: {gender}
Symptoms: {symptoms_joined_by_comma}
Medical History: {medical_history_notes or "None provided"}

Classify the triage level as one of: EMERGENCY, URGENT, STANDARD, SELF_CARE.
Provide a confidence score between 0.0 and 1.0.
List any red flags observed (CARDIAC_EVENT_RISK, STROKE_RISK, PEDIATRIC_HIGH_FEVER).
Explain your reasoning in one or two sentences.
```

`instructor` wraps the OpenAI call and retries automatically if the model response fails Pydantic validation, ensuring the caller always receives a fully typed `LLMTriageAssessment` object.

---

## Semantic Safety Override Detection

Safety overrides use **vector embeddings + semantic search** so that paraphrased or colloquial symptom descriptions (e.g. *"my face feels numb and droopy"*) still match the correct clinical rule — not just exact keyword matches.

### How it works

**At startup — build the override index**

Each safety rule is represented by a set of descriptive trigger strings. The service calls the OpenAI Embeddings API (`text-embedding-3-small`) once for every trigger string and stores the resulting vectors in an in-memory list alongside the rule metadata.

```
Override trigger strings (examples)
────────────────────────────────────────────────────────────────
CARDIAC_EVENT_RISK  → "chest pain and difficulty breathing"
                      "tightness in the chest with shortness of breath"
                      "chest pressure and inability to breathe properly"

STROKE_RISK         → "face drooping on one side"
                      "sudden arm weakness or numbness"
                      "slurred or garbled speech"
                      "facial asymmetry with limb weakness"

PEDIATRIC_HIGH_FEVER→ "high fever in a young child"
                      "child under 12 with temperature above 39 degrees"
                      "infant with severe fever"
────────────────────────────────────────────────────────────────
All vectors are computed once and kept in memory as a list of
{vector, rule_name, confidence_score=1.0} dicts.
```

**At request time — search for a matching override**

When a patient's symptoms arrive the service:

1. Joins the symptoms list into a single string.
2. Calls the Embeddings API to produce a query vector for that string.
3. Computes **cosine similarity** between the query vector and every stored override vector.
4. If the highest similarity score exceeds a configured threshold (default `0.82`), the corresponding safety rule fires and the LLM call is skipped.
5. If no vector crosses the threshold, the request proceeds to the LLM assessment step.

```
Incoming symptoms  ──► embed ──► query_vector
                                      │
                          ┌───────────▼────────────┐
                          │  cosine_similarity with │
                          │  each override vector   │
                          └───────────┬────────────┘
                                      │
                          max_score ≥ 0.82?
                            │              │
                           YES             NO
                            │              │
                      Override fires   LLM Assessment
                      (rule_name,      (gpt-4o-mini
                       score=1.0)       + instructor)
```

### In-memory store layout

```python
override_index: list[dict] = [
    {
        "trigger_text": "chest pain and difficulty breathing",
        "rule":         "CARDIAC_EVENT_RISK",
        "triage_level": "EMERGENCY",
        "vector":       [0.021, -0.143, ...]  # 1536-dim float list
    },
    ...
]
```

The index is populated once at application startup (`@app.on_event("startup")`) and shared across all requests via FastAPI dependency injection. No external vector database is required.

---

## Running the Service

**Prerequisites:** Add `OPENAI_API_KEY` to `.env` in the project root.

```bash
# Activate virtual environment
& c:\Wip\venv\Scripts\Activate.ps1

# Start server
python main.py
# → http://127.0.0.1:8000
# → Swagger docs: http://127.0.0.1:8000/docs

# Run tests (no API key required)
pytest tests/test_triage.py -v
```

---

## Test Coverage

The test suite covers all six acceptance criteria with 25 tests. The LLM path (AC-1) is covered using a mock. The capacity scenario (AC-3) uses a patched department loader to inject a controlled zero-slot fixture.

| AC | Scenario | Tests |
|---|---|---|
| AC-1 | Valid patient → 201, patient_id, triage_level, matched_department | 4 |
| AC-2 | Chest pain + SOB → EMERGENCY + CARDIAC_EVENT_RISK | 4 |
| AC-3 | 0-slot department → capacity_flag + next_best_department | 4 |
| AC-4 | FAST stroke indicators → EMERGENCY + STROKE_RISK | 4 |
| AC-5 | Pediatric fever > 39°C → EMERGENCY + PEDIATRIC_HIGH_FEVER | 5 |
| AC-6 | Missing/empty symptoms → 422 structured validation error | 4 |
