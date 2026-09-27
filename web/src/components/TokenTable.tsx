import { useSettingsError } from "../useSettingsError";
import {
  SettingsPanel,
  SettingsFeedback,
  SettingsButton,
} from "./SettingsLayout";
import { useEffect, useState } from "react";
import { KeyRound } from "lucide-react";
import { api } from "../api";
import { tokenStatus, visibleTokens } from "../tokenMetadata";
import type { TokenCounts, TokenMetadata, TokenStatus } from "../tokenMetadata";
import { Pagination } from "./Pagination";

export function TokenTable({
  tokens,
  error = "",
  filter,
  onFilter,
  onRefresh,
  onError,
  counts,
  page,
  size,
  total,
  loading,
  onPage,
  onSize,
}: {
  tokens: TokenMetadata[] | null;
  error?: string;
  filter: TokenStatus | "All";
  onFilter: (filter: TokenStatus | "All") => void;
  onRefresh: () => Promise<void>;
  onError: (value: string) => void;
  counts: TokenCounts;
  page: number;
  size: number;
  total: number;
  loading: boolean;
  onPage: (page: number) => void;
  onSize: (size: number) => void;
}) {
  const {
    error: requestError,
    reportError,
    clearError,
  } = useSettingsError(onError);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const [pending, setPending] = useState<string[]>([]);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, []);
  async function revoke(token: TokenMetadata) {
    setPending((items) => [...items, token.id]);
    setNotice("");
    clearError();
    try {
      await api(`/v1/tokens/${token.id}/revoke`, {});
      setNotice(`Token “${token.name}” revoked.`);
      await onRefresh();
    } catch (error) {
      reportError(error);
    } finally {
      setPending((items) => items.filter((id) => id !== token.id));
    }
  }
  const rows = visibleTokens(tokens ?? [], filter, now);
  return (
    <SettingsPanel
      title="Your tokens"
      description="Tokens issued by your account, across repositories."
      toolbar={
        <>
          <SettingsButton
            disabled={loading}
            onClick={() => {
              clearError();
              void onRefresh().catch(reportError);
            }}
          >
            Refresh tokens
          </SettingsButton>
          <div
            className="token-filters"
            role="group"
            aria-label="Filter tokens by status"
          >
            {(["Active", "All", "Expired", "Revoked"] as const).map((value) => (
              <SettingsButton
                compact
                key={value}
                aria-pressed={filter === value}
                onClick={() => onFilter(value)}
              >
                {value}{" "}
                <span>{counts[value.toLowerCase() as keyof TokenCounts]}</span>
              </SettingsButton>
            ))}
          </div>
        </>
      }
    >
      {requestError && (
        <SettingsFeedback tone="error">{requestError}</SettingsFeedback>
      )}
      {notice && <SettingsFeedback tone="success">{notice}</SettingsFeedback>}
      {error ? (
        <SettingsFeedback tone="error">{error}</SettingsFeedback>
      ) : tokens === null || loading ? (
        <SettingsFeedback>Loading token metadata…</SettingsFeedback>
      ) : rows.length ? (
        <div className="token-table-scroll">
          <table className="token-table">
            <caption>
              {filter === "All"
                ? "All account tokens"
                : `${filter} account tokens`}
            </caption>
            <thead>
              <tr>
                {[
                  "Name",
                  "Repository",
                  "Environment",
                  "Capabilities",
                  "Expires",
                  "Status",
                  "Actions",
                ].map((title) => (
                  <th scope="col" key={title}>
                    {title}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((token) => (
                <tr key={token.id}>
                  <th scope="row">{token.name}</th>
                  <td>
                    <code>{token.repository}</code>
                  </td>
                  <td>
                    {token.environment === "*"
                      ? "All environments"
                      : token.environment === "default"
                        ? "Repository default"
                        : token.environment}
                  </td>
                  <td>
                    <div className="token-badges">
                      {token.permissions.map((permission) => (
                        <span className="layer-badge" key={permission}>
                          {permission}
                        </span>
                      ))}
                      {token.workloads?.map((workload) => (
                        <span className="layer-badge" key={workload}>
                          {workload}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td>
                    <time
                      dateTime={new Date(token.expires * 1000).toISOString()}
                    >
                      {new Date(token.expires * 1000).toLocaleString()}
                    </time>
                  </td>
                  <td>
                    <span
                      className={`token-status ${tokenStatus(token, now).toLowerCase()}`}
                    >
                      {tokenStatus(token, now)}
                    </span>
                  </td>
                  <td>
                    {tokenStatus(token, now) === "Active" ? (
                      <SettingsButton
                        variant="danger"
                        compact
                        disabled={pending.includes(token.id)}
                        onClick={() => void revoke(token)}
                        aria-label={`Revoke ${token.name}`}
                      >
                        {pending.includes(token.id) ? "Revoking…" : "Revoke"}
                      </SettingsButton>
                    ) : (
                      <span className="token-inactive-action">No actions</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="token-empty">
          <KeyRound size={24} />
          <h4>
            {filter === "All"
              ? "No tokens issued"
              : `No ${filter.toLowerCase()} tokens`}
          </h4>
          <p>
            {filter === "Active"
              ? "Create a token above, or choose All to see expired and revoked tokens."
              : "Tokens matching this filter will appear here."}
          </p>
        </div>
      )}
      <Pagination
        label="Tokens"
        page={page}
        size={size}
        total={total}
        loading={loading}
        onPage={onPage}
        onSize={onSize}
      />
    </SettingsPanel>
  );
}
