import { useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { dt } from "../lib/format";
import type { Review } from "../lib/types";
import { cx, ErrorNote } from "./ui";

const OPTIONS: [Review, string, string][] = [
  ["UNVERIFIED", "UNVERIFIED", "border-line-2 text-ink-2"],
  ["CONFIRMED", "CONFIRM", "border-olive-dim text-olive"],
  ["REJECTED", "REJECT", "border-red-dim text-red"],
  ["NEEDS_REVIEW", "NEEDS REVIEW", "border-amber-dim text-amber"],
];

/** Human-in-the-loop review: analysts confirm/reject AI observations and record notes. */
export default function ReviewControls({ kind, id, status, notes, reviewedBy, reviewedAt, onSaved }: {
  kind: "events" | "tracks"; id: number; status: Review; notes: string | null; reviewedBy?: string | null; reviewedAt?: string | null;
  onSaved: (updated: any) => void;
}) {
  const { can } = useAuth();
  const [sel, setSel] = useState<Review>(status);
  const [text, setText] = useState(notes || "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => { setSaved(false); setErr(null); }, [id]);
  useEffect(() => { setSel(status); setText(notes || ""); }, [id, status, notes]);
  const editable = can("review:write");

  const save = async (next: Review = sel) => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.patch(`/api/${kind}/${id}/review`, { review_status: next, analyst_notes: text });
      setSaved(true);
      onSaved(r);
    } catch (e) { setErr(e as ApiError); } finally { setBusy(false); }
  };

  return (
    <div className="space-y-2" aria-label="Analyst review">
      <div className="label">ANALYST REVIEW</div>
      <div className="grid grid-cols-2 gap-1">
        {OPTIONS.map(([v, label, cls]) => (
          <button key={v} type="button" disabled={!editable || busy} onClick={() => { setSel(v); setSaved(false); }}
            className={cx("btn btn-sm justify-center", cls, sel === v && "!bg-panel-3 ring-1 ring-current")} aria-pressed={sel === v}>
            {label}
          </button>
        ))}
      </div>
      <textarea className="field min-h-16 text-[12px]" placeholder={editable ? "Analyst notes (stored encrypted)…" : "Read-only"} value={text}
        onChange={(e) => { setText(e.target.value); setSaved(false); }} maxLength={4000} disabled={!editable} aria-label="Analyst notes" />
      {editable && (
        <button className="btn btn-primary btn-sm w-full justify-center" disabled={busy || (sel === status && text === (notes || ""))} onClick={() => save()}>
          {busy ? "SAVING…" : saved ? "✔ REVIEW RECORDED" : "RECORD REVIEW"}
        </button>
      )}
      {reviewedBy && <div className="mono text-[9px] text-muted">LAST REVIEW: {reviewedBy} · {dt(reviewedAt)}</div>}
      {err && <ErrorNote error={err} compact />}
    </div>
  );
}
