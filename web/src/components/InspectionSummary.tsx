import type { InspectionContext } from "../types";

export function InspectionSummary({
  context,
}: {
  context?: InspectionContext | null;
}) {
  if (!context) return null;
  return (
    <details className="inspection-details">
      <summary>
        Inspection context:{" "}
        {context.mode === "deployment"
          ? `deployment ${context.environment}`
          : "stack preview"}
      </summary>
      <p>
        {context.mode === "deployment"
          ? "Includes the selected deployment’s runfile, variables, and authorized preparation credentials."
          : "Uses repository and platform configuration. Deployment variables and credentials are excluded."}
      </p>
      <p>Deployment planning remains separate.</p>
      <dl>
        <dt>Repository</dt>
        <dd>{context.repository}</dd>
        <dt>Source revision</dt>
        <dd>
          <code>{context.revision}</code>
        </dd>
        <dt>Stack file</dt>
        <dd>
          <code>{context.path}</code>
        </dd>
        <dt>Configuration revision</dt>
        <dd>
          <code>{context.config_revision}</code>
        </dd>
        {context.mode === "deployment" && (
          <>
            <dt>Deployment revision</dt>
            <dd>{context.environment_revision}</dd>
          </>
        )}
      </dl>
    </details>
  );
}
