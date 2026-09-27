import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { RepositoryFilterFields } from "./RepositoryFilterEditor";

const fields = {
  properties: [{ id: "property-1", name: "stacksmith", value: "stack" }],
  topics: [{ id: "topic-1", value: "stacksmith-stack" }],
  busy: false,
};

it("explains personal-account limitations without offering unusable property controls", () => {
  const markup = renderToStaticMarkup(
    <RepositoryFilterFields {...fields} organizations={false} />,
  );
  expect(markup).toContain("Use topics for personal repositories.");
  expect(markup).not.toContain('name="property_name"');
  expect(markup).not.toContain("Remove property rule");
  expect(markup).not.toContain("Clear the property name");
  expect(markup).toMatch(/aria-label="Topic 1"[^>]*value="stacksmith-stack"/);
  expect(markup).toContain('aria-label="Topic 1"');
});

it("provides explicit non-submit removal controls for organization property rules", () => {
  const markup = renderToStaticMarkup(
    <RepositoryFilterFields {...fields} organizations />,
  );
  expect(markup).toContain('<fieldset class="repository-filter-group">');
  expect(markup).toContain("Add property rule");
  expect(markup.indexOf("Add property rule")).toBeLessThan(
    markup.indexOf("</fieldset>"),
  );
  expect(markup).toContain('type="button" aria-label="Remove property rule 1"');
  expect(markup).toContain('name="property_name"');
  expect(markup).not.toContain("Clear the property name");
});

it("explains an empty property list while keeping topic filtering available", () => {
  const markup = renderToStaticMarkup(
    <RepositoryFilterFields {...fields} properties={[]} organizations />,
  );
  expect(markup).toContain("No custom property rules.");
  expect(markup).toContain('aria-label="Topic 1"');
  expect(markup).not.toContain("Remove property rule");
});

it("disables property actions while saving", () => {
  const markup = renderToStaticMarkup(
    <RepositoryFilterFields {...fields} organizations busy />,
  );
  expect(markup).toContain(
    '<fieldset class="repository-filter-group" disabled="">',
  );
  expect(markup.match(/<fieldset[^>]*disabled/g)).toHaveLength(2);
});
