import {
  Activity, BarChart3, Brain, Crosshair, FileText, LogOut, Map, MonitorPlay, PanelLeftClose, PanelLeftOpen, Radar,
  Server, Settings, ShieldCheck, Siren, Waypoints, type LucideIcon,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useAuth } from "../lib/auth";
import { useFeed } from "../lib/feed";
import { clock } from "../lib/format";
import { navigate, useApi, useNow, useOnline, useRoute } from "../lib/hooks";
import { cx, DemoTag, Dot } from "./ui";

export const NAV: { path: string; label: string; icon: LucideIcon; perm?: string }[] = [
  { path: "/command", label: "COMMAND", icon: Radar },
  { path: "/live", label: "LIVE FEED", icon: MonitorPlay, perm: "live:view" },
  { path: "/missions", label: "MISSIONS", icon: Crosshair },
  { path: "/map", label: "INTELLIGENCE MAP", icon: Map },
  { path: "/analysis", label: "AI ANALYSIS", icon: Brain },
  { path: "/tracking", label: "TRACKING", icon: Waypoints },
  { path: "/events", label: "EVENTS", icon: Siren },
  { path: "/analytics", label: "ANALYTICS", icon: BarChart3, perm: "analytics:read" },
  { path: "/reports", label: "REPORTS", icon: FileText, perm: "report:read" },
  { path: "/security", label: "SECURITY", icon: ShieldCheck, perm: "audit:read" },
  { path: "/system", label: "SYSTEM", icon: Server },
  { path: "/settings", label: "SETTINGS", icon: Settings },
];

function readCollapsed() {
  try { return localStorage.getItem("aegis.nav.collapsed") === "1"; } catch { return false; }
}

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <div className="relative shrink-0 overflow-hidden rounded-full border border-olive-dim" style={{ width: size, height: size }}>
      <div className="absolute inset-0 animate-sweep" style={{ background: "conic-gradient(from 0deg, rgba(138,156,85,0.55), transparent 25%)" }} />
      <div className="absolute inset-[22%] rounded-full border border-olive-dim/70" />
      <div className="absolute left-1/2 top-1/2 h-1 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-olive" />
    </div>
  );
}

function StatusItem({ label, value, tone = "olive" }: { label: string; value: string; tone?: "olive" | "amber" | "red" | "cyan" }) {
  const c = { olive: "text-olive", amber: "text-amber", red: "text-red", cyan: "text-cyan" }[tone];
  return (
    <div className="hidden items-center gap-1.5 xl:flex">
      <Dot tone={tone} />
      <span className="label">{label}</span>
      <span className={cx("mono text-[10px] tracking-wider", c)}>{value}</span>
    </div>
  );
}

