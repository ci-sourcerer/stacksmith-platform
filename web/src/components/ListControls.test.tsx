import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { ListAddButton, StringListEditor } from "./ListControls";
import { Inspector } from "./Inspector";
import { PropertyEditor } from "./PropertyEditor";

it("renders collections as individual inputs with non-submit plus and remove controls", () => {
  const markup = renderToStaticMarkup(
    <StringListEditor
      label="Topics"
      itemLabel="Topic"
      items={[
        { id: "a", value: "first" },
        { id: "b", value: "second" },
      ]}
      onChange={() => {}}
    />,
  );
  expect(markup).toContain('aria-label="Topic 1"');
  expect(markup).toContain('aria-label="Topic 2"');
  expect(markup).toContain('aria-label="Remove topic 1"');
  expect(markup).toContain('aria-label="Remove topic 2"');
  expect(markup.match(/type="button"/g)).toHaveLength(3);
  expect(markup).toContain("Add topic");
  expect(markup).toContain("lucide-plus");
  expect(markup).not.toContain("comma");
});

it("allows adding the first item to an empty list", () => {
  const markup = renderToStaticMarkup(
    <StringListEditor
      label="Tags"
      itemLabel="Tag"
      items={[]}
      onChange={() => {}}
    />,
  );
  expect(markup).toContain("Add tag");
  expect(markup).not.toContain("<input");
});

it("prevents additions at the configured list limit", () => {
  const markup = renderToStaticMarkup(
    <StringListEditor
      label="Topics"
      itemLabel="Topic"
      items={[{ id: "a", value: "one" }]}
      maxItems={1}
      onChange={() => {}}
    />,
  );
  expect(markup).toMatch(/<button[^>]*disabled[^>]*class="list-add-button/);
});

it("never submits a parent form when adding an item", () => {
  const markup = renderToStaticMarkup(
    <ListAddButton type="submit">Add item</ListAddButton>,
  );
  expect(markup).toContain('type="button"');
  expect(markup).not.toContain('type="submit"');
});

it("uses the same list controls for component tags", () => {
  const markup = renderToStaticMarkup(
    <Inspector
      readOnly={false}
      name="example"
      existingNames={["example"]}
      onRename={async () => {}}
      component={{
        type: "example",
        tags: ["application", "private"],
        properties: {},
      }}
      relationships={[]}
      onChange={() => {}}
      onRemove={() => {}}
      onClose={() => {}}
    />,
  );
  expect(markup).toContain('aria-label="Tag 1"');
  expect(markup).toContain('aria-label="Tag 2"');
  expect(markup).toContain("Add tag");
  expect(markup).not.toContain("application, private");
});

it("uses shared plus and remove buttons for nested property lists", () => {
  const markup = renderToStaticMarkup(
    <PropertyEditor
      inputs={[{ name: "items", type: "list(string)" }]}
      properties={{ items: ["one"] }}
      relationships={[]}
      onChange={() => {}}
    />,
  );
  expect(markup).toContain("list-add-button");
  expect(markup).toContain('aria-label="Remove list item 1"');
  expect(markup).toContain("Add list item");
});
