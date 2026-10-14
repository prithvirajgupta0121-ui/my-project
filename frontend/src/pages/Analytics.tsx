import { BarChart3, Brain, Cpu, Siren, Waypoints } from "lucide-react";
import type { ReactNode } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import MissionPicker, { useMissionParam } from "../components/MissionPicker";
import { Empty, ErrorNote, Panel, ScanLoader, Stat } from "../components/ui";
import { human, timeOnly } from "../lib/format";
import { useApi } from "../lib/hooks";
import { SEVERITY_COLORS } from "../lib/types";

const AX = { stroke: "#75806f", fontSize: 10, tickLine: false, axisLine: { stroke: "#34413a" } } as const;
const GRID = <CartesianGrid stroke="#253027" strokeDasharray="2 4" vertical={false} />;
const TIP = { contentStyle: { background: "#0f1310", border: "1px solid #34413a", fontSize: 11, color: "#d5dbcf" }, cursor: { fill: "rgba(138,156,85,0.08)" } };
const OLIVE = "#8a9c55", CYAN = "#5fb7c2";

function Chart({ title, icon, children, h = "h-52", note }: { title: string; icon?: any; children: ReactNode; h?: string; note?: string }) {
  return (
    <Panel title={title} icon={icon} bodyClass={`${h} p-2`} right={note && <span className="mono text-[9px] text-amber">{note}</span>}>
      <ResponsiveContainer width="100%" height="100%">{children as any}</ResponsiveContainer>
    </Panel>
  );
}

