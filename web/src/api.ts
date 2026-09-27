export async function api<T>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
  headers?: Record<string, string>,
  method?: "DELETE" | "PUT",
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: method ?? (body === undefined ? "GET" : "POST"),
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-Composer-Request": "1",
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  let data: unknown;
  if (response.headers.get("content-type")?.includes("application/json")) {
    try {
      data = await response.json();
    } catch (error) {
      if (!(error instanceof SyntaxError)) throw error;
      throw new Error("The server returned an invalid response. Please retry.");
    }
  }
  if (!response.ok) {
    throw new ApiError(
      requestError(data, response.status),
      response.status,
      typeof data === "object" && data !== null && "diagnostics" in data
        ? (data.diagnostics as InspectionDiagnostics)
        : undefined,
    );
  }
  if (data === undefined)
    throw new Error("The server returned an invalid response. Please retry.");
  return data as T;
}

function requestError(data: unknown, status: number): string {
  if (typeof data === "object" && data !== null && "detail" in data) {
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail) && data.detail.length) {
      return data.detail
        .map((issue) =>
          typeof issue === "object" &&
          issue !== null &&
          typeof issue.msg === "string"
            ? issue.msg
            : "Invalid request value.",
        )
        .join(" ");
    }
  }
  return status === 401
    ? "Your session expired. Please sign in again."
    : `The request failed (${status}). Please retry.`;
}
export interface InspectionDiagnostics {
  stage: string;
  code: string;
  exit_code: number;
  stdout: string;
  stderr: string;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public diagnostics?: InspectionDiagnostics,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
