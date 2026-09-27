import { useEffect, useState } from "react";
import { api } from "../api";

type RecentRun = {
  id: string;
  environment: string;
  status: string;
  action: string;
  revision: string;
  created: number;
  actor: string;
};

export function LatestRun({
  repository,
  environment,
  ready,
  refresh,
  actionLabel,
  runUrl,
}: {
  repository: string;
  environment: string;
  ready: boolean;
  refresh: number;
  actionLabel: (action: string) => string;
  runUrl: (id: string) => string;
}) {
  const [run, setRun] = useState<RecentRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    if (!ready) return;
    let active = true;
    const load = async () => {
      try {
        const result = await api<{ items: RecentRun[] }>(
          `/v1/runs?repository=${encodeURIComponent(repository)}${environment ? `&environment=${encodeURIComponent(environment)}` : ""}&page=1&page_size=1`,
        );
        if (active) {
          setRun(result.items[0] ?? null);
          setError("");
        }
      } catch (error) {
        if (active)
          setError(
            error instanceof Error ? error.message : "The request failed.",
          );
      } finally {
        if (active) setLoading(false);
      }
    };
    void load();
    const timer = setInterval(() => void load(), 15000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [repository, environment, ready, refresh, retry]);

  return (
    <aside className="latest-run" aria-label="Latest run">
      <h2>Latest run</h2>
      {!ready ? (
        <p>Choose an available repository to view its latest run.</p>
      ) : error ? (
        <div role="alert">
          <p>{error}</p>
          <button type="button" onClick={() => setRetry((value) => value + 1)}>
            Retry latest run
          </button>
        </div>
      ) : loading ? (
        <p role="status">Loading latest run…</p>
      ) : !run ? (
        <p>
          {environment
            ? "No runs in this environment yet."
            : "No runs in this repository yet."}
        </p>
      ) : (
        <>
          <span className={`platform-status ${run.status}`}>
            {run.status.replaceAll("_", " ")}
          </span>
          <h3>{actionLabel(run.action)}</h3>
          <dl>
            {!environment && (
              <div>
                <dt>Environment</dt>
                <dd>{run.environment}</dd>
              </div>
            )}
            <div>
              <dt>Commit</dt>
              <dd>
                <code>{run.revision.slice(0, 10)}</code>
              </dd>
            </div>
            <div>
              <dt>Started</dt>
              <dd>
                <time dateTime={new Date(run.created * 1000).toISOString()}>
                  {new Date(run.created * 1000).toLocaleString()}
                </time>
              </dd>
            </div>
            <div>
              <dt>By</dt>
              <dd>{run.actor}</dd>
            </div>
          </dl>
          <a href={runUrl(run.id)}>View run →</a>
        </>
      )}
    </aside>
  );
}
