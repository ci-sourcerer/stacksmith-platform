import { describe, expect, it } from "vitest";
import { createDraftHistory, inspectionBody } from "./history";
import {
  canRestore,
  parseRecovery,
  recoveryKey,
  recoveryRecord,
} from "./recovery";
import type { Snapshot } from "./types";

const snapshot: Snapshot = {
  repository: "example/stack",
  path: "stack.yaml",
  branch: "main",
  head: "base-commit",
  snapshot: "signed-token",
  source: "components: {}\n",
  editable: true,
  reason: null,
  document: { name: "test", components: {} },
};

describe("draft recovery", () => {
  it("round trips components, rules and layout without persisting the signed snapshot", () => {
    const draft = createDraftHistory({
      components: { api: { type: "service", properties: { size: 2 } } },
      disabled: ["default-role"],
      renames: [{ old: "api", new: "service" }],
      positions: { api: { x: 20, y: 40 } },
    }).present;
    const saved = JSON.stringify(recoveryRecord(snapshot, draft));
    expect(parseRecovery(saved).draft).toEqual(draft);
    expect(saved).not.toContain("signed-token");
    expect(
      canRestore(parseRecovery(saved), {
        ...snapshot,
        snapshot: "fresh-token",
      }),
    ).toBe(true);
  });

  it("isolates accounts, repositories, paths, and branches", () => {
    expect(
      new Set([
        recoveryKey(snapshot, "alice"),
        recoveryKey(snapshot, "bob"),
        recoveryKey({ ...snapshot, repository: "other/stack" }, "alice"),
        recoveryKey({ ...snapshot, path: "other.yaml" }, "alice"),
        recoveryKey({ ...snapshot, branch: "feature" }, "alice"),
      ]).size,
    ).toBe(5);
  });

  it.each([
    { head: "new-commit" },
    { source: "components: {different: {type: service}}" },
    { repository: "other/stack" },
    { path: "other.yaml" },
    { branch: "feature" },
    { editable: false },
  ])("does not restore over a different base: %j", (change) => {
    expect(
      canRestore(recoveryRecord(snapshot, createDraftHistory().present), {
        ...snapshot,
        ...change,
      }),
    ).toBe(false);
  });

  it.each([
    "not-json",
    "null",
    '{"version": 2}',
    JSON.stringify({
      ...recoveryRecord(snapshot, createDraftHistory().present),
      draft: { components: [], disabled: [], positions: {}, routes: [] },
    }),
  ])("rejects malformed recovery data", (source) => {
    expect(() => parseRecovery(source)).toThrow();
  });

  it.each([
    { components: { api: { type: "service", tags: {} } } },
    { components: { api: { type: "service", properties: [] } } },
    { positions: { api: { x: "10", y: 20 } } },
    { routes: [{ source: "api" }] },
  ])("rejects data that cannot be rendered safely: %j", (invalid) => {
    expect(() =>
      parseRecovery(
        JSON.stringify({
          ...recoveryRecord(snapshot, createDraftHistory().present),
          draft: { ...createDraftHistory().present, ...invalid },
        }),
      ),
    ).toThrow();
  });
});

describe("inspection dependencies", () => {
  it("ignores all layout edits but detects property and rule changes", () => {
    const draft = createDraftHistory({
      components: { api: { type: "service" } },
    }).present;
    expect(
      inspectionBody(snapshot.snapshot, {
        ...draft,
        positions: { api: { x: 100, y: 200 } },
        routes: [],
      } as typeof draft),
    ).toBe(inspectionBody(snapshot.snapshot, draft));
    expect(
      inspectionBody(snapshot.snapshot, { ...draft, disabled: ["rule"] }),
    ).not.toBe(inspectionBody(snapshot.snapshot, draft));
    expect(
      inspectionBody(snapshot.snapshot, {
        ...draft,
        components: { api: { type: "service", properties: { size: 2 } } },
      }),
    ).not.toBe(inspectionBody(snapshot.snapshot, draft));
  });
});
