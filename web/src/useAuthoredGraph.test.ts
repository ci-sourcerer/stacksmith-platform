import { expect, it } from "vitest";
import { knownGraph } from "./useAuthoredGraph";
import { connectComponents } from "./model";

it("draws emitted bracket references immediately, including escaped names", () => {
  const components = connectComponents(
    {
      'data"store': { type: "data" },
      app: { type: "service", properties: { settings: [{ keep: true }] } },
    },
    'data"store',
    "url",
    "app",
    ["settings", 0, "url"],
  );
  expect(knownGraph(components, new Map())).toEqual([
    {
      source: 'data"store',
      output: "url",
      target: "app",
      property: "settings",
      path: ["settings", 0, "url"],
      kind: "reference",
      rule: null,
    },
  ]);
});

it("relocates known expressions after a list removal without retaining stale paths", () => {
  expect(
    knownGraph(
      {
        app: {
          type: "service",
          properties: { values: ["prefix {{ components.data.url }}"] },
        },
      },
      new Map([
        [
          "prefix {{ components.data.url }}",
          [{ source: "data", output: "url" }],
        ],
      ]),
    )[0].path,
  ).toEqual(["values", 0]);
  expect(
    knownGraph(
      { app: { type: "service", properties: {} } },
      new Map([
        [
          "prefix {{ components.data.url }}",
          [{ source: "data", output: "url" }],
        ],
      ]),
    ),
  ).toEqual([]);
});
