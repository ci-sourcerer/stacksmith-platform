import { expect, it } from "vitest";
import {
  filterRepositories,
  repositoryNextIndex,
  repositoryPopup,
  repositoryWindow,
} from "./repositorySelection";

const options = [
  { id: "zeta/service", description: "Payments production", writable: false },
  {
    id: "acme/infra",
    description: "Production infrastructure",
    writable: true,
  },
  { id: "acme/app", description: "Development application" },
];
it("filters all search terms across names and descriptions without changing the source list", () => {
  expect(
    filterRepositories(options, " ACME production ").map((item) => item.id),
  ).toEqual(["acme/infra"]);
  expect(filterRepositories(options, "").map((item) => item.id)).toEqual([
    "acme/app",
    "acme/infra",
    "zeta/service",
  ]);
  expect(filterRepositories(options, "missing")).toEqual([]);
  expect(options[0].id).toBe("zeta/service");
});
it("keeps the rendered window bounded across thousands of repositories", () => {
  for (const scroll of [0, 6400, 300000, 9999999]) {
    const range = repositoryWindow(5000, scroll, 320);
    expect(range.end - range.start).toBeLessThanOrEqual(12);
    expect(range.start).toBeGreaterThanOrEqual(0);
    expect(range.end).toBeLessThanOrEqual(5000);
  }
  expect(repositoryWindow(0, 20000, 320)).toEqual({ start: 0, end: 0 });
  expect(
    filterRepositories(
      Array.from({ length: 1000 }, (_, index) => ({
        id: `acme/repo-${index}`,
      })),
      "repo-999",
    ),
  ).toEqual([{ id: "acme/repo-999" }]);
});
it("supports bounded arrow, page, home, and end navigation without selecting", () => {
  expect(repositoryNextIndex(0, "ArrowUp", 1000, 320)).toBe(0);
  expect(repositoryNextIndex(0, "End", 1000, 320)).toBe(999);
  expect(repositoryNextIndex(999, "Home", 1000, 320)).toBe(0);
  expect(repositoryNextIndex(20, "PageDown", 1000, 320)).toBe(25);
  expect(repositoryNextIndex(20, "PageUp", 1000, 320)).toBe(15);
  expect(repositoryNextIndex(0, "ArrowDown", 0, 320)).toBe(-1);
});
it("places a popup within narrow viewports and opens upwards when necessary", () => {
  const mobile = repositoryPopup(
    { left: 20, top: 100, bottom: 140, width: 350 },
    { width: 390, height: 844 },
    1000,
  );
  expect(mobile.left + mobile.width).toBeLessThanOrEqual(378);
  expect(mobile.left).toBeGreaterThanOrEqual(12);
  const lower = repositoryPopup(
    { left: 300, top: 700, bottom: 740, width: 320 },
    { width: 1024, height: 800 },
    1000,
  );
  expect(lower.top).toBeLessThan(700);
  expect(lower.height).toBeLessThanOrEqual(320);
});
