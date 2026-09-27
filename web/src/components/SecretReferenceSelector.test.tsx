import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import {
  SecretReferenceSelector,
  filterSecretReferences,
} from "./SecretReferenceSelector";

it("renders an accessible loading picker without permitting arbitrary reference input", () => {
  const markup = renderToStaticMarkup(
    <SecretReferenceSelector
      repository="acme/service"
      environment="dev"
      value=""
      onChange={() => {}}
      onError={() => {}}
    />,
  );
  expect(markup).toContain('role="combobox"');
  expect(markup).toContain('aria-expanded="false"');
  expect(markup).toContain('aria-autocomplete="list"');
  expect(markup).toContain("Loading deployment secrets");
  expect(markup).toContain('type="hidden" name="reference"');
  expect(markup).not.toContain('role="option"');
});

it("filters secret metadata by name or source without secret values", () => {
  const references = [
    {
      reference: "aws:prod/token",
      name: "prod/token",
      source: "aws",
      supports_json_key: true,
    },
    {
      reference: "local-token",
      name: "local-token",
      source: "configured",
      supports_json_key: false,
    },
  ];
  expect(filterSecretReferences(references, "PROD")).toEqual([references[0]]);
  expect(filterSecretReferences(references, "AWS")).toEqual([references[0]]);
  expect(filterSecretReferences(references, "configured")).toEqual([
    references[1],
  ]);
  expect(filterSecretReferences(references, "missing")).toEqual([]);
});
