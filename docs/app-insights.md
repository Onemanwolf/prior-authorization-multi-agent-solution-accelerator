# Application Insights: Foundry Agent Troubleshooting

Use this guide to investigate token usage, requests, responses, errors, latency,
and MCP tools for the prior-authorization solution. Queries are read-only and use
Application Insights telemetry; they do not invoke agents or models.

## Open Logs

1. Open Azure Portal and select the subscription and resource group for the
   deployment you are investigating.
2. Open that deployment's **Application Insights** resource, then **Logs**.
3. Switch to **KQL mode** if the portal opens the simple query editor.
4. Paste one complete query at a time and select **Run**. Include any `let`
   statements above the query, without a blank line separating them.

The examples use a 24-hour window. Adjust every `ago(24h)` in a query together
and ensure the portal time picker includes that interval. Allow a few minutes
for telemetry ingestion after submitting a review.

These examples use Application Insights table names. If querying the **Log
Analytics workspace** directly, the equivalents are `AppRequests`,
`AppDependencies`, `AppTraces`, `AppExceptions`, and `AppEvents`. Column names
also differ: for example, `TimeGenerated`, `Properties`, `OperationId`, `Id`,
`ParentId`, `AppRoleName`, and `DurationMs`. Run the examples from the Application
Insights resource to avoid mixing the two schemas.

## Telemetry Map

| Table | What to investigate |
| :--- | :--- |
| `requests` | Incoming backend and hosted-agent HTTP requests, status, duration, hosted identity |
| `dependencies` | Model calls (`chat`), agent spans (`invoke_agent`), tools (`execute_tool`), backend phases |
| `traces` | Application and agent log messages |
| `exceptions` | Captured exceptions and exception types |
| `customEvents` | Evaluation events, if evaluation logging is configured |

The four hosted-agent names are:

- `clinical-reviewer-agent`
- `coverage-assessment-agent`
- `compliance-agent`
- `synthesis-agent`

The backend uses the role name `prior-auth-backend`. Agent role names can be
shared or host-assigned: do not rely on `cloud_RoleName` alone to identify an
agent. On **requests**, prefer `gen_ai.agent.name`, then
`azure.ai.agentserver.agent_name`. Some versions emit `gen_ai.agent.id` as
`<agent-name>:<version>`.

On **dependencies**, `gen_ai.agent.name` can instead be a code-level agent name.
This solution configures matching names in its agent code, but check the request
metadata before assuming that every dependency label is a deployed agent name.

`operation_Id` is the distributed trace ID, `id` is the current span ID, and
`operation_ParentId` identifies the parent span. A complete review can include
several agents in the **same trace**. Joining every request to every dependency
on `operation_Id` alone can multiply rows and inflate token totals. Use parent
span IDs for attribution or `in`/`distinct` trace filtering for investigation.

## Token Usage by Agent and Model

This is the token-usage query for agent calls. It counts only model-call spans,
not the surrounding agent or tool spans. The parent lookup supplements a missing
code-level agent name without joining all requests in the trace.

```kql
let AgentSpans =
    dependencies
    | where timestamp > ago(24h)
    | where customDimensions["gen_ai.operation.name"] == "invoke_agent"
    | summarize arg_max(timestamp, *) by operation_Id, id
    | project operation_Id, AgentSpanId = id,
        ParentAgent = tostring(customDimensions["gen_ai.agent.name"]);
dependencies
| where timestamp > ago(24h)
| where customDimensions["gen_ai.operation.name"] == "chat"
| summarize arg_max(timestamp, *) by operation_Id, id
| extend
    InputTokens = tolong(customDimensions["gen_ai.usage.input_tokens"]),
    OutputTokens = tolong(customDimensions["gen_ai.usage.output_tokens"]),
    Model = tostring(customDimensions["gen_ai.request.model"])
| where isnotnull(InputTokens) or isnotnull(OutputTokens)
| join kind=leftouter AgentSpans on
    operation_Id, $left.operation_ParentId == $right.AgentSpanId
| extend Agent = coalesce(
    tostring(customDimensions["gen_ai.agent.name"]),
    ParentAgent,
    "Unattributed")
| summarize
    ModelCalls = count(),
    InputTokens = sum(InputTokens),
    OutputTokens = sum(OutputTokens)
    by Agent, Model
| extend TotalTokens = InputTokens + OutputTokens
| order by TotalTokens desc
```

