# Prior Authorization Review — Multi-Agent Solution Accelerator: Responsible AI FAQ

- ### What is the Prior Authorization Review — Multi-Agent Solution Accelerator?

    The Prior Authorization Review — Multi-Agent Solution Accelerator is an AI-assisted prior authorization (PA) review application built with the **Microsoft Agent Framework**, **Azure OpenAI gpt-5.4 on Microsoft Foundry**, and **Anthropic Healthcare MCP Servers**. It uses four specialized AI agents — Compliance, Clinical Reviewer, Coverage, and Synthesis — coordinated by an orchestrator to evaluate prior authorization requests against coverage policies and produce a recommendation with confidence scoring and an audit justification document. The solution is designed to assist human reviewers by automating the intake triage, clinical data extraction, and policy criteria mapping steps of the PA review process.

- ### What can the Prior Authorization Review — Multi-Agent Solution Accelerator do?

    The solution accelerator is capable of the following:

    - **Compliance validation:** Validates that all required documentation is present in a PA request — patient demographics, provider credentials, diagnosis and procedure codes, clinical notes quality, and authorization details.
    - **Clinical data extraction:** Extracts and structures clinical data from unstructured clinical notes, validates ICD-10 diagnosis codes against the official code set, and searches PubMed and clinical trials databases for supporting evidence.
    - **Coverage assessment:** Verifies provider credentials via the NPI Registry, searches CMS National and Local Coverage Determinations (NCDs/LCDs), and maps clinical evidence to each coverage criterion with auditable MET/NOT_MET/INSUFFICIENT assessments and per-criterion confidence scores.
    - **Decision synthesis:** Evaluates a three-gate decision rubric (Provider → Codes → Medical Necessity) and produces an APPROVE or PEND recommendation with confidence level and rationale.
    - **Audit trail generation:** Produces an 8-section audit justification document (Markdown and PDF) with complete data source attribution, timestamp tracking, and confidence breakdowns.
    - **Human-in-the-loop decision panel:** Presents the AI recommendation to a human reviewer who can Accept or Override with documented rationale. Override decisions flow through to notification letters and audit records.
    - **Notification letter generation:** Produces approval or pend notification letters (text and PDF) with clinical justification data and authorization numbers.

- ### What is/are the Prior Authorization Review — Multi-Agent Solution Accelerator's intended use(s)?

    This is a **solution accelerator** — not a production-ready application. It is intended as a reference architecture and working prototype that customers can use as a starting point to build, customize, and extend their own prior authorization solution based on their specific requirements. Microsoft does not provide production support for this accelerator. Customers are responsible for testing, validation, regulatory compliance, and production deployment within their own environment.

    The solution is designed to:

    - Serve as a customizable starting point for organizations building AI-assisted PA review systems
    - Demonstrate the multi-agent pattern using Microsoft Agent Framework with Azure OpenAI gpt-5.4 on Microsoft Foundry
    - Showcase integration with healthcare MCP servers (NPI Registry, ICD-10 Codes, CMS Coverage, PubMed, Clinical Trials)
    - Illustrate skills-based agent architecture where domain experts can update clinical rules without code changes
    - Provide a reference for gate-based decision synthesis with full audit transparency
    - Be extended with payer-specific policies, EHR/EMR integrations, additional agents, and production-grade infrastructure by the adopting organization

    The solution is **not** intended for:

    - Production clinical use without the customer first conducting comprehensive testing, validation, and regulatory compliance
    - Autonomous decision-making without human clinical oversight
    - Replacing qualified clinical reviewers or professional medical judgment
    - Use as a medical device or diagnostic tool
    - Processing real patient data without appropriate HIPAA-compliant infrastructure

- ### How was the Prior Authorization Review — Multi-Agent Solution Accelerator evaluated? What metrics are used to measure performance?

    The solution was evaluated through the following methods:

    - **End-to-end functional testing:** Verified that all agents produce structured output conforming to defined JSON schemas, that the gate-based decision rubric produces correct recommendations for known test cases, and that audit trail documents contain all required sections.
    - **MCP tool integration testing:** Confirmed that ICD-10 code validation, NPI Registry lookups, CMS Coverage policy searches, PubMed searches, and Clinical Trials searches return accurate results through the MCP protocol.
    - **Structured output validation:** Tested that agent responses parse correctly and that confidence scores, criterion assessments, and documentation gap lists are properly extracted from model output.
    - **Decision rubric evaluation:** Verified gate-based logic against sample cases covering approve, pend, and override scenarios, including edge cases with missing documentation and invalid codes.
    - **Confidence scoring calibration:** Assessed that per-criterion confidence scores and the weighted composite confidence score produce reasonable values across diverse clinical scenarios.

    Users and organizations adopting this accelerator should conduct their own evaluations aligned with their specific clinical workflows, payer policies, and regulatory requirements. Microsoft Foundry provides evaluation tools that can be leveraged for this purpose.

