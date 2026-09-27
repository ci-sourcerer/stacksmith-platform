import {
  SettingsPanel,
  SettingsFeedback,
  SettingsButton,
} from "./SettingsLayout";
import { useEffect, useState } from "react";
import { api } from "../api";
import { useApplication } from "../ApplicationContext";
import type { Platform } from "../types";
import { PlatformView } from "./PlatformView";

export function EffectiveConfiguration() {
  const { workspace, session } = useApplication();
  const [configuration, setConfiguration] = useState<Platform>();
  const [retry, setRetry] = useState(0);
  const [error, setError] = useState("");
  useEffect(() => {
    const abort = new AbortController();
    setConfiguration(undefined);
    setError("");
    if (session.mode !== "demo" && !workspace.repository) return;
    api<Platform>(
      `/platform${workspace.repository ? `?repository=${encodeURIComponent(workspace.repository)}` : ""}`,
      undefined,
      abort.signal,
    )
      .then((value) => {
        if (!abort.signal.aborted) setConfiguration(value);
      })
      .catch((failure: unknown) => {
        if (!abort.signal.aborted)
          setError(
            failure instanceof Error
              ? failure.message
              : "Configuration unavailable.",
          );
      });
    return () => abort.abort();
  }, [workspace.repository, session.mode, retry]);
  if (!workspace.repository && session.mode !== "demo")
    return (
      <SettingsPanel title="Configuration inspection">
        <SettingsFeedback>
          Choose a repository in{" "}
          <a href="/settings?section=workspace">Workspace settings</a> to
          inspect its effective configuration.
        </SettingsFeedback>
      </SettingsPanel>
    );
  if (error)
    return (
      <SettingsPanel
        title="Configuration inspection"
        toolbar={
          <SettingsButton onClick={() => setRetry((value) => value + 1)}>
            Retry configuration
          </SettingsButton>
        }
      >
        <SettingsFeedback tone="error">{error}</SettingsFeedback>
      </SettingsPanel>
    );
  return configuration ? (
    <PlatformView platform={configuration} />
  ) : (
    <SettingsPanel title="Configuration inspection">
      <SettingsFeedback>Loading effective configuration…</SettingsFeedback>
    </SettingsPanel>
  );
}
