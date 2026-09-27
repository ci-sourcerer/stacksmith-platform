import { useSettingsError } from "../useSettingsError";
import {
  SettingsPanel,
  SettingsFields,
  SettingsField,
  SettingsActions,
  SettingsFeedback,
  SettingsButton,
} from "./SettingsLayout";
import { useEffect, useState } from "react";
import { ListAddButton, ListRemoveButton } from "./ListControls";
import { api } from "../api";

type Role = "viewer" | "runner" | "provisioner";
type Grant = {
  id: string;
  login: string;
  environment: string;
  role: Role;
  workloads: string[];
};
type Environment = { name: string; settings?: { discovery_mode: string } };
const roleLabels = {
  viewer: "Stack viewer",
  runner: "Stack runner",
  provisioner: "Stack provisioner",
};

function RoleChoices({
  role,
  workloads,
  onChange,
  disabled = false,
}: {
  role: Role;
  workloads: string[];
  onChange: (role: Role, workloads: string[]) => void;
  disabled?: boolean;
}) {
  return (
    <fieldset className="role-choices" disabled={disabled}>
      <SettingsField label="Role">
        <select
          value={role}
          onChange={(event) => {
            const next = event.target.value as Role;
            onChange(next, next === "viewer" ? [] : workloads);
          }}
        >
          {Object.entries(roleLabels).map(([value, label]) => (
            <option value={value} key={value}>
              {label}
            </option>
          ))}
        </select>
      </SettingsField>
      {role !== "viewer" && (
        <div
          className="role-workloads"
          role="group"
          aria-label="Allowed workloads"
        >
          {["infrastructure", "operations"].map((workload) => (
            <label key={workload}>
              <input
                type="checkbox"
                checked={workloads.includes(workload)}
                onChange={(event) =>
                  onChange(
                    role,
                    event.target.checked
                      ? [...workloads, workload]
                      : workloads.filter((value) => value !== workload),
                  )
                }
              />
              {workload === "infrastructure" ? "Infrastructure" : "Operations"}
            </label>
          ))}
          {!workloads.length && <small>Select at least one workload.</small>}
        </div>
      )}
    </fieldset>
  );
}

function GrantRow({
  grant,
  scope,
  busy,
  onSave,
  onRemove,
}: {
  grant: Grant;
  scope: string;
  busy: boolean;
  onSave: (role: Role, workloads: string[]) => void;
  onRemove: () => void;
}) {
  const [role, setRole] = useState(grant.role);
  const [workloads, setWorkloads] = useState(grant.workloads);
  useEffect(() => {
    setRole(grant.role);
    setWorkloads(grant.workloads);
  }, [grant]);
  const changed =
    role !== grant.role ||
    workloads.length !== grant.workloads.length ||
    workloads.some((value) => !grant.workloads.includes(value));
  return (
    <tr>
      <th scope="row">{grant.login}</th>
      <td>{scope}</td>
      <td>
        <RoleChoices
          role={role}
          workloads={workloads}
          disabled={busy}
          onChange={(next, selected) => {
            setRole(next);
            setWorkloads(selected);
          }}
        />
      </td>
      <td>
        <div className="permission-actions">
          <SettingsButton
            compact
            disabled={
              busy || !changed || (role !== "viewer" && !workloads.length)
            }
            onClick={() => onSave(role, workloads)}
          >
            Save
          </SettingsButton>
          <ListRemoveButton
            className="settings-button settings-button-danger settings-button-compact"
            disabled={busy}
            onClick={onRemove}
            label={`Remove ${grant.login} from ${scope}`}
          />
        </div>
      </td>
    </tr>
  );
}

