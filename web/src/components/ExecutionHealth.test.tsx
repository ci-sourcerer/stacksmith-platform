import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { ExecutionHealthDetails } from "./ExecutionHealth";

it("shows Compose worker heartbeats", () => {
  const markup = renderToStaticMarkup(
    <ExecutionHealthDetails
      status={{
        mode: "worker",
        observed_at: 1,
        pending: [],
        stale_workers: 2,
        workers: [{ id: "compose-worker", driver: "docker", heartbeat: 1 }],
      }}
    />,
  );
  expect(markup).toContain("compose-worker");
  expect(markup).toContain("Last heartbeat");
  expect(markup).not.toContain("Kubernetes launches");
});
it("shows Kubernetes Jobs and recovery without missing-worker warnings", () => {
  const markup = renderToStaticMarkup(
    <ExecutionHealthDetails
      status={{
        mode: "kubernetes-jobs",
        observed_at: 1,
        pending: [],
        recovery: { completed: 1, overdue: false, error: null },
        supervisors: [
          {
            run: "test-run",
            job: "supervisor-job",
            generation: 2,
            state: "active",
            status: "running",
            phase: "plan",
            execution: "execution-job",
            execution_state: "running",
            error: "Scheduling retry required",
          },
        ],
      }}
    />,
  );
  expect(markup).toContain("supervisor-job");
  expect(markup).toContain("execution-job");
  expect(markup).toContain("Scheduling retry required");
  expect(markup).not.toContain("heartbeat");
  expect(markup).not.toContain("active workers");
});
it("renders recovery failures in a notification box", () => {
  const markup = renderToStaticMarkup(
    <ExecutionHealthDetails
      status={{
        mode: "kubernetes-jobs",
        observed_at: 1,
        pending: [],
        recovery: {
          completed: null,
          overdue: true,
          error: "Recovery is unavailable",
        },
        supervisors: [],
      }}
    />,
  );
  expect(markup).toContain('role="alert"');
  expect(markup).toContain("Recovery is unavailable");
});
