import { expect, it } from "vitest";
import { diagramNodeData, diagramRoutes } from "./diagram";
import { relationshipKey } from "./model";
import type { Relationship } from "./types";

const edge: Relationship = {
  source: "producer",
  target: "consumer",
  output: "arn",
  property: "policy",
  path: ["policy"],
  kind: "reference",
  rule: null,
};

it("preserves catalog ports and appends unique referenced ports in encounter order", () => {
  const data = diagramNodeData(
    { producer: { type: "role" }, consumer: { type: "unknown" } },
    {
      role: {
        display_name: "Role",
        module_source: "role",
        module_version: "1",
        inputs: [],
        outputs: [{ name: "name" }, { name: "arn", description: "ARN" }],
      },
    },
    [edge, edge, { ...edge, output: "id", property: "identity" }],
  );
  expect(data.get("producer")?.definition.outputs).toEqual([
    { name: "name" },
    { name: "arn", description: "ARN" },
    { name: "id" },
  ]);
  expect(data.get("consumer")?.definition.inputs).toEqual([
    { name: "policy" },
    { name: "identity" },
  ]);
});

it("keeps the first saved route when duplicate keys exist", () => {
  const first = { ...edge, points: [{ x: 10, y: 20 }] };
  expect(
    diagramRoutes([first, { ...edge, points: [{ x: 50, y: 60 }] }]).get(
      relationshipKey(edge),
    ),
  ).toBe(first);
});

it("drops removed relationships and components without retaining old indexes", () => {
  const components = {
    producer: { type: "role" },
    consumer: { type: "unknown" },
  };
  expect(
    diagramNodeData(components, {}, [edge]).get("consumer")?.definition.inputs,
  ).toHaveLength(1);
  expect(
    diagramNodeData(components, {}, []).get("consumer")?.definition.inputs,
  ).toEqual([]);
  expect(
    diagramNodeData({ consumer: components.consumer }, {}, [edge]).has(
      "producer",
    ),
  ).toBe(false);
});
