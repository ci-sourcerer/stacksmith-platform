import { expect, it } from "vitest";
import {
  effectiveLayers,
  layerReference,
  layerSource,
  moveLayer,
  parseLayer,
  validLayer,
} from "./layers";

const base = `git+https://github.com/acme/platform.git//config/base.yaml@${"a".repeat(40)}`;
const extra = `git+https://github.com/acme/service//config/extra.yaml@${"b".repeat(40)}`;

it("round-trips existing pinned references without silently rewriting them", () => {
  expect(layerReference(parseLayer(base, "base"))).toBe(base);
  expect(validLayer(parseLayer(base, "base"))).toBe(true);
  expect(layerSource(base)).toBe(
    `https://github.com/acme/platform/blob/${"a".repeat(40)}/config/base.yaml`,
  );
});
it("preserves unsupported references until explicitly replaced", () => {
  const layer = parseLayer("/deployment/platform.yaml", "local");
  expect(layerReference(layer)).toBe("/deployment/platform.yaml");
  expect(validLayer(layer)).toBe(false);
  expect(layerSource(layerReference(layer))).toBeUndefined();
});
it("rejects incomplete or mutable rows", () => {
  expect(
    validLayer({
      id: "test",
      repository: "acme/repo",
      file: "config.yaml",
      commit: "main",
    }),
  ).toBe(false);
  expect(validLayer({ ...parseLayer(base, "test"), file: "" })).toBe(false);
});
it("matches inheritance, replacement, and first-occurrence deduplication", () => {
  expect(effectiveLayers([base], [extra], "inherit", true)).toEqual([
    { reference: base, origin: "Platform" },
  ]);
  expect(effectiveLayers([base], [base, extra], "append", true)).toEqual([
    { reference: base, origin: "Platform" },
    { reference: extra, origin: "Repository" },
  ]);
  expect(effectiveLayers([base], [extra], "replace", true)).toEqual([
    { reference: extra, origin: "Repository" },
  ]);
  expect(
    effectiveLayers([], [extra, base], "replace", false).map(
      (item) => item.reference,
    ),
  ).toEqual([extra, base]);
});
it("moves rows without changing their identity and respects boundaries", () => {
  const layers = [parseLayer(base, "base"), parseLayer(extra, "extra")];
  expect(moveLayer(layers, 0, 1).map((item) => item.id)).toEqual([
    "extra",
    "base",
  ]);
  expect(moveLayer(layers, 0, -1)).toEqual(layers);
});
