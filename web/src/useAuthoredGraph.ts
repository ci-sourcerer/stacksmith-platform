import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { valueAtPath } from "./model";
import type { Components, Relationship } from "./types";

export interface AuthoredGraph {
  edges: Relationship[];
  diagnostics: { path: (string | number)[]; message: string }[];
}

type Reference = Pick<Relationship, "source" | "output">;

export function knownGraph(
  components: Components,
  templates: Map<string, Reference[]>,
): Relationship[] {
  return Object.entries(components).flatMap(([target, component]) =>
    referencesInValue(component.properties ?? {}, [], templates).map(
      (reference) => ({
        ...reference,
        target,
        property: String(reference.path[0]),
        kind: "reference" as const,
        rule: null,
      }),
    ),
  );
}

function referencesInValue(
  value: unknown,
  path: (string | number)[],
  templates: Map<string, Reference[]>,
): (Reference & { path: (string | number)[] })[] {
  if (Array.isArray(value))
    return value.flatMap((child, index) =>
      referencesInValue(child, [...path, index], templates),
    );
  if (value !== null && typeof value === "object")
    return Object.entries(value).flatMap(([key, child]) =>
      referencesInValue(child, [...path, key], templates),
    );
  if (typeof value !== "string") return [];
  return (templates.get(value) ?? generatedReference(value)).map(
    (reference) => ({ ...reference, path }),
  );
}

function generatedReference(value: string): Reference[] {
  const match =
    /^\{\{\s*components\[("(?:\\.|[^"\\])*")\]\[("(?:\\.|[^"\\])*")\]\s*\}\}$/.exec(
      value,
    );
  if (!match) return [];
  try {
    return [{ source: JSON.parse(match[1]), output: JSON.parse(match[2]) }];
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
    return [];
  }
}

export function useAuthoredGraph(
  key: string,
  components: Components,
  enabled: boolean,
) {
  const [cache, setCache] = useState(new Map<string, AuthoredGraph>());
  const [failure, setFailure] = useState<{ key: string; message: string }>();
  const [attempt, setAttempt] = useState(0);
  const templates = useRef(new Map<string, Reference[]>());
  useEffect(() => {
    if (!enabled) return;
    const abort = new AbortController();
    api<AuthoredGraph>("/graph", JSON.parse(key), abort.signal)
      .then((graph) => {
        if (abort.signal.aborted) return;
        for (const edge of graph.edges) {
          const template = valueAtPath(
            components[edge.target]?.properties,
            edge.path,
          );
          if (typeof template !== "string") continue;
          const references = templates.current.get(template) ?? [];
          if (
            !references.some(
              (item) =>
                item.source === edge.source && item.output === edge.output,
            )
          )
            templates.current.set(template, [
              ...references,
              { source: edge.source, output: edge.output },
            ]);
        }
        setCache((previous) => {
          const next = new Map(previous).set(key, graph);
          if (next.size > 201) next.delete(next.keys().next().value!);
          return next;
        });
      })
      .catch((error: unknown) => {
        if (!abort.signal.aborted)
          setFailure({
            key,
            message:
              error instanceof Error
                ? error.message
                : "Could not inspect authored references.",
          });
      });
    return () => abort.abort();
  }, [key, enabled, attempt]);
  const edges = useMemo(
    () => cache.get(key)?.edges ?? knownGraph(components, templates.current),
    [cache, key, components],
  );
  return {
    edges,
    diagnostics: cache.get(key)?.diagnostics ?? [],
    current: cache.has(key),
    error: failure?.key === key ? failure.message : "",
    retry: () => {
      setFailure(undefined);
      setAttempt((value) => value + 1);
    },
  };
}
