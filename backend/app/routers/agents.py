"""Per-agent API endpoints for standalone invocation.

These routes expose each agent individually for:
  - Foundry Control Plane registration (each agent as a "Custom Agent")
  - Per-agent evaluation and benchmarking
  - Red-teaming and adversarial testing
  - Future microservices migration (each route becomes its own service)

The orchestrator continues to call agents in-process for production
reviews via POST /api/review/stream. These endpoints are for external
callers only.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter

from app.agents.clinical_agent import run_clinical_review
from app.agents.compliance_agent import run_compliance_review
from app.agents.coverage_agent import run_coverage_review
from app.agents.synthesis_agent import run_synthesis_review
from app.models.schemas import (
    CoverageAgentRequest,
    PriorAuthRequest,
    SynthesisAgentRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["agents"])


def _request_to_dict(req: PriorAuthRequest) -> dict:
    """Convert PriorAuthRequest to the dict format agents expect."""
    return {
        "patient_name": req.patient_name,
        "patient_dob": req.patient_dob,
        "provider_npi": req.provider_npi,
        "diagnosis_codes": req.diagnosis_codes,
        "procedure_codes": req.procedure_codes,
        "clinical_notes": req.clinical_notes,
        "insurance_id": req.insurance_id,
    }


@router.post("/clinical")
async def invoke_clinical_agent(request: PriorAuthRequest):
    """Run the Clinical Reviewer Agent in isolation.

    Accepts a PA request and returns the clinical review output
    (diagnosis validation, clinical extraction, literature support,
    clinical trials, clinical summary).
    """
    logger.info("Standalone clinical agent invocation for patient: %s", request.patient_name)
    started = datetime.now(timezone.utc).isoformat()
    result = await run_clinical_review(_request_to_dict(request))
    completed = datetime.now(timezone.utc).isoformat()
    return {
        "agent": "clinical-reviewer-agent",
        "started": started,
        "completed": completed,
        "result": result,
    }


@router.post("/compliance")
async def invoke_compliance_agent(request: PriorAuthRequest):
    """Run the Compliance Validation Agent in isolation.

    Accepts a PA request and returns the compliance checklist
    (documentation completeness, missing items, additional info requests).
    """
    logger.info("Standalone compliance agent invocation for patient: %s", request.patient_name)
    started = datetime.now(timezone.utc).isoformat()
    result = await run_compliance_review(_request_to_dict(request))
    completed = datetime.now(timezone.utc).isoformat()
    return {
        "agent": "compliance-agent",
        "started": started,
        "completed": completed,
        "result": result,
    }


@router.post("/coverage")
async def invoke_coverage_agent(body: CoverageAgentRequest):
    """Run the Coverage Assessment Agent in isolation.

    Accepts a PA request plus clinical findings (from a prior Clinical
    Agent run or test fixtures). Returns provider verification, coverage
    policies, criteria assessment, and documentation gaps.
    """
    logger.info("Standalone coverage agent invocation for patient: %s", body.request.patient_name)
    started = datetime.now(timezone.utc).isoformat()
    result = await run_coverage_review(
        _request_to_dict(body.request),
        body.clinical_findings,
    )
    completed = datetime.now(timezone.utc).isoformat()
    return {
        "agent": "coverage-assessment-agent",
        "started": started,
        "completed": completed,
        "result": result,
    }


@router.post("/synthesis")
async def invoke_synthesis_agent(body: SynthesisAgentRequest):
    """Run the Synthesis Decision Agent in isolation.

    Accepts a PA request plus all three upstream agent results (from
    prior agent runs or test fixtures). Returns the final recommendation
    with confidence scoring, decision gate, and audit trail data.
    """
    logger.info("Standalone synthesis agent invocation for patient: %s", body.request.patient_name)
    started = datetime.now(timezone.utc).isoformat()
    result = await run_synthesis_review(
        request_data=_request_to_dict(body.request),
        compliance_result=body.compliance_result,
        clinical_result=body.clinical_result,
        coverage_result=body.coverage_result,
        cpt_validation=body.cpt_validation,
    )
    completed = datetime.now(timezone.utc).isoformat()
    return {
        "agent": "synthesis-decision-agent",
        "started": started,
        "completed": completed,
        "result": result,
    }