Interpretation:

- `ModelCalls` is not the number of submitted reviews. MCP/tool loops can cause
  multiple model calls during one agent invocation.
- `Unattributed` means tokens were recorded but no agent name was available on
  the model span or its immediate agent parent. It does not mean zero usage.
- SDK versions may emit another inference operation name. Use the discovery
  query below before extending the `chat` filter; never sum both parent agent
  usage and child model usage for the same inference.
- Missing token attributes, retries, incomplete traces, and sampling affect the
  totals. The query counts observed telemetry without sampling extrapolation;
  use Azure billing/model metrics for authoritative consumption and cost.
- Cached input tokens are generally a subset of input tokens, not extra tokens
  to add again. Reasoning-token breakdowns depend on the SDK/model. Inspect the
  emitted dimensions before calculating separate cached or reasoning totals.

## Token Usage Over Time

Use this for an input/output token chart across all captured model calls.

```kql
dependencies
| where timestamp > ago(24h)
| where customDimensions["gen_ai.operation.name"] == "chat"
| summarize arg_max(timestamp, *) by operation_Id, id
| extend
    InputTokens = tolong(customDimensions["gen_ai.usage.input_tokens"]),
    OutputTokens = tolong(customDimensions["gen_ai.usage.output_tokens"])
| where isnotnull(InputTokens) or isnotnull(OutputTokens)
| summarize InputTokens = sum(InputTokens), OutputTokens = sum(OutputTokens)
    by bin(timestamp, 1h)
| order by timestamp asc
| render timechart
```

## Individual Model Calls and Response IDs

Use this to find expensive calls, token-heavy responses, and a trace ID for
further investigation. No prompt or response body is projected.

```kql
dependencies
| where timestamp > ago(24h)
| where customDimensions["gen_ai.operation.name"] == "chat"
| extend
    Agent = tostring(customDimensions["gen_ai.agent.name"]),
    Model = tostring(customDimensions["gen_ai.request.model"]),
    ResponseId = tostring(customDimensions["gen_ai.response.id"]),
    InputTokens = tolong(customDimensions["gen_ai.usage.input_tokens"]),
    OutputTokens = tolong(customDimensions["gen_ai.usage.output_tokens"]),
    FinishReasons = tostring(customDimensions["gen_ai.response.finish_reasons"]),
    ErrorType = tostring(customDimensions["error.type"])
| project timestamp, Agent, Model, duration, success, resultCode,
    InputTokens, OutputTokens, FinishReasons, ErrorType,
    ResponseId, operation_Id, id, operation_ParentId
| order by timestamp desc
| take 200
```

A hosted-agent response ID (`caresp_...`) and its downstream model response ID
(`resp_...` or `chatcmpl-...`) are different identifiers. Use `operation_Id` and
span relationships to connect them; do not assume the strings match.

## Hosted-Agent Request History

Change `SelectedAgent` to one of the four agent names. This starts from the
hosted-agent request, not a backend dispatch or model-call span.

