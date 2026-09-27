import { ApiError } from "../api";

export function InspectionDetails({ error }: { error: unknown }) {
  if (!(error instanceof ApiError) || !error.diagnostics) return null;
  return (
    <details className="inspection-details">
      <summary>Inspection details</summary>
      <p>
        {error.diagnostics.stage} · {error.diagnostics.code} · exit{" "}
        {error.diagnostics.exit_code}
      </p>
      {error.diagnostics.stderr && (
        <pre aria-label="Inspection standard error">
          {error.diagnostics.stderr}
        </pre>
      )}
      {error.diagnostics.stdout && (
        <pre aria-label="Inspection standard output">
          {error.diagnostics.stdout}
        </pre>
      )}
    </details>
  );
}
