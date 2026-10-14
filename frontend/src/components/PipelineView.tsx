import { Check, Loader2, X } from "lucide-react";
import type { StageState } from "../lib/types";
import { cx } from "./ui";

export const STAGE_ORDER = ["DRONE_DATA", "INGESTION", "PREPROCESS", "AI_DETECT", "TRACKING", "SCENE_AI", "EVENT_AI", "GEO_ANALYSIS", "INTELLIGENCE", "REPORT"];
export const STAGE_LABEL: Record<string, string> = {
  DRONE_DATA: "DRONE DATA", INGESTION: "INGESTION", PREPROCESS: "PREPROCESS", AI_DETECT: "AI DETECT", TRACKING: "TRACKING",
  SCENE_AI: "SCENE AI", EVENT_AI: "EVENT AI", GEO_ANALYSIS: "GEO ANALYSIS", INTELLIGENCE: "INTELLIGENCE", REPORT: "REPORT",
};

function metricLine(stage: string, m: Record<string, any> = {}): string {
  const f = (v: any) => (v === null || v === undefined ? "—" : v);
  switch (stage) {
    case "DRONE_DATA": return m.files !== undefined ? `${m.files} file(s)` : "";
    case "INGESTION": return m.videos !== undefined ? `${m.videos} video · ${m.images} image · validated` : "";
    case "PREPROCESS": return m.frames !== undefined ? `${m.frames} frames · ${f(m.avg_ms)} ms${m.avg_quality ? ` · Q${m.avg_quality}` : ""}` : "";
    case "AI_DETECT": return m.detections !== undefined ? `${m.detections} det · ${f(m.avg_inference_ms)} ms` : m.provider ? m.provider : "";
    case "TRACKING": return m.tracks !== undefined ? `${m.tracks} tracks` : "";
    case "SCENE_AI": return m.samples !== undefined ? `${m.samples} samples${m.dominant ? ` · ${String(m.dominant).replace("_", " ")}` : ""}` : "";
    case "EVENT_AI": return m.events !== undefined ? `${m.events} events` : "";
    case "GEO_ANALYSIS": return m.route_length_m !== undefined ? `route ${Math.round(m.route_length_m)} m · ${m.geolocated_detections} geo` : "";
    case "INTELLIGENCE": return m.tracks !== undefined ? `${m.detections} det · ${m.tracks} trk · ${m.events} evt` : "";
    case "REPORT": return m.ready ? "report data ready" : "";
    default: return "";
  }
}

export default function PipelineView({ stages, vertical, compact }: { stages?: Record<string, StageState>; vertical?: boolean; compact?: boolean }) {
  const s = stages || {};
  return (
    <ol className={cx(vertical ? "flex flex-col gap-0" : "grid grid-cols-2 gap-1 sm:grid-cols-5 min-[1900px]:grid-cols-10")} aria-label="AI processing pipeline">
      {STAGE_ORDER.map((k, i) => {
        const st = s[k]?.state || "PENDING";
        const running = st === "RUNNING";
        const done = st === "DONE";
        const failed = st === "FAILED";
        const border = failed ? "border-red" : running ? "border-cyan" : done ? "border-olive-dim" : "border-line";
        const text = failed ? "text-red" : running ? "text-cyan" : done ? "text-olive" : "text-dim";
        return (
          <li key={k} className={cx("flex min-w-0", vertical ? "flex-col items-center" : "items-center")}>
            <div className={cx("relative w-full overflow-hidden border bg-void/70", border, vertical ? "px-3 py-1.5" : "px-2 py-1.5", compact && "py-1")}
              data-testid={`stage-${k}`} data-state={st}>
              {running && <div className="absolute inset-y-0 left-0 w-1/3 animate-scan bg-gradient-to-r from-transparent via-cyan/15 to-transparent" />}
              <div className="relative flex items-center gap-1.5">
                <span className={cx("flex h-3.5 w-3.5 shrink-0 items-center justify-center border", border)}>
                  {done && <Check size={9} className="text-olive" />}
                  {running && <Loader2 size={9} className="animate-spin text-cyan" />}
                  {failed && <X size={9} className="text-red" />}
                </span>
                <span className={cx("mono truncate text-[10px] tracking-[0.12em]", text)}>{STAGE_LABEL[k]}</span>
                {vertical && <span className="mono ml-auto text-[9px] text-muted">{st}</span>}
              </div>
              {!compact && <div className="mono relative mt-0.5 truncate text-[9px] text-muted">{metricLine(k, s[k]?.metrics) || " "}</div>}
            </div>
            {vertical && i < STAGE_ORDER.length - 1 && <div className={cx("h-2.5 w-px", done ? "bg-olive-dim" : "bg-line")} />}
          </li>
        );
      })}
    </ol>
  );
}
