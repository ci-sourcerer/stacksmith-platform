import type { Components, Relationship } from "./types";

export type JsonValue =
  string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export type JsonValueType =
  "string" | "number" | "boolean" | "array" | "object" | "null";

export interface ValueConstraint {
  type: JsonValueType | "any";
  items?: ValueConstraint;
  tupleItems?: ValueConstraint[];
  properties?: Record<string, ValueConstraint>;
  additionalProperties?: ValueConstraint;
}

export function parseTerraformType(source?: string): ValueConstraint {
  if (!source) return { type: "any" };
  const normalized = source.trim().replace(/^\$\{([\s\S]*)\}$/, "$1");
  let position = 0;

  function whitespace() {
    while (/\s/.test(normalized[position] ?? "")) position++;
  }

  function identifier(): string {
    whitespace();
    const match = /^[\w-]+/.exec(normalized.slice(position));
    if (!match) throw new Error("Invalid Terraform type");
    position += match[0].length;
    return match[0];
  }

  function expect(character: string) {
    whitespace();
    if (normalized[position] !== character)
      throw new Error("Invalid Terraform type");
    position++;
  }

  function parse(): ValueConstraint {
    const name = identifier();
    const simpleTypes: Record<string, JsonValueType | "any"> = {
      string: "string",
      number: "number",
      bool: "boolean",
      any: "any",
      dynamic: "any",
    };
    if (simpleTypes[name]) return { type: simpleTypes[name] };
    expect("(");
    if (name === "object") {
      expect("{");
      const properties: Record<string, ValueConstraint> = {};
      whitespace();
      while (normalized[position] !== "}") {
        const key = identifier();
        expect("=");
        properties[key] = parse();
        whitespace();
        if (normalized[position] === ",") position++;
        whitespace();
      }
      expect("}");
      expect(")");
      return { type: "object", properties };
    }
    if (name === "tuple") {
      expect("[");
      const tupleItems: ValueConstraint[] = [];
      whitespace();
      while (normalized[position] !== "]") {
        tupleItems.push(parse());
        whitespace();
        if (normalized[position] === ",") position++;
        whitespace();
      }
      expect("]");
      expect(")");
      return { type: "array", tupleItems };
    }
    const item = parse();
    expect(")");
    if (name === "list" || name === "set")
      return { type: "array", items: item };
    if (name === "map") return { type: "object", additionalProperties: item };
    if (name === "optional") return item;
    throw new Error("Unsupported Terraform type");
  }

  try {
    const result = parse();
    whitespace();
    return position === normalized.length ? result : { type: "any" };
  } catch {
    return { type: "any" };
  }
}

export function defaultValueForConstraint(
  constraint: ValueConstraint,
): JsonValue {
  if (constraint.type === "array")
    return constraint.tupleItems?.map(defaultValueForConstraint) ?? [];
  if (constraint.type === "object" && constraint.properties)
    return Object.fromEntries(
      Object.entries(constraint.properties).map(([key, child]) => [
        key,
        defaultValueForConstraint(child),
      ]),
    );
  if (constraint.type === "any" || constraint.type === "null") return "";
  return defaultJsonValue(constraint.type);
}

export function matchesValueConstraint(
  value: JsonValue,
  constraint: ValueConstraint,
): boolean {
  if (typeof value === "string" && value.includes("{{ components["))
    return true;
  if (constraint.type === "any") return true;
  if (jsonValueType(value) !== constraint.type) return false;
  if (Array.isArray(value)) {
    return value.every((item, index) => {
      const child = constraint.tupleItems?.[index] ?? constraint.items;
      return !child || matchesValueConstraint(item, child);
    });
  }
  if (value !== null && typeof value === "object") {
    return Object.entries(value).every(([key, child]) => {
      const childConstraint =
        constraint.properties?.[key] ?? constraint.additionalProperties;
      return !childConstraint || matchesValueConstraint(child, childConstraint);
    });
  }
  return true;
}

export function jsonValueType(value: JsonValue): JsonValueType {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  if (typeof value === "object") return "object";
  if (typeof value === "number") return "number";
  if (typeof value === "boolean") return "boolean";
  return "string";
}

export function defaultJsonValue(type: JsonValueType): JsonValue {
  switch (type) {
    case "string":
      return "";
    case "number":
      return 0;
    case "boolean":
      return false;
    case "array":
      return [];
    case "object":
      return {};
    case "null":
      return null;
  }
}

export function isJsonValue(value: unknown): value is JsonValue {
  if (value === null || typeof value === "string" || typeof value === "boolean")
    return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(isJsonValue);
  return typeof value === "object" && Object.values(value).every(isJsonValue);
}

export function nextComponentName(
  type: string,
  components: Components,
): string {
  const base = type.replace(/[^a-zA-Z0-9_-]/g, "-");
  if (!Object.hasOwn(components, base)) return base;
  let index = 2;
  while (Object.hasOwn(components, `${base}-${index}`)) index++;
  return `${base}-${index}`;
}

