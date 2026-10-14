import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { api, ApiError } from "./api";

// ------------------------------------------------------------- hash router
function getHash() {
  return window.location.hash.replace(/^#/, "") || "/command";
}
function subscribe(cb: () => void) {
  window.addEventListener("hashchange", cb);
  return () => window.removeEventListener("hashchange", cb);
}
export function useRoute(): { path: string; parts: string[]; query: URLSearchParams } {
  const h = useSyncExternalStore(subscribe, getHash, () => "/command");
  const [path, qs] = h.split("?");
  return { path, parts: path.split("/").filter(Boolean), query: new URLSearchParams(qs || "") };
}
export function navigate(to: string) {
  if (getHash() !== to) window.location.hash = to;
}

// ------------------------------------------------------------ data hooks
export function useApi<T = any>(path: string | null, opts: { poll?: number; deps?: unknown[] } = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState<boolean>(!!path);
  const alive = useRef(true);
  const load = useCallback(async (quiet = false) => {
    if (!path) return;
    if (!quiet) setLoading(true);
    try {
      const d = await api.get<T>(path);
      if (alive.current) { setData(d); setError(null); }
    } catch (e) {
      if (alive.current) setError(e as ApiError);
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    alive.current = true;
    load();
    let t: number | undefined;
    if (opts.poll) t = window.setInterval(() => load(true), opts.poll);
    return () => { alive.current = false; if (t) clearInterval(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, opts.poll, ...(opts.deps || [])]);
  return { data, error, loading, reload: load, setData };
}

export function useNow(interval = 1000) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), interval);
    return () => clearInterval(t);
  }, [interval]);
  return now;
}

export function useOnline() {
  const [online, setOnline] = useState(typeof navigator === "undefined" ? true : navigator.onLine);
  useEffect(() => {
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => { window.removeEventListener("online", on); window.removeEventListener("offline", off); };
  }, []);
  return online;
}
