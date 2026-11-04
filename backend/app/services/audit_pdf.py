"""Audit Justification PDF generation for prior authorization reviews.

Produces a professional, color-coded PDF with 8 sections matching the
markdown audit justification document. Uses fpdf2 (already a project
dependency via notification.py).

Returns base64-encoded PDF bytes for JSON transport.
"""

import base64
import io
from datetime import datetime, timezone

from fpdf import FPDF


# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------
_BLUE = (0, 70, 140)
_LIGHT_BLUE_FILL = (220, 235, 250)
_GREEN_FILL = (212, 237, 218)
_GREEN_TEXT = (25, 135, 84)
_AMBER_FILL = (255, 243, 205)
_AMBER_TEXT = (133, 100, 4)
_RED_TEXT = (180, 0, 0)
_GRAY_ROW = (248, 249, 250)
_GRAY_TEXT = (100, 100, 100)
_LIGHT_GRAY_TEXT = (150, 150, 150)
_BLACK = (0, 0, 0)
_WHITE = (255, 255, 255)


class _AuditPDF(FPDF):
    """Custom FPDF subclass for audit justification documents."""

    def header(self) -> None:
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*_GRAY_TEXT)
        self.cell(
            0, 6,
            "PRIOR AUTHORIZATION REVIEW -- AUDIT JUSTIFICATION",
            align="C",
        )
        self.ln(4)
        self.set_draw_color(*_BLUE)
        self.set_line_width(0.5)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(6)

    def footer(self) -> None:
        self.set_y(-20)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(*_LIGHT_GRAY_TEXT)
        self.cell(0, 4, "AI-ASSISTED DRAFT -- REVIEW REQUIRED", align="C")
        self.ln(3)
        self.cell(
            0, 4,
            f"Page {self.page_no()}/{{nb}}",
            align="C",
        )


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _section_heading(pdf: FPDF, number: int, title: str) -> None:
    """Render a numbered section heading with blue underline."""
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*_BLUE)
    pdf.cell(0, 8, f"{number}. {title}")
    pdf.ln(2)
    pdf.set_draw_color(*_BLUE)
    pdf.set_line_width(0.3)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(4)
    pdf.set_text_color(*_BLACK)


def _kv(pdf: FPDF, key: str, value: str, bold_value: bool = False) -> None:
    """Render a key-value pair."""
    pdf.set_x(10)  # Reset to left margin
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(50, 6, f"{key}:")
    pdf.set_font("Helvetica", "B" if bold_value else "", 9)
    pdf.cell(0, 6, _safe_str(value))
    pdf.ln(5)


def _bullet(pdf: FPDF, text: str) -> None:
    """Render a bullet point."""
    pdf.set_font("Helvetica", "", 9)
    pdf.set_x(10)  # Reset to left margin
    pdf.cell(5, 5, "-")
    pdf.multi_cell(0, 5, _safe_str(text))
    pdf.set_x(10)  # Reset after multi_cell


def _decision_badge(pdf: FPDF, recommendation: str) -> None:
    """Render a colored decision badge."""
    is_approve = recommendation.lower() in ("approve", "approved")
    fill = _GREEN_FILL if is_approve else _AMBER_FILL
    text = "APPROVE" if is_approve else "PEND FOR REVIEW"

    pdf.set_fill_color(*fill)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*_BLACK)
    pdf.cell(0, 10, f"Decision: {text}", align="C", fill=True)
    pdf.ln(8)


def _confidence_bar(pdf: FPDF, value: float, level: str) -> None:
    """Render a simple confidence bar with percentage."""
    bar_width = 60
    bar_height = 6
    x = 10  # left margin
    y = pdf.get_y()

    # Background
    pdf.set_fill_color(230, 230, 230)
    pdf.rect(x, y, bar_width, bar_height, "F")

    # Fill
    pct = max(0.0, min(1.0, value))
    if pct >= 0.8:
        pdf.set_fill_color(*_GREEN_TEXT)
    elif pct >= 0.5:
        pdf.set_fill_color(*_AMBER_TEXT)
    else:
        pdf.set_fill_color(*_RED_TEXT)
    pdf.rect(x, y, bar_width * pct, bar_height, "F")

    # Label — at x=73, well within right margin at x=200
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*_BLACK)
    pdf.set_xy(x + bar_width + 3, y)
    pdf.cell(40, bar_height, f"{int(pct * 100)}% ({level})")
    pdf.ln(8)


