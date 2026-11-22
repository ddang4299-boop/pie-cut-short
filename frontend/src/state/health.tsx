import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

import { ApiError, getHealth, type Health } from "../lib/api";

export type BackendLevel = "checking" | "online" | "warning" | "degraded" | "offline";

interface HealthState {
  data: Health | null;
  error: ApiError | null;
  loading: boolean;
  level: BackendLevel;
  refresh: () => void;
}

/** Traffic light for the header: green online, yellow warnings, red degraded/offline. */
export function backendLevel(data: Health | null, error: ApiError | null, loading: boolean): BackendLevel {
  if (error) return "offline";
  if (!data) return loading ? "checking" : "offline";
  if (data.status !== "ok") return "degraded";
  return data.issues.some((i) => i.severity === "warning") ? "warning" : "online";
}

const HealthContext = createContext<HealthState | null>(null);
const POLL_MS = 15_000;

export function HealthProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<Health | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const requestId = useRef(0);

  const load = useCallback(async (refresh: boolean) => {
    const id = ++requestId.current;
    setLoading(true);
    try {
      const health = await getHealth(refresh);
      if (id !== requestId.current) return;
      setData(health);
      setError(null);
    } catch (err) {
      if (id !== requestId.current) return;
      setError(err instanceof ApiError ? err : new ApiError({ code: "UNKNOWN", severity: "error", params: {} }, 0));
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
    const timer = window.setInterval(() => void load(false), POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const value: HealthState = {
    data,
    error,
    loading,
    level: backendLevel(data, error, loading),
    refresh: () => void load(true),
  };
  return <HealthContext.Provider value={value}>{children}</HealthContext.Provider>;
}

export function useHealth(): HealthState {
  const ctx = useContext(HealthContext);
  if (!ctx) throw new Error("useHealth must be used inside <HealthProvider>");
  return ctx;
}
