import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { RepositorySelector } from "./RepositorySelector";

it("keeps the full selected repository available while closed without mounting the list", () => {
  const id = "organization/" + "long-name-".repeat(12);
  const markup = renderToStaticMarkup(
    <RepositorySelector
      repositories={[{ id }]}
      selected={id}
      onSelect={() => {}}
    />,
  );
  expect(markup).toContain(id);
  expect(markup).toContain('aria-haspopup="listbox"');
  expect(markup).toContain('aria-expanded="false"');
  expect(markup).not.toContain('role="option"');
});
it("distinguishes loading from discovery failure", () => {
  expect(
    renderToStaticMarkup(
      <RepositorySelector
        repositories={[]}
        selected=""
        onSelect={() => {}}
        loading
      />,
    ),
  ).toContain("Loading repositories");
  expect(
    renderToStaticMarkup(
      <RepositorySelector
        repositories={[]}
        selected=""
        onSelect={() => {}}
        error="GitHub is unavailable"
      />,
    ),
  ).toContain('role="alert"');
});
