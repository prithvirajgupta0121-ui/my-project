import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError, onAuthError } from "./api";
import type { SessionInfo } from "./types";

interface AuthCtx {
  session: SessionInfo | null;
  loading: boolean;
  expired: string | null;
  can: (perm: string) => boolean;
  login: (username: string, password: string) => Promise<void>;
  enterDemo: () => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [expired, setExpired] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setSession(await api.get<SessionInfo>("/api/auth/me"));
    } catch (e) {
      setSession(null);
      const err = e as ApiError;
      if (err.code === "SESSION_EXPIRED") setExpired(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => onAuthError((e) => {
    setSession((s) => {
      if (s) setExpired(e.code === "SESSION_EXPIRED" ? e.message : "SESSION EXPIRED — please sign in again.");
      return null;
    });
  }), []);

  const value = useMemo<AuthCtx>(() => ({
    session, loading, expired,
    can: (perm) => !!session?.permissions.includes(perm),
    login: async (username, password) => {
      const s = await api.post<SessionInfo>("/api/auth/login", { username, password });
      setExpired(null);
      setSession(s);
    },
    enterDemo: async () => {
      const s = await api.post<SessionInfo>("/api/auth/demo");
      setExpired(null);
      setSession(s);
    },
    logout: async () => {
      try { await api.post("/api/auth/logout"); } catch { /* already gone */ }
      setSession(null);
      window.location.hash = "/command";
    },
    refresh,
  }), [session, loading, expired, refresh]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAuth outside provider");
  return c;
}
