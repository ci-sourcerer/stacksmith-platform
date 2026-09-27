const ANSI_ESCAPE = /\u001b\[[0-?]*[ -/]*[@-~]/g;

export function importantLogLines(logs: string): string {
  return logs
    .split(/\r?\n/)
    .filter((line) =>
      /\b(error|warn(?:ing)?|fatal|critical|failed|exception|traceback)\b/i.test(
        line.replace(ANSI_ESCAPE, ""),
      ),
    )
    .join("\n");
}
