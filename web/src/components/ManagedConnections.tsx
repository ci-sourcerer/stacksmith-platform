import { ShieldCheck, X } from "lucide-react";
import type { DisabledAssociation, AssociationScope } from "../associations";
import { affectedConnections } from "../associations";
import type { Relationship } from "../types";

export interface ManagedRule {
  name: string;
  description?: string;
}

function DisabledScopeLabel({ item }: { item: DisabledAssociation }) {
  return (
    <span>
      Disabled for{" "}
      {item.scope.kind === "stack" ? (
        "entire stack"
      ) : (
        <code>{item.scope.component}</code>
      )}
    </span>
  );
}

export function ManagedConnectionSummary({
  disabled,
  onManage,
}: {
  disabled: DisabledAssociation[];
  onManage: () => void;
}) {
  return (
    <section
      className="managed-summary"
      aria-label="Managed connection settings"
    >
      <h3>Managed connections</h3>
      {disabled.length ? (
        <ul>
          {disabled.map((item) => (
            <li key={JSON.stringify(item)}>
              <code>{item.rule}</code>
              <DisabledScopeLabel item={item} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No rule opt-outs in this draft.</p>
      )}
      <button className="text-button" onClick={onManage}>
        Manage connections
      </button>
    </section>
  );
}

export function ManagedConnections({
  rules,
  disabled,
  knownConnections,
  stale,
  readOnly,
  error,
  onToggle,
  onRetry,
  onClose,
}: {
  rules: ManagedRule[];
  disabled: DisabledAssociation[];
  knownConnections: Relationship[];
  stale: boolean;
  readOnly: boolean;
  error: string;
  onToggle: (rule: string, enabled: boolean, scope: AssociationScope) => void;
  onRetry: () => void;
  onClose: () => void;
}) {
  return (
    <aside
      className="inspector managed-connections"
      aria-label="Managed connections"
    >
      <div className="panel-title">
        <span>Managed connections</span>
        <button
          className="icon-button"
          aria-label="Close managed connections"
          onClick={onClose}
        >
          <X size={16} />
        </button>
      </div>
      <div className="inspector-body">
        <p>
          Rules connect matching components automatically. Opt-outs stay in your
          stack source until you re-enable them.
        </p>
        <h3>Disabled scopes ({disabled.length})</h3>
        {disabled.length ? (
          disabled.map((item) => (
            <div className="managed-scope" key={JSON.stringify(item)}>
              <code className="connection-rule-name">{item.rule}</code>
              <DisabledScopeLabel item={item} />
              {item.scope.kind === "component" &&
              disabled.some(
                (other) =>
                  other.rule === item.rule && other.scope.kind === "stack",
              ) ? (
                <>
                  <p className="muted">
                    The stack-wide opt-out takes precedence.
                  </p>
                  <button
                    className="text-button"
                    disabled={readOnly}
                    onClick={() => onToggle(item.rule, true, { kind: "stack" })}
                  >
                    Review stack-wide setting…
                  </button>
                </>
              ) : (
                <button
                  className="button small"
                  disabled={readOnly}
                  onClick={() => onToggle(item.rule, true, item.scope)}
                >
                  {item.scope.kind === "stack"
                    ? "Re-enable across entire stack…"
                    : `Re-enable for ${item.scope.component}`}
                </button>
              )}
            </div>
          ))
        ) : (
          <p className="muted">No rule opt-outs in this draft.</p>
        )}
        <h3>Platform rules</h3>
        {stale && (
          <p className="impact-stale">
            From the last inspection. Connections may have changed.
          </p>
        )}
        {rules.map((rule) => (
          <div className="managed-rule" key={rule.name}>
            <ShieldCheck size={16} aria-hidden="true" />
            <div>
              <code className="connection-rule-name">{rule.name}</code>
              {rule.description && <p>{rule.description}</p>}
              <p className="muted">
                {
                  affectedConnections(knownConnections, rule.name, {
                    kind: "stack",
                  }).length
                }{" "}
                connections in the last inspection
              </p>
              {!disabled.some(
                (item) =>
                  item.rule === rule.name && item.scope.kind === "stack",
              ) && (
                <button
                  className="text-button"
                  disabled={readOnly}
                  onClick={() => onToggle(rule.name, false, { kind: "stack" })}
                >
                  Disable across entire stack…
                </button>
              )}
            </div>
          </div>
        ))}
        {!rules.length && (
          <p className="muted">No managed rules are configured.</p>
        )}
        {error && (
          <div className="connection-error" role="status">
            <p>{error}</p>
            <button className="text-button" onClick={onRetry}>
              Retry inspection
            </button>
          </div>
        )}
        {readOnly && (
          <p className="muted">Connections are read-only in this view.</p>
        )}
      </div>
    </aside>
  );
}
