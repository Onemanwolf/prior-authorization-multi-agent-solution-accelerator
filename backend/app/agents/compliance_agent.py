"""Compliance Validation Agent — HTTP dispatch to hosted agent container.

The agent logic and SKILL.md all live in agents/compliance/.
This module is the thin orchestrator-side caller that forwards the request.
"""

from app.config import settings
from app.services.hosted_agents import invoke_hosted_agent


async def run_compliance_review(request_data: dict) -> dict:
    """Dispatch to the Compliance Validation hosted agent.

    Args:
        request_data: Dict with patient_name, patient_dob, provider_npi,
            diagnosis_codes, procedure_codes, clinical_notes, insurance_id.

    Returns:
        Dict with checklist, overall_status, missing_items,
        additional_info_requests.
    """
    return await invoke_hosted_agent(
        "compliance-agent",
        settings.HOSTED_AGENT_COMPLIANCE_URL,
        request_data,
        foundry_agent_name=settings.HOSTED_AGENT_COMPLIANCE_NAME,
    )