- ### What are the limitations of the Prior Authorization Review — Multi-Agent Solution Accelerator? How can users minimize the impact of these limitations when using the system?

    The solution has the following limitations:

    - **AI-generated content requires human review:** All recommendations are drafts that require qualified clinical review before any authorization decision is finalized. The system may generate recommendations that do not reflect actual clinical guidelines.
    - **Coverage policies are limited to Medicare:** The coverage assessment uses CMS National and Local Coverage Determinations (NCDs/LCDs) only. Commercial insurance, Medicare Advantage, and Medicaid plan policies are not included.
    - **English language only:** The system supports English language input and output only. Clinical notes, diagnosis descriptions, and policy criteria must be in English.
    - **In-memory data storage:** The demo uses an in-memory Python dictionary for review storage. Data is lost on restart and the system is single-process only. Production deployments require PostgreSQL or equivalent persistent storage.
    - **No authentication or RBAC:** The demo does not include identity management, role-based access control, or audit logging of user actions. Production deployments must implement appropriate access controls.
    - **No EHR/EMR integration:** Clinical notes must be manually entered or pasted. The system does not integrate with FHIR, HL7, or other health information exchange standards.
    - **NPI verification limitations:** Issuance of an NPI does not ensure the provider is currently licensed or credentialed. The NPPES registry is self-reported data. Verify credentials through state licensing boards.
    - **Third-party MCP servers and patient data exposure:** This solution sends clinical data (diagnosis codes, procedure codes, clinical notes excerpts, provider identifiers) to **third-party remote MCP servers** hosted outside of your organization's network. These are all hosted by Anthropic on `hcls.mcp.claude.com` (NPI Registry, ICD-10 Codes, CMS Coverage, Clinical Trials) and `pubmed.mcp.claude.com` (PubMed). Data transmitted to these servers is subject to Anthropic's data handling, privacy, and retention policies — not your organization's. **This may constitute disclosure of Protected Health Information (PHI) to third parties under HIPAA.** Organizations must evaluate whether appropriate Business Associate Agreements (BAAs), data processing agreements, or other contractual safeguards are in place before using these MCP servers with real patient data.
    - **Model output variability:** AI model responses may vary between invocations. The structured output parsing handles this variability, but edge cases may produce unexpected results.
    - **Synthetic demo data:** The sample case included in the application uses synthetic patient data and should not be treated as clinically accurate.

    To minimize the impact of these limitations:

    - Always require human clinical review before finalizing any authorization decision
    - **Do not use third-party MCP servers with real patient data** without first establishing BAAs or equivalent data processing agreements with each MCP server provider, or replace them with self-hosted MCP servers within your organization's HIPAA-compliant infrastructure
    - Extend coverage policies with payer-specific rules for your organization
    - Implement proper data persistence, authentication, and encryption for production use
    - Conduct thorough testing with representative clinical cases from your domain
    - Monitor AI confidence scores and flag low-confidence recommendations for additional review
    - Customize agent skills (SKILL.md files) to reflect your organization's clinical guidelines

- ### What operational factors and settings allow for effective and responsible use of the Prior Authorization Review — Multi-Agent Solution Accelerator?

    The following operational factors and settings support responsible use:

    - **Skills-based architecture:** Agent behaviors are defined in SKILL.md files that can be reviewed and updated by domain experts (clinicians, compliance officers) without code changes. This allows clinical rules to be audited and maintained by qualified personnel.
    - **LENIENT mode default:** The system ships in LENIENT mode, which only produces APPROVE or PEND recommendations — never DENY. This ensures that edge cases default to human review rather than automated denial.
    - **Configurable decision policy:** The decision rubric can be switched between LENIENT and STRICT modes. Organizations should choose the mode that aligns with their risk tolerance and regulatory requirements.
    - **Confidence scoring transparency:** Every criterion assessment includes a confidence score, and the composite recommendation includes a confidence level (HIGH/MEDIUM/LOW). Low-confidence recommendations should be flagged for additional review.
    - **Audit justification document:** Every review produces a comprehensive audit trail with data source attribution, enabling post-hoc review of the AI's reasoning and evidence basis.
    - **Override traceability:** When a human reviewer overrides the AI recommendation, the override rationale is recorded and flows through to notification letters and audit documents, maintaining a complete decision record.
    - **Model selection:** The solution uses the **Azure OpenAI gpt-5.4** model deployed through Microsoft Foundry. Organizations should evaluate model capabilities and costs for their use case. See [GPT-5.4 in Microsoft Foundry](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/introducing-gpt-5-4-in-microsoft-foundry/4499785) for pricing and details.
    - **Temperature and token settings:** Model parameters including temperature and max tokens can be configured to balance creativity versus determinism for clinical use cases.
    - **Azure Application Insights:** Observability is built in via OpenTelemetry, enabling monitoring of agent performance, error rates, and response times in production.
    - **MCP server configurability:** Healthcare MCP server endpoints are configurable, allowing organizations to point to their own validated data sources or add additional MCP servers for specialty-specific needs.
