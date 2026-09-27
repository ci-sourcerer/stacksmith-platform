import { afterEach, expect, it, vi } from "vitest";
import { api } from "./api";

afterEach(() => vi.unstubAllGlobals());

it("reports API error details for the GUI", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json({ detail: "Sign in again." }, { status: 401 }),
      ),
  );
  await expect(api("/repositories")).rejects.toThrow("Sign in again.");
});

it("retains structured inspection diagnostics for the details panel", async () => {
  const diagnostics = {
    stage: "info associations",
    code: "command_failed",
    exit_code: 2,
    stdout: "partial",
    stderr: "Missing input",
  };
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { detail: "Inspection failed", diagnostics },
          { status: 422 },
        ),
      ),
  );
  await expect(api("/review", {})).rejects.toMatchObject({
    message: "Inspection failed",
    status: 422,
    diagnostics,
  });
});

it("handles HTML server errors without exposing HTML or a JSON parse error", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(new Response("<h1>Bad Gateway</h1>", { status: 502 })),
  );
  await expect(api("/repositories")).rejects.toThrow(
    "The request failed (502). Please retry.",
  );
});

it("reports request validation errors", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json({ detail: [{ msg: "Field required" }] }, { status: 422 }),
      ),
  );
  await expect(api("/review", {})).rejects.toThrow("Field required");
});

it("reports malformed JSON responses", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response("{", { headers: { "Content-Type": "application/json" } }),
      ),
  );
  await expect(api("/session")).rejects.toThrow(
    "The server returned an invalid response. Please retry.",
  );
});

it("preserves abort errors", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockRejectedValue(new DOMException("Aborted", "AbortError")),
  );
  await expect(api("/review")).rejects.toMatchObject({ name: "AbortError" });
});

it("reports administrator repository registration validation details", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      Response.json(
        {
          detail: [
            {
              loc: ["body", "config"],
              msg: "Configuration must use a full commit SHA",
            },
          ],
        },
        { status: 422 },
      ),
    ),
  );
  await expect(
    api("/v1/repositories", { repository: "acme/service" }),
  ).rejects.toThrow("Configuration must use a full commit SHA");
});

it("keeps API authentication headers when passing a run idempotency key", async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json({ id: "run-1" }));
  vi.stubGlobal("fetch", fetch);
  await api("/v1/runs", { action: "plan" }, undefined, {
    "Idempotency-Key": "attempt-1",
  });
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/runs",
    expect.objectContaining({
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-Composer-Request": "1",
        "Idempotency-Key": "attempt-1",
      },
    }),
  );
});
