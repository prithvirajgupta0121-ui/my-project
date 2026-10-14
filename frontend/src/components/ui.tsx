import { AlertTriangle, CheckCircle2, HelpCircle, Loader2, X, XCircle, type LucideIcon } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import type { ApiError } from "../lib/api";
import { SEVERITY_COLORS, type Review } from "../lib/types";

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

export function Panel({ title, icon: Icon, right, children, className, bodyClass, code }: {
  title?: ReactNode; icon?: LucideIcon; right?: ReactNode; children: ReactNode; className?: string; bodyClass?: string; code?: string;
}) {
  return (
    <section className={cx("panel brackets flex min-h-0 flex-col", className)}>
      {title && (
        <header className="flex h-8 shrink-0 items-center gap-2 border-b border-line px-3">
          {Icon && <Icon size={13} className="text-olive" />}
          <h2 className="label !text-ink-2">{title}</h2>
          {code && <span className="mono text-[9px] text-dim">{code}</span>}
          <div className="ml-auto flex items-center gap-2">{right}</div>
        </header>
      )}
      <div className={cx("min-h-0 flex-auto", bodyClass ?? "p-3")}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, tone = "ink", icon: Icon, onClick }: {
  label: string; value: ReactNode; sub?: ReactNode; tone?: "ink" | "olive" | "amber" | "red" | "cyan"; icon?: LucideIcon; onClick?: () => void;
}) {
  const color = { ink: "text-ink", olive: "text-olive", amber: "text-amber", red: "text-red", cyan: "text-cyan" }[tone];
  return (
    <button type="button" onClick={onClick} disabled={!onClick}
      className="panel brackets flex min-w-0 flex-col items-start px-3 py-2.5 text-left transition-colors enabled:hover:border-line-2 disabled:cursor-default">
      <div className="flex w-full items-center gap-1.5">
        {Icon && <Icon size={12} className="text-muted" />}
        <span className="label truncate">{label}</span>
      </div>
      <div className={cx("mono mt-1 text-2xl leading-none tabular-nums", color)}>{value}</div>
      {sub && <div className="mono mt-1 truncate text-[10px] text-muted">{sub}</div>}
    </button>
  );
}

const STATUS_TONES: Record<string, string> = {
  QUEUED: "text-ink-2 border-line-2", PROCESSING: "text-cyan border-cyan-dim", ANALYZING: "text-cyan border-cyan-dim",
  REVIEW: "text-amber border-amber-dim", COMPLETED: "text-olive border-olive-dim", FAILED: "text-red border-red-dim",
  ARCHIVED: "text-dim border-line", RUNNING: "text-cyan border-cyan-dim", DONE: "text-olive border-olive-dim",
  PENDING: "text-dim border-line", ACTIVE: "text-cyan border-cyan-dim", LOST: "text-muted border-line", CLOSED: "text-muted border-line",
  SUCCESS: "text-olive border-olive-dim", FAILURE: "text-red border-red-dim", DENIED: "text-amber border-amber-dim",
  LIVE: "text-red border-red-dim", PAUSED: "text-amber border-amber-dim", STOPPED: "text-muted border-line",
  OPERATIONAL: "text-olive border-olive-dim", SECURE: "text-olive border-olive-dim", PROTECTED: "text-olive border-olive-dim",
  LOCAL: "text-olive border-olive-dim", MONITORED: "text-olive border-olive-dim", READY: "text-olive border-olive-dim",
  UNAVAILABLE: "text-amber border-amber-dim", IDLE: "text-muted border-line", BUSY: "text-cyan border-cyan-dim",
  STANDBY: "text-muted border-line", "OFFLINE-CAPABLE": "text-olive border-olive-dim", FAILURE_DB: "text-red",
};

export function Badge({ children, tone, className, title }: { children: ReactNode; tone?: string; className?: string; title?: string }) {
  const t = tone ? STATUS_TONES[tone] ?? "text-ink-2 border-line-2" : STATUS_TONES[String(children)] ?? "text-ink-2 border-line-2";
  return (
    <span title={title} className={cx("mono inline-flex items-center gap-1 whitespace-nowrap border px-1.5 py-[1px] text-[10px] uppercase tracking-wider", t, className)}>
      {children}
    </span>
  );
}

export function SeverityBadge({ s }: { s: string }) {
  const c = SEVERITY_COLORS[s] || "#999";
  return (
    <span className="mono inline-flex items-center gap-1 border px-1.5 py-[1px] text-[10px] tracking-wider" style={{ color: c, borderColor: c + "66" }}>
      <span className="h-1.5 w-1.5" style={{ background: c }} />{s}
    </span>
  );
}

export function ReviewBadge({ s }: { s: Review | string }) {
  const map: Record<string, [string, LucideIcon, string]> = {
    CONFIRMED: ["text-olive border-olive-dim", CheckCircle2, "CONFIRMED"],
    REJECTED: ["text-red border-red-dim", XCircle, "REJECTED"],
    NEEDS_REVIEW: ["text-amber border-amber-dim", AlertTriangle, "NEEDS REVIEW"],
    UNVERIFIED: ["text-ink-2 border-line-2", HelpCircle, "UNVERIFIED"],
  };
  const [cls, Icon, label] = map[s] || map.UNVERIFIED;
  return <span className={cx("mono inline-flex items-center gap-1 border px-1.5 py-[1px] text-[10px] tracking-wider", cls)}><Icon size={10} />{label}</span>;
}

