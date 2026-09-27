export type TokenMetadata = {
  id: string;
  name: string;
  revoked: number;
  expires: number;
  repository: string;
  environment: string;
  permissions: string[];
  workloads?: string[];
};
export type TokenStatus = "Active" | "Expired" | "Revoked";
export type TokenCounts = Record<
  "active" | "all" | "expired" | "revoked",
  number
>;

export function tokenStatus(token: TokenMetadata, now: number): TokenStatus {
  return token.revoked
    ? "Revoked"
    : token.expires <= now
      ? "Expired"
      : "Active";
}

export function visibleTokens(
  tokens: TokenMetadata[],
  filter: TokenStatus | "All",
  now: number,
): TokenMetadata[] {
  return tokens
    .filter((token) => filter === "All" || tokenStatus(token, now) === filter)
    .sort((left, right) => {
      const leftActive = tokenStatus(left, now) === "Active";
      const rightActive = tokenStatus(right, now) === "Active";
      if (leftActive !== rightActive) return leftActive ? -1 : 1;
      return (
        (leftActive
          ? left.expires - right.expires
          : right.expires - left.expires) || left.name.localeCompare(right.name)
      );
    });
}
