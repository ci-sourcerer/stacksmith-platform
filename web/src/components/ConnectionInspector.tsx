import { useEffect, useRef } from "react";
import { ArrowLeft, Link2Off, ShieldCheck, Undo2, X } from "lucide-react";
import { affectedConnections, type AssociationScope } from "../associations";
import type { Relationship } from "../types";
import { AssociationImpact, ConnectionEndpoints } from "./AssociationImpact";

export function ConnectionInspector({
  relationship,
  description,
  knownConnections,
  componentDisabled,
  stackDisabled,
  readOnly,
  stale,
  error,
  onToggle,
  onRemoveReference,
  onManage,
  onRetry,
  onClose,
}: {
  relationship: Relationship;
  description?: string;
  knownConnections: Relationship[];
  componentDisabled: boolean;
  stackDisabled: boolean;
  readOnly: boolean;
  stale: boolean;
  error: string;
  onToggle: (rule: string, enabled: boolean, scope: AssociationScope) => void;
  onRemoveReference: () => void;
  onManage: () => void;
  onRetry: () => void;
  onClose: () => void;
}) {
  const actionsRef = useRef<HTMLDivElement>(null);
  const previousDisabled = useRef({
    component: componentDisabled,
    stack: stackDisabled,
  });
  useEffect(() => {
    if (
      previousDisabled.current.component !== componentDisabled ||
      previousDisabled.current.stack !== stackDisabled
    )
      actionsRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    previousDisabled.current = {
      component: componentDisabled,
      stack: stackDisabled,
    };
  }, [componentDisabled, stackDisabled]);
  const scope: AssociationScope = {
    kind: "component",
    component: relationship.target,
  };
  return (
    <aside
      className="inspector connection-inspector"
      aria-label="Connection details"
    >
      <div className="panel-title">
        <span>Connection details</span>
        <button
          className="icon-button"
          aria-label="Close connection details"
          onClick={onClose}
        >
          <X size={16} />
        </button>
      </div>
      <div className="inspector-body">
        <button className="text-button connection-back" onClick={onManage}>
          <ArrowLeft size={14} />
          Managed connections
        </button>
        <ConnectionEndpoints relationship={relationship} />
        {relationship.kind === "association" && relationship.rule ? (
          <>
            <span className="managed-badge">
              <ShieldCheck size={14} />
              Managed by platform
            </span>
            <h3>Rule</h3>
            <code className="connection-rule-name">{relationship.rule}</code>
            {description && <p>{description}</p>}
            <AssociationImpact
              scope={scope}
              relationships={affectedConnections(
                knownConnections,
                relationship.rule,
                scope,
              )}
              stale={stale || componentDisabled || stackDisabled}
            />
            {!stackDisabled && (
              <details className="connection-more">
                <summary>More actions</summary>
                <button
                  className="text-button danger"
                  disabled={readOnly}
                  onClick={() =>
                    onToggle(relationship.rule!, false, { kind: "stack" })
                  }
                >
                  Disable rule across entire stack…
                </button>
              </details>
            )}
          </>
        ) : (
          <>
            <span className="managed-badge">Authored reference</span>
            <p>
              This connection is written in the receiving component’s
              properties.
            </p>
          </>
        )}
        {error && (
          <div className="connection-error" role="status">
            <p>{error}</p>
          </div>
        )}
        {readOnly && (
          <p className="muted">Connections are read-only in this view.</p>
        )}
      </div>
      <div className="connection-actions" ref={actionsRef}>
        {relationship.kind === "association" && relationship.rule ? (
          <>
            {stackDisabled ? (
              <div className="connection-result">
                <p role="status">
                  Disabled for entire stack. Re-enable the stack rule before
                  reconnecting this component.
                </p>
                <button
                  className="button small"
                  disabled={readOnly}
                  onClick={() =>
                    onToggle(relationship.rule!, true, { kind: "stack" })
                  }
                >
                  Re-enable across entire stack…
                </button>
              </div>
            ) : componentDisabled ? (
              <div className="connection-result">
                <p role="status">
                  Disconnected from <strong>{relationship.target}</strong>
                </p>
                <button
                  className="button small"
                  disabled={readOnly}
                  onClick={() => onToggle(relationship.rule!, true, scope)}
                >
                  <Undo2 size={14} />
                  Undo disconnect
                </button>
              </div>
            ) : (
              <button
                className="button danger connection-primary"
                disabled={readOnly}
                onClick={() => onToggle(relationship.rule!, false, scope)}
              >
                <Link2Off size={15} />
                Disconnect from {relationship.target}
              </button>
            )}
          </>
        ) : (
          <button
            className="button danger connection-primary"
            disabled={readOnly}
            onClick={onRemoveReference}
          >
            <Link2Off size={15} />
            Remove reference
          </button>
        )}
        {error && (
          <button className="text-button connection-retry" onClick={onRetry}>
            Retry inspection
          </button>
        )}
      </div>
    </aside>
  );
}
