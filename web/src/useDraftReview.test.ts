// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { useDraftReview } from "./useDraftReview";
import type { Review } from "./types";

vi.mock("./api", () => ({ api: vi.fn() }));

let root: Root;
let container: HTMLDivElement;
let state: ReturnType<typeof useDraftReview>;
const original = JSON.stringify({ snapshot: "signed", environment: "dev" });
const latest = JSON.stringify({ snapshot: "signed", environment: "prod" });
const review: Review = {
  source: "draft",
  diff: "",
  catalog: {},
  edges: [],
  associations: { rules: [] },
  validation: "Valid",
};

function Harness({
  draft,
  attempt,
  enabled,
}: {
  draft: string;
  attempt: number;
  enabled: boolean;
}) {
  state = useDraftReview(draft, enabled, attempt);
  return null;
}

async function render(draft = original, attempt = 0, enabled = true) {
  await act(async () =>
    root.render(createElement(Harness, { draft, attempt, enabled })),
  );
}

async function debounce() {
  await act(async () => {
    vi.advanceTimersByTime(400);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue(review);
  container = document.createElement("div");
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("debounces edits and does not reinspect an unchanged semantic key", async () => {
  await render();
  await act(async () => {
    vi.advanceTimersByTime(399);
  });
  expect(api).not.toHaveBeenCalled();
  await render(latest);
  await debounce();
  expect(api).toHaveBeenCalledTimes(1);
  expect(vi.mocked(api).mock.calls[0][1]).toEqual(JSON.parse(latest));
  expect(state.result).toEqual({ key: latest, review });
  expect(state.inspecting).toBe(false);
  await render(latest);
  await debounce();
  expect(api).toHaveBeenCalledTimes(1);
});

it("finishes active work and only submits the newest pending draft", async () => {
  let finish!: (value: Review) => void;
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise<Review>((resolve) => {
        finish = resolve;
      }) as never,
  );
  await render();
  await debounce();
  const signal = vi.mocked(api).mock.calls[0][2]!;
  await render(JSON.stringify({ snapshot: "intermediate" }));
  await debounce();
  await render(latest);
  await debounce();
  expect(api).toHaveBeenCalledTimes(1);
  expect(signal.aborted).toBe(false);
  expect(state.inspecting).toBe(true);
  await act(async () => finish({ ...review, validation: "Obsolete" }));
  expect(api).toHaveBeenCalledTimes(2);
  expect(vi.mocked(api).mock.calls[1][1]).toEqual(JSON.parse(latest));
  expect(state.result?.review.validation).toBe("Valid");
  expect(state.result?.key).toBe(latest);
});

it("rejects obsolete failures and supports retry after a current failure", async () => {
  let fail!: (error: Error) => void;
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise<Review>((_, reject) => {
        fail = reject;
      }) as never,
  );
  await render();
  await debounce();
  await render(latest);
  await debounce();
  vi.mocked(api).mockRejectedValueOnce(new Error("Current failure"));
  await act(async () => fail(new Error("Obsolete failure")));
  expect((state.failure as Error).message).toBe("Current failure");
  expect(state.inspecting).toBe(false);
  await render(latest, 1);
  expect(state.failure).toBeUndefined();
  await debounce();
  expect(api).toHaveBeenCalledTimes(3);
  expect(state.result?.key).toBe(latest);
  expect(state.failure).toBeUndefined();
});

it("skips pending work when inspection is disabled and aborts on unmount", async () => {
  let finish!: (value: Review) => void;
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise<Review>((resolve) => {
        finish = resolve;
      }) as never,
  );
  await render();
  await debounce();
  const signal = vi.mocked(api).mock.calls[0][2]!;
  await render(latest);
  await debounce();
  await render(latest, 0, false);
  await act(async () => finish(review));
  expect(api).toHaveBeenCalledTimes(1);
  expect(state.result).toBeUndefined();
  expect(state.inspecting).toBe(false);
  await act(async () => root.unmount());
  expect(signal.aborted).toBe(true);
  root = createRoot(container);
});
