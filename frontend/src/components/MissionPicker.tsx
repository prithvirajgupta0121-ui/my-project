import { useEffect } from "react";
import { navigate, useApi, useRoute } from "../lib/hooks";
import type { Mission } from "../lib/types";

/** Mission selector bound to the `?mission=` hash query parameter. */
export function useMissionParam(autoSelect = true): [number | null, (id: number | null) => void, Mission[] | null] {
  const { path, query } = useRoute();
  const list = useApi<Mission[]>("/api/missions?include_archived=false");
  const raw = query.get("mission");
  const id = raw ? Number(raw) : null;
  const set = (v: number | null) => {
    const q = new URLSearchParams(query);
    if (v === null) q.delete("mission"); else q.set("mission", String(v));
    const qs = q.toString();
    navigate(qs ? `${path}?${qs}` : path);
  };
  useEffect(() => {
    if (autoSelect && id === null && list.data?.length) {
      const best = list.data.find((m) => ["REVIEW", "COMPLETED"].includes(m.status) && (m.counts?.frames ?? m.counts?.detections ?? 1)) || list.data[0];
      set(best.id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list.data, id, autoSelect]);
  return [id, set, list.data];
}

export default function MissionPicker({ value, onChange, missions, allowAll }: {
  value: number | null; onChange: (v: number | null) => void; missions: Mission[] | null; allowAll?: boolean;
}) {
  return (
    <select className="field !w-auto !max-w-80 !py-1" aria-label="Select mission" value={value ?? ""}
      onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
      {allowAll && <option value="">ALL MISSIONS</option>}
      {!allowAll && value === null && <option value="">SELECT MISSION…</option>}
      {(missions || []).map((m) => (
        <option key={m.id} value={m.id}>{m.code} — {m.name}{m.is_demo ? " [DEMO]" : ""} ({m.status})</option>
      ))}
    </select>
  );
}
