import { ShieldCheck, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";

export function Header() {
  return (
    <header className="mb-10">
      {/* Gradient banner */}
      <div className="relative overflow-hidden rounded-xl bg-gradient-to-r from-brand via-brand-dark to-brand-darker px-6 py-8 text-white shadow-lg">
        {/* Decorative circles */}
        <div className="absolute -right-10 -top-10 h-40 w-40 rounded-full bg-white/10" />
        <div className="absolute -right-4 bottom-0 h-24 w-24 rounded-full bg-white/5" />

        <div className="relative flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg bg-white/20 backdrop-blur-sm">
            <ShieldCheck className="h-7 w-7" />
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-2xl sm:text-3xl font-bold tracking-tight">
                Prior Authorization Review
              </h1>
              <Badge className="bg-white/20 text-white border-white/30 text-[10px] uppercase tracking-widest">
                AI-Powered
              </Badge>
            </div>
            <p className="mt-2 text-sm text-blue-100/90 max-w-lg">
              Multi-agent clinical review powered by Azure OpenAI gpt-5.4 &amp; Microsoft
              Agent Framework with real-time MCP verification
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs text-blue-200/80">
              <Sparkles className="h-3 w-3" />
              <span>4 specialized AI agents</span>
              <span className="mx-1">&#183;</span>
              <span>5 MCP data sources</span>
              <span className="mx-1">&#183;</span>
              <span>Gate-based decision rubric</span>
            </div>
          </div>
        </div>
      </div>
    </header>
  );
}
