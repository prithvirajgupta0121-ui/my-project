import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import type { IntelItem, StageState } from "./types";

export interface PipelineMsg {
  type: "pipeline"; mission_id: number; code: string; status: string; progress: number;
  stages: Record<string, StageState>; counters: Record<string, number>; demo: boolean;
}
export interface SecurityMsg { type: "security"; action: string; result: string; severity: string; user: string; resource: string; ts: string }

interface FeedCtx {
  connected: boolean;
  intel: IntelItem[];
  pipelines: Record<number, PipelineMsg>;
  security: SecurityMsg[];
  lastMessageAt: number;
  subscribe: (fn: (m: any) => void) => () => void;
}

const Ctx = createContext<FeedCtx>({ connected: false, intel: [], pipelines: {}, security: [], lastMessageAt: 0, subscribe: () => () => {} });

export function FeedProvider({ children, enabled }: { children: ReactNode; enabled: boolean }) {
  const [connected, setConnected] = useState(false);
  const [intel, setIntel] = useState<IntelItem[]>([]);
  const [pipelines, setPipelines] = useState<Record<number, PipelineMsg>>({});
  const [security, setSecurity] = useState<SecurityMsg[]>([]);
  const [lastMessageAt, setLast] = useState(0);
  const subs = useRef(new Set<(m: any) => void>());

  useEffect(() => {
    if (!enabled || typeof WebSocket === "undefined") return;
    let ws: WebSocket | null = null;
    let retry = 0;
    let closed = false;
    let pingT: number | undefined;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onopen = () => {
        retry = 0;
        setConnected(true);
        pingT = window.setInterval(() => ws?.readyState === 1 && ws.send("ping"), 25000);
      };
      ws.onmessage = (ev) => {
        let m: any;
        try { m = JSON.parse(ev.data); } catch { return; }
        setLast(Date.now());
        if (m.type === "hello") setIntel((m.recent || []).slice().reverse());
        else if (m.type === "intel") setIntel((l) => [m, ...l].slice(0, 120));
        else if (m.type === "pipeline") setPipelines((p) => ({ ...p, [m.mission_id]: m }));
        else if (m.type === "security") setSecurity((l) => [m, ...l].slice(0, 50));
        subs.current.forEach((fn) => fn(m));
      };
      ws.onclose = () => {
        setConnected(false);
        clearInterval(pingT);
        if (!closed) setTimeout(connect, Math.min(10000, 800 * 2 ** retry++));
      };
    };
    connect();
    return () => { closed = true; clearInterval(pingT); ws?.close(); };
  }, [enabled]);

  const subscribe = useCallback((fn: (m: any) => void) => {
    subs.current.add(fn);
    return () => { subs.current.delete(fn); };
  }, []);
  return <Ctx.Provider value={{ connected, intel, pipelines, security, lastMessageAt, subscribe }}>{children}</Ctx.Provider>;
}

export const useFeed = () => useContext(Ctx);

/** Subscribe to raw realtime messages with a stable subscription. */
export function useFeedListener(fn: (m: any) => void) {
  const { subscribe } = useContext(Ctx);
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => subscribe((m) => ref.current(m)), [subscribe]);
}