def _table_header(pdf: FPDF, columns: list[tuple[str, int]]) -> None:
    """Render a table header row with blue background."""
    pdf.set_fill_color(*_LIGHT_BLUE_FILL)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*_BLUE)
    for label, width in columns:
        pdf.cell(width, 7, label, border=1, fill=True)
    pdf.ln()
    pdf.set_text_color(*_BLACK)


def _table_row(
    pdf: FPDF,
    cells: list[tuple[str, int]],
    row_index: int,
    status_col: int | None = None,
) -> None:
    """Render a table data row with alternating background."""
    if row_index % 2 == 1:
        pdf.set_fill_color(*_GRAY_ROW)
    else:
        pdf.set_fill_color(*_WHITE)

    pdf.set_font("Helvetica", "", 8)
    for i, (text, width) in enumerate(cells):
        # Color-code status columns
        if status_col is not None and i == status_col:
            val = text.upper().strip()
            if val == "MET" or val == "YES":
                pdf.set_text_color(*_GREEN_TEXT)
                pdf.set_font("Helvetica", "B", 8)
            elif val == "NOT_MET" or val == "NO":
                pdf.set_text_color(*_RED_TEXT)
                pdf.set_font("Helvetica", "B", 8)
            elif val == "INSUFFICIENT":
                pdf.set_text_color(*_AMBER_TEXT)
                pdf.set_font("Helvetica", "B", 8)

        pdf.cell(width, 6, _safe_str(text)[:60], border=1, fill=True)

        # Reset after status column
        if status_col is not None and i == status_col:
            pdf.set_text_color(*_BLACK)
            pdf.set_font("Helvetica", "", 8)

    pdf.ln()


def _safe_str(value) -> str:
    """Convert value to string, replacing characters unsupported by Helvetica."""
    if value is None:
        return "N/A"
    s = str(value)
    # Replace Unicode characters not in the Helvetica (Latin-1) character set
    s = s.replace("\u2014", "--")   # em dash
    s = s.replace("\u2013", "-")    # en dash
    s = s.replace("\u2018", "'")    # left single quote
    s = s.replace("\u2019", "'")    # right single quote
    s = s.replace("\u201c", '"')    # left double quote
    s = s.replace("\u201d", '"')    # right double quote
    s = s.replace("\u2022", "-")    # bullet
    s = s.replace("\u2026", "...")  # ellipsis
    # Catch-all: replace any remaining non-Latin-1 chars with '?'
    s = s.encode("latin-1", errors="replace").decode("latin-1")
    return s


def _check_page_space(pdf: FPDF, needed: float) -> None:
    """Add a new page if insufficient space remains."""
    if pdf.get_y() + needed > pdf.h - 25:
        pdf.add_page()


# ---------------------------------------------------------------------------
# Reusable section renderers
# ---------------------------------------------------------------------------

def _render_section_1_executive_summary(
    pdf: FPDF,
    request_data: dict,
    synthesis: dict,
    recommendation: str,
    confidence: float,
    confidence_level: str,
    now: str,
) -> None:
    """Section 1: Executive Summary."""
    _section_heading(pdf, 1, "Executive Summary")
    _decision_badge(pdf, recommendation)
    _kv(pdf, "Review Date", now)
    _kv(pdf, "Patient", f"{request_data.get('patient_name', 'N/A')} (DOB: {request_data.get('patient_dob', 'N/A')})")
    _kv(pdf, "Provider NPI", request_data.get("provider_npi", "N/A"))
    _kv(pdf, "Insurance ID", request_data.get("insurance_id") or "Not provided")
    _kv(pdf, "Diagnosis Codes", ", ".join(request_data.get("diagnosis_codes", [])))
    _kv(pdf, "Procedure Codes", ", ".join(request_data.get("procedure_codes", [])))
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(50, 6, "Confidence:")
    pdf.ln(1)
    _confidence_bar(pdf, confidence, confidence_level)
    summary_text = synthesis.get("summary", "N/A")
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, "Summary:")
    pdf.ln(5)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 5, _safe_str(summary_text))
    pdf.ln(4)