```kql
let SelectedAgent = "coverage-assessment-agent";
requests
| where timestamp > ago(24h)
| extend
    AgentId = tostring(customDimensions["gen_ai.agent.id"]),
    Agent = coalesce(
        tostring(customDimensions["gen_ai.agent.name"]),
        tostring(customDimensions["azure.ai.agentserver.agent_name"]),
        tostring(split(tostring(customDimensions["gen_ai.agent.id"]), ":")[0]))
| where Agent == SelectedAgent
| extend
    AgentVersion = iff(AgentId contains ":", tostring(split(AgentId, ":")[1]), ""),
    ConversationId = coalesce(
        tostring(customDimensions["gen_ai.conversation.id"]),
        tostring(customDimensions["azure.ai.agentserver.conversation_id"])),
    ResponseId = coalesce(
        tostring(customDimensions["azure.ai.agentserver.response_id"]),
        tostring(customDimensions["gen_ai.response.id"]))
| project timestamp, Agent, AgentVersion, name, duration, success,
    resultCode, ConversationId, ResponseId, operation_Id, id
| order by timestamp desc
| take 200
```

HTTP success does not guarantee a completed clinical review: an HTTP 200 response
may contain an agent-level failure, incomplete output, or a request for tool
approval. Inspect model finish reasons, exceptions, and the application's review
result as well as request status.

## Find a Trace by Response or Conversation ID

Replace the placeholder with an exact response ID or conversation ID. The first
stage finds trace IDs; the second returns metadata across those traces without
duplicating spans through a many-to-many join.

```kql
let LookupId = "REPLACE_WITH_RESPONSE_OR_CONVERSATION_ID";
let MatchingTraces =
    union requests, dependencies, traces
    | where timestamp > ago(24h)
    | where tostring(customDimensions["gen_ai.response.id"]) == LookupId
        or tostring(customDimensions["azure.ai.agentserver.response_id"]) == LookupId
        or tostring(customDimensions["gen_ai.conversation.id"]) == LookupId
        or tostring(customDimensions["azure.ai.agentserver.conversation_id"]) == LookupId
    | where isnotempty(operation_Id)
    | distinct operation_Id;
union withsource=TelemetryTable requests, dependencies, traces, exceptions
| where timestamp > ago(24h)
| where operation_Id in (MatchingTraces)
| extend
    GenAiOperation = tostring(customDimensions["gen_ai.operation.name"]),
    Agent = coalesce(
        tostring(customDimensions["gen_ai.agent.name"]),
        tostring(customDimensions["azure.ai.agentserver.agent_name"])),
    ErrorType = tostring(customDimensions["error.type"])
| project timestamp, TelemetryTable, cloud_RoleName, Agent, name,
    GenAiOperation, duration, success, resultCode, ErrorType,
    operation_Id, id, operation_ParentId
| order by timestamp asc
| take 500
```

If the lookup is empty, the ID may not have been exported or the trace may be
outside the time window. Start with request history to obtain `operation_Id`.

## End-to-End Timeline for One Review

Copy a trace ID from the previous queries. This shows backend, hosted-agent,
model, and tool spans together when distributed trace context is preserved.

```kql
let TraceId = "REPLACE_WITH_OPERATION_ID";
union withsource=TelemetryTable requests, dependencies
| where timestamp > ago(24h)
| where operation_Id == TraceId
| extend
    Operation = tostring(customDimensions["gen_ai.operation.name"]),
    Agent = tostring(customDimensions["gen_ai.agent.name"]),
    Tool = tostring(customDimensions["gen_ai.tool.name"]),
    InputTokens = tolong(customDimensions["gen_ai.usage.input_tokens"]),
    OutputTokens = tolong(customDimensions["gen_ai.usage.output_tokens"])
| project timestamp, TelemetryTable, cloud_RoleName, name, Operation,
    Agent, Tool, duration, success, resultCode, InputTokens, OutputTokens,
    id, operation_ParentId
| order by timestamp asc
```

Do not sum durations in this timeline as end-to-end latency: spans can be nested
or parallel. Compliance and clinical run in parallel; coverage follows clinical,
then synthesis and backend audit generation. Use the backend request duration
for end-to-end wall-clock time.

