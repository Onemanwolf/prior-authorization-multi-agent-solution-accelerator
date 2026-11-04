"use client";

import { useEffect, useState } from "react";
import {
  CheckCircle2,
  Circle,
  Loader2,
  AlertCircle,
  ShieldCheck,
  Stethoscope,
  FileSearch,
  Brain,
  ClipboardList,
  Timer,
} from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type {
  ReviewProgress,
  PhaseId,
  AgentId,
  AgentStatus,
} from "@/lib/types";

interface ProgressTrackerProps {
  progress: ReviewProgress;
}

const PHASE_CONFIG: Record<
  PhaseId,
  { label: string; description: string }
> = {
  preflight: {
    label: "Pre-flight Validation",
    description: "Validating procedure code formats",
  },
  phase_1: {
    label: "Phase 1: Parallel Analysis",
    description: "Compliance + Clinical Reviewer agents",
  },
  phase_2: {
    label: "Phase 2: Coverage Review",
    description: "Provider verification + coverage criteria",
  },
  phase_3: {
    label: "Phase 3: Decision Synthesis",
    description: "Applying gate-based decision rubric",
  },
  phase_4: {
    label: "Phase 4: Audit Trail",
    description: "Building audit trail and justification",
  },
};

const PHASE_ORDER: PhaseId[] = [
  "preflight",
  "phase_1",
  "phase_2",
  "phase_3",
  "phase_4",
];

const AGENT_CONFIG: Record<
  AgentId,
  { label: string; icon: typeof ShieldCheck }
> = {
  compliance: { label: "Compliance Agent", icon: ClipboardList },
  clinical: { label: "Clinical Reviewer", icon: Stethoscope },
  coverage: { label: "Coverage Agent", icon: FileSearch },
  synthesis: { label: "Synthesis Engine", icon: Brain },
};

function PhaseIcon({
  status,
}: {
  status: "pending" | "running" | "completed";
}) {
  if (status === "completed") {
    return <CheckCircle2 className="h-5 w-5 text-success shrink-0" />;
  }
  if (status === "running") {
    return <Loader2 className="h-5 w-5 text-brand animate-spin shrink-0" />;
  }
  return <Circle className="h-5 w-5 text-gray-300 shrink-0" />;
}

function AgentStatusBadge({ status }: { status: AgentStatus }) {
  if (status === "done") {
    return <Badge variant="success">Done</Badge>;
  }
  if (status === "running") {
    return (
      <Badge variant="default" className="bg-brand">
        Active
      </Badge>
    );
  }
  if (status === "error") {
    return <Badge variant="destructive">Error</Badge>;
  }
  return <Badge variant="secondary">Waiting</Badge>;
}

function ElapsedTimer() {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(interval);
  }, []);

  const mins = Math.floor(elapsed / 60);
  const secs = elapsed % 60;
  return (
    <span className="font-mono text-sm text-muted-foreground">
      {mins}:{secs.toString().padStart(2, "0")}
    </span>
  );
}

export function ProgressTracker({ progress }: ProgressTrackerProps) {
  // Determine which agents to display based on the active phases
  const visibleAgents = getVisibleAgents(progress);

  return (
    <Card className="border-brand/30 bg-gradient-to-b from-info-light/50 to-white">
      <CardContent className="pt-6 space-y-5">
        {/* Header: progress bar + percentage + timer */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-5 w-5 text-brand" />
              <span className="font-semibold text-sm">
                Multi-Agent Review in Progress
              </span>
            </div>
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1.5 text-muted-foreground">
                <Timer className="h-4 w-4" />
                <ElapsedTimer />
              </div>
              <Badge variant="outline" className="font-mono">
                {progress.progressPct}%
              </Badge>
            </div>
          </div>
          <Progress value={progress.progressPct} className="h-2.5" />
          <p className="text-xs text-muted-foreground">{progress.message}</p>
        </div>

        {/* Phase timeline */}
        <div className="space-y-0">
          {PHASE_ORDER.map((phaseId, idx) => {
            const phase = PHASE_CONFIG[phaseId];
            const status = progress.phases[phaseId];
            const isLast = idx === PHASE_ORDER.length - 1;

            return (
              <div key={phaseId} className="flex gap-3">
                {/* Vertical connector + icon */}
                <div className="flex flex-col items-center">
                  <PhaseIcon status={status} />
                  {!isLast && (
                    <div
                      className={`w-0.5 flex-1 min-h-4 ${
                        status === "completed"
                          ? "bg-success/50"
                          : status === "running"
                            ? "bg-brand/50"
                            : "bg-gray-200"
                      }`}
                    />
                  )}
                </div>

                {/* Phase content */}
                <div className="pb-4 flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span
                      className={`text-sm font-medium ${
                        status === "pending"
                          ? "text-gray-400"
                          : status === "running"
                            ? "text-brand"
                            : "text-foreground"
                      }`}
                    >
                      {phase.label}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {phase.description}
                  </p>

                  {/* Agent cards for this phase */}
                  {status !== "pending" && (
                    <div className="mt-2 space-y-1.5">
                      {getAgentsForPhase(phaseId).map((agentId) => {
                        if (!visibleAgents.has(agentId)) return null;
                        const agent = AGENT_CONFIG[agentId];
                        const agentState = progress.agents[agentId];
                        const AgentIcon = agent.icon;

                        return (
                          <div
                            key={agentId}
                            className={`flex items-center gap-2 rounded-md border px-3 py-2 text-sm ${
                              agentState.status === "running"
                                ? "border-brand/30 bg-info-light/50"
                                : agentState.status === "done"
                                  ? "border-success/30 bg-success-light/30"
                                  : agentState.status === "error"
                                    ? "border-destructive/30 bg-destructive/5"
                                    : "border-gray-100 bg-gray-50/30"
                            }`}
                          >
                            <AgentIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
                            <span className="font-medium truncate">
                              {agent.label}
                            </span>
                            <span className="text-xs text-muted-foreground truncate flex-1 ml-1">
                              {agentState.detail}
                            </span>
                            <AgentStatusBadge status={agentState.status} />
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        {/* Error display */}
        {progress.error && (
          <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
            <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
            <span>{progress.error}</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function getAgentsForPhase(phaseId: PhaseId): AgentId[] {
  switch (phaseId) {
    case "phase_1":
      return ["compliance", "clinical"];
    case "phase_2":
      return ["coverage"];
    case "phase_3":
      return ["synthesis"];
    default:
      return [];
  }
}

function getVisibleAgents(progress: ReviewProgress): Set<AgentId> {
  const visible = new Set<AgentId>();
  for (const agentId of Object.keys(progress.agents) as AgentId[]) {
    if (progress.agents[agentId].status !== "pending") {
      visible.add(agentId);
    }
  }
  return visible;
}
