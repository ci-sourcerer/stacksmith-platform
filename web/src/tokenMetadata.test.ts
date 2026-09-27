import { expect, it } from "vitest";
import { tokenStatus, visibleTokens } from "./tokenMetadata";
import type { TokenMetadata } from "./tokenMetadata";

const token: TokenMetadata = {
  id: "one",
  name: "deploy",
  repository: "acme/service",
  environment: "*",
  permissions: ["view", "deploy"],
  expires: 200,
  revoked: 0,
};
it("expires at the boundary and gives revocation precedence", () => {
  expect(tokenStatus(token, 199)).toBe("Active");
  expect(tokenStatus(token, 200)).toBe("Expired");
  expect(tokenStatus({ ...token, revoked: 100 }, 300)).toBe("Revoked");
});
it("filters by status and sorts active expiry before inactive history", () => {
  const tokens = [
    { ...token, id: "expired", expires: 50 },
    { ...token, id: "later", expires: 300 },
    { ...token, id: "revoked", revoked: 90 },
    token,
  ];
  expect(visibleTokens(tokens, "Active", 100).map((item) => item.id)).toEqual([
    "one",
    "later",
  ]);
  expect(visibleTokens(tokens, "Revoked", 100).map((item) => item.id)).toEqual([
    "revoked",
  ]);
  expect(visibleTokens(tokens, "All", 100).map((item) => item.id)).toEqual([
    "one",
    "later",
    "revoked",
    "expired",
  ]);
  expect(visibleTokens([], "Expired", 100)).toEqual([]);
});
