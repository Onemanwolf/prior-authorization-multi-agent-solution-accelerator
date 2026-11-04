import type { PriorAuthRequest } from "./types";

export const SAMPLE_REQUEST: PriorAuthRequest = {
  patient_name: "John Smith",
  patient_dob: "1958-03-15",
  provider_npi: "1720180003",
  diagnosis_codes: ["R91.1", "J18.9", "R05.9"],
  procedure_codes: ["31628"],
  clinical_notes:
    "68-year-old male presenting with persistent right lower lobe pulmonary " +
    "nodule. CT chest (01/15/2026) demonstrates a 1.8 cm spiculated nodule " +
    "in the RLL, increased from 1.2 cm on prior CT (10/12/2025), consistent " +
    "with interval growth over 3 months. PET/CT (01/22/2026) shows FDG " +
    "avidity with SUV max of 4.2, concerning for malignancy.\n\n" +
    "PMH: COPD (mild, GOLD stage I), hypertension, hyperlipidemia. " +
    "40 pack-year smoking history, quit 5 years ago. No prior history of " +
    "malignancy. Family history significant for lung cancer (father, age 72). " +
    "Medications: albuterol inhaler PRN, lisinopril 10 mg daily, " +
    "atorvastatin 20 mg daily. Allergies: NKDA.\n\n" +
    "Physical exam: Vitals BP 132/78, HR 76, RR 16, SpO2 95% on room air. " +
    "Lungs with decreased breath sounds at right base; no wheezing or " +
    "crackles. Remainder of exam unremarkable.\n\n" +
    "Labs (01/20/2026): WBC 9.2 K/uL, Hgb 14.1 g/dL, Platelets 245 K/uL, " +
    "INR 1.0, Creatinine 0.9 mg/dL, BUN 18 mg/dL. Comprehensive metabolic " +
    "panel within normal limits.\n\n" +
    "Pulmonary function tests (01/18/2026): FEV1 78% predicted, FVC 82% " +
    "predicted, FEV1/FVC ratio 0.73, DLCO 71% predicted. Patient is an " +
    "acceptable surgical candidate.\n\n" +
    "Patient completed a 14-day course of amoxicillin-clavulanate with no " +
    "resolution of the nodule. Given the spiculated morphology, interval " +
    "growth, FDG avidity (SUV 4.2), and significant smoking history, there " +
    "is high suspicion for primary lung malignancy per Fleischner Society " +
    "guidelines. Recommend CT-guided transbronchial lung biopsy (CPT 31628) " +
    "for tissue diagnosis. Risks including pneumothorax and bleeding were " +
    "discussed; patient consents to proceed.",
  insurance_id: "MCR-123456789A",
};