export default function Analytics() {
  const [mission, setMission, missions] = useMissionParam(false);
  const a = useApi<any>(`/api/analytics${mission !== null ? `?mission_id=${mission}` : ""}`, { poll: 15000, deps: [mission] });
  const d = a.data;
  const sys = d?.system;
  const samples = (sys?.samples || []).map((s: any) => ({ t: timeOnly(s.ts), cpu: s.cpu_percent, mem: s.memory_percent, inf: s.inference_ms_avg }));

  return (
    <div className="flex h-full flex-col gap-2 overflow-y-auto p-2">
      <div className="panel brackets flex flex-wrap items-center gap-2 px-3 py-1.5">
        <BarChart3 size={14} className="text-olive" /><span className="label !text-ink">ANALYTICS CENTER</span>
        <MissionPicker value={mission} onChange={setMission} missions={missions} allowAll />
        <span className="mono ml-auto text-[9px] text-amber">DETECTION / TRACK / EVENT FIGURES ARE AI OBSERVATIONS — COUNTS INCLUDE UNVERIFIED ITEMS</span>
      </div>
      {a.error && <ErrorNote error={a.error} onRetry={() => a.reload()} />}
      {!d ? <ScanLoader label="COMPUTING ANALYTICS" /> : d.empty ? <Empty icon={BarChart3} title="NO DATA" hint="Process a mission to populate analytics." /> : (
        <>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
            <Stat label="DETECTIONS" value={d.detection.total.toLocaleString()} tone="cyan" icon={Brain} />
            <Stat label="CLASSES" value={d.detection.per_class.length} />
            <Stat label="TRACKS" value={d.tracking.total} tone="cyan" icon={Waypoints} />
            <Stat label="ACTIVE TRACKS" value={d.tracking.active} />
            <Stat label="EVENTS" value={d.events.total} tone="amber" icon={Siren} />
            <Stat label="CONFIRMED" value={d.events.review.find((r: any) => r.status === "CONFIRMED")?.count ?? 0} tone="olive" />
            <Stat label="CPU" value={`${Math.round(sys.current.cpu_percent)}%`} icon={Cpu} />
            <Stat label="GPU" value={sys.current.gpu.available ? "CUDA" : "N/A"} sub={sys.current.gpu.available ? "" : "CPU inference"} tone={sys.current.gpu.available ? "olive" : "amber"} />
          </div>

          <div className="label mt-1 px-1">DETECTION ANALYTICS</div>
          <div className="grid grid-cols-1 gap-2 lg:grid-cols-3">
            <Chart title="OBJECTS PER CLASS" icon={Brain}>
              <BarChart data={d.detection.per_class} layout="vertical" margin={{ left: 10, right: 16 }}>
                {GRID}<XAxis type="number" {...AX} /><YAxis type="category" dataKey="cls" {...AX} width={80} />
                <Tooltip {...TIP} /><Bar dataKey="count" name="Detections" fill={OLIVE} radius={[0, 3, 3, 0]} isAnimationActive={false} />
              </BarChart>
            </Chart>
            <Chart title="CONFIDENCE DISTRIBUTION">
              <BarChart data={d.detection.confidence_hist}>
                {GRID}<XAxis dataKey="bin" {...AX} interval={1} /><YAxis {...AX} width={36} />
                <Tooltip {...TIP} /><Bar dataKey="count" name="Detections" fill={CYAN} radius={[3, 3, 0, 0]} isAnimationActive={false} />
              </BarChart>
            </Chart>
            <Chart title="DETECTION FREQUENCY (PER 5 s OF MISSION TIME)">
              <LineChart data={d.detection.frequency}>
                {GRID}<XAxis dataKey="t" {...AX} tickFormatter={(v) => `${v}s`} /><YAxis {...AX} width={36} />
                <Tooltip {...TIP} labelFormatter={(l) => `T+${l}s`} /><Line dataKey="count" name="Detections" stroke={CYAN} strokeWidth={2} dot={false} isAnimationActive={false} />
              </LineChart>
            </Chart>
          </div>

          <div className="label mt-1 px-1">TRACKING ANALYTICS</div>
          <div className="grid grid-cols-1 gap-2 lg:grid-cols-3">
            <Chart title="TRACK DURATION" icon={Waypoints}>
              <BarChart data={d.tracking.duration_hist}>
                {GRID}<XAxis dataKey="bin" {...AX} /><YAxis {...AX} width={30} allowDecimals={false} />
                <Tooltip {...TIP} /><Bar dataKey="count" name="Tracks" fill={OLIVE} radius={[3, 3, 0, 0]} isAnimationActive={false} />
              </BarChart>
            </Chart>
            <Chart title="OBSERVATIONS — TOP TRACKS">
              <BarChart data={d.tracking.observations}>
                {GRID}<XAxis dataKey="track" {...AX} interval={2} /><YAxis {...AX} width={30} />
                <Tooltip {...TIP} formatter={(v: any, _n: any, p: any) => [`${v} (${p.payload.cls}, ${p.payload.duration}s)`, "Observations"]} />
                <Bar dataKey="observations" name="Observations" fill={CYAN} radius={[3, 3, 0, 0]} isAnimationActive={false} />
              </BarChart>
            </Chart>
            <Chart title="TRACKS BY CLASS">
              <BarChart data={Object.entries(d.tracking.by_class).map(([k, v]) => ({ cls: k, count: v }))} layout="vertical" margin={{ left: 10, right: 16 }}>
                {GRID}<XAxis type="number" {...AX} allowDecimals={false} /><YAxis type="category" dataKey="cls" {...AX} width={80} />
                <Tooltip {...TIP} /><Bar dataKey="count" name="Tracks" fill={OLIVE} radius={[0, 3, 3, 0]} isAnimationActive={false} />
              </BarChart>
            </Chart>
          </div>

          <div className="label mt-1 px-1">EVENT ANALYTICS</div>
          <div className="grid grid-cols-1 gap-2 lg:grid-cols-3">
            <Chart title="EVENTS OVER TIME (BY SEVERITY)" icon={Siren}>
              <BarChart data={d.events.over_time}>
                {GRID}<XAxis dataKey="t" {...AX} tickFormatter={(v) => `${v}s`} /><YAxis {...AX} width={30} allowDecimals={false} />
                <Tooltip {...TIP} labelFormatter={(l) => `T+${l}s`} /><Legend wrapperStyle={{ fontSize: 10 }} />
                {["INFO", "WARNING", "HIGH", "CRITICAL"].map((s) => <Bar key={s} dataKey={s} stackId="s" fill={SEVERITY_COLORS[s]} stroke="#0f1310" strokeWidth={1} isAnimationActive={false} />)}
              </BarChart>
            </Chart>
            <Chart title="EVENT TYPES">
              <BarChart data={d.events.by_type.map((x: any) => ({ ...x, label: human(x.type) }))} layout="vertical" margin={{ left: 10, right: 16 }}>
                {GRID}<XAxis type="number" {...AX} allowDecimals={false} /><YAxis type="category" dataKey="label" {...AX} width={150} />
                <Tooltip {...TIP} /><Bar dataKey="count" name="Events" fill={OLIVE} radius={[0, 3, 3, 0]} isAnimationActive={false} />
              </BarChart>
            </Chart>
            <Chart title="REVIEW STATUS (HUMAN-IN-THE-LOOP)">
              <BarChart data={d.events.review.map((x: any) => ({ ...x, label: human(x.status) }))}>
                {GRID}<XAxis dataKey="label" {...AX} /><YAxis {...AX} width={30} allowDecimals={false} />
                <Tooltip {...TIP} /><Bar dataKey="count" name="Events" fill={CYAN} radius={[3, 3, 0, 0]} isAnimationActive={false} />
              </BarChart>
            </Chart>
          </div>

          <div className="label mt-1 px-1">SYSTEM ANALYTICS · REAL LOCAL METRICS</div>
          <div className="grid grid-cols-1 gap-2 lg:grid-cols-3">
            <Chart title="CPU & MEMORY (%)" icon={Cpu}>
              <LineChart data={samples}>
                {GRID}<XAxis dataKey="t" {...AX} minTickGap={40} /><YAxis {...AX} width={30} domain={[0, 100]} />
                <Tooltip {...TIP} /><Legend wrapperStyle={{ fontSize: 10 }} />
                <Line dataKey="cpu" name="CPU %" stroke={CYAN} strokeWidth={2} dot={false} isAnimationActive={false} />
                <Line dataKey="mem" name="Memory %" stroke={OLIVE} strokeWidth={2} dot={false} isAnimationActive={false} />
              </LineChart>
            </Chart>
            <Chart title="AI INFERENCE TIME (ms / frame, recent)">
              <LineChart data={(sys.inference_recent || []).map((v: number, i: number) => ({ i, ms: Math.round(v * 10) / 10 }))}>
                {GRID}<XAxis dataKey="i" {...AX} hide /><YAxis {...AX} width={36} />
                <Tooltip {...TIP} labelFormatter={() => "Inference"} /><Line dataKey="ms" name="ms" stroke={CYAN} strokeWidth={2} dot={false} isAnimationActive={false} />
              </LineChart>
            </Chart>
            <Chart title="PROCESSING FPS / TIME PER MISSION">
              <BarChart data={sys.processing}>
                {GRID}<XAxis dataKey="code" {...AX} tickFormatter={(v) => String(v).slice(-7)} /><YAxis {...AX} width={30} />
                <Tooltip {...TIP} formatter={(v: any, n: any, p: any) => [`${v} fps · ${p.payload.wall_s}s wall · ${p.payload.inference_ms} ms inference`, "Processing"]} />
                <Bar dataKey="fps" name="Processing FPS" fill={OLIVE} radius={[3, 3, 0, 0]} isAnimationActive={false} />
              </BarChart>
            </Chart>
          </div>
        </>
      )}
    </div>
  );
}
