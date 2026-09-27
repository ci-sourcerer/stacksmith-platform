import type { Draft } from "./history";
import type { ComponentDefinition, Relationship } from "./types";

export type AssociationScope =
  { kind: "stack" } | { kind: "component"; component: string };

export interface DisabledAssociation {
  rule: string;
  scope: AssociationScope;
}

export function setRuleEnabled(
  draft: Draft,
  rule: string,
  enabled: boolean,
  scope: AssociationScope = { kind: "stack" },
): Draft {
  if (scope.kind === "component") {
    if (!Object.hasOwn(draft.components, scope.component)) return draft;
    const component = draft.components[scope.component];
    if (enabled === !component.disabled_associations?.includes(rule))
      return draft;
    const updated: ComponentDefinition = {
      ...component,
      disabled_associations: enabled
        ? component.disabled_associations!.filter((name) => name !== rule)
        : [...(component.disabled_associations ?? []), rule],
    };
    if (!updated.disabled_associations?.length)
      delete updated.disabled_associations;
    return {
      ...draft,
      components: { ...draft.components, [scope.component]: updated },
    };
  }
  if (enabled === !draft.disabled.includes(rule)) return draft;
  return {
    ...draft,
    disabled: enabled
      ? draft.disabled.filter((name) => name !== rule)
      : [...new Set([...draft.disabled, rule])],
  };
}

export function affectedConnections(
  relationships: Relationship[],
  rule: string,
  scope: AssociationScope,
): Relationship[] {
  return relationships.filter(
    (edge) =>
      edge.kind === "association" &&
      edge.rule === rule &&
      (scope.kind === "stack" ||
        edge.source === scope.component ||
        edge.target === scope.component),
  );
}

export function disabledAssociationScopes(
  draft: Pick<Draft, "components" | "disabled">,
): DisabledAssociation[] {
  return [
    ...[...new Set(draft.disabled)].sort().map((rule) => ({
      rule,
      scope: { kind: "stack" as const },
    })),
    ...Object.entries(draft.components)
      .sort(([a], [b]) => a.localeCompare(b))
      .flatMap(([component, value]) =>
        [...new Set(value.disabled_associations ?? [])].sort().map((rule) => ({
          rule,
          scope: { kind: "component" as const, component },
        })),
      ),
  ];
}

export function visibleAssociation(
  draft: Pick<Draft, "components" | "disabled">,
  edge: Relationship,
): boolean {
  return (
    edge.kind === "association" &&
    Object.hasOwn(draft.components, edge.source) &&
    Object.hasOwn(draft.components, edge.target) &&
    !draft.disabled.includes(edge.rule ?? "") &&
    !draft.components[edge.source].disabled_associations?.includes(
      edge.rule ?? "",
    ) &&
    !draft.components[edge.target].disabled_associations?.includes(
      edge.rule ?? "",
    )
  );
}
