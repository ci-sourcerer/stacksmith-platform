import { ChevronDown, Layers } from "lucide-react";
import type { InspectionContext } from "../types";

export function InspectionSummary({
  context,
}: {
  context?: InspectionContext | null;
}) {
  if (!context) return null;
  return (
    <details className="inspection-summary">
      <summary>
        <Layers size={16} aria-hidden="true" />
        <span>
          Inspection context:{" "}
          <span className="inspection-summary-mode">
            {context.mode === "deployment"
              ? `deployment ${context.environment}`
              : "stack preview"}
          </span>
        </span>
        <ChevronDown
          className="inspection-summary-chevron"
          size={16}
          aria-hidden="true"
        />
      </summary>
      <div className="inspection-summary-content">
        <p className="inspection-summary-description">
          {context.mode === "deployment"
            ? "Includes the selected deployment’s runfile, variables, and authorized preparation credentials."
            : "Uses repository and platform configuration. Deployment variables and credentials are excluded."}{" "}
          Deployment planning remains separate.
        </p>
        <dl>
          <div>
            <dt>Repository</dt>
            <dd>{context.repository}</dd>
          </div>
          <div>
            <dt>Source revision</dt>
            <dd>
              <code>{context.revision}</code>
            </dd>
          </div>
          <div>
            <dt>Stack file</dt>
            <dd>
              <code>{context.path}</code>
            </dd>
          </div>
          <div>
            <dt>Configuration revision</dt>
            <dd>
              <code>{context.config_revision}</code>
            </dd>
          </div>
          {context.mode === "deployment" && (
            <div>
              <dt>Deployment revision</dt>
              <dd>{context.environment_revision}</dd>
            </div>
          )}
        </dl>
      </div>
    </details>
  );
}