def _render_section_2_medical_necessity(
    pdf: FPDF,
    coverage_result: dict,
    clinical_result: dict,
) -> None:
    """Section 2: Medical Necessity Assessment."""
    _check_page_space(pdf, 40)
    _section_heading(pdf, 2, "Medical Necessity Assessment")

    # Provider info — normalize the data before rendering
    pv = coverage_result.get("provider_verification", {})
    if pv and isinstance(pv, dict):
        provider_name = pv.get("name", "")
        if not provider_name or provider_name == "N/A":
            for field in ["provider_name", "full_name", "last_name"]:
                if pv.get(field) and isinstance(pv[field], str):
                    first = pv.get("first_name", "")
                    if field == "last_name" and first:
                        provider_name = f"{first} {pv[field]}"
                    else:
                        provider_name = pv[field]
                    break

        specialty = pv.get("specialty", "")
        if isinstance(specialty, dict):
            specialty = specialty.get(
                "primary_taxonomy_description",
                specialty.get("description", ""),
            )
        elif not specialty or specialty == "N/A":
            for field in ["primary_taxonomy_description", "taxonomy_description"]:
                if pv.get(field) and isinstance(pv[field], str):
                    specialty = pv[field]
                    break

        pv_status = str(pv.get("status", "N/A")).upper()
        if pv_status in ("A", "ACTIVE"):
            pv_status = "VERIFIED"
        elif pv_status in ("D", "DEACTIVATED"):
            pv_status = "INACTIVE"

        if provider_name:
            _kv(pdf, "Provider", f"{provider_name} -- {specialty}")
        else:
            _kv(pdf, "Provider", f"NPI {pv.get('npi', 'N/A')} -- {specialty}")
        _kv(pdf, "Provider Status", pv_status)

    # Coverage policies
    policies = coverage_result.get("coverage_policies", [])
    if policies:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, "Coverage Policies Applied:")
        pdf.ln(5)
        for p in policies:
            if isinstance(p, dict):
                _bullet(pdf, f"{p.get('policy_id', '?')}: {p.get('title', 'N/A')} ({p.get('type', '?')})")

    # Clinical evidence
    extraction = clinical_result.get("clinical_extraction", {})
    if isinstance(extraction, dict) and extraction:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, "Clinical Evidence Summary:")
        pdf.ln(5)
        if extraction.get("chief_complaint"):
            _bullet(pdf, f"Chief Complaint: {extraction['chief_complaint']}")
        if extraction.get("history_of_present_illness"):
            hpi = extraction["history_of_present_illness"]
            if len(hpi) > 200:
                hpi = hpi[:200] + "..."
            _bullet(pdf, f"HPI: {hpi}")
        if extraction.get("prior_treatments"):
            treatments = extraction["prior_treatments"][:5]
            _bullet(pdf, f"Prior Treatments: {'; '.join(str(t) for t in treatments)}")
        if extraction.get("severity_indicators"):
            indicators = extraction["severity_indicators"][:5]
            _bullet(pdf, f"Severity Indicators: {'; '.join(str(i) for i in indicators)}")
        if extraction.get("diagnostic_findings"):
            findings = extraction["diagnostic_findings"][:5]
            _bullet(pdf, f"Diagnostic Findings: {'; '.join(str(f) for f in findings)}")
        _kv(pdf, "Extraction Confidence", f"{extraction.get('extraction_confidence', 0)}%")

    # Literature support (PubMed)
    lit = clinical_result.get("literature_support", [])
    if lit and isinstance(lit, list):
        pdf.ln(3)
        _check_page_space(pdf, 20)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, "Literature Support (PubMed):")
        pdf.ln(5)
        for ref in lit[:5]:
            if isinstance(ref, dict):
                title = _safe_str(ref.get("title", "Untitled"))
                pmid = ref.get("pmid", "")
                relevance = _safe_str(ref.get("relevance", ""))
                pmid_label = f" (PMID: {pmid})" if pmid else ""
                _bullet(pdf, f"{title}{pmid_label}")
                if relevance:
                    pdf.set_x(15)
                    pdf.set_font("Helvetica", "I", 8)
                    pdf.multi_cell(0, 4, _safe_str(f"Relevance: {relevance}"))
                    pdf.set_font("Helvetica", "", 9)

    # Clinical trials (ClinicalTrials.gov)
    trials = clinical_result.get("clinical_trials", [])
    if trials and isinstance(trials, list):
        pdf.ln(3)
        _check_page_space(pdf, 20)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, "Relevant Clinical Trials:")
        pdf.ln(5)
        for trial in trials[:5]:
            if isinstance(trial, dict):
                nct_id = trial.get("nct_id", "")
                title = _safe_str(trial.get("title", "Untitled"))
                status = trial.get("status", "")
                relevance = _safe_str(trial.get("relevance", ""))
                status_label = f" [{status}]" if status else ""
                _bullet(pdf, f"{nct_id}: {title}{status_label}")
                if relevance:
                    pdf.set_x(15)
                    pdf.set_font("Helvetica", "I", 8)
                    pdf.multi_cell(0, 4, _safe_str(f"Relevance: {relevance}"))
                    pdf.set_font("Helvetica", "", 9)

    pdf.ln(3)


