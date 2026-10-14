import { Boxes, Cpu, HardDrive, MemoryStick, Microchip, ScrollText, Server } from "lucide-react";
import { Line, LineChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, Legend } from "recharts";
import { Badge, Dot, ErrorNote, Meter, Panel, ScanLoader, SeverityBadge } from "../components/ui";
import { useAuth } from "../lib/auth";
import { dt, timeOnly } from "../lib/format";
import { useApi } from "../lib/hooks";

export default function SystemPage() {
  const { can } = useAuth();
  const h = useApi<any>("/api/system/health", { poll: 5000 });
  const models = useApi<any[]>("/api/system/models");
  const sev = useApi<any[]>(can("audit:read") ? "/api/security/system-events?limit=60" : null, { poll: 15000 });
  const d = h.data;
  const m = d?.metrics;
  const hist = (d?.history || []).map((s: any) => ({ t: timeOnly(s.ts), cpu: s.cpu_percent, mem: s.memory_percent, disk: s.disk_percent }));
  const comp = (n: string) => d?.components.find((c: any) => c.name === n);

  return (
    <div className="grid h-full grid-cols-1 gap-2 overflow-y-auto p-2 lg:grid-cols-3">
      {h.error && <div className="lg:col-span-3"><ErrorNote error={h.error} onRetry={() => h.reload()} /></div>}
      {!d ? <ScanLoader label="READING LOCAL METRICS" /> : (
        <>
          <Panel title="SYSTEM HEALTH" icon={Server} right={<Badge>{d.overall}</Badge>} className="lg:col-span-2">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              {[["CPU", Cpu, m.cpu_percent, `${m.cpu_count} cores · load ${m.load_avg?.[0] ?? "—"}`],
                ["MEMORY", MemoryStick, m.memory_percent, `${m.memory_used_mb} / ${m.memory_total_mb} MB · proc ${m.process_rss_mb} MB`],
                ["DISK", HardDrive, m.disk_percent, `${m.disk_free_gb} GB free (data volume)`]].map(([k, I, v, sub]: any) => (
                <div key={k} className="border border-line bg-void/60 p-2">
                  <div className="flex items-center gap-1.5"><I size={12} className="text-muted" /><span className="label">{k}</span></div>
                  <div className="mono my-1 text-2xl tabular-nums text-ink">{Math.round(v)}%</div>
                  <Meter value={v} />
                  <div className="mono mt-1 text-[9px] text-muted">{sub}</div>
                </div>
              ))}
              <div className="border border-line bg-void/60 p-2">
                <div className="flex items-center gap-1.5"><Microchip size={12} className="text-muted" /><span className="label">GPU</span></div>
                <div className={`mono my-1 text-lg ${m.gpu.available ? "text-olive" : "text-amber"}`}>{m.gpu.available ? `${m.gpu.devices} CUDA` : "UNAVAILABLE"}</div>
                <div className="mono text-[9px] text-muted">{m.gpu.message}</div>
              </div>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">
              {["DATABASE", "AI ENGINE", "PROCESS QUEUE", "STREAM", "STORAGE", "NETWORK", "GPU"].map((n) => {
                const c = comp(n);
                if (!c) return null;
                const tone = ["SECURE", "READY", "LOCAL", "IDLE", "STANDBY", "OFFLINE-CAPABLE", "AVAILABLE"].includes(c.status) ? "olive" : ["BUSY", "LIVE"].includes(c.status) ? "cyan" : c.status === "FAILURE" ? "red" : "amber";
                return (
                  <div key={n} className="border border-line px-2 py-1.5">
                    <div className="flex items-center gap-1.5"><Dot tone={tone as any} pulse={tone === "cyan"} /><span className="label">{n}</span></div>
                    <div className="mono text-[12px] text-ink">{c.status}</div>
                    <div className="text-[10px] text-muted">{c.detail}</div>
                  </div>
                );
              })}
              <div className="border border-line px-2 py-1.5">
                <div className="label">AI INFERENCE</div>
                <div className="mono text-[12px] text-ink">{m.inference_ms_avg ?? "—"} ms</div>
                <div className="text-[10px] text-muted">avg of recent frames</div>
              </div>
            </div>
          </Panel>
          <Panel title="RUNTIME" icon={Server}>
            <dl className="mono grid grid-cols-[120px_1fr] gap-y-1 text-[11px]">
              <dt className="text-muted">VERSION</dt><dd>{d.version}</dd>
              <dt className="text-muted">ENVIRONMENT</dt><dd>{d.env.toUpperCase()}</dd>
              <dt className="text-muted">QUEUE</dt><dd>running {d.queue.running ?? "—"} · waiting {d.queue.queued.length}</dd>
              <dt className="text-muted">LIVE STREAMS</dt><dd>{d.live.streams} · {d.live.fps} fps</dd>
              <dt className="text-muted">HOST UPTIME</dt><dd>{Math.round(m.uptime_s / 3600)} h</dd>
              <dt className="text-muted">METRICS</dt><dd className="text-olive">REAL LOCAL (psutil)</dd>
            </dl>
          </Panel>
          <Panel title="RESOURCE HISTORY (5 s SAMPLES)" icon={Cpu} bodyClass="h-56 p-2" className="lg:col-span-2">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={hist}>
                <CartesianGrid stroke="#253027" strokeDasharray="2 4" vertical={false} />
                <XAxis dataKey="t" stroke="#75806f" fontSize={10} minTickGap={50} tickLine={false} />
                <YAxis domain={[0, 100]} stroke="#75806f" fontSize={10} width={30} tickLine={false} />
                <Tooltip contentStyle={{ background: "#0f1310", border: "1px solid #34413a", fontSize: 11 }} />
                <Legend wrapperStyle={{ fontSize: 10 }} />
                <Line dataKey="cpu" name="CPU %" stroke="#5fb7c2" strokeWidth={2} dot={false} isAnimationActive={false} />
                <Line dataKey="mem" name="Memory %" stroke="#8a9c55" strokeWidth={2} dot={false} isAnimationActive={false} />
                <Line dataKey="disk" name="Disk %" stroke="#a4ad9c" strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </Panel>
          <Panel title="AI MODEL REGISTRY" icon={Boxes} bodyClass="overflow-y-auto p-0">
            {models.error && <ErrorNote error={models.error} />}
            <ul className="divide-y divide-line/60">
              {(models.data || []).map((x) => (
                <li key={x.id} className="px-3 py-2">
                  <div className="flex items-center gap-2"><span className="mono text-[11px] text-ink">{x.name}</span><Badge>{x.status}</Badge><span className="mono ml-auto text-[9px] text-muted">{x.task}</span></div>
                  <div className="mono text-[9px] text-muted">{x.provider} · v{x.version}{x.sha256 ? ` · ${x.sha256}` : ""}</div>
                  <div className="text-[10px] text-dim">{x.notes}</div>
                </li>
              ))}
            </ul>
            <div className="border-t border-line p-2 text-[10px] text-muted">To enable YOLO inference: place a local <span className="mono">.onnx</span> model in <span className="mono">data/models</span> and set <span className="mono">AEGIS_YOLO_MODEL_PATH</span>. No models are downloaded automatically.</div>
          </Panel>
          {sev.data && (
            <Panel title="SYSTEM EVENTS" icon={ScrollText} bodyClass="max-h-72 overflow-y-auto p-0" className="lg:col-span-3">
              <table className="table">
                <thead><tr><th>Time</th><th>Category</th><th>Severity</th><th>Message</th></tr></thead>
                <tbody>{sev.data.map((e) => <tr key={e.id}><td className="mono text-[10px] text-muted">{dt(e.timestamp)}</td><td className="mono text-[10px]">{e.category}</td><td><SeverityBadge s={e.severity} /></td><td className="text-[11px]">{e.message}</td></tr>)}</tbody>
              </table>
            </Panel>
          )}
        </>
      )}
    </div>
  );
}
