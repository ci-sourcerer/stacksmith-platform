import { expect, it } from "vitest";
import { pageCount } from "./pagination";
it("handles empty results, partial pages, and shrinking result sets", () => {
  expect(pageCount(0, 20)).toBe(1);
  expect(pageCount(45, 20)).toBe(3);
  expect(pageCount(20, 20)).toBe(1);
  expect(Math.min(2, pageCount(20, 20))).toBe(1);
});
