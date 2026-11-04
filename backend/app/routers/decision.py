"""Decision and notification endpoint for prior authorization review."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.models.schemas import DecisionRequest, DecisionResponse, NotificationLetter
from app.agents.orchestrator import get_review, store_decision
from app.services.notification import (
    generate_authorization_number,
    generate_approval_letter,
    generate_pend_letter,
    generate_letter_pdf,
)
from app.services.audit_pdf import regenerate_audit_pdf_with_override

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/decision", response_model=DecisionResponse)
async def submit_decision(request: DecisionRequest):
    """Accept or override the AI recommendation and generate a notification letter.

    Requires that the review has already been completed (request_id must exist
    in the review store). Generates a notification letter (approval or pend)
    and persists the decision record.
    """
    stored = get_review(request.request_id)
    if not stored:
        raise HTTPException(
            status_code=404,
            detail=f"Review {request.request_id} not found",
        )

    if stored.get("decision"):
        raise HTTPException(
            status_code=409,
            detail="Decision already recorded for this review",
        )

    review_response = stored["response"]
    request_data = stored["request_data"]

    # Determine final recommendation
    if request.action == "accept":
        final_recommendation = review_response["recommendation"]
    elif request.action == "override":
        if not request.override_recommendation:
            raise HTTPException(
                status_code=422,
                detail="override_recommendation required when action is 'override'",
            )
        if request.override_recommendation not in ("approve", "pend_for_review"):
            raise HTTPException(
                status_code=422,
                detail="override_recommendation must be 'approve' or 'pend_for_review'",
            )
        final_recommendation = request.override_recommendation
    else:
        raise HTTPException(
            status_code=422,
            detail="action must be 'accept' or 'override'",
        )

    # Generate authorization number
    auth_number = generate_authorization_number()

    # Extract provider name from coverage agent results
    provider_name = "Provider"
    agent_results = review_response.get("agent_results", {})
    if agent_results:
        coverage = agent_results.get("coverage") or {}
        pv = coverage.get("provider_verification") or {}
        if isinstance(pv, dict) and pv.get("name"):
            provider_name = pv["name"]

    # Common kwargs for letter generation
    documentation_gaps = [
        g if isinstance(g, dict) else {}
        for g in review_response.get("documentation_gaps", [])
    ]

    # Override information for letters
    is_overridden = request.action == "override"
    original_recommendation = review_response["recommendation"]
    override_kwargs = {
        "was_overridden": is_overridden,
        "override_rationale": request.override_rationale or "",
        "override_reviewer": request.reviewer_name,
        "original_recommendation": original_recommendation,
    }

    common_kwargs = {
        "authorization_number": auth_number,
        "patient_name": request_data.get("patient_name", ""),
        "patient_dob": request_data.get("patient_dob", ""),
        "provider_name": provider_name,
        "provider_npi": request_data.get("provider_npi", ""),
        "procedure_codes": request_data.get("procedure_codes", []),
        "diagnosis_codes": request_data.get("diagnosis_codes", []),
        "summary": review_response.get("summary", ""),
        "insurance_id": request_data.get("insurance_id", ""),
        "policy_references": review_response.get("policy_references", []),
        "confidence": review_response.get("confidence", 0),
        "confidence_level": review_response.get("confidence_level", ""),
        "clinical_rationale": review_response.get("clinical_rationale", ""),
        "coverage_criteria_met": review_response.get("coverage_criteria_met", []),
        "documentation_gaps": documentation_gaps,
        **override_kwargs,
    }

    # Generate notification letter
    try:
        if final_recommendation == "approve":
            letter_dict = generate_approval_letter(**common_kwargs)
        else:
            letter_dict = generate_pend_letter(
                **common_kwargs,
                missing_documentation=review_response.get("missing_documentation", []),
                coverage_criteria_not_met=review_response.get("coverage_criteria_not_met", []),
            )
    except Exception as e:
        logger.error("Letter generation failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Letter generation failed: {e}",
        )

    # Enrich letter_dict with fields needed for PDF rendering
    letter_dict["patient_dob"] = request_data.get("patient_dob", "")
    letter_dict["provider_npi"] = request_data.get("provider_npi", "")
    letter_dict["procedure_codes"] = request_data.get("procedure_codes", [])
    letter_dict["diagnosis_codes"] = request_data.get("diagnosis_codes", [])
    letter_dict["summary"] = review_response.get("summary", "")
    letter_dict["insurance_id"] = request_data.get("insurance_id", "")
    letter_dict["policy_references"] = review_response.get("policy_references", [])
    letter_dict["clinical_rationale"] = review_response.get("clinical_rationale", "")
    letter_dict["coverage_criteria_met"] = review_response.get("coverage_criteria_met", [])
    letter_dict["coverage_criteria_not_met"] = review_response.get("coverage_criteria_not_met", [])
    letter_dict["documentation_gaps"] = documentation_gaps
    if final_recommendation != "approve":
        letter_dict["missing_documentation"] = review_response.get("missing_documentation", [])

    # Include override info in letter_dict for PDF rendering
    letter_dict["was_overridden"] = is_overridden
    letter_dict["override_rationale"] = request.override_rationale or ""
    letter_dict["override_reviewer"] = request.reviewer_name if is_overridden else ""
    letter_dict["original_recommendation"] = original_recommendation

    # Generate PDF (may fail on encoding issues — catch gracefully)
    try:
        letter_dict["pdf_base64"] = generate_letter_pdf(letter_dict)
    except Exception as e:
        logger.error("Letter PDF generation failed: %s", e, exc_info=True)
        letter_dict["pdf_base64"] = None  # Proceed without PDF

    decided_at = datetime.now(timezone.utc).isoformat()

    # Regenerate audit PDF with override information if decision was overridden
    updated_audit_pdf = None
    if is_overridden:
        try:
            agent_results = review_response.get("agent_results", {})
            audit_trail_data = review_response.get("audit_trail", {})
            if isinstance(audit_trail_data, str):
                audit_trail_data = {}
            updated_audit_pdf = regenerate_audit_pdf_with_override(
                original_args={
                    "request_data": request_data,
                    "synthesis": review_response,
                    "compliance_result": (agent_results.get("compliance") or {}),
                    "clinical_result": (agent_results.get("clinical") or {}),
                    "coverage_result": (agent_results.get("coverage") or {}),
                    "audit_trail": audit_trail_data,
                },
                was_overridden=True,
                override_rationale=request.override_rationale or "",
                override_reviewer=request.reviewer_name,
                original_recommendation=original_recommendation,
                final_recommendation=final_recommendation,
                decided_at=decided_at,
            )
            # Update the stored review's audit PDF (get_review returns a ref)
            stored["response"]["audit_justification_pdf"] = updated_audit_pdf
        except Exception as e:
            logger.error("Audit PDF regeneration failed: %s", e, exc_info=True)

    # Build and persist decision record
    decision_record = {
        "request_id": request.request_id,
        "authorization_number": auth_number,
        "final_recommendation": final_recommendation,
        "decided_by": request.reviewer_name,
        "decided_at": decided_at,
        "was_overridden": request.action == "override",
        "override_rationale": request.override_rationale,
        "letter": letter_dict,
    }

    store_decision(request.request_id, decision_record)

    # Build the NotificationLetter — filter to known fields only
    _letter_fields = {
        "authorization_number", "letter_type", "effective_date",
        "expiration_date", "patient_name", "provider_name",
        "body_text", "appeal_rights", "documentation_deadline",
        "pdf_base64",
    }
    letter_for_model = {k: v for k, v in letter_dict.items() if k in _letter_fields}

    return DecisionResponse(
        request_id=request.request_id,
        authorization_number=auth_number,
        final_recommendation=final_recommendation,
        decided_by=request.reviewer_name,
        decided_at=decided_at,
        was_overridden=request.action == "override",
        override_rationale=request.override_rationale if is_overridden else None,
        original_recommendation=original_recommendation if is_overridden else None,
        letter=NotificationLetter(**letter_for_model),
        updated_audit_justification_pdf=updated_audit_pdf,
    )
