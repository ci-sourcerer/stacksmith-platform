// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { useConfigurationEvents } from "./useConfigurationEvents";
import type { Snapshot } from "./types";

vi.mock("./api", () => ({ api: vi.fn() }));

class Events extends EventTarget {
  static instances: Events[] = [];
  close = vi.fn();
  constructor(public url: string) {
    super();
    Events.instances.push(this);
  }
}

const original: Snapshot = {
  snapshot: "signed-original",
  source: "original source",
  path: "stack.yaml",
  repository: "owner/repo",
  branch: "main",
  head: "commit",
  editable: true,
  reason: null,
  document: { name: "example", components: {} },
};
let root: Root;
const refresh = vi.fn();
const failure = vi.fn();

function Harness({ snapshot = original }: { snapshot?: Snapshot }) {
  useConfigurationEvents(true, snapshot, refresh, failure);
  return null;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  Events.instances = [];
  vi.stubGlobal("EventSource", Events);
  root = createRoot(document.createElement("div"));
});

afterEach(async () => {
  await act(async () => root.unmount());
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("coalesces configuration events and refreshes only the signed base", async () => {
  const updated = { ...original, snapshot: "signed-updated" };
  vi.mocked(api).mockResolvedValue(updated);
  await act(async () => root.render(createElement(Harness)));
  await act(async () => {
    for (let index = 0; index < 100; index++)
      Events.instances[0].dispatchEvent(new Event("configuration"));
    vi.advanceTimersByTime(100);
  });
  expect(api).toHaveBeenCalledTimes(1);
  expect(api).toHaveBeenCalledWith(
    "/refresh-snapshot",
    { snapshot: original.snapshot },
    expect.any(AbortSignal),
  );
  expect(refresh).toHaveBeenCalledWith(updated);
  expect(Events.instances).toHaveLength(1);
  await act(async () =>
    root.render(createElement(Harness, { snapshot: updated })),
  );
  await act(async () => {
    Events.instances[0].dispatchEvent(new Event("configuration"));
    vi.advanceTimersByTime(100);
  });
  expect(api).toHaveBeenLastCalledWith(
    "/refresh-snapshot",
    { snapshot: updated.snapshot },
    expect.any(AbortSignal),
  );
  expect(Events.instances).toHaveLength(1);
});

it("discards a late refresh after another stack has opened and closes the stream", async () => {
  let complete: (snapshot: Snapshot) => void = () => undefined;
  vi.mocked(api).mockImplementation(
    () =>
      new Promise<Snapshot>((resolve) => {
        complete = resolve;
      }) as ReturnType<typeof api>,
  );
  await act(async () => root.render(createElement(Harness)));
  await act(async () => {
    Events.instances[0].dispatchEvent(new Event("configuration"));
    vi.advanceTimersByTime(100);
  });
  await act(async () =>
    root.render(
      createElement(Harness, {
        snapshot: { ...original, snapshot: "other-stack" },
      }),
    ),
  );
  await act(async () => complete({ ...original, snapshot: "old-refresh" }));
  expect(refresh).not.toHaveBeenCalled();
  await act(async () => root.unmount());
  expect(Events.instances[0].close).toHaveBeenCalledOnce();
});
