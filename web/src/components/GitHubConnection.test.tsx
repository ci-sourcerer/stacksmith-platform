// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { GitHubConnection } from "./GitHubConnection";

vi.mock("../api", () => ({ api: vi.fn() }));
let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.resetAllMocks();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function render() {
  await act(async () => root.render(<GitHubConnection />));
}

it.each([null, "https://github.com/install"])(
  "shows a disconnected state with installation URL %s",
  async (install_url) => {
    vi.mocked(api).mockResolvedValue({ install_url, installations: [] });
    await render();
    expect(container.textContent).toContain("No GitHub accounts connected.");
    expect(container.querySelector("a")?.textContent ?? null).toBe(
      install_url ? "Connect GitHub" : null,
    );
  },
);

it.each([1, 2])(
  "shows %i connected accounts and the secondary connect action",
  async (count) => {
    vi.mocked(api).mockResolvedValue({
      install_url: "https://github.com/install",
      installations: Array.from({ length: count }, (_, id) => ({
        id,
        account: `account-${id}`,
        management_url: id === 0 ? "https://github.com/manage" : null,
      })),
    });
    await render();
    expect(container.querySelectorAll("li")).toHaveLength(count);
    expect(container.textContent).toContain("Connect another account");
    expect(container.textContent).not.toContain("Connect GitHub");
    expect(
      container.querySelectorAll('a[aria-label^="Manage repository access"]'),
    ).toHaveLength(1);
    for (let id = 0; id < count; id++) {
      expect(container.querySelectorAll("li")[id].textContent).toContain(
        `account-${id}Connected`,
      );
    }
  },
);

it("keeps a connected account visible without either URL", async () => {
  vi.mocked(api).mockResolvedValue({
    install_url: null,
    installations: [{ id: 1, account: "personal", management_url: null }],
  });
  await render();
  expect(container.textContent).toContain("personalConnected");
  expect(container.querySelector("a")).toBeNull();
});

it("shows loading and failure states without claiming disconnection, and supports retry", async () => {
  let reject!: (reason: Error) => void;
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise((_, fail) => {
        reject = fail;
      }),
  );
  await render();
  expect(container.querySelector('[role="status"]')?.textContent).toContain(
    "Loading",
  );
  expect(container.textContent).not.toContain("No GitHub accounts");
  await act(async () => reject(new Error("GitHub unavailable")));
  expect(container.querySelector('[role="alert"]')?.textContent).toBe(
    "GitHub unavailable",
  );
  expect(container.textContent).not.toContain("No GitHub accounts");
  vi.mocked(api).mockResolvedValue({ install_url: null, installations: [] });
  await act(async () => container.querySelector("button")!.click());
  expect(container.querySelector('[role="alert"]')).toBeNull();
  expect(container.textContent).toContain("No GitHub accounts connected.");
});
