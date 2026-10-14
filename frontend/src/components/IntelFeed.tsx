import { Radio } from "lucide-react";
import { useFeed } from "../lib/feed";
import { timeOnly } from "../lib/format";
import { SEVERITY_COLORS } from "../lib/types";
import { cx, Empty, Panel } from "./ui";

const KIND_LABEL: Record<string, string> = {
  AI_OBSERVATION: "AI OBSERVATION", TRACK_UPDATE: "TRACK UPDATE", EVENT: "EVENT", SYSTEM: "SYSTEM",
  MISSION: "MISSION", REPORT: "REPORT",
};

export default function IntelFeed({ className, limit = 60 }: { className?: string; limit?: number }) {
  const { intel, connected } = useFeed();
  return (
    <Panel title="LIVE INTELLIGENCE FEED" icon={Radio} className={className} bodyClass="overflow-y-auto"
      right={<span className={cx("mono text-[9px] tracking-widest", connected ? "text-olive" : "text-amber")}>{connected ? "● STREAMING" : "○ RECONNECTING"}</span>}>
      <div className="stream-bar h-[2px] w-full opacity-60" />
      {intel.length === 0 ? (
        <Empty icon={Radio} title="AWAITING INTELLIGENCE" hint="Observations, track updates and events appear here in real time as missions are analysed." />
      ) : (
        <ol className="divide-y divide-line/70" aria-live="polite">
          {intel.slice(0, limit).map((m, i) => {
            const c = m.kind === "EVENT" ? SEVERITY_COLORS[m.severity] || "#5fb7c2" : m.kind === "AI_OBSERVATION" ? "#5fb7c2" : m.kind === "TRACK_UPDATE" ? "#8a9c55" : "#a4ad9c";
            return (
              <li key={`${m.ts}-${i}`} className={cx("px-3 py-2", i === 0 && "animate-feed-in")}>
                <div className="flex items-baseline gap-2">
                  <span className="mono text-[11px] tabular-nums text-ink">{timeOnly(m.ts)}</span>
                  <span className="mono text-[9px] tracking-[0.15em]" style={{ color: c }}>{KIND_LABEL[m.kind] || m.kind}</span>
                  {m.demo && <span className="mono ml-auto text-[8px] tracking-widest text-amber/80">SIM</span>}
                </div>
                <div className="mt-0.5 text-[12px] text-ink">{m.title}</div>
                {m.body && <div className="text-[11px] text-muted">{m.body}</div>}
                {m.mission && <div className="mono text-[9px] text-dim">{m.mission}</div>}
              </li>
            );
          })}
        </ol>
      )}
    </Panel>
  );
}
