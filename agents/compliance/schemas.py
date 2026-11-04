"""Pydantic output schema for the Compliance Validation Agent.

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


class ChecklistItem(BaseModel):
    item: str = ""
    status: str = "incomplete"  # "complete", "incomplete", "missing"
    detail: str = ""


class ComplianceResult(BaseModel):
    agent_name: str = "Compliance Agent"
    checks_performed: list[AgentCheck] = []
    checklist: list[ChecklistItem] = []
    overall_status: str = "incomplete"
    missing_items: list[str] = []
    additional_info_requests: list[str] = []
    error: str | None = None
