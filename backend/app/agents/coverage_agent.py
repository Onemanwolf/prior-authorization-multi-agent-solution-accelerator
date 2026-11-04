"""Coverage Assessment Agent — HTTP dispatch to hosted agent container.

The agent logic, MCP connections, and SKILL.md all live in agents/coverage/.
This module is the thin orchestrator-side caller that forwards the request.
"""

from app.config import settings
from app.services.hosted_agents import invoke_hosted_agent


async def run_coverage_review(request_data: dict, clinical_findings: dict) -> dict:
    """Dispatch to the Coverage Assessment hosted agent.

    Args:
        request_data: Dict with provider_npi, procedure_codes,
            diagnosis_codes, clinical_notes, and patient info.
        clinical_findings: Output from the Clinical Reviewer Agent.

    Returns:
        Dict with provider_verification, criteria_assessment
        (MET/NOT_MET/INSUFFICIENT with confidence), coverage_policies,
        documentation_gaps, tool_results.
    """
    return await invoke_hosted_agent(
        "coverage-assessment-agent",
        settings.HOSTED_AGENT_COVERAGE_URL,
        {
            "request": request_data,
            "clinical_findings": clinical_findings,
        },
        foundry_agent_name=settings.HOSTED_AGENT_COVERAGE_NAME,
    )
