// @vitest-environment jsdom
import { act, type ComponentProps, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Canvas } from "./Canvas";
import type { ComposerNode } from "./ComponentNode";
import type { Relationship } from "../types";

const flow = vi.hoisted(() => ({
  nodes: [] as ComposerNode[],
  edges: [] as { id: string }[],
}));

vi.mock("@xyflow/react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@xyflow/react")>()),
  ReactFlow: ({
    children,
    "aria-busy": busy,
    nodes,
    edges,
  }: {
    children: ReactNode;
    "aria-busy": boolean;
    nodes: ComposerNode[];
    edges: { id: string }[];
  }) => {
    flow.nodes = nodes;
    flow.edges = edges;
    return (
      <div data-testid="flow" aria-busy={busy}>
        {children}
      </div>
    );
  },
  Panel: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Background: () => null,
  Controls: () => null,
  MiniMap: () => null,
  useReactFlow: () => ({
    screenToFlowPosition: vi.fn(),
    getInternalNode: vi.fn(),
  }),
}));

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  container = document.createElement("div");
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  vi.unstubAllGlobals();
});

async function render(overrides: Partial<ComponentProps<typeof Canvas>> = {}) {
  await act(async () =>
    root.render(
      <Canvas
        readOnly={false}
        positions={{}}
        routes={[]}
        components={{ app: { type: "service" } }}
        catalog={{}}
        relationships={[]}
        selected={null}
        selectedRelationship={null}
        onLabelPositionChange={vi.fn()}
        onPositionChange={vi.fn()}
        onRouteChange={vi.fn()}
        onSelect={vi.fn()}
        onSelectRelationship={vi.fn()}
        onConnect={vi.fn()}
        onAdd={vi.fn()}
        inspecting={true}
        hasInspectedAssociations={false}
        inspectionLabel="Inspecting managed connections…"
        canUndo={true}
        canRedo={false}
        onUndo={vi.fn()}
        onRedo={vi.fn()}
        {...overrides}
      />,
    ),
  );
}

it("marks the initial diagram incomplete and clears the blur and announcement on completion", async () => {
  await render();
  expect(
    container.querySelector('[data-testid="flow"]')?.getAttribute("aria-busy"),
  ).toBe("true");
  expect(
    container.querySelector(".canvas.is-loading-connections"),
  ).toBeTruthy();
  expect(container.querySelector('[role="status"]')?.textContent).toContain(
    "Loading managed connections",
  );
  expect(container.querySelector('[role="status"]')?.textContent).toContain(
    "Connections will appear when ready",
  );
  await render({ inspecting: false, hasInspectedAssociations: true });
  expect(
    container.querySelector('[data-testid="flow"]')?.getAttribute("aria-busy"),
  ).toBe("false");
  expect(container.querySelector(".is-loading-connections")).toBeNull();
  expect(container.querySelector('[role="status"]')).toBeNull();
});

it("keeps an inspected canvas clear and editing controls available during a refresh", async () => {
  await render({ hasInspectedAssociations: true });
  expect(container.querySelector(".is-loading-connections")).toBeNull();
  expect(container.querySelector('[role="status"]')?.textContent).toContain(
    "Updating managed connections",
  );
  expect(container.querySelector('[role="status"]')?.textContent).toContain(
    "Previous connections stay visible",
  );
  expect(
    container.querySelector<HTMLButtonElement>('button[aria-label="Undo"]')
      ?.disabled,
  ).toBe(false);
});

it("stops presenting loading when inspection fails", async () => {
  await render();
  await render({
    inspecting: false,
    inspectionLabel: "Managed connections incomplete",
  });
  expect(container.querySelector(".is-loading-connections")).toBeNull();
  expect(container.querySelector('[role="status"]')).toBeNull();
  expect(container.textContent).toContain("Managed connections incomplete");
});

it("reuses node definitions during movement and selection, and removes stale edges", async () => {
  const components = { app: { type: "service" }, role: { type: "role" } };
  const catalog = {};
  const relationships: Relationship[] = [
    {
      source: "role",
      target: "app",
      output: "arn",
      property: "role",
      path: ["role"],
      kind: "reference",
      rule: null,
    },
  ];
  const shared = { components, catalog, relationships };
  await render(shared);
  const data = flow.nodes.find((node) => node.id === "app")!.data;
  await render({
    ...shared,
    positions: { app: { x: 50, y: 80 } },
    selected: "app",
  });
  expect(flow.nodes.find((node) => node.id === "app")!.data).toBe(data);
  expect(flow.nodes.find((node) => node.id === "app")!.position).toEqual({
    x: 50,
    y: 80,
  });
  expect(flow.nodes.find((node) => node.id === "app")!.selected).toBe(true);
  expect(flow.edges).toHaveLength(1);
  await render({ ...shared, components: { app: components.app } });
  expect(flow.edges).toHaveLength(0);
});
