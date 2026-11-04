"""Pydantic output schema for the Coverage Assessment Agent.

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


class ProviderVerification(BaseModel):
    npi: str = ""
    name: str = ""
    specialty: str = ""
    status: str = ""  # "active", "inactive", "not_found"
    detail: str = ""


class CoveragePolicy(BaseModel):
    policy_id: str = ""
    title: str = ""
    type: str = ""  # "LCD", "NCD"
    relevant: bool = True


class CriterionAssessment(BaseModel):
    criterion: str = ""
    status: str = "INSUFFICIENT"  # "MET", "NOT_MET", "INSUFFICIENT"
    confidence: int = 0  # 0-100 per-criterion confidence
    evidence: list[str] = []
    notes: str = ""
    source: str = ""
    # Backward compat field
    met: bool = False


class DocumentationGap(BaseModel):
    what: str = ""
    critical: bool = False
    request: str = ""


class CoverageResult(BaseModel):
    agent_name: str = "Coverage Agent"
    checks_performed: list[AgentCheck] = []
    provider_verification: ProviderVerification | None = None
    coverage_policies: list[CoveragePolicy] = []
    criteria_assessment: list[CriterionAssessment] = []
    coverage_criteria_met: list[str] = []
    coverage_criteria_not_met: list[str] = []
    policy_references: list[str] = []
    coverage_limitations: list[str] = []
    documentation_gaps: list[DocumentationGap] = []
    tool_results: list[ToolResult] = []
    error: str | None = None