If trace context is not propagated, the backend and hosted agent can have
different trace IDs. Correlate using emitted response/conversation IDs and the
timestamp range; a missing join is not proof that the agent was never called.

## Failed Requests and Dependencies

Inspect 403/permission failures, 424/agent-session readiness failures,
429/throttling, and other failed operations. Status-code meanings depend on the
endpoint; use the associated exception/logs to establish the cause.

```kql
union withsource=TelemetryTable requests, dependencies
| where timestamp > ago(24h)
| extend ErrorType = tostring(customDimensions["error.type"])
| where success == false
    or resultCode in ("401", "403", "424", "429", "500", "502", "503", "504")
    or isnotempty(ErrorType)
| project timestamp, TelemetryTable, cloud_RoleName, name,
    resultCode, duration, ErrorType, operation_Id, id, operation_ParentId
| order by timestamp desc
| take 200
```

## Exception Summary

This groups exception types without displaying potentially sensitive exception
messages. Filter a known `operation_Id` before inspecting exception details.

```kql
exceptions
| where timestamp > ago(24h)
| summarize CapturedExceptions = count(), LastSeen = max(timestamp)
    by cloud_RoleName, type, problemId
| order by CapturedExceptions desc
```

## Latency and Failure Rate by Operation

Compare model inference, agent invocation, and tool execution. Counts reflect
captured spans, not unique user requests; role names are not agent identities.

```kql
dependencies
| where timestamp > ago(24h)
| extend Operation = tostring(customDimensions["gen_ai.operation.name"])
| where Operation in ("chat", "invoke_agent", "execute_tool")
| summarize
    Calls = count(),
    Failures = countif(success == false),
    AverageMs = avg(duration),
    P50Ms = percentile(duration, 50),
    P95Ms = percentile(duration, 95)
    by cloud_RoleName, Operation
| extend FailurePercent = round(100.0 * Failures / Calls, 2)
| order by P95Ms desc
```

## MCP and Skill Tool Calls

The `execute_tool` operation includes both remote MCP tools and local skill
tools. Inspect `ToolType` and `Tool` before classifying a failure as remote MCP.
This query intentionally omits tool arguments and results.

```kql
dependencies
| where timestamp > ago(24h)
| where customDimensions["gen_ai.operation.name"] == "execute_tool"
| extend
    Tool = tostring(customDimensions["gen_ai.tool.name"]),
    ToolType = tostring(customDimensions["gen_ai.tool.type"]),
    ToolCallId = tostring(customDimensions["gen_ai.tool.call.id"]),
    ErrorType = tostring(customDimensions["error.type"])
| project timestamp, Tool, ToolType, ToolCallId, name,
    duration, success, ErrorType, operation_Id, id, operation_ParentId
| order by timestamp desc
| take 200
```

For `cannot import name 'Client' from 'mcp'`, discovery can fail before any
`execute_tool` span exists. Check agent/session logs and the dependency smoke
tests documented in [Troubleshooting](troubleshooting.md).

## Backend Review Phases

Use this to identify whether delays occur before agent dispatch, during a
review phase, or in audit generation. It uses this solution's backend span
names, not a generic Foundry metric.

```kql
dependencies
| where timestamp > ago(24h)
| where cloud_RoleName == "prior-auth-backend"
| where name in (
    "prior_auth_review", "phase_1_parallel", "phase_2_coverage",
    "phase_3_synthesis", "phase_4_audit")
| extend
    ReviewId = tostring(customDimensions["request_id"]),
    Recommendation = tostring(customDimensions["recommendation"]),
    Confidence = todouble(customDimensions["confidence"])
| project timestamp, name, duration, success, ReviewId,
    Recommendation, Confidence, operation_Id, id, operation_ParentId
| order by timestamp desc
| take 200
```

Not every phase carries every attribute. An application `request_id`, a hosted
conversation ID, and an OpenTelemetry `operation_Id` are different identifiers.

## Inspect Request and Response Content Safely

