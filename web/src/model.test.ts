import { describe, expect, it } from "vitest";
import {
  canRemove,
  connectComponents,
  defaultJsonValue,
  isJsonValue,
  jsonValueType,
  nextComponentName,
  relationshipKey,
  removeReference,
  routesAfterRemoval,
} from "./model";

describe("authored graph changes", () => {
  it("connects nested values and appends list slots without replacing siblings", () => {
    const components = {
      data: { type: "database" },
      app: {
        type: "service",
        properties: { settings: { urls: ["keep"], replicas: 3 } },
      },
    };
    const next = connectComponents(components, "data", "endpoint", "app", [
      "settings",
      "urls",
      1,
    ]);
    expect(next.app.properties).toEqual({
      settings: {
        urls: ["keep", '{{ components["data"]["endpoint"] }}'],
        replicas: 3,
      },
    });
    expect(components.app.properties.settings.urls).toEqual(["keep"]);
    expect(() =>
      connectComponents(components, "data", "id", "app", [
        "settings",
        "replicas",
        "invalid",
      ]),
    ).toThrow("not an object");
    expect(() =>
      connectComponents(components, "data", "id", "app", [
        "settings",
        "urls",
        4,
      ]),
    ).toThrow("list slot");
  });
  it("only invalidates array routes whose indices move after a removal", () => {
    const edge = {
      source: "data",
      target: "app",
      output: "id",
      property: "settings",
      path: ["settings", "urls", 1],
      kind: "reference" as const,
      rule: null,
    };
    const earlier = { ...edge, path: ["settings", "urls", 0] };
    const later = { ...edge, path: ["settings", "urls", 2] };
    const sibling = { ...edge, path: ["settings", "replicas"] };
    expect(routesAfterRemoval([earlier, edge, later, sibling], edge)).toEqual([
      earlier,
      sibling,
    ]);
  });
  it("writes escaped bracket references and preserves unrelated properties", () => {
    expect(
      connectComponents(
        {
          "data-store": { type: "database" },
          api: { type: "service", properties: { replicas: 3 } },
        },
        "data-store",
        "endpoint",
        "api",
        "url",
      ).api.properties,
    ).toEqual({
      replicas: 3,
      url: '{{ components["data-store"]["endpoint"] }}',
    });
  });
  it("rejects self references", () => {
    expect(() =>
      connectComponents(
        { api: { type: "service" } },
        "api",
        "url",
        "api",
        "url",
      ),
    ).toThrow("own output");
  });
  it("chooses a unique component name", () => {
    expect(
      nextComponentName("database", {
        database: { type: "database" },
        "database-2": { type: "database" },
      }),
    ).toBe("database-3");
  });
  it("blocks removal of a producer with explicit consumers", () => {
    expect(
      canRemove(
        [
          {
            source: "database",
            target: "api",
            output: "endpoint",
            property: "url",
            path: ["url"],
            kind: "reference",
            rule: null,
          },
        ],
        "database",
      ),
    ).toBe(false);
    expect(
      canRemove(
        [
          {
            source: "network",
            target: "api",
            output: "id",
            property: "network_id",
            path: ["network_id"],
            kind: "association",
            rule: "managed",
          },
        ],
        "network",
      ),
    ).toBe(true);
  });

  it("removes a nested output reference without dropping sibling values", () => {
    const relationship = {
      source: "database",
      target: "api",
      output: "endpoint",
      property: "settings",
      path: ["settings", "database", "url"],
      kind: "reference" as const,
      rule: null,
    };
    expect(
      removeReference(
        {
          database: { type: "database" },
          api: {
            type: "service",
            properties: {
              settings: {
                database: { url: "reference", pool_size: 4 },
                replicas: 2,
              },
            },
          },
        },
        relationship,
      ).api.properties,
    ).toEqual({
      settings: { database: { pool_size: 4 }, replicas: 2 },
    });
    expect(relationshipKey(relationship)).toContain("database");
  });
});

describe("visual JSON values", () => {
  it("identifies every supported value type", () => {
    expect(
      ["value", 2, true, ["item"], { nested: null }, null].map(jsonValueType),
    ).toEqual(["string", "number", "boolean", "array", "object", "null"]);
  });

  it("creates safe empty values when changing a field type", () => {
    expect(defaultJsonValue("object")).toEqual({});
    expect(defaultJsonValue("array")).toEqual([]);
    expect(defaultJsonValue("boolean")).toBe(false);
  });

  it("rejects values JSON cannot represent", () => {
    expect(isJsonValue({ nested: ["ok", 1, false, null] })).toBe(true);
    expect(isJsonValue({ invalid: Number.NaN })).toBe(false);
    expect(isJsonValue({ invalid: undefined })).toBe(false);
  });
});
