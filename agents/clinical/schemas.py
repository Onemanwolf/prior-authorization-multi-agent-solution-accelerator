"""Pydantic output schema for the Clinical Reviewer Agent.

Kept in sync with backend/app/models/schemas.py.
Used as response_format for MAF structured output — guarantees the
agent emits valid JSON matching this model on every call.
"""
from pydantic import BaseModel


class AgentCheck(BaseModel):
    """A single rule/check that an agent performed."""

    rule: str = ""
    result: str = "info"  # "pass", "fail", "warning", "info"
    detail: str = ""


class ToolResult(BaseModel):
    tool_name: str = ""
    status: str = "warning"  # "pass", "fail", "warning"
    detail: str = ""


class DiagnosisValidation(BaseModel):
    code: str = ""
    valid: bool = False
    description: str = ""
    billable: bool = False
    hierarchy_note: str = ""  # only when non-billable code has specific children


class ClinicalExtraction(BaseModel):
    chief_complaint: str = ""
    history_of_present_illness: str = ""
    prior_treatments: list[str] = []
    severity_indicators: list[str] = []
    functional_limitations: list[str] = []
    diagnostic_findings: list[str] = []
    duration_and_progression: str = ""
    medical_history_and_comorbidities: str = ""
    extraction_confidence: int = 0  # 0-100 overall extraction confidence


class ProcedureValidation(BaseModel):
    code: str = ""
    valid: bool = False
    description: str = ""
    source: str = ""  # "orchestrator_preflight" or "unverified"


class LiteratureReference(BaseModel):
    title: str = ""
    pmid: str = ""
    relevance: str = ""


class ClinicalTrialReference(BaseModel):
    nct_id: str = ""
    title: str = ""
    status: str = ""
    relevance: str = ""


class ClinicalResult(BaseModel):
    agent_name: str = "Clinical Reviewer Agent"
    checks_performed: list[AgentCheck] = []
    diagnosis_validation: list[DiagnosisValidation] = []
    procedure_validation: list[ProcedureValidation] = []
    clinical_extraction: ClinicalExtraction | None = None
    literature_support: list[LiteratureReference] = []
    clinical_trials: list[ClinicalTrialReference] = []
    clinical_summary: str = ""
    tool_results: list[ToolResult] = []
    error: str | None = None
