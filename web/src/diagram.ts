import { relationshipKey } from "./model";
import type {
  Catalog,
  Components,
  DiagramRoute,
  Port,
  Relationship,
} from "./types";

function addPort(index: Map<string, Set<string>>, name: string, port: string) {
  if (!index.has(name)) index.set(name, new Set());
  index.get(name)!.add(port);
}

function extendPorts(ports: Port[], extra: Set<string> | undefined): Port[] {
  const known = new Set(ports.map((port) => port.name));
  return [
    ...ports,
    ...Array.from(extra ?? [])
      .filter((name) => !known.has(name))
      .map((name) => ({ name })),
  ];
}

export function diagramNodeData(
  components: Components,
  catalog: Catalog,
  relationships: Relationship[],
) {
  const inputs = new Map<string, Set<string>>();
  const outputs = new Map<string, Set<string>>();
  for (const edge of relationships) {
    addPort(inputs, edge.target, edge.property);
    addPort(outputs, edge.source, edge.output);
  }
  return new Map(
    Object.entries(components).map(([name, component]) => {
      const definition = catalog[component.type] ?? {
        display_name: component.type,
        module_source: "",
        module_version: "",
        inputs: [],
        outputs: [],
      };
      return [
        name,
        {
          name,
          type: component.type,
          definition: {
            ...definition,
            inputs: extendPorts(definition.inputs, inputs.get(name)),
            outputs: extendPorts(definition.outputs ?? [], outputs.get(name)),
          },
        },
      ];
    }),
  );
}

export function diagramRoutes(routes: DiagramRoute[]) {
  const indexed = new Map<string, DiagramRoute>();
  for (const route of routes) {
    const key = relationshipKey(route);
    if (!indexed.has(key)) indexed.set(key, route);
  }
  return indexed;
}
