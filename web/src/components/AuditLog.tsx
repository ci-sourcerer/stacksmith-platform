import { useEffect, useState } from "react";
import { api } from "../api";
import { useApplication } from "../ApplicationContext";
import { runTargetUrl } from "../runSelection";
import type { Page } from "../pagination";
import { Pagination } from "./Pagination";
import {
  SettingsActions,
  SettingsButton,
  SettingsFeedback,
  SettingsField,
  SettingsPanel,
} from "./SettingsLayout";

type AuditEvent = {
  id: string;
  repository: string | null;
  run: string;
  action: string;
  actor: string;
  detail: Record<string, unknown> | null;
  created: number;
};

const filters = [
  ["repository", "Repository"],
  ["run_id", "Run ID"],
  ["actor", "Actor"],
  ["action", "Event"],
] as const;

function summaryValue(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (typeof value === "boolean" || typeof value === "number")
    return String(value);
  if (Array.isArray(value))
    return value.map(summaryValue).filter(Boolean).join(", ");
  return "";
}

export function auditSummary(
  event: Pick<AuditEvent, "action" | "detail">,
): string {
  const detail = event.detail ?? {};
  if (event.action.startsWith("phase."))
    return (
      summaryValue(detail.phase_name) ||
      (typeof detail.phase === "string" ? detail.phase.trim() : "")
    );
  if (event.action.startsWith("run."))
    return event.action === "run.created"
      ? [summaryValue(detail.revision).slice(0, 10), summaryValue(detail.event)]
          .filter(Boolean)
          .join(" · ")
      : summaryValue(detail.note) || summaryValue(detail.message);
  if (event.action.startsWith("secret."))
    return [summaryValue(detail.name), summaryValue(detail.environment)]
      .filter(Boolean)
      .join(" · ");
  if (event.action.startsWith("execution_credential."))
    return [summaryValue(detail.provider), summaryValue(detail.environment)]
      .filter(Boolean)
      .join(" · ");
  return [
    "login",
    "role",
    "environment",
    "scope",
    "mode",
    "refs",
    "enabled",
    "reason",
    "root",
    "resolved_mode",
    "token",
  ]
    .map((key) => {
      const value = summaryValue(detail[key]);
      return value ? `${key.replaceAll("_", " ")}: ${value}` : "";
    })
    .filter(Boolean)
    .join(" · ");
}

function positiveInteger(
  value: string | null,
  fallback: number,
  maximum = Infinity,
) {
  return value && /^\d+$/.test(value) && Number(value) > 0
    ? Math.min(Number(value), maximum)
    : fallback;
}

export function AuditLog() {
  const { location, navigate } = useApplication();
  const [result, setResult] = useState<Page<AuditEvent>>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const page = positiveInteger(location.searchParams.get("page"), 1);
  const size = positiveInteger(location.searchParams.get("page_size"), 20, 100);
  const query = new URLSearchParams({
    page: String(page),
    page_size: String(size),
  });
  for (const [name] of filters) {
    const value = location.searchParams.get(name);
    if (value) query.set(name, value);
  }
  const search = query.toString();
  useEffect(() => {
    const abort = new AbortController();
    setLoading(true);
    setError("");
    setResult(undefined);
    api<Page<AuditEvent>>(`/v1/audit?${search}`, undefined, abort.signal)
      .then((value) => {
        if (!abort.signal.aborted) setResult(value);
      })
      .catch((failure: unknown) => {
        if (!abort.signal.aborted)
          setError(
            failure instanceof Error
              ? failure.message
              : "Audit events could not be loaded.",
          );
      })
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => abort.abort();
  }, [search, refresh]);
  function update(parameters: URLSearchParams) {
    parameters.set("section", "audit");
    void navigate(`/settings?${parameters}`);
  }
  return (
    <SettingsPanel
      title="Platform activity"
      description="Run, configuration, and account events across the platform."
      toolbar={
        <SettingsButton
          disabled={loading}
          onClick={() => setRefresh((value) => value + 1)}
        >
          Refresh
        </SettingsButton>
      }
    >
      <form
        key={search}
        className="audit-filter-form"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          const parameters = new URLSearchParams({ page_size: String(size) });
          for (const [name] of filters) {
            const value = String(data.get(name) ?? "").trim();
            if (value) parameters.set(name, value);
          }
          update(parameters);
        }}
      >
        <div className="audit-filters">
          {filters.map(([name, label]) => (
            <SettingsField key={name} label={label}>
              <input
                name={name}
                defaultValue={location.searchParams.get(name) ?? ""}
                placeholder={
                  name === "repository"
                    ? "owner/repository"
                    : name === "action"
                      ? "run.created"
                      : undefined
                }
              />
            </SettingsField>
          ))}
        </div>
        <SettingsActions>
          <SettingsButton type="submit">Apply filters</SettingsButton>
          <SettingsButton onClick={() => update(new URLSearchParams())}>
            Clear filters
          </SettingsButton>
        </SettingsActions>
      </form>
      {error ? (
        <SettingsFeedback tone="error">
          {error}{" "}
          <SettingsButton onClick={() => setRefresh((value) => value + 1)}>
            Retry
          </SettingsButton>
        </SettingsFeedback>
      ) : loading ? (
        <SettingsFeedback>Loading audit events…</SettingsFeedback>
      ) : (
        <div className="settings-table-scroll">
          <table className="platform-data-table audit-table">
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Event</th>
                <th scope="col">Actor</th>
                <th scope="col">Repository</th>
                <th scope="col">Run</th>
                <th scope="col">Summary</th>
              </tr>
            </thead>
            <tbody>
              {result?.items.length ? (
                result.items.map((event) => (
                  <tr key={event.id}>
                    <td>{new Date(event.created * 1000).toLocaleString()}</td>
                    <th scope="row">{event.action.replace(/[._]/g, " ")}</th>
                    <td>{event.actor}</td>
                    <td>{event.repository ?? "Platform / account"}</td>
                    <td>
                      {event.run && event.repository ? (
                        <a
                          href={runTargetUrl(
                            { repository: event.repository, environment: "" },
                            event.run,
                          )}
                        >
                          {event.run.slice(0, 10)}
                        </a>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="audit-event-summary">
                      {auditSummary(event)}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={6}>No audit events match these filters.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
      <Pagination
        label="Audit events"
        page={page}
        size={size}
        total={result?.total ?? 0}
        loading={loading}
        onPage={(value) => {
          const parameters = new URLSearchParams(search);
          parameters.set("page", String(value));
          update(parameters);
        }}
        onSize={(value) => {
          const parameters = new URLSearchParams(search);
          parameters.set("page_size", String(value));
          parameters.delete("page");
          update(parameters);
        }}
      />
    </SettingsPanel>
  );
}