The previous queries show metadata, status, IDs, latency, and usage. Application
Insights does **not** automatically retain every HTTP body or model response.
In this solution, sensitive-content recording is disabled by default through
`ENABLE_OTEL_SENSITIVE_DATA=false`. Token counts do not require enabling it.

Prior-authorization prompts and outputs can contain protected health
information (PHI). Do not enable sensitive logging in a shared/production
environment just to troubleshoot usage. For approved, isolated testing with
synthetic cases only, the agent setting `ENABLE_OTEL_SENSITIVE_DATA=true` opts
into content recording. Update the agent's deployment configuration and deploy
a new version; protect access and retention, then disable it after testing.

The following query reads recorded agent messages **only if the deployed SDK
exported them**. It cannot recover historical content that was never logged.
Replace the trace ID and do not export results containing patient information.

```kql
let TraceId = "REPLACE_WITH_APPROVED_SYNTHETIC_TEST_OPERATION_ID";
dependencies
| where timestamp > ago(24h)
| where operation_Id == TraceId
| where customDimensions["gen_ai.operation.name"] == "invoke_agent"
| extend
    Agent = tostring(customDimensions["gen_ai.agent.name"]),
    InputMessages = tostring(customDimensions["gen_ai.input.messages"]),
    OutputMessages = tostring(customDimensions["gen_ai.output.messages"])
| where isnotempty(InputMessages) or isnotempty(OutputMessages)
| project timestamp, Agent, InputMessages, OutputMessages, operation_Id, id
| order by timestamp asc
```

Recorded content may be truncated, redacted, or exported as events rather than
span attributes in another SDK version. For normal operation, use the frontend
review dashboard and application results rather than turning telemetry into a
patient-record store. Model responses may end in tool calls or incomplete
results; a transport-level success is not a final recommendation.

## Discover Available Fields or Investigate Empty Results

First verify that data is arriving and identify its role names:

```kql
union withsource=TelemetryTable requests, dependencies, traces, exceptions
| where timestamp > ago(24h)
| summarize Rows = count(), LastSeen = max(timestamp)
    by TelemetryTable, cloud_RoleName
| order by LastSeen desc
```

Then discover which operation names and attribute keys are present without
printing their values or message bodies:

```kql
dependencies
| where timestamp > ago(24h)
| where isnotempty(customDimensions["gen_ai.operation.name"])
| extend Operation = tostring(customDimensions["gen_ai.operation.name"])
| mv-expand Attribute = bag_keys(customDimensions)
| summarize Spans = dcount(id) by Operation, Attribute = tostring(Attribute)
| order by Operation asc, Attribute asc
```

If data is missing:

1. Confirm you opened App Insights for the active deployment, not a resource in
   the old subscription or environment.
2. Submit a synthetic test review and allow time for ingestion.
3. Check that the backend has `APPLICATION_INSIGHTS_CONNECTION_STRING` and the
   agents have the explicit `OTEL_CONNECTION_STRING` value. Do not paste either
   value into shared logs or support tickets.
4. Confirm the agents run the current image and enable instrumentation before
   constructing `Agent` and `ResponsesHostServer`.
5. Check sampling, retention, time range, and workspace access permissions.
6. If hosted-agent requests exist but model/tool spans do not, check the
   in-container instrumentation and SDK dependencies. Platform telemetry alone
   does not guarantee every Agent Framework span is exported.

These queries follow documented telemetry conventions and repository
instrumentation. They have not been executed against your live deployment;
available dimensions and completeness depend on the deployed SDK versions.

## References

- [Solution architecture](architecture.md)
- [Observability implementation notes](technical-notes.md#observability)
- [Deployment and runtime troubleshooting](troubleshooting.md)
- [Application Insights log-based metrics](https://learn.microsoft.com/azure/azure-monitor/app/standard-metrics)
- [GenAI span conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/)
- [GenAI agent span conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/)
