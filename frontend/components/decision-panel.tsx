"use client";

import { useState, useMemo } from "react";
import { toast } from "sonner";
import { Check, ArrowRightLeft, Download, Loader2, Gavel, Award, FileText, CheckCircle2, AlertTriangle } from "lucide-react";
import { submitDecision } from "@/lib/api";
import type { ReviewResponse, DecisionResponse } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface Props {
  review: ReviewResponse;
  onDecision?: (decision: DecisionResponse) => void;
}

type Mode = "initial" | "override" | "submitted";

export function DecisionPanel({ review, onDecision }: Props) {
  const [mode, setMode] = useState<Mode>("initial");
  const [reviewerName, setReviewerName] = useState("");
  const [overrideRec, setOverrideRec] = useState<"approve" | "pend_for_review">(
    review.recommendation === "approve" ? "pend_for_review" : "approve"
  );
  const [overrideRationale, setOverrideRationale] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [decision, setDecision] = useState<DecisionResponse | null>(null);

  const isApproval = decision?.final_recommendation === "approve";

  // Build a blob URL for the PDF viewer when we have PDF data
  const pdfBlobUrl = useMemo(() => {
    if (!decision?.letter.pdf_base64) return null;
    try {
      const byteChars = atob(decision.letter.pdf_base64);
      const byteNumbers = new Uint8Array(byteChars.length);
      for (let i = 0; i < byteChars.length; i++) {
        byteNumbers[i] = byteChars.charCodeAt(i);
      }
      const blob = new Blob([byteNumbers], { type: "application/pdf" });
      return URL.createObjectURL(blob);
    } catch {
      return null;
    }
  }, [decision?.letter.pdf_base64]);

  const handleAccept = async () => {
    if (!reviewerName.trim()) {
      setError("Reviewer name is required");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const resp = await submitDecision({
        request_id: review.request_id,
        action: "accept",
        reviewer_name: reviewerName.trim(),
      });
      setDecision(resp);
      setMode("submitted");
      onDecision?.(resp);
      toast.success("Decision recorded", {
        description: `Auth #${resp.authorization_number}`,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Decision failed");
      toast.error("Decision failed");
    } finally {
      setLoading(false);
    }
  };

  const handleOverrideSubmit = async () => {
    if (!reviewerName.trim()) {
      setError("Reviewer name is required");
      return;
    }
    if (!overrideRationale.trim()) {
      setError("Override rationale is required");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const resp = await submitDecision({
        request_id: review.request_id,
        action: "override",
        override_recommendation: overrideRec,
        override_rationale: overrideRationale.trim(),
        reviewer_name: reviewerName.trim(),
      });
      setDecision(resp);
      setMode("submitted");
      onDecision?.(resp);
      toast.success("Override recorded", {
        description: `Auth #${resp.authorization_number}`,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Decision failed");
      toast.error("Override failed");
    } finally {
      setLoading(false);
    }
  };

  const handleDownload = () => {
    if (!decision) return;
    if (decision.letter.pdf_base64) {
      const byteChars = atob(decision.letter.pdf_base64);
      const byteNumbers = new Uint8Array(byteChars.length);
      for (let i = 0; i < byteChars.length; i++) {
        byteNumbers[i] = byteChars.charCodeAt(i);
      }
      const blob = new Blob([byteNumbers], { type: "application/pdf" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${decision.authorization_number}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } else {
      const blob = new Blob([decision.letter.body_text], {
        type: "text/plain",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${decision.authorization_number}.txt`;
      a.click();
      URL.revokeObjectURL(url);
    }
  };

  if (mode === "submitted" && decision) {
    return (
      <Card className="mt-6 bg-muted/30 shadow-sm">
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Award className="h-5 w-5 text-success" />
            Decision Recorded
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-2">
            <Badge variant="success" className="text-sm px-3 py-1.5">
              Auth #: {decision.authorization_number}
            </Badge>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold">Final Recommendation:</span>
            <Badge
              variant={isApproval ? "success" : "warning"}
              className="text-sm px-3 py-1.5 font-bold"
            >
              {isApproval ? (
                <CheckCircle2 className="mr-1.5 h-4 w-4" />
              ) : (
                <AlertTriangle className="mr-1.5 h-4 w-4" />
              )}
              {decision.final_recommendation.replace(/_/g, " ").toUpperCase()}
            </Badge>
            {decision.was_overridden && (
              <Badge variant="outline" className="text-warning border-warning/50">
                Overridden
              </Badge>
            )}
          </div>
          {decision.was_overridden && decision.override_rationale && (
            <div className="rounded-md border border-warning/30 bg-warning/5 p-3 space-y-1">
              <p className="text-sm font-semibold text-warning">Clinician Override</p>
              {decision.original_recommendation && (
                <p className="text-xs text-muted-foreground">
                  Original AI Recommendation:{" "}
                  <span className="font-medium">
                    {decision.original_recommendation.replace(/_/g, " ").toUpperCase()}
                  </span>
                </p>
              )}
              <p className="text-sm">{decision.override_rationale}</p>
            </div>
          )}
          <div>
            <p className="text-sm font-medium mb-2 flex items-center gap-1.5">
              <FileText className="h-3.5 w-3.5 text-muted-foreground" />
              Notification Letter
            </p>
            {pdfBlobUrl ? (
              <div className="rounded-md border overflow-hidden">
                <iframe
                  src={pdfBlobUrl}
                  className="w-full h-[500px]"
                  title="Notification Letter PDF"
                />
              </div>
            ) : (
              <ScrollArea className="h-[300px] rounded-md border bg-card p-4">
                <pre className="whitespace-pre-wrap font-mono text-xs">
                  {decision.letter.body_text}
                </pre>
              </ScrollArea>
            )}
          </div>
          <div className="flex items-center gap-3">
            <Button onClick={handleDownload} className="bg-gradient-to-r from-brand to-brand-dark hover:from-brand-hover hover:to-brand-hover-dark text-white shadow-sm">
              <Download className="mr-2 h-4 w-4" />
              Download Notification Letter
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Decided by: {decision.decided_by} | {decision.decided_at}
          </p>
        </CardContent>
      </Card>
    );
  }

  if (mode === "override") {
    return (
      <Card className="mt-6 bg-muted/30 shadow-sm">
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <ArrowRightLeft className="h-5 w-5 text-warning" />
            Override Recommendation
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="reviewer_override">Reviewer Name</Label>
            <Input
              id="reviewer_override"
              value={reviewerName}
              onChange={(e) => setReviewerName(e.target.value)}
              placeholder="Dr. Jane Doe"
            />
          </div>
          <div className="space-y-2">
            <Label>New Recommendation</Label>
            <Select
              value={overrideRec}
              onValueChange={(v) =>
                setOverrideRec(v as "approve" | "pend_for_review")
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="approve">Approve</SelectItem>
                <SelectItem value="pend_for_review">Pend for Review</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="rationale">Override Rationale (required)</Label>
            <Textarea
              id="rationale"
              value={overrideRationale}
              onChange={(e) => setOverrideRationale(e.target.value)}
              placeholder="Explain why you are overriding the AI recommendation..."
              className="min-h-[80px]"
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              onClick={handleOverrideSubmit}
              disabled={loading}
              className="bg-warning text-white hover:bg-warning-dark"
            >
              {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {loading ? "Submitting..." : "Submit Override"}
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setMode("initial");
                setError(null);
              }}
              disabled={loading}
            >
              Cancel
            </Button>
          </div>
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="mt-6 bg-muted/30 shadow-sm">
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <Gavel className="h-5 w-5 text-primary" />
          Reviewer Decision
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="reviewer_name">Reviewer Name</Label>
          <Input
            id="reviewer_name"
            value={reviewerName}
            onChange={(e) => setReviewerName(e.target.value)}
            placeholder="Dr. Jane Doe"
          />
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            onClick={handleAccept}
            disabled={loading}
            className="bg-success text-white hover:bg-success-dark"
          >
            {loading ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Check className="mr-2 h-4 w-4" />
            )}
            {loading ? "Submitting..." : "Accept Recommendation"}
          </Button>
          <Button
            variant="secondary"
            onClick={() => {
              setMode("override");
              setError(null);
            }}
            disabled={loading}
            className="border border-warning/50 bg-warning-light text-warning-dark hover:bg-warning/20"
          >
            <ArrowRightLeft className="mr-2 h-4 w-4" />
            Override
          </Button>
        </div>
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  );
}