def _render_section_3_criterion_evaluation(
    pdf: FPDF,
    coverage_result: dict,
    synthesis: dict,
    audit_trail: dict,
) -> None:
    """Section 3: Criterion-by-Criterion Evaluation."""
    _check_page_space(pdf, 40)
    _section_heading(pdf, 3, "Criterion-by-Criterion Evaluation")

    criteria = coverage_result.get("criteria_assessment", [])
    if not criteria:
        criteria = synthesis.get("criteria_assessment", [])

    if criteria:
        met_count = audit_trail.get("criteria_met_count", "0/0")
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, f"Criteria Met: {met_count}")
        pdf.ln(6)

        col_widths = [55, 22, 22, 91]
        columns = [
            ("Criterion", col_widths[0]),
            ("Status", col_widths[1]),
            ("Confidence", col_widths[2]),
            ("Key Evidence", col_widths[3]),
        ]
        _table_header(pdf, columns)

        for idx, c in enumerate(criteria):
            if not isinstance(c, dict):
                continue
            _check_page_space(pdf, 8)
            status = c.get("status", "INSUFFICIENT")
            conf = f"{c.get('confidence', 0)}%"
            evidence = c.get("evidence", [])
            evidence_text = "; ".join(evidence[:2]) if isinstance(evidence, list) else str(evidence)
            _table_row(
                pdf,
                [
                    (_safe_str(c.get("criterion", "N/A")), col_widths[0]),
                    (status, col_widths[1]),
                    (conf, col_widths[2]),
                    (evidence_text[:80], col_widths[3]),
                ],
                idx,
                status_col=1,
            )
    elif synthesis.get("coverage_criteria_met") or synthesis.get("coverage_criteria_not_met"):
        met_list = synthesis.get("coverage_criteria_met", [])
        not_met_list = synthesis.get("coverage_criteria_not_met", [])
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, f"Criteria Met: {len(met_list)}/{len(met_list) + len(not_met_list)}")
        pdf.ln(6)

        col_widths = [120, 22, 48]
        columns = [
            ("Criterion", col_widths[0]),
            ("Status", col_widths[1]),
            ("Source", col_widths[2]),
        ]
        _table_header(pdf, columns)

        for idx, criterion_text in enumerate(met_list):
            _check_page_space(pdf, 8)
            _table_row(
                pdf,
                [
                    (_safe_str(criterion_text)[:80], col_widths[0]),
                    ("MET", col_widths[1]),
                    ("Synthesis", col_widths[2]),
                ],
                idx,
                status_col=1,
            )
        for idx2, criterion_text in enumerate(not_met_list):
            _check_page_space(pdf, 8)
            _table_row(
                pdf,
                [
                    (_safe_str(criterion_text)[:80], col_widths[0]),
                    ("NOT_MET", col_widths[1]),
                    ("Synthesis", col_widths[2]),
                ],
                len(met_list) + idx2,
                status_col=1,
            )
    else:
        pdf.set_font("Helvetica", "I", 9)
        pdf.cell(0, 6, "No coverage criteria were identified for evaluation.")
        pdf.ln(5)

    pdf.ln(4)


