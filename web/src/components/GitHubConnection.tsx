import {
  SettingsPanel,
  SettingsActions,
  SettingsFeedback,
  SettingsButton,
} from "./SettingsLayout";
import { useEffect, useState } from "react";
import { api } from "../api";

type Connection = {
  install_url: string | null;
  installations: {
    id: number;
    account: string;
    management_url: string | null;
  }[];
};

export function GitHubConnection() {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    setError("");
    api<Connection>("/github-connection")
      .then((value) => {
        if (active) setConnection(value);
      })
      .catch((failure: unknown) => {
        if (active)
          setError(
            failure instanceof Error
              ? failure.message
              : "GitHub connection unavailable.",
          );
      });
    return () => {
      active = false;
    };
  }, [attempt]);
  if (error)
    return (
      <SettingsPanel
        title="GitHub access"
        toolbar={
          <SettingsButton onClick={() => setAttempt((value) => value + 1)}>
            Retry connection
          </SettingsButton>
        }
      >
        <SettingsFeedback tone="error">{error}</SettingsFeedback>
      </SettingsPanel>
    );
  if (!connection)
    return (
      <SettingsPanel title="GitHub access">
        <SettingsFeedback>Loading GitHub connection…</SettingsFeedback>
      </SettingsPanel>
    );
  return (
    <SettingsPanel
      title="GitHub access"
      description="Connect the GitHub App and choose which repositories it can access."
      footer={
        connection.install_url && (
          <SettingsActions>
            <a
              className="settings-button settings-button-primary"
              href={connection.install_url}
            >
              {connection.installations.length
                ? "Connect another account"
                : "Connect GitHub"}
            </a>
          </SettingsActions>
        )
      }
    >
      <div className="github-connection">
        {connection.installations.length ? (
          <ul
            className="github-accounts"
            aria-label="Connected GitHub accounts"
          >
            {connection.installations.map((installation) => (
              <li key={installation.id}>
                <div>
                  <strong>{installation.account}</strong>
                  <span className="platform-muted">Connected</span>
                </div>
                {installation.management_url && (
                  <a
                    className="settings-button settings-button-secondary"
                    href={installation.management_url}
                    aria-label={`Manage repository access for ${installation.account}`}
                  >
                    Manage repository access
                  </a>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <SettingsFeedback>No GitHub accounts connected.</SettingsFeedback>
        )}
      </div>
    </SettingsPanel>
  );
}
