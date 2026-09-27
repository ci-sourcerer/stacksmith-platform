import type { Catalog, Components, DiagramPoint } from "./types";

export const GRID_SIZE = 22;

export function snapPoint(point: DiagramPoint): DiagramPoint {
  return {
    x: Math.round(point.x / GRID_SIZE) * GRID_SIZE,
    y: Math.round(point.y / GRID_SIZE) * GRID_SIZE,
  };
}

export function initialPosition(index: number): DiagramPoint {
  return { x: (index % 3) * 440 + 44, y: Math.floor(index / 3) * 440 + 44 };
}

function componentHeight(type: string, catalog: Catalog): number {
  return (
    132 +
    ((catalog[type]?.inputs.length ?? 0) +
      (catalog[type]?.outputs?.length ?? 0)) *
      28
  );
}

export function newComponentPosition(
  type: string,
  components: Components,
  positions: Record<string, DiagramPoint>,
  catalog: Catalog,
): DiagramPoint {
  for (let index = 0; ; index++) {
    const candidate = initialPosition(index);
    if (
      Object.entries(components).every(([name, component], componentIndex) => {
        const existing = positions[name] ?? initialPosition(componentIndex);
        return (
          candidate.x + 276 <= existing.x ||
          existing.x + 276 <= candidate.x ||
          candidate.y + componentHeight(type, catalog) + 44 <= existing.y ||
          existing.y + componentHeight(component.type, catalog) + 44 <=
            candidate.y
        );
      })
    )
      return candidate;
  }
}