def _render_section_4_validation_checks(
    pdf: FPDF,
    coverage_result: dict,
    clinical_result: dict,
    compliance_result: dict,
) -> None:
    """Section 4: Validation Checks."""
    _check_page_space(pdf, 40)
    _section_heading(pdf, 4, "Validation Checks")

    pv = coverage_result.get("provider_verification", {})
    if pv and isinstance(pv, dict):
        pv_status_display = str(pv.get("status", "N/A")).upper()
        if pv_status_display in ("A", "ACTIVE"):
            pv_status_display = "VERIFIED"
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, f"Provider Verification: NPI {pv.get('npi', 'N/A')} - {pv_status_display}")
        pdf.ln(6)

    dx_val = clinical_result.get("diagnosis_validation", [])
    if dx_val:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, "Diagnosis Code Validation:")
        pdf.ln(6)

        dx_cols = [("Code", 25), ("Description", 100), ("Billable", 25), ("Valid", 25)]
        _table_header(pdf, dx_cols)

        for idx, d in enumerate(dx_val):
            if not isinstance(d, dict):
                continue
            _check_page_space(pdf, 8)
            desc = _safe_str(d.get("description", "N/A"))[:55]
            billable = "Yes" if d.get("billable") else "No"
            valid = "Yes" if d.get("valid") else "No"
            _table_row(
                pdf,
                [
                    (d.get("code", "?"), 25),
                    (desc, 100),
                    (billable, 25),
                    (valid, 25),
                ],
                idx,
                status_col=3,
            )
        pdf.ln(4)

    checklist = compliance_result.get("checklist", [])
    if checklist:
        _check_page_space(pdf, 30)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, "Compliance Checklist:")
        pdf.ln(6)

        cl_cols = [("Item", 50), ("Status", 25), ("Detail", 100)]
        _table_header(pdf, cl_cols)

        for idx, item in enumerate(checklist):
            if not isinstance(item, dict):
                continue
            _check_page_space(pdf, 8)
            status = item.get("status", "?")
            _table_row(
                pdf,
                [
                    (_safe_str(item.get("item", "?")), 50),
                    (status, 25),
                    (_safe_str(item.get("detail", ""))[:70], 100),
                ],
                idx,
                status_col=1,
            )
        pdf.ln(4)


def _render_section_5_decision_rationale(
    pdf: FPDF,
    synthesis: dict,
    recommendation: str,
    confidence: float,
    confidence_level: str,
) -> None:
    """Section 5: Decision Rationale."""
    _check_page_space(pdf, 40)
    _section_heading(pdf, 5, "Decision Rationale")

    _kv(pdf, "Decision", recommendation.upper(), bold_value=True)
    _kv(pdf, "Confidence", f"{confidence_level} ({int(confidence * 100)}%)")

    gate_raw = synthesis.get("decision_gate", "N/A")
    gate_parts = [g.strip() for g in str(gate_raw).split("|") if g.strip()]
    if len(gate_parts) > 1:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_x(10)
        pdf.cell(0, 6, "Decision Gates:")
        pdf.ln(5)
        for gp in gate_parts:
            is_pass = "PASS" in gp.upper()
            pdf.set_x(10)
            pdf.set_font("Helvetica", "B", 9)
            if is_pass:
                pdf.set_text_color(*_GREEN_TEXT)
                pdf.cell(5, 5, "-")
                pdf.cell(15, 5, "[PASS]")
            else:
                pdf.set_text_color(*_RED_TEXT)
                pdf.cell(5, 5, "-")
                pdf.cell(15, 5, "[FAIL]")
            pdf.set_text_color(*_BLACK)
            pdf.set_font("Helvetica", "", 9)
            gate_text = gp
            for prefix in ("PASS - ", "PASS -", "FAIL - ", "FAIL -"):
                idx = gate_text.upper().find(prefix)
                if idx != -1:
                    gate_text = gate_text[idx + len(prefix):]
                    break
            pdf.multi_cell(0, 5, _safe_str(gate_text))
    else:
        _kv(pdf, "Decision Gate", gate_raw)

    pdf.ln(2)
    rationale = synthesis.get("clinical_rationale", "No rationale provided.")
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 5, _safe_str(rationale))
    pdf.ln(3)

    met_criteria = synthesis.get("coverage_criteria_met", [])
    if met_criteria:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, "Key Supporting Facts:")
        pdf.ln(5)
        for m in met_criteria:
            _bullet(pdf, m)
    pdf.ln(3)