export default function Shell({ children }: { children: ReactNode }) {
  const { session, logout, can } = useAuth();
  const { connected } = useFeed();
  const route = useRoute();
  const now = useNow();
  const online = useOnline();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const health = useApi<any>("/api/system/health", { poll: 15000 });
  useEffect(() => { try { localStorage.setItem("aegis.nav.collapsed", collapsed ? "1" : "0"); } catch { /* ignore */ } }, [collapsed]);
  useEffect(() => {
    const mq = window.matchMedia?.("(max-width: 1100px)");
    if (mq?.matches) setCollapsed(true);
    const on = (e: MediaQueryListEvent) => e.matches && setCollapsed(true);
    mq?.addEventListener?.("change", on);
    return () => mq?.removeEventListener?.("change", on);
  }, []);

  const comp = (n: string) => health.data?.components?.find((c: any) => c.name === n);
  const overall = health.data?.overall ?? (health.error ? "DEGRADED" : "…");
  const db = comp("DATABASE")?.status ?? "…";
  const ai = comp("AI ENGINE")?.status ?? "…";
  const demo = session?.user.is_demo;

  return (
    <div className="flex h-full flex-col">
      {/* ------------------------------------------------ header */}
      <header className="relative z-[1000] flex h-12 shrink-0 items-center gap-4 border-b border-line bg-void/95 px-3">
        <button className="flex items-center gap-2.5" onClick={() => navigate("/command")} aria-label="AEGIS command">
          <Logo />
          <div className="text-left leading-tight">
            <div className="mono text-[13px] font-semibold tracking-[0.28em] text-ink">AEGIS</div>
            <div className="mono text-[8px] tracking-[0.22em] text-olive">AERIAL INTELLIGENCE</div>
          </div>
        </button>
        <div className="mono hidden border border-red-dim px-1.5 py-0.5 text-[9px] tracking-[0.2em] text-red/90 lg:block">RESTRICTED SYSTEM</div>
        <div className="flex items-center gap-4">
          <StatusItem label="" value="SECURE LOCAL NETWORK" />
          <StatusItem label="SYSTEM" value={overall} tone={overall === "OPERATIONAL" ? "olive" : "red"} />
          <StatusItem label="AI ENGINE" value={ai} tone={ai === "READY" ? "olive" : "amber"} />
          <StatusItem label="DATABASE" value={db} tone={db === "SECURE" ? "olive" : "red"} />
        </div>
        <div className="ml-auto flex items-center gap-3">
          <div className="hidden items-center gap-2 lg:flex">
            {!online && <span className="mono border border-amber-dim px-1.5 py-0.5 text-[9px] tracking-widest text-amber">OFFLINE MODE</span>}
            <span className="mono border border-line-2 px-1.5 py-0.5 text-[9px] tracking-widest text-ink-2">LOCAL PROCESSING</span>
            <span className="mono border border-line-2 px-1.5 py-0.5 text-[9px] tracking-widest text-ink-2">NO CLOUD UPLOAD</span>
          </div>
          <div className="flex items-center gap-1.5" title={connected ? "Realtime link active" : "Realtime link reconnecting"}>
            <Activity size={12} className={connected ? "text-olive" : "text-amber animate-pulse-soft"} />
            <span className="mono hidden text-[9px] tracking-widest text-muted md:inline">{connected ? "LINK" : "RECONNECT"}</span>
          </div>
          <div className="mono text-right leading-tight">
            <div className="text-[13px] tabular-nums text-ink">{clock(now)}<span className="text-[9px] text-muted">Z</span></div>
            <div className="text-[9px] tabular-nums text-muted">LOCAL {clock(now, false)}</div>
          </div>
          <div className="flex items-center gap-2 border-l border-line pl-3">
            <div className="text-right leading-tight">
              <div className="mono text-[11px] text-ink">{session?.user.username}</div>
              <div className="mono text-[9px] tracking-widest text-olive">{session?.user.role}{demo ? " · DEMO" : ""}</div>
            </div>
            <button className="btn btn-ghost btn-sm" onClick={logout} aria-label="Sign out" title="Sign out"><LogOut size={14} /></button>
          </div>
        </div>
      </header>
      {demo && (
        <div className="mono flex h-6 shrink-0 items-center justify-center gap-3 border-b border-amber-dim bg-amber-dim/15 text-[10px] tracking-[0.2em] text-amber">
          <DemoTag /> DEMO ENVIRONMENT — SYNTHETIC FOOTAGE OVER FICTIONAL TERRAIN · ALL AI OUTPUT REQUIRES HUMAN REVIEW
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        {/* ------------------------------------------------ nav */}
        <nav className={cx("flex shrink-0 flex-col border-r border-line bg-void/80 transition-[width] duration-200", collapsed ? "w-12" : "w-52")} aria-label="Primary">
          <div className="flex-1 overflow-y-auto py-2">
            {NAV.filter((n) => !n.perm || can(n.perm)).map((n) => {
              const active = route.path.startsWith(n.path);
              return (
                <button key={n.path} onClick={() => navigate(n.path)} title={n.label} aria-current={active ? "page" : undefined}
                  className={cx("group relative flex w-full items-center gap-2.5 px-3.5 py-2 text-left transition-colors",
                    active ? "bg-olive-deep/60 text-ink" : "text-muted hover:bg-panel-2 hover:text-ink")}>
                  {active && <span className="absolute inset-y-1 left-0 w-[2px] bg-olive" />}
                  <span className={cx("mono text-[9px]", active ? "text-olive" : "text-dim")}>◉</span>
                  <n.icon size={14} className={active ? "text-olive" : ""} />
                  {!collapsed && <span className="mono truncate text-[11px] tracking-[0.12em]">{n.label}</span>}
                </button>
              );
            })}
          </div>
          <div className="border-t border-line p-2">
            {!collapsed && (
              <div className="mono mb-2 space-y-0.5 px-1 text-[9px] tracking-widest text-dim">
                <div>MISSION DATA SECURE</div>
                <div>ANALYST REVIEW REQUIRED</div>
                <div>SESSION {session?.session.ref}</div>
              </div>
            )}
            <button className="btn btn-ghost btn-sm w-full justify-center" onClick={() => setCollapsed((c) => !c)} aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}>
              {collapsed ? <PanelLeftOpen size={14} /> : <><PanelLeftClose size={14} /> COLLAPSE</>}
            </button>
          </div>
        </nav>
        <main className="min-w-0 flex-1 overflow-hidden">{children}</main>
      </div>
    </div>
  );
}
