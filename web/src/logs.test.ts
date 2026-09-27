import { expect, it } from "vitest";
import { ansiSegments, findLogMatches, hideLogPrefixes } from "./logs";

it("finds literal case-insensitive text across ANSI style boundaries", () => {
  const segments = ansiSegments("start ERROR[1] and \x1b[31merr\x1b[0mor[1]");
  expect(findLogMatches(segments, "error[1]")).toEqual([6, 19]);
  expect(findLogMatches(segments, "missing")).toEqual([]);
  expect(findLogMatches(segments, "")).toEqual([]);
});

it("hides recognized prefixes without changing other log content or styles", () => {
  const input = [
    "[prepare]",
    ...[
      "TRACE",
      "DEBUG",
      "INFO",
      "WARN",
      "WARNING",
      "ERROR",
      "FATAL",
      "CRITICAL",
      "STDOUT",
      "STDERR",
    ].map((level) => `20:53:27.337 ${level}   tofu:   message`),
    "\u001b[90m20:53:27.337\u001b[0m \u001b[31mERROR\u001b[0m  tofu: \u001b[31mError: detail\u001b[0m",
    "  │   diagnostic context",
    "20:53:27.337 OTHER  untouched",
    "20:53:27.337 INFO",
    "20:53:27.337 INFO   tofu: ",
  ].join("\n");
  const segments = hideLogPrefixes(ansiSegments(input));
  expect(segments.map((segment) => segment.text).join("")).toBe(
    [
      "[prepare]",
      ...Array(10).fill("  message"),
      "Error: detail",
      "  │   diagnostic context",
      "20:53:27.337 OTHER  untouched",
      "20:53:27.337 INFO",
      "",
    ].join("\n"),
  );
  expect(
    segments.find((segment) => segment.text === "Error: detail")?.style.color,
  ).toBe("#cc241d");
});

it("preserves terminal colors and removes terminal hyperlinks", () => {
  const segments = ansiSegments(
    "\x1b[31mError\x1b[0m\n\x1b]8;;https://example.com\x1b\\details\x1b]8;;\x1b\\\n",
  );
  expect(segments.map((segment) => segment.text).join("")).toBe(
    "Error\ndetails\n",
  );
  expect(segments[0].style.color).toBe("#cc241d");
});
