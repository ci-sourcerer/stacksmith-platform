import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import type { Review } from "./types";

export function useDraftReview(
  key: string,
  enabled: boolean,
  attempt: number,
  controller = false,
  sourceKey?: string,
) {
  const [result, setResult] = useState<{
    key: string;
    review: Review;
    sourceKey?: string;
  }>();
  const [editorId] = useState(() => crypto.randomUUID());
  const generation = useRef(0);
  const [failure, setFailure] = useState<{
    key: string;
    attempt: number;
    error: unknown;
  }>();
  const [completed, setCompleted] = useState<{
    key: string;
    attempt: number;
  }>();
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
    const abort = new AbortController();
    const lifetimeSignal = controller ? abort.signal : lifetime.current.signal;
    const requestGeneration = ++generation.current;
    const timer = setTimeout(() => {
      const inspect = async () => {
        if (superseded || lifetimeSignal.aborted) return;
        try {
          const review = await api<Review>(
            "/review",
            controller
              ? {
                  ...JSON.parse(sourceKey ?? key),
                  ...JSON.parse(key),
                  editor_id: editorId,
                  generation: requestGeneration,
                }
              : JSON.parse(key),
            lifetimeSignal,
          );
          if (!superseded && !lifetimeSignal.aborted)
            setResult({ key, review, ...(controller ? { sourceKey } : {}) });
        } catch (error) {
          if (
            error instanceof ApiError &&
            error.diagnostics?.code === "inspection_superseded"
          )
            return;
          if (!superseded && !lifetimeSignal.aborted)
            setFailure({ key, attempt, error });
        } finally {
          if (!superseded && !lifetimeSignal.aborted)
            setCompleted({ key, attempt });
        }
      };
      if (controller) void inspect();
      else active.current = active.current.then(inspect);
    }, 400);
    return () => {
      clearTimeout(timer);
      if (controller) abort.abort();
      // Legacy deployments finish the active request before sending pending work.
      superseded = true;
    };
  }, [key, enabled, attempt, controller, editorId]);

  return {
    editorId,
    result,
    failure:
      enabled && failure?.key === key && failure.attempt === attempt
        ? failure.error
        : undefined,
    inspecting:
      enabled && (completed?.key !== key || completed?.attempt !== attempt),
  };
}
