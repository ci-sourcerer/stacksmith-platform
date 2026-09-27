import {
  SettingsPanel,
  SettingsFields,
  SettingsField,
  SettingsActions,
  SettingsFeedback,
  SettingsButton,
} from "./SettingsLayout";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useApplication } from "../ApplicationContext";
import type { Repository } from "../types";
import {
  composerUrl,
  selectSoleTarget,
  type DeploymentTarget,
  type InspectionTargets,
  type WorkspaceSelection,
} from "../workspaceSelection";
import { HelpTip } from "./HelpTip";
import { RepositorySelector } from "./RepositorySelector";

export function WorkspaceSettings() {
  const { session, workspace, applyWorkspace } = useApplication();
  const [draft, setDraft] = useState(() => ({
    ...workspace,
    environment: session.execution_enabled ? workspace.environment : "",
  }));
  const pending = useRef<AbortController | null>(null);
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [paths, setPaths] = useState<string[]>([]);
  const [environments, setEnvironments] = useState<DeploymentTarget[]>([]);
  const [canConfigure, setCanConfigure] = useState(false);
  const [targetAvailability, setTargetAvailability] =
    useState<InspectionTargets["availability"]>("unconfigured");
  const [loading, setLoading] = useState(true);
  const [loadingChoices, setLoadingChoices] = useState(false);
  const [error, setError] = useState("");
  const [stackError, setStackError] = useState("");
  const [targetError, setTargetError] = useState("");
  const [stacksLoading, setStacksLoading] = useState(false);
  const [targetsLoading, setTargetsLoading] = useState(false);
  const [stackRetry, setStackRetry] = useState(0);
  const [targetRetry, setTargetRetry] = useState(0);
  const [saved, setSaved] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(
    () =>
      setDraft({
        ...workspace,
        environment: session.execution_enabled ? workspace.environment : "",
      }),
    [workspace, session.execution_enabled],
  );
  useEffect(() => () => pending.current?.abort(), []);
  useEffect(() => {
    if (session.mode === "demo") {
      setLoading(false);
      return;
    }
    const abort = new AbortController();
    setLoading(true);
    setError("");
    api<{ repositories: Repository[] }>(
      "/repository-discovery",
      undefined,
      abort.signal,
    )
      .then((result) => {
        if (!abort.signal.aborted) setRepositories(result.repositories);
      })
      .catch((failure: unknown) => {
        if (!abort.signal.aborted)
          setError(
            failure instanceof Error
              ? failure.message
              : "Repository discovery failed.",
          );
      })
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => abort.abort();
  }, [session.mode, refresh]);
  useEffect(() => {
    setStacksLoading(false);
    setPaths([]);
    setStackError("");
    if (
      !draft.repository ||
      !repositories.some((item) => item.name === draft.repository)
    )
      return;
    const abort = new AbortController();
    setStacksLoading(true);
    api<{ paths: string[] }>(
      `/repositories/${draft.repository}/stacks`,
      undefined,
      abort.signal,
    )
      .then((result) => {
        if (!abort.signal.aborted) setPaths(result.paths);
      })
      .catch((failure: unknown) => {
        if (!abort.signal.aborted)
          setStackError(
            failure instanceof Error
              ? failure.message
              : "Stack discovery failed.",
          );
      })
      .finally(() => {
        if (!abort.signal.aborted) setStacksLoading(false);
      });
    return () => abort.abort();
  }, [draft.repository, repositories, stackRetry]);
  useEffect(() => {
    setTargetsLoading(false);
    setEnvironments([]);
    setCanConfigure(false);
    setTargetError("");
    if (
      !session.execution_enabled ||
      !draft.repository ||
      !repositories.some((item) => item.name === draft.repository)
    )
      return;
    const abort = new AbortController();
    setTargetsLoading(true);
    api<InspectionTargets>(
      `/inspection-targets?repository=${encodeURIComponent(draft.repository)}`,
      undefined,
      abort.signal,
    )
      .then((result) => {
        if (abort.signal.aborted) return;
        setEnvironments(result.targets);
        setCanConfigure(result.can_configure);
        setTargetAvailability(result.availability);
        setDraft((selection) => selectSoleTarget(selection, result.targets));
      })
      .catch((failure: unknown) => {
        if (!abort.signal.aborted)
          setTargetError(
            failure instanceof Error
              ? failure.message
              : "Environment discovery failed.",
          );
      })
      .finally(() => {
        if (!abort.signal.aborted) setTargetsLoading(false);
      });
    return () => abort.abort();
  }, [draft.repository, repositories, session.execution_enabled, targetRetry]);
  function change(next: WorkspaceSelection) {
    setDraft(next);
    setSaved(false);
    setError("");
  }
  const unavailable =
    !!draft.repository &&
    !loading &&
    !error &&
    !repositories.some((item) => item.name === draft.repository);
  const invalidPath =
    !!draft.path &&
    !loading &&
    !loadingChoices &&
    !error &&
    !stacksLoading &&
    !stackError &&
    !paths.includes(draft.path);
  const invalidEnvironment =
    !!draft.environment &&
    !loading &&
    !loadingChoices &&
    !error &&
    !targetsLoading &&
    !targetError &&
    !environments.some((item) => item.name === draft.environment);
  const changed =
    draft.repository !== workspace.repository ||
    draft.path !== workspace.path ||
    draft.environment !== workspace.environment ||
    !!draft.preview !== !!workspace.preview;
  if (session.mode === "demo")
    return (
      <SettingsPanel title="Active workspace">
        <p>
          Composer uses the local example stack. GitHub and deployment settings
          are available in connected deployments.
        </p>
        <a className="button" href="/">
          Open Composer
        </a>
      </SettingsPanel>
    );
  return (
    <SettingsPanel
      title="Active workspace"
      description="Choose the repository, stack file, and inspection environment used by Composer. Runs remembers its target separately."
      className="workspace-settings"
      footer={
        <>
          <SettingsActions
            feedback={
              <>
                {" "}
                {changed && (
                  <SettingsFeedback>
                    Unapplied workspace changes.
                  </SettingsFeedback>
                )}
                <p className="field-help">
                  Refresh reloads available choices without applying the draft.
                </p>
                {saved && (
                  <SettingsFeedback tone="success">
                    Workspace applied.
                  </SettingsFeedback>
                )}
              </>
            }
          >
            <SettingsButton
              type="submit"
              variant="primary"
              disabled={
                loading ||
                loadingChoices ||
                !draft.repository ||
                unavailable ||
                invalidPath ||
                invalidEnvironment ||
                !!error ||
                stacksLoading ||
                targetsLoading ||
                (!!draft.path && !!stackError) ||
                (!!draft.environment && !!targetError)
              }
            >
              {loadingChoices ? "Validating workspace…" : "Apply workspace"}
            </SettingsButton>
            <SettingsButton
              type="button"
              disabled={loading || loadingChoices}
              onClick={() => setRefresh((value) => value + 1)}
            >
              Refresh choices
            </SettingsButton>
            <HelpTip label="About refreshing choices">
              Reload available repositories, stack files, and environments
              without applying your selections.
            </HelpTip>
          </SettingsActions>
          <div className="settings-navigation">
            <p>
              Open the applied workspace
              {workspace.repository ? `: ${workspace.repository}` : ""}
              {workspace.path ? ` / ${workspace.path}` : ""}
              {workspace.environment
                ? ` / ${workspace.environment}`
                : " / stack preview"}
              .
            </p>
            <SettingsActions>
              <a
                className="settings-button settings-button-text"
                href={composerUrl(workspace)}
              >
                Open Composer
              </a>
              {session.execution_enabled && (
                <a
                  className="settings-button settings-button-text"
                  href="/runs"
                >
                  Open Runs
                </a>
              )}
            </SettingsActions>
          </div>
        </>
      }
      form={{
        onSubmit: async (event) => {
          event.preventDefault();
          setSaved(false);
          setLoadingChoices(true);
          const abort = new AbortController();
          pending.current = abort;
          try {
            const [available, stacks, deployments] = await Promise.all([
              api<{ repositories: Repository[] }>(
                "/repository-discovery",
                undefined,
                abort.signal,
              ),
              !draft.path
                ? Promise.resolve({ paths: [] })
                : api<{ paths: string[] }>(
                    `/repositories/${draft.repository}/stacks`,
                    undefined,
                    abort.signal,
                  ),
              session.execution_enabled && (draft.environment || !targetError)
                ? api<InspectionTargets>(
                    `/inspection-targets?repository=${encodeURIComponent(draft.repository)}`,
                    undefined,
                    abort.signal,
                  )
                : Promise.resolve({
                    targets: [],
                    availability: "unconfigured" as const,
                    can_configure: false,
                  }),
            ]);
            if (abort.signal.aborted) return;
            setRepositories(available.repositories);
            setPaths(stacks.paths);
            setEnvironments(deployments.targets);
            setCanConfigure(deployments.can_configure);
            setTargetAvailability(deployments.availability);
            if (
              !available.repositories.some(
                (item) => item.name === draft.repository,
              ) ||
              (draft.path && !stacks.paths.includes(draft.path)) ||
              (draft.environment &&
                !deployments.targets.some(
                  (item) => item.name === draft.environment,
                ))
            ) {
              setError(
                "This workspace is no longer available. Review your repository, stack, and environment selections.",
              );
              return;
            }
            setError("");
            setSaved(
              await applyWorkspace(
                selectSoleTarget(draft, deployments.targets),
              ),
            );
          } catch (failure) {
            if (abort.signal.aborted) return;
            setError(
              failure instanceof Error
                ? failure.message
                : "Workspace validation failed.",
            );
          } finally {
            if (!abort.signal.aborted) setLoadingChoices(false);
          }
        },
      }}
    >
      <SettingsFields>
        <div className="workspace-field">
          <RepositorySelector
            label="Active repository"
            description="Repository supplying source files for Composer and repository settings."
            repositories={repositories.map((item) => ({
              id: item.name,
              account: item.account,
              description: item.description,
              writable: item.writable,
            }))}
            selected={draft.repository}
            onSelect={(repository) =>
              change({ repository, path: "", environment: "" })
            }
            loading={loading}
            disabled={loadingChoices}
            required
          />
        </div>
        {loading && (
          <SettingsFeedback>
            {refresh ? "Refreshing repositories…" : "Loading repositories…"}
          </SettingsFeedback>
        )}
        {unavailable && (
          <SettingsFeedback tone="error">
            The selected repository is no longer accessible. Choose another
            repository or review{" "}
            <a href="/settings?section=github">GitHub access in Settings</a>.
          </SettingsFeedback>
        )}
        {!loading && !repositories.length && !error && (
          <p>
            No accessible repositories. Review{" "}
            <a href="/settings?section=github">GitHub access</a> or ask an
            administrator for platform access.
          </p>
        )}
        <SettingsField
          label="Composer stack"
          description={
            <>
              {" "}
              Select the stack file to edit in Composer. Runs do not require a
              Composer stack.{" "}
            </>
          }
        >
          <select
            value={draft.path}
            disabled={
              !draft.repository ||
              loading ||
              stacksLoading ||
              loadingChoices ||
              unavailable
            }
            onChange={(event) => change({ ...draft, path: event.target.value })}
          >
            <option value="">
              {loading || stacksLoading
                ? "Loading stack files…"
                : "No stack selected"}
            </option>
            {(loading || stacksLoading) &&
              draft.path &&
              !paths.includes(draft.path) && (
                <option value={draft.path}>{draft.path} (loading…)</option>
              )}
            {invalidPath && (
              <option value={draft.path}>{draft.path} (unavailable)</option>
            )}
            {paths.map((path) => (
              <option key={path}>{path}</option>
            ))}
          </select>
        </SettingsField>
        {stacksLoading && (
          <SettingsFeedback>Loading stack files…</SettingsFeedback>
        )}
        {stackError && (
          <SettingsFeedback tone="error">
            <p>{stackError}</p>
            <SettingsButton
              type="button"
              onClick={() => setStackRetry((value) => value + 1)}
            >
              Retry stack discovery
            </SettingsButton>
          </SettingsFeedback>
        )}
        {draft.repository &&
          !loading &&
          !stacksLoading &&
          !stackError &&
          !loadingChoices &&
          !paths.length &&
          !unavailable && (
            <p>
              No stack files found. This repository can still be used for Runs.
            </p>
          )}
        {session.execution_enabled && (
          <SettingsField
            label="Environment"
            description={
              <>
                {" "}
                Composer inspection context. Stack preview excludes deployment
                variables and credentials.{" "}
              </>
            }
          >
            <select
              value={draft.environment}
              disabled={
                !draft.repository ||
                loading ||
                targetsLoading ||
                loadingChoices ||
                unavailable
              }
              onChange={(event) =>
                change({
                  ...draft,
                  environment: event.target.value,
                  preview: event.target.value ? undefined : true,
                })
              }
            >
              <option value="">Stack preview (no deployment)</option>
              {(loading || targetsLoading) &&
                draft.environment &&
                !environments.some(
                  (item) => item.name === draft.environment,
                ) && (
                  <option value={draft.environment}>
                    {draft.environment} (loading…)
                  </option>
                )}
              {invalidEnvironment && (
                <option value={draft.environment}>
                  {draft.environment} (unavailable)
                </option>
              )}
              {environments.map((item) => (
                <option key={item.name}>{item.name}</option>
              ))}
            </select>
          </SettingsField>
        )}
        {targetsLoading && (
          <SettingsFeedback>Loading environments…</SettingsFeedback>
        )}
        {targetError && (
          <SettingsFeedback tone="error">
            <p>{targetError}</p>
            <SettingsButton
              type="button"
              onClick={() => setTargetRetry((value) => value + 1)}
            >
              Retry environment discovery
            </SettingsButton>
          </SettingsFeedback>
        )}
        {session.execution_enabled &&
          !targetsLoading &&
          !targetError &&
          draft.repository &&
          !loading &&
          !loadingChoices &&
          !unavailable &&
          !error && (
            <p>
              {targetAvailability === "unconfigured"
                ? "No deployment targets are configured for this repository. "
                : targetAvailability === "forbidden"
                  ? "You do not have Infrastructure planning access to a deployment target. "
                  : ""}

              {canConfigure ? (
                <>
                  {" "}
                  <a href="/settings?section=environments">
                    Configure deployments
                  </a>
                  .
                </>
              ) : !environments.some((item) => item.can_inspect) ? (
                <>
                  {" "}
                  Ask an administrator to configure a target or grant access.
                </>
              ) : null}
            </p>
          )}
        {error && <SettingsFeedback tone="error">{error}</SettingsFeedback>}
        {invalidPath && (
          <SettingsFeedback tone="error">
            The selected stack is unavailable. Choose a discovered stack or
            clear the stack selection.
          </SettingsFeedback>
        )}
        {invalidEnvironment && (
          <SettingsFeedback tone="error">
            The selected environment is unavailable. Choose another environment
            or clear the selection.
          </SettingsFeedback>
        )}
      </SettingsFields>
    </SettingsPanel>
  );
}