def _render_section_6_documentation_gaps(
    pdf: FPDF,
    coverage_result: dict,
) -> None:
    """Section 6: Documentation Gaps."""
    gaps = coverage_result.get("documentation_gaps", [])
    if gaps:
        _check_page_space(pdf, 30)
        _section_heading(pdf, 6, "Documentation Gaps")

        for g in gaps:
            if isinstance(g, dict):
                critical = g.get("critical", False)
                label = "[CRITICAL]" if critical else "[Non-critical]"
                pdf.set_x(10)
                pdf.set_font("Helvetica", "B", 9)
                if critical:
                    pdf.set_text_color(*_RED_TEXT)
                else:
                    pdf.set_text_color(*_AMBER_TEXT)
                pdf.cell(5, 5, "-")
                pdf.cell(25, 5, label)
                pdf.set_text_color(*_BLACK)
                pdf.set_font("Helvetica", "", 9)
                pdf.multi_cell(0, 5, _safe_str(g.get("what", "N/A")))
                if g.get("request"):
                    pdf.set_x(40)
                    pdf.set_font("Helvetica", "I", 8)
                    pdf.multi_cell(0, 4, _safe_str(f"Action: {g['request']}"))
                    pdf.set_font("Helvetica", "", 9)

        pdf.ln(3)


def _render_section_7_audit_trail(
    pdf: FPDF,
    audit_trail: dict,
) -> None:
    """Section 7: Audit Trail."""
    _check_page_space(pdf, 40)
    _section_heading(pdf, 7, "Audit Trail")

    data_sources = audit_trail.get("data_sources", [])
    if data_sources:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, "Data Sources:")
        pdf.ln(5)
        for src in data_sources:
            _bullet(pdf, src)
        pdf.ln(3)

    _kv(pdf, "Review Started", audit_trail.get("review_started", "N/A"))
    _kv(pdf, "Review Completed", audit_trail.get("review_completed", "N/A"))
    _kv(pdf, "Extraction Confidence", f"{audit_trail.get('extraction_confidence', 0)}%")
    _kv(pdf, "Assessment Confidence", f"{audit_trail.get('assessment_confidence', 0)}%")
    _kv(pdf, "Criteria Met", audit_trail.get("criteria_met_count", "0/0"))
    pdf.ln(3)


def _render_section_8_regulatory_compliance(pdf: FPDF) -> None:
    """Section 8: Regulatory Compliance."""
    _check_page_space(pdf, 30)
    _section_heading(pdf, 8, "Regulatory Compliance")

    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, "Decision Policy: LENIENT Mode (default)")
    pdf.ln(5)
    pdf.set_font("Helvetica", "", 9)
    _bullet(pdf, "Provider verification: Required")
    _bullet(pdf, "Code validation: Required")
    _bullet(pdf, "Medical necessity criteria: All must be MET for approval")
    _bullet(pdf, "Unmet/insufficient criteria: Results in PEND (not DENY)")
    pdf.ln(4)


def _render_disclaimer_footer(pdf: FPDF, now: str) -> None:
    """Final disclaimer bar and generation timestamp."""
    _check_page_space(pdf, 15)
    pdf.ln(4)
    pdf.set_fill_color(*_AMBER_FILL)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(*_AMBER_TEXT)
    pdf.multi_cell(
        0, 4,
        "DISCLAIMER: This is an AI-assisted draft. Coverage policies reflect "
        "Medicare LCDs/NCDs only. If this review is for a commercial or Medicare "
        "Advantage plan, payer-specific policies were not applied. All decisions "
        "require human clinical review before finalization.",
        fill=True,
    )
    pdf.set_text_color(*_GRAY_TEXT)
    pdf.set_font("Helvetica", "I", 7)
    pdf.ln(3)
    pdf.cell(
        0, 4,
        f"Generated: {now} | AI-Assisted Prior Authorization Review System",
        align="C",
    )