export function Permissions({
  repository,
  onError,
}: {
  repository: string;
  onError: (value: string) => void;
}) {
  const {
    error: requestError,
    reportError,
    clearError,
  } = useSettingsError(onError);
  const [retry, setRetry] = useState(0);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [login, setLogin] = useState("");
  const [environment, setEnvironment] = useState("*");
  const [role, setRole] = useState<Role>("viewer");
  const [workloads, setWorkloads] = useState<string[]>([]);
  const [adding, setAdding] = useState(false);
  const [notice, setNotice] = useState("");
  const path = `/v1/repositories/${repository}`;
  useEffect(() => {
    let active = true;
    setLoading(true);
    clearError();
    setAdding(false);
    setNotice("");
    Promise.all([
      api<Grant[]>(`${path}/grants`),
      api<Environment[]>(`${path}/environments`),
    ])
      .then(([assigned, targets]) => {
        if (active) {
          setGrants(assigned);
          setEnvironments(targets);
        }
      })
      .catch((error) => {
        if (active) reportError(error);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [path, reportError, clearError, retry]);
  const scopeName = (scope: string) =>
    scope === "*"
      ? "All environments"
      : environments.find((item) => item.name === scope)?.settings
            ?.discovery_mode === "single"
        ? "Repository default"
        : scope;
  async function save(
    user: string,
    scope: string,
    selectedRole: Role,
    selectedWorkloads: string[],
  ) {
    setBusy(true);
    clearError();
    setNotice("");
    try {
      await api(`${path}/grants`, {
        login: user.trim(),
        environment: scope,
        role: selectedRole,
        workloads: selectedWorkloads,
      });
      setGrants(await api<Grant[]>(`${path}/grants`));
      setNotice("Role assignment saved.");
      setAdding(false);
    } catch (error) {
      reportError(error);
    } finally {
      setBusy(false);
    }
  }
  async function remove(grant: Grant) {
    setBusy(true);
    clearError();
    setNotice("");
    try {
      await api(
        `${path}/grants/${encodeURIComponent(grant.id)}`,
        undefined,
        undefined,
        undefined,
        "DELETE",
      );
      setGrants(await api<Grant[]>(`${path}/grants`));
      setNotice("Role assignment removed. Pending phases will recheck access.");
    } catch (error) {
      reportError(error);
    } finally {
      setBusy(false);
    }
  }
  return (
    <SettingsPanel
      title="Role assignments"
      description="Manage who can view, plan, execute, and approve work in this repository."
      toolbar={
        <ListAddButton
          onClick={() => {
            setAdding(!adding);
            setLogin("");
            setEnvironment("*");
            setRole("viewer");
            setWorkloads([]);
          }}
        >
          Assign role
        </ListAddButton>
      }
    >
      <p>
        Viewers read stack results. Runners request plans and execution.
        Provisioners also manage settings and approve their allowed workloads.
        Repository-wide Provisioners can author Composer changes and edit shared
        configuration.
      </p>
      <p>
        Assignments add access across their scopes. Configured platform Admins
        retain full access and are the only users who can assign roles.
      </p>
      {requestError && (
        <SettingsFeedback tone="error">
          {requestError}{" "}
          <SettingsButton
            compact
            onClick={() => setRetry((value) => value + 1)}
          >
            Retry role assignments
          </SettingsButton>
        </SettingsFeedback>
      )}
      {notice && <SettingsFeedback tone="success">{notice}</SettingsFeedback>}
      {adding && (
        <form
          className="permission-add"
          onSubmit={(event) => {
            event.preventDefault();
            void save(login, environment, role, workloads);
          }}
        >
          <SettingsFields>
            <SettingsField label="GitHub login">
              <input
                value={login}
                required
                onChange={(event) => setLogin(event.target.value)}
              />
            </SettingsField>
            <SettingsField label="Scope">
              <select
                value={environment}
                onChange={(event) => setEnvironment(event.target.value)}
              >
                <option value="*">All environments</option>
                {environments.map((item) => (
                  <option key={item.name} value={item.name}>
                    {scopeName(item.name)}
                  </option>
                ))}
              </select>
            </SettingsField>
            <RoleChoices
              role={role}
              workloads={workloads}
              onChange={(next, selected) => {
                setRole(next);
                setWorkloads(selected);
              }}
              disabled={busy}
            />
          </SettingsFields>
          <SettingsActions>
            <SettingsButton
              type="submit"
              variant="primary"
              disabled={
                busy ||
                !login.trim() ||
                (role !== "viewer" && !workloads.length)
              }
            >
              Save assignment
            </SettingsButton>
          </SettingsActions>
        </form>
      )}
      {loading ? (
        <SettingsFeedback>Loading role assignments…</SettingsFeedback>
      ) : grants.length ? (
        <div className="permission-table-scroll">
          <table className="permission-table">
            <thead>
              <tr>
                <th>User</th>
                <th>Scope</th>
                <th>Role and workloads</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {grants.map((grant) => (
                <GrantRow
                  key={grant.id}
                  grant={grant}
                  scope={scopeName(grant.environment)}
                  busy={busy}
                  onSave={(next, selected) =>
                    void save(grant.login, grant.environment, next, selected)
                  }
                  onRemove={() => void remove(grant)}
                />
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        !requestError && (
          <SettingsFeedback>
            No stack roles have been assigned.
          </SettingsFeedback>
        )
      )}
    </SettingsPanel>
  );
}
