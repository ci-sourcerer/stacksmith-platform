import { useEffect, useState } from "react";
import { LoaderCircle, RefreshCw, Search } from "lucide-react";
import { api } from "../api";
import { ErrorBanner } from "./ErrorBanner";
import { InspectionDetails } from "./InspectionDetails";
import { InspectionSummary } from "./InspectionSummary";
import type { Draft } from "../history";
import type { Resolution, Snapshot } from "../types";

export function ResolutionView({
  snapshot,
  draft,
  selected,
  environment,
}: {
  snapshot: Snapshot;
  draft: Draft;
  selected: string | null;
  environment: string | null;
}) {
  const [address, setAddress] = useState("");
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<"effective" | "events">("effective");
  const [result, setResult] = useState<Resolution>();
  const [error, setError] = useState("");
  const [inspectionFailure, setInspectionFailure] = useState<unknown>();
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const abort = new AbortController();
    setLoading(true);
    setResult(undefined);
    setError("");
    setInspectionFailure(undefined);
    api<Resolution>(
      "/resolution",
      {
        snapshot: snapshot.snapshot,
        components: draft.components,
        disabled_associations: draft.disabled,
        positions: draft.positions,
        routes: draft.routes,
        query: query || null,
        environment,
      },
      abort.signal,
    )
      .then((resolved) => {
        if (!abort.signal.aborted) setResult(resolved);
      })
      .catch((failure: unknown) => {
        if (!abort.signal.aborted) {
          setInspectionFailure(failure);
          setError(
            failure instanceof Error
              ? failure.message
              : "Could not inspect configuration resolution.",
          );
        }
      })
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => abort.abort();
  }, [snapshot.snapshot, draft, query, revision, environment]);

  function explain(next: string) {
    setAddress(next);
    setQuery(next.trim());
    setTab("events");
    setRevision((value) => value + 1);
  }

  return (
    <main className="information-view resolution-view">
      <h1>Configuration resolution</h1>
      <p>
        {environment
          ? `Deployment inspection: ${environment}`
          : "Stack preview: deployment variables and credentials are excluded."}
      </p>
      {result && <InspectionSummary context={result.context} />}
      <InspectionDetails error={inspectionFailure} />
      <div className="notice">
        Values and templates are shown and may include secrets. Resolution
        executes configured Python validations and transforms; it does not
        generate infrastructure files or run a plan. This uses Composer’s
        managed config and current draft
        {environment ? ` in environment ${environment}` : ""}.
      </div>
      <form
        className="resolution-query"
        onSubmit={(event) => {
          event.preventDefault();
          explain(address);
        }}
      >
        <label htmlFor="resolution-address">Configuration address</label>
        <div className="resolution-query-controls">
          <input
            id="resolution-address"
            value={address}
            onChange={(event) => setAddress(event.target.value)}
            placeholder="components.api.properties.instance_type"
            maxLength={1000}
          />
          <button className="button primary" type="submit">
            <Search size={15} /> Explain
          </button>
          <button className="button" type="button" onClick={() => explain("")}>
            All configuration
          </button>
        </div>
        <p className="muted">
          Use addresses under inputs, components, stack, or config. Leave empty
          to inspect everything. Property queries also follow contributing
          inputs and managed mappings.
        </p>
        <div className="resolution-shortcuts">
          {selected && (
            <button
              className="button"
              type="button"
              onClick={() => explain(`components.${selected}.properties`)}
            >
              Explain {selected}
            </button>
          )}
          <button
            className="button"
            type="button"
            onClick={() => explain("inputs")}
          >
            Explain inputs
          </button>
        </div>
      </form>
      <div
        className="resolution-tabs"
        role="group"
        aria-label="Resolution display"
      >
        <button
          className={`button ${tab === "effective" ? "primary" : ""}`}
          aria-pressed={tab === "effective"}
          onClick={() => setTab("effective")}
        >
          {query ? "Resolved value" : "Effective configuration"}
        </button>
        <button
          className={`button ${tab === "events" ? "primary" : ""}`}
          aria-pressed={tab === "events"}
          onClick={() => setTab("events")}
        >
          Resolution history {result && `(${result.events.length})`}
        </button>
        <button
          className="button"
          onClick={() => setRevision((value) => value + 1)}
          disabled={loading}
        >
          <RefreshCw size={15} /> Refresh
        </button>
      </div>
      {loading && (
        <div className="notice" role="status">
          <LoaderCircle size={17} className="spin" /> Resolving the current
          draft…
        </div>
      )}
      {error && (
        <ErrorBanner
          message={`${error} Check the address and required inputs, then retry with Refresh.`}
          signIn={false}
          onDismiss={() => setError("")}
        />
      )}
      {result && (
        <section
          aria-label={
            tab === "events" ? "Resolution history" : "Effective configuration"
          }
        >
          {result.query && (
            <h2>
              <code>{result.query}</code>
            </h2>
          )}
          {tab === "effective" ? (
            result.query ? (
              <pre className="resolution-json">
                {JSON.stringify(result.value, null, 2)}
              </pre>
            ) : (
              Object.entries(result.effective ?? {}).map(([name, value]) => (
                <details className="resolution-section" key={name}>
                  <summary>{name}</summary>
                  <pre className="resolution-json">
                    {JSON.stringify(value, null, 2)}
                  </pre>
                </details>
              ))
            )
          ) : result.events.length ? (
            <ol className="resolution-events">
              {result.events.map((event, index) => (
                <li key={index}>
                  <details className="resolution-section">
                    <summary>
                      <strong>{event.action}</strong> <code>{event.path}</code>
                      <span className="resolution-source">
                        {event.source || "Resolver"}
                      </span>
                    </summary>
                    <pre className="resolution-json">
                      {JSON.stringify(event, null, 2)}
                    </pre>
                  </details>
                </li>
              ))}
            </ol>
          ) : (
            <p className="notice">
              No resolution events were recorded for this address.
            </p>
          )}
        </section>
      )}
    </main>
  );
}
