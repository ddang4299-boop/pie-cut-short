import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { ApiError, listJobs, listSources, uploadSource, type Job, type Source } from "../lib/api";

/** Sources + jobs, polled from the backend (1 s while something runs, 4 s otherwise). */

export interface Upload {
  key: string;
  name: string;
  loaded: number;
  total: number;
  abort: () => void;
}

interface SourcesState {
  sources: Source[];
  jobs: Job[];
  loaded: boolean;
  error: ApiError | null;
  uploads: Upload[];
  activeJobCount: number;
  currentSourceId: string | null;
  currentSource: Source | null;
  setCurrentSourceId: (id: string | null) => void;
  refresh: () => Promise<void>;
  upload: (file: File) => Promise<Source>;
  replaceSource: (source: Source) => void;
}

const FAST_MS = 1000;
const SLOW_MS = 4000;
const CURRENT_KEY = "shortforge.currentSource";

function readCurrent(): string | null {
  try {
    return window.localStorage.getItem(CURRENT_KEY);
  } catch {
    return null;
  }
}

const SourcesContext = createContext<SourcesState | null>(null);

export function SourcesProvider({ children }: { children: ReactNode }) {
  const [sources, setSources] = useState<Source[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [currentSourceId, setCurrentId] = useState<string | null>(readCurrent);
  const inFlight = useRef(false);

  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const [s, j] = await Promise.all([listSources(), listJobs(40)]);
      setSources(s);
      setJobs(j);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError({ code: "UNKNOWN", severity: "error", params: {} }, 0));
    } finally {
      inFlight.current = false;
      setLoaded(true);
    }
  }, []);

  const activeJobCount = jobs.filter((j) => j.status === "queued" || j.status === "running").length;
  const busy = activeJobCount > 0 || uploads.length > 0 || sources.some((s) => s.status === "downloading" || s.status === "processing");

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), busy ? FAST_MS : SLOW_MS);
    return () => window.clearInterval(timer);
  }, [refresh, busy]);

  const setCurrentSourceId = useCallback((id: string | null) => {
    setCurrentId(id);
    try {
      if (id) window.localStorage.setItem(CURRENT_KEY, id);
      else window.localStorage.removeItem(CURRENT_KEY);
    } catch {
      /* storage unavailable */
    }
  }, []);

  const replaceSource = useCallback((source: Source) => {
    setSources((prev) => {
      const index = prev.findIndex((s) => s.id === source.id);
      if (index === -1) return [source, ...prev];
      const next = [...prev];
      next[index] = source;
      return next;
    });
  }, []);

  const upload = useCallback(
    async (file: File) => {
      const key = `${file.name}-${file.size}-${Date.now()}`;
      const handle = uploadSource(file, (loadedBytes, total) =>
        setUploads((prev) => prev.map((u) => (u.key === key ? { ...u, loaded: loadedBytes, total } : u))),
      );
      setUploads((prev) => [...prev, { key, name: file.name, loaded: 0, total: file.size, abort: handle.abort }]);
      try {
        const source = await handle.promise;
        replaceSource(source);
        setCurrentSourceId(source.id);
        return source;
      } finally {
        setUploads((prev) => prev.filter((u) => u.key !== key));
        void refresh();
      }
    },
    [refresh, replaceSource, setCurrentSourceId],
  );

  const currentSource = useMemo(() => sources.find((s) => s.id === currentSourceId) ?? null, [sources, currentSourceId]);

  const value: SourcesState = {
    sources,
    jobs,
    loaded,
    error,
    uploads,
    activeJobCount,
    currentSourceId,
    currentSource,
    setCurrentSourceId,
    refresh,
    upload,
    replaceSource,
  };
  return <SourcesContext.Provider value={value}>{children}</SourcesContext.Provider>;
}

export function useSources(): SourcesState {
  const ctx = useContext(SourcesContext);
  if (!ctx) throw new Error("useSources must be used inside <SourcesProvider>");
  return ctx;
}
