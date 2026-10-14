import { KeyRound, Lock, PlayCircle, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Logo } from "../components/Shell";
import { ErrorNote, Field } from "../components/ui";
import type { ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { clock } from "../lib/format";
import { useApi, useNow } from "../lib/hooks";

export default function Login() {
  const { login, enterDemo, expired } = useAuth();
  const cfg = useApi<any>("/api/auth/config");
  const now = useNow();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState<"" | "login" | "demo">("");
  const [error, setError] = useState<ApiError | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!username || !password) {
      setError({ code: "VALIDATION_ERROR", message: "Enter username and password.", status: 0 } as ApiError);
      return;
    }
    setBusy("login");
    setError(null);
    try { await login(username.trim(), password); } catch (err) { setError(err as ApiError); setPassword(""); } finally { setBusy(""); }
  };
  const demo = async () => {
    setBusy("demo");
    setError(null);
    try { await enterDemo(); } catch (err) { setError(err as ApiError); } finally { setBusy(""); }
  };

  return (
    <div className="flex min-h-full items-center justify-center p-6">
      <div className="grid w-full max-w-5xl overflow-hidden border border-line md:grid-cols-[1.15fr_1fr]">
        {/* identity */}
        <div className="relative hidden flex-col justify-between overflow-hidden bg-void/90 p-8 md:flex">
          <div className="pointer-events-none absolute -right-24 top-1/2 h-[420px] w-[420px] -translate-y-1/2 rounded-full border border-olive-dim/30">
            <div className="absolute inset-[15%] rounded-full border border-olive-dim/25" />
            <div className="absolute inset-[32%] rounded-full border border-olive-dim/20" />
            <div className="absolute inset-0 animate-sweep rounded-full" style={{ background: "conic-gradient(from 0deg, rgba(138,156,85,0.18), transparent 22%)" }} />
            <div className="absolute left-1/2 top-0 h-full w-px bg-olive-dim/20" />
            <div className="absolute left-0 top-1/2 h-px w-full bg-olive-dim/20" />
          </div>
          <div className="relative">
            <div className="flex items-center gap-3">
              <Logo size={40} />
              <div className="mono text-[10px] tracking-[0.3em] text-red/80">RESTRICTED SYSTEM</div>
            </div>
            <h1 className="mono mt-10 text-5xl font-semibold tracking-[0.3em] text-ink">AEGIS</h1>
            <div className="mono mt-2 text-sm tracking-[0.35em] text-olive">AERIAL INTELLIGENCE</div>
            <div className="mono mt-1 text-[11px] tracking-[0.3em] text-muted">MISSION ANALYSIS SYSTEM</div>
            <p className="mt-8 max-w-sm text-[13px] leading-relaxed text-ink-2">
              AI-powered aerial mission intelligence &amp; geospatial analysis. Defensive monitoring and
              decision-support — every AI output is an analytical observation that requires human review.
            </p>
          </div>
          <div className="relative grid grid-cols-2 gap-2">
            {["LOCAL PROCESSING", "NO CLOUD UPLOAD", "MISSION DATA SECURE", "ANALYST REVIEW REQUIRED"].map((t) => (
              <div key={t} className="mono border border-line bg-panel/60 px-2 py-1.5 text-[9px] tracking-[0.2em] text-ink-2">● {t}</div>
            ))}
            <div className="mono col-span-2 mt-2 text-[10px] text-dim">
              {clock(now)}Z · v{cfg.data?.version ?? "—"} · Fictional interface — no affiliation with any government or military organisation.
            </div>
          </div>
        </div>
        {/* form */}
        <div className="panel !border-0 flex flex-col justify-center gap-5 p-8">
          <div className="md:hidden flex items-center gap-3"><Logo /><span className="mono tracking-[0.3em]">AEGIS</span></div>
          <div>
            <div className="flex items-center gap-2"><Lock size={14} className="text-olive" /><span className="label !text-ink">SECURE SIGN-IN</span></div>
            <p className="mt-1 text-[12px] text-muted">Authorised personnel only. All access is audited.</p>
          </div>
          {expired && <ErrorNote error={{ code: "SESSION_EXPIRED", message: expired } as any} compact />}
          <form onSubmit={submit} className="flex flex-col gap-3" noValidate>
            <Field label="Username">
              <input className="field" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} maxLength={64} autoFocus aria-label="Username" />
            </Field>
            <Field label="Password">
              <input className="field" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} maxLength={256} aria-label="Password" />
            </Field>
            {error && <ErrorNote error={error} compact />}
            <button type="submit" className="btn btn-primary mt-1 justify-center py-2" disabled={!!busy}>
              <KeyRound size={13} /> {busy === "login" ? "AUTHENTICATING…" : "AUTHENTICATE"}
            </button>
          </form>
          {cfg.data?.demo_enabled && (
            <div className="border-t border-line pt-4">
              <button className="btn w-full justify-center border-amber-dim py-2 text-amber" onClick={demo} disabled={!!busy}>
                <PlayCircle size={14} /> {busy === "demo" ? "PREPARING SIMULATION…" : "ENTER DEMO MODE"}
              </button>
              <p className="mt-2 text-[11px] text-muted">
                Opens a sandboxed demo account with synthetic aerial footage over fictional terrain.
                All demo content is labelled <span className="mono text-amber">SIMULATION / DEMO DATA</span>.
              </p>
            </div>
          )}
          <div className="mono flex items-center gap-2 text-[9px] tracking-widest text-dim">
            <ShieldCheck size={11} /> ARGON2ID · CSRF PROTECTED · SESSION EXPIRY · FAILED-LOGIN LOCKOUT
          </div>
        </div>
      </div>
    </div>
  );
}
