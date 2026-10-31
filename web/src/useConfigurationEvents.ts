import { useEffect, useRef } from "react";
import { api } from "./api";
import type { Snapshot } from "./types";

export function useConfigurationEvents(
  enabled: boolean,
  snapshot: Snapshot | undefined,
  onRefresh: (snapshot: Snapshot) => void,
  onError: (error: unknown) => void,
) {
  const current = useRef({ snapshot, onRefresh, onError });
  useEffect(() => {
    current.current = { snapshot, onRefresh, onError };
  }, [snapshot, onRefresh, onError]);

  useEffect(() => {
    if (!enabled || typeof EventSource === "undefined") return;
    const events = new EventSource("/api/configuration-events");
    let pending: AbortController | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const original = current.current.snapshot;
        if (!original) return;
        pending?.abort();
        const abort = new AbortController();
        pending = abort;
        api<Snapshot>(
          "/refresh-snapshot",
          { snapshot: original.snapshot },
          abort.signal,
        )
          .then((updated) => {
            if (
              !abort.signal.aborted &&
              current.current.snapshot?.snapshot === original.snapshot
            )
              current.current.onRefresh(updated);
          })
          .catch((error: unknown) => {
            if (!abort.signal.aborted) current.current.onError(error);
          });
      }, 100);
    };
    events.addEventListener("configuration", refresh);
    return () => {
      clearTimeout(timer);
      pending?.abort();
      events.close();
    };
  }, [enabled]);
}