export function Dot({ tone = "olive", pulse }: { tone?: "olive" | "amber" | "red" | "cyan" | "dim"; pulse?: boolean }) {
  const c = { olive: "bg-olive", amber: "bg-amber", red: "bg-red", cyan: "bg-cyan", dim: "bg-dim" }[tone];
  return <span className={cx("inline-block h-1.5 w-1.5 rounded-full", c, pulse && "animate-pulse-soft")} />;
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-muted">
      <Loader2 size={14} className="animate-spin" />
      {label && <span className="label">{label}</span>}
    </div>
  );
}

export function ScanLoader({ label = "LOADING" }: { label?: string }) {
  return (
    <div className="flex h-full min-h-24 flex-col items-center justify-center gap-3">
      <div className="relative h-[2px] w-40 overflow-hidden bg-line">
        <div className="absolute inset-y-0 w-1/3 animate-scan bg-gradient-to-r from-transparent via-olive to-transparent" />
      </div>
      <span className="label">{label}</span>
    </div>
  );
}

export function ErrorNote({ error, onRetry, compact }: { error: ApiError | Error | string | null; onRetry?: () => void; compact?: boolean }) {
  if (!error) return null;
  const e = typeof error === "string" ? { message: error, code: "" } : (error as any);
  return (
    <div role="alert" className={cx("flex items-start gap-2 border border-red-dim bg-red-dim/20 text-[12px] text-[#f0c2bd]", compact ? "px-2 py-1" : "px-3 py-2")}>
      <AlertTriangle size={14} className="mt-0.5 shrink-0 text-red" />
      <div className="min-w-0 flex-1">
        {e.code && <span className="mono mr-2 text-[10px] text-red">{String(e.code).replace(/_/g, " ")}</span>}
        <span>{e.message}</span>
      </div>
      {onRetry && <button className="btn btn-sm" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function Empty({ icon: Icon, title, hint, action }: { icon?: LucideIcon; title: string; hint?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex h-full min-h-32 flex-col items-center justify-center gap-2 p-6 text-center">
      {Icon && <Icon size={22} className="text-dim" />}
      <div className="label !text-ink-2">{title}</div>
      {hint && <div className="max-w-sm text-[12px] text-muted">{hint}</div>}
      {action}
    </div>
  );
}

/** Mandatory framing for any AI output. */
export function AIObservation({ confidence, status, className }: { confidence?: number | null; status?: Review | string; className?: string }) {
  const reviewed = status === "CONFIRMED" || status === "REJECTED";
  return (
    <div className={cx("mono flex flex-wrap items-center gap-x-3 gap-y-1 border-l-2 border-amber bg-amber-dim/15 px-2 py-1 text-[10px] tracking-wider", className)}>
      <span className="text-amber">AI OBSERVATION</span>
      {confidence !== undefined && confidence !== null && <span className="text-ink-2">CONFIDENCE: {Math.round(confidence * 100)}%</span>}
      <span className={reviewed ? "text-olive" : "text-amber"}>
        STATUS: {reviewed ? `ANALYST ${status}` : "REQUIRES HUMAN REVIEW"}
      </span>
    </div>
  );
}

export function DemoTag({ className }: { className?: string }) {
  return <span className={cx("mono border border-amber-dim bg-amber-dim/20 px-1.5 py-[1px] text-[9px] tracking-widest text-amber", className)}>SIMULATION / DEMO DATA</span>;
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[2000] flex items-start justify-center overflow-y-auto bg-black/70 p-4 pt-[6vh]" onMouseDown={onClose}>
      <div role="dialog" aria-modal="true" aria-label={title} className={cx("panel brackets w-full", wide ? "max-w-4xl" : "max-w-xl")} onMouseDown={(e) => e.stopPropagation()}>
        <header className="flex h-9 items-center border-b border-line px-3">
          <h2 className="label !text-ink">{title}</h2>
          <button className="btn btn-ghost btn-sm ml-auto" onClick={onClose} aria-label="Close"><X size={14} /></button>
        </header>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

export function Field({ label, error, children, hint }: { label: string; error?: string; children: ReactNode; hint?: string }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="label">{label}</span>
      {children}
      {error ? <span className="mono text-[10px] text-red">{error}</span> : hint ? <span className="text-[10px] text-dim">{hint}</span> : null}
    </label>
  );
}

export function Meter({ value, max = 100, tone = "olive", label }: { value: number | null | undefined; max?: number; tone?: string; label?: string }) {
  const v = Math.max(0, Math.min(1, (value ?? 0) / max));
  const c = v > 0.85 ? "bg-red" : v > 0.65 ? "bg-amber" : tone === "cyan" ? "bg-cyan" : "bg-olive";
  return (
    <div className="flex items-center gap-2" aria-label={label}>
      <div className="h-1.5 flex-1 bg-line"><div className={cx("h-full transition-all", c)} style={{ width: `${v * 100}%` }} /></div>
      <span className="mono w-10 text-right text-[10px] text-ink-2">{value === null || value === undefined ? "—" : `${Math.round(value)}${max === 100 ? "%" : ""}`}</span>
    </div>
  );
}

export function Toolbar({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center gap-2">{children}</div>;
}

export function Select({ value, onChange, options, label, className }: { value: string; onChange: (v: string) => void; options: (string | [string, string])[]; label?: string; className?: string }) {
  return (
    <select aria-label={label} className={cx("field !w-auto !py-1", className)} value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map((o) => {
        const [v, l] = Array.isArray(o) ? o : [o, o];
        return <option key={v} value={v}>{l}</option>;
      })}
    </select>
  );
}
