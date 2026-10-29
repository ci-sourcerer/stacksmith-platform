import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Review } from "./types";

export function useDraftReview(
  key: string,
  enabled: boolean,
  attempt: number,
  immediate = false,
) {
  const [result, setResult] = useState<{ key: string; review: Review }>();
  const [failure, setFailure] = useState<{
    key: string;
    attempt: number;
    error: unknown;
  }>();
  const [completed, setCompleted] = useState<{
    key: string;
    attempt: number;
  }>();
  const immediateRef = useRef(immediate);
  immediateRef.current = immediate;
  const active = useRef<Promise<void>>(Promise.resolve());
  const lifetime = useRef(new AbortController());

  useEffect(() => {
    const abort = new AbortController();
    lifetime.current = abort;
    return () => abort.abort();
  }, []);

  useEffect(() => {
    if (!enabled) return;
    setFailure(undefined);
    setCompleted(undefined);
    let superseded = false;
    const lifetimeSignal = lifetime.current.signal;
    const timer = setTimeout(
      () => {
        active.current = active.current.then(async () => {
          if (superseded || lifetimeSignal.aborted) return;
          try {
            const review = await api<Review>(
              "/review",
              JSON.parse(key),
              lifetimeSignal,
            );
            if (!superseded && !lifetimeSignal.aborted)
              setResult({ key, review });
          } catch (error) {
            if (!superseded && !lifetimeSignal.aborted)
              setFailure({ key, attempt, error });
          } finally {
            if (!superseded && !lifetimeSignal.aborted)
              setCompleted({ key, attempt });
          }
        });
      },
      immediateRef.current ? 0 : 400,
    );
    return () => {
      clearTimeout(timer);
      // Keep the active request alive so its server job finishes before the next.
      superseded = true;
    };
  }, [key, enabled, attempt]);

  return {
    result,
    failure:
      enabled && failure?.key === key && failure.attempt === attempt
        ? failure.error
        : undefined,
    inspecting:
      enabled && (completed?.key !== key || completed?.attempt !== attempt),
  };
}
