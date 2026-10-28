import { expect, it } from "vitest";
import { auditSummary } from "./AuditLog";

it.each([
  ["phase.claimed", { phase: 0, phase_name: "inspection" }, "inspection"],
  ["phase.started", { phase: "prepare" }, "prepare"],
  ["phase.claimed", { phase: 999 }, ""],
  ["run.succeeded", { message: "  " }, ""],
  ["run.failed", { message: "Execution failed" }, "Execution failed"],
  ["run.reconciled", { note: "Worker recovered" }, "Worker recovered"],
  [
    "secret.revoked",
    { name: "AWS_TOKEN", environment: "production", value: "hidden" },
    "AWS_TOKEN · production",
  ],
  [
    "execution_credential.bound",
    { provider: "aws", environment: "dev" },
    "aws · dev",
  ],
  ["repository.deployment_changed", { enabled: false }, "enabled: false"],
  [
    "configuration.updated",
    { scope: "platform", refs: [], mode: null },
    "scope: platform",
  ],
  ["unknown.event", { internal: { value: "hidden" } }, ""],
  ["run.succeeded", null, ""],
])("summarizes %s using meaningful context", (action, detail, expected) => {
  expect(auditSummary({ action, detail })).toBe(expected);
});