def _render_all_audit_sections(
    pdf: FPDF,
    request_data: dict,
    synthesis: dict,
    compliance_result: dict,
    clinical_result: dict,
    coverage_result: dict,
    audit_trail: dict,
    recommendation: str,
    confidence: float,
    confidence_level: str,
    now: str,
) -> None:
    """Render all 8 standard audit sections onto the given PDF."""
    _render_section_1_executive_summary(
        pdf, request_data, synthesis, recommendation, confidence, confidence_level, now,
    )
    _render_section_2_medical_necessity(pdf, coverage_result, clinical_result)
    _render_section_3_criterion_evaluation(pdf, coverage_result, synthesis, audit_trail)
    _render_section_4_validation_checks(pdf, coverage_result, clinical_result, compliance_result)
    _render_section_5_decision_rationale(pdf, synthesis, recommendation, confidence, confidence_level)
    _render_section_6_documentation_gaps(pdf, coverage_result)
    _render_section_7_audit_trail(pdf, audit_trail)
    _render_section_8_regulatory_compliance(pdf)


# ---------------------------------------------------------------------------
# Main PDF generator
# ---------------------------------------------------------------------------

def generate_audit_justification_pdf(
    request_data: dict,
    synthesis: dict,
    compliance_result: dict,
    clinical_result: dict,
    coverage_result: dict,
    audit_trail: dict,
) -> str:
    """Generate a professional audit justification PDF.

    Args:
        request_data: Original prior auth request data.
        synthesis: Orchestrator synthesis output (recommendation, confidence, etc.).
        compliance_result: Compliance agent output.
        clinical_result: Clinical reviewer agent output.
        coverage_result: Coverage agent output.
        audit_trail: Audit trail metadata dict.

    Returns:
        Base64-encoded PDF string.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    recommendation = synthesis.get("recommendation", "pend_for_review")
    confidence = synthesis.get("confidence", 0)
    confidence_level = synthesis.get("confidence_level", "LOW")

    pdf = _AuditPDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=25)

    # --- Disclaimer banner ---
    pdf.set_fill_color(*_AMBER_FILL)
    pdf.set_font("Helvetica", "BI", 8)
    pdf.set_text_color(*_AMBER_TEXT)
    pdf.multi_cell(
        0, 4,
        "WARNING: AI-ASSISTED DRAFT -- REVIEW REQUIRED. "
        "All recommendations are drafts requiring human clinical review. "
        "Coverage policies reflect Medicare LCDs/NCDs only. "
        "Commercial and Medicare Advantage plans may differ.",
        fill=True,
    )
    pdf.set_text_color(*_BLACK)
    pdf.ln(6)

    # Render all 8 sections using shared helpers
    _render_all_audit_sections(
        pdf, request_data, synthesis, compliance_result, clinical_result,
        coverage_result, audit_trail, recommendation, confidence,
        confidence_level, now,
    )

    # --- Final disclaimer and footer ---
    _render_disclaimer_footer(pdf, now)

    # --- Output to base64 ---
    buf = io.BytesIO()
    pdf.output(buf)
    pdf_bytes = buf.getvalue()
    return base64.b64encode(pdf_bytes).decode("ascii")


def regenerate_audit_pdf_with_override(
    original_args: dict,
    was_overridden: bool,
    override_rationale: str,
    override_reviewer: str,
    original_recommendation: str,
    final_recommendation: str,
    decided_at: str,
) -> str:
    """Regenerate the full audit PDF with all 8 sections plus Section 9: Clinician Override.

    Re-generates the complete audit justification document (Sections 1-8)
    and appends a Section 9 with override details, so the PDF is clearly
    an audit document rather than a notification letter.

    Returns base64-encoded PDF string.
    """
    request_data = original_args.get("request_data", {})
    synthesis = original_args.get("synthesis", {})
    compliance_result = original_args.get("compliance_result", {})
    clinical_result = original_args.get("clinical_result", {})
    coverage_result = original_args.get("coverage_result", {})
    audit_trail = original_args.get("audit_trail", {})

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    recommendation = synthesis.get("recommendation", "pend_for_review")
    confidence = synthesis.get("confidence", 0)
    confidence_level = synthesis.get("confidence_level", "LOW")

    pdf = _AuditPDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=25)

    # --- Disclaimer banner ---
    pdf.set_fill_color(*_AMBER_FILL)
    pdf.set_font("Helvetica", "BI", 8)
    pdf.set_text_color(*_AMBER_TEXT)
    pdf.multi_cell(
        0, 4,
        "WARNING: AI-ASSISTED DRAFT -- REVIEW REQUIRED. "
        "All recommendations are drafts requiring human clinical review. "
        "Coverage policies reflect Medicare LCDs/NCDs only. "
        "Commercial and Medicare Advantage plans may differ.",
        fill=True,
    )
    pdf.set_text_color(*_BLACK)
    pdf.ln(4)

    # Override alert banner at top
    if was_overridden:
        pdf.set_fill_color(255, 220, 220)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(180, 0, 0)
        pdf.multi_cell(
            0, 5,
            f"CLINICIAN OVERRIDE: The original AI recommendation "
            f"({original_recommendation.replace('_', ' ').upper()}) was overridden to "
            f"{final_recommendation.replace('_', ' ').upper()} by {override_reviewer} "
            f"on {decided_at}.",
            fill=True,
        )
        pdf.set_text_color(*_BLACK)
    pdf.ln(6)

    # --- Render ALL 8 original audit sections ---
    # Section 1 uses the final (overridden) recommendation in its decision badge
    # so the PDF clearly reflects the clinician's decision.
    _render_section_1_executive_summary(
        pdf, request_data, synthesis,
        final_recommendation if was_overridden else recommendation,
        confidence, confidence_level, now,
    )
    _render_section_2_medical_necessity(pdf, coverage_result, clinical_result)
    _render_section_3_criterion_evaluation(pdf, coverage_result, synthesis, audit_trail)
    _render_section_4_validation_checks(pdf, coverage_result, clinical_result, compliance_result)
    _render_section_5_decision_rationale(pdf, synthesis, recommendation, confidence, confidence_level)
    _render_section_6_documentation_gaps(pdf, coverage_result)
    _render_section_7_audit_trail(pdf, audit_trail)
    _render_section_8_regulatory_compliance(pdf)

    # --- Section 9: Clinician Override Record (new section) ---
    _check_page_space(pdf, 60)
    _section_heading(pdf, 9, "Clinician Override Record")

    _kv(pdf, "Override Status", "YES -- Decision was overridden", bold_value=True)
    _kv(pdf, "Overridden By", override_reviewer, bold_value=True)
    _kv(pdf, "Override Date", decided_at)
    _kv(pdf, "Original AI Recommendation", original_recommendation.replace("_", " ").upper())
    _kv(pdf, "Final Decision", final_recommendation.replace("_", " ").upper(), bold_value=True)
    pdf.ln(3)

    # Override rationale
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, "Override Rationale:")
    pdf.ln(5)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 5, _safe_str(override_rationale))
    pdf.ln(4)

    # Comparison box
    pdf.set_fill_color(*_AMBER_FILL)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*_AMBER_TEXT)
    pdf.multi_cell(
        0, 4,
        f"AI recommended: {original_recommendation.replace('_', ' ').upper()} "
        f"(Confidence: {confidence_level} {int(confidence * 100)}%) -- "
        f"Clinician decision: {final_recommendation.replace('_', ' ').upper()}",
        fill=True,
    )
    pdf.set_text_color(*_BLACK)
    pdf.ln(6)

    # --- Override-specific disclaimer and footer ---
    _check_page_space(pdf, 15)
    pdf.ln(4)
    pdf.set_fill_color(*_AMBER_FILL)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(*_AMBER_TEXT)
    pdf.multi_cell(
        0, 4,
        "DISCLAIMER: This audit document reflects both the AI-assisted review "
        "and the clinician override. The final decision was made by a licensed "
        "clinician who reviewed and overrode the AI recommendation.",
        fill=True,
    )
    pdf.set_text_color(*_GRAY_TEXT)
    pdf.set_font("Helvetica", "I", 7)
    pdf.ln(3)
    pdf.cell(
        0, 4,
        f"Generated: {now} | AI-Assisted Prior Authorization Review System",
        align="C",
    )

    buf = io.BytesIO()
    pdf.output(buf)
    pdf_bytes = buf.getvalue()
    return base64.b64encode(pdf_bytes).decode("ascii")
