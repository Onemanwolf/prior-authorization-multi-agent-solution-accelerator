"""Clinical Reviewer Agent — HTTP dispatch to hosted agent container.

The agent logic, MCP connections, and SKILL.md all live in agents/clinical/.
This module is the thin orchestrator-side caller that forwards the request.
"""

from app.config import settings
from app.services.hosted_agents import invoke_hosted_agent


async def run_clinical_review(request_data: dict) -> dict:
    """Dispatch to the Clinical Reviewer hosted agent.

    Args:
        request_data: Dict with diagnosis_codes, procedure_codes,
            clinical_notes, patient_name, patient_dob.

    Returns:
        Dict with diagnosis_validation, clinical_extraction (with
        extraction_confidence), literature_support, clinical_trials,
        clinical_summary, tool_results.
    """
    return await invoke_hosted_agent(
        "clinical-reviewer-agent",
        settings.HOSTED_AGENT_CLINICAL_URL,
        request_data,
        foundry_agent_name=settings.HOSTED_AGENT_CLINICAL_NAME,
    )
