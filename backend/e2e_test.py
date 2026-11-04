"""E2E test: POST /review with sample case, save full response."""
import json
import sys
import urllib.request

URL = "http://localhost:8000/api/review"

payload = {
    "patient_name": "John Smith",
    "patient_dob": "1958-03-15",
    "provider_npi": "1720180003",
    "diagnosis_codes": ["R91.1", "J18.9", "R05.9"],
    "procedure_codes": ["31628"],
    "clinical_notes": (
        "68-year-old male presenting with persistent right lower lobe pulmonary "
        "nodule. CT chest (01/15/2026) demonstrates a 1.8 cm spiculated nodule "
        "in the RLL, increased from 1.2 cm on prior CT (10/12/2025), consistent "
        "with interval growth over 3 months. PET/CT (01/22/2026) shows FDG "
        "avidity with SUV max of 4.2, concerning for malignancy.\n\n"
        "PMH: COPD (mild, GOLD stage I), hypertension, hyperlipidemia. "
        "40 pack-year smoking history, quit 5 years ago. No prior history of "
        "malignancy. Family history significant for lung cancer (father, age 72). "
        "Medications: albuterol inhaler PRN, lisinopril 10 mg daily, "
        "atorvastatin 20 mg daily. Allergies: NKDA.\n\n"
        "Physical exam: Vitals BP 132/78, HR 76, RR 16, SpO2 95% on room air. "
        "Lungs with decreased breath sounds at right base; no wheezing or "
        "crackles. Remainder of exam unremarkable.\n\n"
        "Labs (01/20/2026): WBC 9.2 K/uL, Hgb 14.1 g/dL, Platelets 245 K/uL, "
        "INR 1.0, Creatinine 0.9 mg/dL, BUN 18 mg/dL. Comprehensive metabolic "
        "panel within normal limits.\n\n"
        "Pulmonary function tests (01/18/2026): FEV1 78% predicted, FVC 82% "
        "predicted, FEV1/FVC ratio 0.73, DLCO 71% predicted. Patient is an "
        "acceptable surgical candidate.\n\n"
        "Patient completed a 14-day course of amoxicillin-clavulanate with no "
        "resolution of the nodule. Given the spiculated morphology, interval "
        "growth, FDG avidity (SUV 4.2), and significant smoking history, there "
        "is high suspicion for primary lung malignancy per Fleischner Society "
        "guidelines. Recommend CT-guided transbronchial lung biopsy (CPT 31628) "
        "for tissue diagnosis. Risks including pneumothorax and bleeding were "
        "discussed; patient consents to proceed."
    ),
    "insurance_id": "MCR-123456789A",
}

data = json.dumps(payload).encode("utf-8")
req = urllib.request.Request(URL, data=data, headers={"Content-Type": "application/json"})

print(f"POST {URL}")
print("Waiting for response (may take several minutes)...")

try:
    with urllib.request.urlopen(req, timeout=600) as resp:
        body = resp.read().decode("utf-8")
        result = json.loads(body)

        # Save full response
        out_path = "e2e_test_result.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)

        print(f"\nSUCCESS (HTTP {resp.status})")
        print(f"  Recommendation: {result.get('recommendation')}")
        print(f"  Confidence:     {result.get('confidence')}%")
        print(f"  Level:          {result.get('confidence_level')}")
        print(f"  Summary:        {str(result.get('summary', ''))[:120]}...")
        print(f"\nFull response saved to: {out_path}")

        # Quick agent-detail spot checks (uses API response Pydantic field names)
        ar = result.get("agent_results", {})
        for agent_name in ("compliance", "clinical", "coverage"):
            a = ar.get(agent_name, {})
            print(f"\n--- {agent_name.upper()} ---")
            checks = a.get("checks_performed", [])
            print(f"  checks_performed:  {len(checks)}")
            if agent_name == "compliance":
                items = a.get("checklist", [])
                print(f"  checklist items:   {len(items)}")
                print(f"  overall_status:    {a.get('overall_status')}")
            elif agent_name == "clinical":
                dx = a.get("diagnosis_validation", [])
                ce = a.get("clinical_extraction") or {}
                lit = a.get("literature_support", [])
                tri = a.get("clinical_trials", [])
                cs = a.get("clinical_summary", "")
                ec = ce.get("extraction_confidence", "N/A")
                print(f"  dx validated:      {len(dx)}")
                print(f"  extraction fields: cc={bool(ce.get('chief_complaint'))}, hpi={bool(ce.get('history_of_present_illness'))}, prior_tx={bool(ce.get('prior_treatments'))}")
                print(f"  literature refs:   {len(lit)} (has data: {any(r.get('title') for r in lit)})")
                print(f"  clinical trials:   {len(tri)} (has data: {any(t.get('nct_id') for t in tri)})")
                print(f"  clinical_summary:  {str(cs)[:80]}...")
                print(f"  extraction_conf:   {ec}")
            elif agent_name == "coverage":
                pv = a.get("provider_verification") or {}
                cp = a.get("coverage_policies", [])
                ca = a.get("criteria_assessment", [])
                dg = a.get("documentation_gaps", [])
                print(f"  provider npi:      {pv.get('npi')}")
                print(f"  provider name:     {pv.get('name')}")
                print(f"  provider specialty:{pv.get('specialty')}")
                print(f"  provider status:   {pv.get('status')}")
                print(f"  policies:          {len(cp)}")
                print(f"  criteria:          {len(ca)}")
                print(f"  doc gaps:          {len(dg)}")

except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
