import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { AdvancedRunOptions } from "./AdvancedRunOptions";

it("omits the revision override for ordinary users", () => {
  expect(
    renderToStaticMarkup(<AdvancedRunOptions administrator={false} />),
  ).toBe("");
});

it("starts admin options collapsed with a blank optional full-SHA field", () => {
  const markup = renderToStaticMarkup(<AdvancedRunOptions administrator />);
  expect(markup).toContain("Advanced run options");
  expect(markup).toContain('name="revision"');
  expect(markup).toContain('pattern="[0-9a-f]{40}"');
  expect(markup).not.toContain(" open");
  expect(markup).not.toContain("Override set");
});

it("only offers local-backend testing for plan actions", () => {
  expect(
    renderToStaticMarkup(<AdvancedRunOptions administrator action="plan" />),
  ).toContain('name="allow_local_backend"');
  expect(
    renderToStaticMarkup(<AdvancedRunOptions administrator action="apply" />),
  ).not.toContain('name="allow_local_backend"');
});
