import { expect, test } from "vitest";
import { reportLabel } from "./reportLabel";

test("report labels describe the report without its storage path", () => {
  expect(reportLabel("plan/plan-summary.json")).toBe("Plan Summary");
  expect(reportLabel("apply/apply-summary.json")).toBe("Apply Summary");
});
