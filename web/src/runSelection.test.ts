import { expect, it } from "vitest";
import {
  parseRunTarget,
  runTargetAtUrl,
  runTargetKey,
  runTargetUrl,
} from "./runSelection";

it("keeps account preferences separate and rejects invalid saved targets", () => {
  expect(runTargetKey("alice")).not.toBe(runTargetKey("bob"));
  for (const value of [null, "invalid", "null", "{}", '{"repository":1}'])
    expect(parseRunTarget(value)).toBeNull();
});

it("gives URL targets precedence and treats omitted environment as all", () => {
  const saved = { repository: "acme/service", environment: "prod" };
  expect(
    runTargetAtUrl(new URL("https://example.com/runs?repo=team/other"), saved),
  ).toEqual({ repository: "team/other", environment: "" });
  expect(runTargetAtUrl(new URL("https://example.com/runs"), saved)).toEqual(
    saved,
  );
  expect(
    runTargetAtUrl(
      new URL("https://example.com" + runTargetUrl(saved, "run-1")),
      { repository: "", environment: "" },
    ),
  ).toEqual(saved);
  expect(
    parseRunTarget(JSON.stringify({ ...saved, secret: "discard" })),
  ).toEqual(saved);
});
