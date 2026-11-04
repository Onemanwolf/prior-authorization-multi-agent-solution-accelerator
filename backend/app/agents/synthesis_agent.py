"""Synthesis Decision Agent — HTTP dispatch to hosted agent container.

The agent logic and SKILL.md live in agents/synthesis/.
This module is the thin orchestrator-side caller that forwards the combined
outputs from all three specialist agents to the synthesis hosted agent.
"""

from app.config import settings
from app.services.hosted_agents import invoke_hosted_agent


async def run_synthesis_review(
    request_data: dict,
    compliance_result: dict,
    clinical_result: dict,
    coverage_result: dict,
    cpt_validation: dict | None = None,
) -> dict:
    """Dispatch to the Synthesis Decision hosted agent.

    Args:
        request_data: Original prior auth request dict.
        compliance_result: Output from the Compliance Agent.
        clinical_result: Output from the Clinical Reviewer Agent.
        coverage_result: Output from the Coverage Assessment Agent.
        cpt_validation: Optional CPT/HCPCS pre-flight validation results.

    Returns:
        Dict with recommendation, confidence, confidence_level, summary,
        clinical_rationale, coverage_criteria_met/not_met,
        missing_documentation, policy_references, decision_gate,
        audit_trail, and disclaimer.
    """
    return await invoke_hosted_agent(
        "synthesis-decision-agent",
        settings.HOSTED_AGENT_SYNTHESIS_URL,
        {
            "request": request_data,
            "compliance_result": compliance_result,
            "clinical_result": clinical_result,
            "coverage_result": coverage_result,
            "cpt_validation": cpt_validation,
        },
        foundry_agent_name=settings.HOSTED_AGENT_SYNTHESIS_NAME,
    )
