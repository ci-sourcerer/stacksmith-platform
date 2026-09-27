import { useEffect, useState } from "react";
import { api } from "../api";
import { CredentialPhases } from "./CredentialPhases";
import { useSettingsError } from "../useSettingsError";
import {
  SettingsPanel,
  SettingsFields,
  SettingsField,
  SettingsActions,
  SettingsFeedback,
  SettingsButton,
} from "./SettingsLayout";

type Binding = { provider: string; phases: string[] };
type Provider = { name: string; provider: string };

export function ExecutionCredentials({
  repository,
  environment,
  onSaved,
  onError,
}: {
  repository: string;
  environment: string;
  onSaved: (notice?: string) => void;
  onError: (message: string) => void;
}) {
  const base = `/v1/repositories/${repository}/environments/${environment}`;
  const [providers, setProviders] = useState<Provider[]>([]);
  const [bindings, setBindings] = useState<Binding[]>([]);
  const [provider, setProvider] = useState("");
  const [selectedPhases, setSelectedPhases] = useState<string[]>([]);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const { error, reportError, clearError } = useSettingsError(onError);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setFailed(false);
    clearError();
    Promise.all([
      api<{ providers: Provider[] }>(`${base}/execution-credential-providers`),
      api<{ bindings: Binding[] }>(`${base}/execution-credential-bindings`),
    ])
      .then(([catalog, current]) => {
        if (active) {
          setProviders(catalog.providers);
          setBindings(current.bindings);
        }
      })
      .catch((failure) => {
        if (active) {
          setFailed(true);
          reportError(failure);
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [base, refresh, clearError, reportError]);

  async function save() {
    clearError();
    setSaving(true);
    try {
      await api(`${base}/execution-credential-bindings`, {
        provider,
        phases: selectedPhases,
      });
      setProvider("");
      setSelectedPhases([]);
      setRefresh((value) => value + 1);
      onSaved("Execution identity saved. Pending approvals were invalidated.");
    } catch (failure) {
      reportError(failure);
    } finally {
      setSaving(false);
    }
  }

  async function revoke(name: string) {
    clearError();
    setSaving(true);
    try {
      await api(
        `${base}/execution-credential-bindings/${encodeURIComponent(name)}/remove`,
        {},
      );
      setRefresh((value) => value + 1);
      onSaved(
        "Execution identity revoked. Pending approvals were invalidated.",
      );
    } catch (failure) {
      reportError(failure);
    } finally {
      setSaving(false);
    }
  }

  return (
    <SettingsPanel
      title="Execution identities"
      description="Assign temporary, automatically refreshed credentials to execution phases. Each phase can use one approved identity."
      form={{
        onSubmit: (event) => {
          event.preventDefault();
          if (provider && selectedPhases.length) void save();
        },
      }}
      toolbar={
        <SettingsButton
          type="button"
          disabled={loading || saving}
          onClick={() => setRefresh((value) => value + 1)}
        >
          Refresh identities
        </SettingsButton>
      }
      footer={
        <SettingsActions>
          <SettingsButton
            type="submit"
            variant="primary"
            disabled={loading || saving || !provider || !selectedPhases.length}
          >
            Save execution identity
          </SettingsButton>
        </SettingsActions>
      }
    >
      {loading && (
        <SettingsFeedback>Loading execution identities…</SettingsFeedback>
      )}
      {error && <SettingsFeedback tone="error">{error}</SettingsFeedback>}
      {!loading && !failed && !error && (
        <>
          {!bindings.length ? (
            <SettingsFeedback>
              No execution identities assigned yet.
            </SettingsFeedback>
          ) : (
            <ul className="settings-binding-list credential-binding-list">
              {bindings.map((item) => (
                <li key={item.provider}>
                  <div>
                    <strong>{item.provider}</strong>
                    <p>
                      {item.phases.join(", ")}
                      {!providers.some(
                        (entry) => entry.name === item.provider,
                      ) && " · Provider unavailable"}
                    </p>
                  </div>
                  <SettingsButton
                    type="button"
                    variant="danger"
                    compact
                    disabled={saving}
                    onClick={() => void revoke(item.provider)}
                  >
                    Revoke execution identity
                  </SettingsButton>
                </li>
              ))}
            </ul>
          )}
          <div className="credential-form-heading">
            <h4>Assign an execution identity</h4>
          </div>
          {!providers.length ? (
            <SettingsFeedback>
              No execution identity providers are configured. Ask a platform
              administrator to configure one.
            </SettingsFeedback>
          ) : (
            <SettingsFields wide>
              <SettingsField label="Approved identity">
                <select
                  value={provider}
                  onChange={(event) => {
                    setProvider(event.target.value);
                    setSelectedPhases(
                      bindings.find(
                        (item) => item.provider === event.target.value,
                      )?.phases ?? [],
                    );
                  }}
                >
                  <option value="">Select an identity</option>
                  {providers.map((item) => (
                    <option key={item.name} value={item.name}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </SettingsField>
              <CredentialPhases
                selected={selectedPhases}
                onChange={setSelectedPhases}
              />
            </SettingsFields>
          )}
        </>
      )}
    </SettingsPanel>
  );
}
