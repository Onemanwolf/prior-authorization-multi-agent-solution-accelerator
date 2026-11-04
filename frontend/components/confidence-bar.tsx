"use client";

import { useEffect, useState } from "react";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

interface ConfidenceBarProps {
  value: number; // 0-100
  className?: string;
  showLabel?: boolean;
}

export function ConfidenceBar({
  value,
  className,
  showLabel = true,
}: ConfidenceBarProps) {
  const [animated, setAnimated] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => setAnimated(value), 50);
    return () => clearTimeout(timer);
  }, [value]);

  const color =
    value >= 80
      ? "[&>[data-slot=progress-indicator]]:bg-success"
      : value >= 50
        ? "[&>[data-slot=progress-indicator]]:bg-warning"
        : "[&>[data-slot=progress-indicator]]:bg-destructive";

  return (
    <div className={cn("flex items-center gap-2", className)}>
      <Progress
        value={animated}
        className={cn(
          "h-2 flex-1 [&>[data-slot=progress-indicator]]:transition-transform [&>[data-slot=progress-indicator]]:duration-700 [&>[data-slot=progress-indicator]]:ease-out",
          color,
        )}
      />
      {showLabel && (
        <span className="text-xs font-medium tabular-nums text-muted-foreground w-10 text-right">
          {Math.round(value)}%
        </span>
      )}
    </div>
  );
}
