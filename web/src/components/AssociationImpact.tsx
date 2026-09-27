import type { AssociationScope } from "../associations";
import type { Relationship } from "../types";

export function ConnectionEndpoints({
  relationship,
}: {
  relationship: Relationship;
}) {
  return (
    <div className="connection-endpoints">
      <code>
        {relationship.source}.{relationship.output}
      </code>
      <span aria-label="connects to">→</span>
      <code>
        {relationship.target}.{relationship.path.join(".")}
      </code>
    </div>
  );
}

export function AssociationImpact({
  scope,
  relationships,
  stale,
}: {
  scope: AssociationScope;
  relationships: Relationship[];
  stale: boolean;
}) {
  return (
    <section className="connection-impact" aria-label="Affected connections">
      <h3>Affected connections ({relationships.length})</h3>
      <p>
        <strong>Scope: </strong>
        {scope.kind === "stack" ? "Entire stack" : scope.component}
      </p>
      <p className="muted">
        {scope.kind === "stack"
          ? "This changes the rule for every component in this stack, including future matches."
          : "This component is excluded from the rule as both a receiver and a producer. Other rules and authored references stay enabled."}
      </p>
      {stale && (
        <p className="impact-stale">
          From the last inspection. Connections may have changed.
        </p>
      )}
      {relationships.length ? (
        <ul>
          {relationships.map((edge, index) => (
            <li key={index}>
              <ConnectionEndpoints relationship={edge} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">
          No connections were found in the last inspection.
        </p>
      )}
    </section>
  );
}
