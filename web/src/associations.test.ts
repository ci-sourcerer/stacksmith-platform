import { expect, it } from "vitest";
import {
  affectedConnections,
  disabledAssociationScopes,
  setRuleEnabled,
  visibleAssociation,
} from "./associations";
import { createDraftHistory, draftHistoryReducer } from "./history";
import type { Relationship } from "./types";

const edges: Relationship[] = ["api", "worker"].map((target) => ({
  source: "role",
  output: "arn",
  target,
  property: "role_arn",
  path: ["role_arn"],
  kind: "association",
  rule: "execution-role",
}));

it("includes every binding in both component roles, while excluding other rules and authored edges", () => {
  const outgoing = { ...edges[0], source: "api", target: "downstream" };
  const secondBinding = {
    ...edges[0],
    property: "backup_role",
    path: ["backup_role"],
  };
  expect(
    affectedConnections(
      [
        ...edges,
        outgoing,
        secondBinding,
        { ...edges[0], rule: "other-rule" },
        { ...edges[0], kind: "reference", rule: null },
      ],
      "execution-role",
      { kind: "component", component: "api" },
    ),
  ).toEqual([edges[0], outgoing, secondBinding]);
  expect(
    affectedConnections(edges, "execution-role", { kind: "stack" }),
  ).toEqual(edges);
});

it("opts out a component without changing its properties, other opt-outs, layout or authored references", () => {
  const initial = createDraftHistory({
    components: {
      role: { type: "role" },
      api: {
        type: "service",
        tags: ["keep"],
        properties: { role_arn: "authored" },
        disabled_associations: ["other"],
      },
      worker: { type: "service" },
    },
    disabled: ["global-other"],
    routes: [{ ...edges[0], points: [{ x: 10, y: 20 }] }],
  }).present;
  const scope = { kind: "component" as const, component: "api" };
  const changed = setRuleEnabled(initial, "execution-role", false, scope);
  expect(changed.components.api.disabled_associations).toEqual([
    "other",
    "execution-role",
  ]);
  expect(changed.components.api.properties).toBe(
    initial.components.api.properties,
  );
  expect(changed.components.api.tags).toBe(initial.components.api.tags);
  expect(changed.routes).toBe(initial.routes);
  expect(changed.positions).toBe(initial.positions);
  expect(changed.disabled).toBe(initial.disabled);
  expect(visibleAssociation(changed, edges[0])).toBe(false);
  expect(visibleAssociation(changed, edges[1])).toBe(true);
  expect(
    visibleAssociation(changed, {
      ...edges[0],
      source: "api",
      target: "worker",
    }),
  ).toBe(false);
  expect(setRuleEnabled(changed, "execution-role", false, scope)).toBe(changed);
  expect(setRuleEnabled(changed, "execution-role", true, scope)).toEqual(
    initial,
  );
  expect(
    setRuleEnabled(initial, "execution-role", false, {
      kind: "component",
      component: "missing",
    }),
  ).toBe(initial);
});

it("scoped undo preserves intervening edits, removes the empty opt-out field, and supports history undo/redo", () => {
  const initial = createDraftHistory({
    components: { api: { type: "service" } },
  });
  const scope = { kind: "component" as const, component: "api" };
  const changed = draftHistoryReducer(initial, {
    type: "change",
    update: (draft) => setRuleEnabled(draft, "execution-role", false, scope),
  });
  expect(draftHistoryReducer(changed, { type: "undo" }).present).toEqual(
    initial.present,
  );
  expect(
    draftHistoryReducer(draftHistoryReducer(changed, { type: "undo" }), {
      type: "redo",
    }).present,
  ).toEqual(changed.present);
  const restored = setRuleEnabled(
    {
      ...changed.present,
      components: {
        ...changed.present.components,
        api: {
          ...changed.present.components.api,
          properties: { edited: true },
        },
      },
    },
    "execution-role",
    true,
    scope,
  );
  expect(restored.components.api).toEqual({
    type: "service",
    properties: { edited: true },
  });
});

it("keeps stack-wide and component opt-outs independent and discovers unknown disabled rules from the draft", () => {
  const draft = createDraftHistory({
    components: {
      role: { type: "role" },
      api: {
        type: "service",
        disabled_associations: ["execution-role", "unknown"],
      },
    },
    disabled: ["execution-role"],
  }).present;
  expect(disabledAssociationScopes(draft)).toEqual([
    { rule: "execution-role", scope: { kind: "stack" } },
    { rule: "execution-role", scope: { kind: "component", component: "api" } },
    { rule: "unknown", scope: { kind: "component", component: "api" } },
  ]);
  const stackRestored = setRuleEnabled(draft, "execution-role", true);
  expect(visibleAssociation(stackRestored, edges[0])).toBe(false);
  expect(
    visibleAssociation(
      setRuleEnabled(stackRestored, "execution-role", true, {
        kind: "component",
        component: "api",
      }),
      edges[0],
    ),
  ).toBe(true);
  expect(
    visibleAssociation(
      setRuleEnabled(draft, "execution-role", true, {
        kind: "component",
        component: "api",
      }),
      edges[0],
    ),
  ).toBe(false);
});

it("disables one stack-wide rule, retains authored references and routing, and supports undo", () => {
  const initial = createDraftHistory({
    components: {
      api: { type: "service", properties: { role_arn: "authored-value" } },
    },
    routes: [{ ...edges[0], points: [{ x: 10, y: 20 }] }],
  });
  const changed = draftHistoryReducer(initial, {
    type: "change",
    update: (draft) => setRuleEnabled(draft, "execution-role", false),
  });
  expect(changed.present.disabled).toEqual(["execution-role"]);
  expect(changed.present.components).toBe(initial.present.components);
  expect(changed.present.routes).toBe(initial.present.routes);
  expect(
    setRuleEnabled(changed.present, "execution-role", false).disabled,
  ).toEqual(["execution-role"]);
  expect(
    setRuleEnabled(changed.present, "execution-role", true).disabled,
  ).toEqual([]);
  expect(draftHistoryReducer(changed, { type: "undo" }).present).toEqual(
    initial.present,
  );
});
