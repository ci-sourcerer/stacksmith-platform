import type { Draft } from "./history";
import type { Snapshot } from "./types";

export interface Recovery {
  version: 1;
  savedAt: string;
  base: Pick<Snapshot, "repository" | "path" | "branch" | "head" | "source">;
  draft: Draft;
}

export function recoveryKey(snapshot: Snapshot, principal: string): string {
  return `composer-draft:${JSON.stringify([principal, snapshot.repository, snapshot.path, snapshot.branch])}`;
}

export function recoveryRecord(snapshot: Snapshot, draft: Draft): Recovery {
  return {
    version: 1,
    savedAt: new Date().toISOString(),
    base: {
      repository: snapshot.repository,
      path: snapshot.path,
      branch: snapshot.branch,
      head: snapshot.head,
      source: snapshot.source,
    },
    draft,
  };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPoint(value: unknown): boolean {
  return (
    isObject(value) &&
    typeof value.x === "number" &&
    Number.isFinite(value.x) &&
    typeof value.y === "number" &&
    Number.isFinite(value.y)
  );
}

function isStrings(value: unknown): value is string[] {
  return (
    Array.isArray(value) && value.every((item) => typeof item === "string")
  );
}

function isComponent(value: unknown): boolean {
  return (
    isObject(value) &&
    typeof value.type === "string" &&
    (value.properties === undefined || isObject(value.properties)) &&
    (value.tags === undefined || isStrings(value.tags)) &&
    (value.disabled_associations === undefined ||
      isStrings(value.disabled_associations))
  );
}

function isDraft(value: unknown): value is Draft {
  return (
    isObject(value) &&
    isObject(value.components) &&
    Object.values(value.components).every(isComponent) &&
    isStrings(value.disabled) &&
    isObject(value.positions) &&
    Object.values(value.positions).every(isPoint) &&
    (value.renames === undefined ||
      (Array.isArray(value.renames) &&
        value.renames.length <= 100 &&
        value.renames.every(
          (rename) =>
            isObject(rename) &&
            typeof rename.old === "string" &&
            typeof rename.new === "string" &&
            /^[A-Za-z0-9_-]{1,64}$/.test(rename.new),
        ))) &&
    Array.isArray(value.routes) &&
    value.routes.every(
      (route) =>
        isObject(route) &&
        [route.source, route.target, route.output, route.property].every(
          (field) => typeof field === "string",
        ) &&
        (route.kind === "reference" || route.kind === "association") &&
        (route.rule === null || typeof route.rule === "string") &&
        Array.isArray(route.path) &&
        route.path.every(
          (part) => typeof part === "string" || Number.isInteger(part),
        ) &&
        Array.isArray(route.points) &&
        route.points.every(isPoint) &&
        (route.label_position === undefined || isPoint(route.label_position)),
    )
  );
}

export function parseRecovery(source: string): Recovery {
  const value: unknown = JSON.parse(source);
  if (
    !isObject(value) ||
    value.version !== 1 ||
    typeof value.savedAt !== "string" ||
    !Number.isFinite(Date.parse(value.savedAt)) ||
    !isObject(value.base) ||
    typeof value.base.source !== "string" ||
    typeof value.base.path !== "string" ||
    typeof value.base.branch !== "string" ||
    !(
      value.base.repository === null ||
      typeof value.base.repository === "string"
    ) ||
    !(value.base.head === null || typeof value.base.head === "string") ||
    !isDraft(value.draft)
  )
    throw new Error("The saved draft format is invalid.");
  return value as unknown as Recovery;
}

export function canRestore(recovery: Recovery, snapshot: Snapshot): boolean {
  return (
    snapshot.editable &&
    recovery.base.repository === snapshot.repository &&
    recovery.base.path === snapshot.path &&
    recovery.base.branch === snapshot.branch &&
    recovery.base.head === snapshot.head &&
    recovery.base.source === snapshot.source
  );
}
