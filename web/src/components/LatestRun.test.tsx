// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { LatestRun } from "./LatestRun";

vi.mock("../api", () => ({ api: vi.fn() }));
let root: Root;
let container: HTMLDivElement;
const run = {
  id: "newest",
  environment: "dev",
  status: "awaiting_approval",
  action: "apply",
  revision: "abcdef0123456789",
  created: 1700000000,
  actor: "operator",
};

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.useFakeTimers();
  vi.mocked(api).mockReset();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function render(
  environment = "dev",
  refresh = 0,
  repository = "acme/service",
) {
  await act(async () =>
    root.render(
      <LatestRun
        key={JSON.stringify([repository, environment])}
        repository={repository}
        environment={environment}
        ready={true}
        refresh={refresh}
        actionLabel={() => "Plan and apply"}
        runUrl={(id) =>
          `/runs?repo=${encodeURIComponent(repository)}&environment=${environment}&run=${id}`
        }
      />,
    ),
  );
}

it("requests the newest target run and provides its detail link", async () => {
  vi.mocked(api).mockResolvedValue({ items: [run] });
  await render();
  expect(api).toHaveBeenCalledWith(
    "/v1/runs?repository=acme%2Fservice&environment=dev&page=1&page_size=1",
  );
  for (const text of [
    "awaiting approval",
    "Plan and apply",
    "abcdef0123",
    "operator",
  ])
    expect(container.textContent).toContain(text);
  expect(container.querySelector("time")?.dateTime).toBe(
    new Date(run.created * 1000).toISOString(),
  );
  expect(container.querySelector("a")?.getAttribute("href")).toBe(
    "/runs?repo=acme%2Fservice&environment=dev&run=newest",
  );
  expect(container.textContent).not.toContain("Environment");
});

it("shows environment for repository-wide results", async () => {
  vi.mocked(api).mockResolvedValue({ items: [run] });
  await render("");
  expect(api).toHaveBeenCalledWith(
    "/v1/runs?repository=acme%2Fservice&page=1&page_size=1",
  );
  expect(container.textContent).toContain("Environmentdev");
});

it("shows loading and ignores an old target response", async () => {
  let resolveOld!: (value: unknown) => void;
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveOld = resolve;
      }),
  );
  await render();
  expect(container.textContent).toContain("Loading latest run");
  vi.mocked(api).mockResolvedValue({ items: [{ ...run, actor: "new actor" }] });
  await render("prod");
  await act(async () => resolveOld({ items: [run] }));
  expect(container.textContent).toContain("new actor");
  expect(container.textContent).not.toContain("operator");
});

it("clears the previous run while a new target loads", async () => {
  vi.mocked(api).mockResolvedValueOnce({ items: [run] });
  await render();
  vi.mocked(api).mockImplementation(() => new Promise(() => {}));
  await render("dev", 0, "acme/other");
  expect(container.textContent).toContain("Loading latest run");
  expect(container.textContent).not.toContain("operator");
});

it("supports retry after an error and empty results", async () => {
  vi.mocked(api).mockRejectedValueOnce(new Error("Unavailable"));
  await render();
  expect(container.querySelector('[role="alert"]')?.textContent).toContain(
    "Unavailable",
  );
  vi.mocked(api).mockResolvedValue({ items: [] });
  await act(async () => container.querySelector("button")!.click());
  expect(container.textContent).toContain("No runs in this environment yet.");
  await render("");
  expect(container.textContent).toContain("No runs in this repository yet.");
});

it("refreshes on the polling interval and launch refresh, then cleans up", async () => {
  vi.mocked(api).mockResolvedValue({ items: [run] });
  await render();
  await act(async () => vi.advanceTimersByTime(15000));
  expect(api).toHaveBeenCalledTimes(2);
  await render("dev", 1);
  expect(api).toHaveBeenCalledTimes(3);
  await act(async () => root.render(null));
  await act(async () => vi.advanceTimersByTime(15000));
  expect(api).toHaveBeenCalledTimes(3);
});
