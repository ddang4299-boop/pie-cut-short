import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

/** Autosave status shown in the header ("Saving… / Saved"). Pages wrap their writes in track(). */

export type SaveStatus = "idle" | "saving" | "saved" | "error";

interface SaveState {
  status: SaveStatus;
  track: <T>(promise: Promise<T>) => Promise<T>;
}

const SaveContext = createContext<SaveState | null>(null);

export function SaveStatusProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SaveStatus>("idle");
  const pending = useRef(0);
  const failed = useRef(false);

  const track = useCallback(<T,>(promise: Promise<T>): Promise<T> => {
    if (pending.current === 0) failed.current = false; // a new batch of saves
    pending.current += 1;
    setStatus("saving");
    const settle = () => {
      pending.current -= 1;
      if (pending.current === 0) setStatus(failed.current ? "error" : "saved");
    };
    return promise.then(
      (value) => {
        settle();
        return value;
      },
      (err: unknown) => {
        failed.current = true;
        settle();
        throw err;
      },
    );
  }, []);

  const value = useMemo(() => ({ status, track }), [status, track]);
  return <SaveContext.Provider value={value}>{children}</SaveContext.Provider>;
}

export function useSaveStatus(): SaveState {
  const ctx = useContext(SaveContext);
  if (!ctx) throw new Error("useSaveStatus must be used inside <SaveStatusProvider>");
  return ctx;
}
