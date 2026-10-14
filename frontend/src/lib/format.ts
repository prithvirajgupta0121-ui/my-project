export function parseDate(s?: string | null): Date | null {
  if (!s) return null;
  // SQLite returns naive UTC timestamps; treat missing zone as UTC.
  const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s + "Z";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? null : d;
}

export function clock(d: Date = new Date(), utc = true): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return utc
    ? `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`
    : `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function dt(s?: string | null): string {
  const d = parseDate(s);
  if (!d) return "—";
  return `${d.toISOString().slice(0, 10)} ${clock(d)}Z`;
}

export function timeOnly(s?: string | null): string {
  const d = parseDate(s);
  return d ? clock(d) : "—";
}

export function ago(s?: string | null): string {
  const d = parseDate(s);
  if (!d) return "—";
  const sec = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (sec < 60) return `${Math.round(sec)}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
  return `${Math.round(sec / 86400)}d ago`;
}

/** Mission-relative time T+mm:ss.s */
export function tplus(t?: number | null): string {
  if (t === null || t === undefined) return "—";
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `T+${String(m).padStart(2, "0")}:${s.toFixed(1).padStart(4, "0")}`;
}

export const pct = (v?: number | null, digits = 0) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`);

export function bytes(n?: number | null): string {
  if (!n && n !== 0) return "—";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 && i ? 1 : 0)} ${u[i]}`;
}

export const coord = (v?: number | null, d = 5) => (v === null || v === undefined ? "—" : v.toFixed(d));

export function latlon(lat?: number | null, lon?: number | null): string {
  if (lat === null || lat === undefined || lon === null || lon === undefined) return "NO GPS";
  const ns = lat >= 0 ? "N" : "S";
  const ew = lon >= 0 ? "E" : "W";
  return `${Math.abs(lat).toFixed(5)}°${ns} ${Math.abs(lon).toFixed(5)}°${ew}`;
}

export const pad3 = (n: number) => String(n).padStart(3, "0");
export const human = (s?: string | null) => (s || "").replace(/_/g, " ");