export function connectComponents(
  components: Components,
  source: string,
  output: string,
  target: string,
  destination: string | (string | number)[],
): Components {
  if (source === target)
    throw new Error("A component cannot consume its own output.");
  if (!Object.hasOwn(components, source) || !Object.hasOwn(components, target))
    throw new Error("Both components must exist.");
  return {
    ...components,
    [target]: {
      ...components[target],
      properties: setValueAtPath(
        components[target].properties ?? {},
        typeof destination === "string" ? [destination] : destination,
        `{{ components[${JSON.stringify(source)}][${JSON.stringify(output)}] }}`,
      ) as Record<string, unknown>,
    },
  };
}

export function valueAtPath(
  value: unknown,
  path: (string | number)[],
): unknown {
  for (const part of path) {
    if (
      value === null ||
      typeof value !== "object" ||
      !Object.hasOwn(value, part)
    )
      return undefined;
    value = (value as Record<string | number, unknown>)[part];
  }
  return value;
}

function setValueAtPath(
  value: unknown,
  path: (string | number)[],
  replacement: string,
): unknown {
  const [head, ...tail] = path;
  if (head === undefined) throw new Error("Choose a connection destination.");
  if (typeof head === "number") {
    if (
      (value !== undefined && !Array.isArray(value)) ||
      !Number.isInteger(head) ||
      head < 0
    )
      throw new Error("The connection destination is not a valid list slot.");
    const items = [...((value as unknown[] | undefined) ?? [])];
    if (head > items.length)
      throw new Error("The connection destination is not a valid list slot.");
    items[head] = tail.length
      ? setValueAtPath(items[head], tail, replacement)
      : replacement;
    return items;
  }
  if (
    value !== undefined &&
    (value === null || typeof value !== "object" || Array.isArray(value))
  )
    throw new Error("The connection destination is not an object.");
  return {
    ...((value as Record<string, unknown> | undefined) ?? {}),
    [head]: tail.length
      ? setValueAtPath(
          value !== undefined && Object.hasOwn(value, head)
            ? (value as Record<string, unknown>)[head]
            : undefined,
          tail,
          replacement,
        )
      : replacement,
  };
}

export function pathStartsWith(
  path: (string | number)[],
  prefix: (string | number)[],
): boolean {
  return prefix.every((part, index) => path[index] === part);
}

export function routesAfterRemoval<T extends Relationship>(
  routes: T[],
  relationship: Relationship,
): T[] {
  return routes.filter((route) => {
    if (route.target !== relationship.target) return true;
    if (pathStartsWith(route.path, relationship.path)) return false;
    const index = relationship.path.at(-1);
    return !(
      typeof index === "number" &&
      pathStartsWith(route.path, relationship.path.slice(0, -1)) &&
      typeof route.path[relationship.path.length - 1] === "number" &&
      (route.path[relationship.path.length - 1] as number) >= index
    );
  });
}

export function canRemove(
  relationships: Relationship[],
  name: string,
): boolean {
  return !relationships.some(
    (edge) => edge.source === name && edge.kind === "reference",
  );
}

export function relationshipKey(relationship: Relationship): string {
  return JSON.stringify([
    relationship.kind,
    relationship.source,
    relationship.output,
    relationship.target,
    relationship.property,
    relationship.path,
    relationship.rule,
  ]);
}

export function removeReference(
  components: Components,
  relationship: Relationship,
): Components {
  if (relationship.kind !== "reference") return components;
  const target = components[relationship.target];
  if (!target?.properties) return components;
  return {
    ...components,
    [relationship.target]: {
      ...target,
      properties: removePropertyAtPath(target.properties, relationship.path),
    },
  };
}

export function removePropertyAtPath(
  value: Record<string, unknown>,
  path: (string | number)[],
): Record<string, unknown> {
  const [head, ...tail] = path;
  if (typeof head !== "string" || !Object.hasOwn(value, head)) return value;
  if (!tail.length)
    return Object.fromEntries(
      Object.entries(value).filter(([name]) => name !== head),
    );
  const child = removeNestedValue(value[head], tail);
  return child === value[head] ? value : { ...value, [head]: child };
}

function removeNestedValue(value: unknown, path: (string | number)[]): unknown {
  const [head, ...tail] = path;
  if (Array.isArray(value) && typeof head === "number" && head in value) {
    if (!tail.length) return value.filter((_, index) => index !== head);
    const child = removeNestedValue(value[head], tail);
    return child === value[head]
      ? value
      : value.map((item, index) => (index === head ? child : item));
  }
  if (
    value !== null &&
    typeof value === "object" &&
    typeof head === "string" &&
    Object.hasOwn(value, head)
  ) {
    const object = value as Record<string, unknown>;
    if (!tail.length)
      return Object.fromEntries(
        Object.entries(object).filter(([name]) => name !== head),
      );
    const child = removeNestedValue(object[head], tail);
    return child === object[head] ? value : { ...object, [head]: child };
  }
  return value;
}
