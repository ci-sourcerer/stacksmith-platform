import { useCallback, useState } from "react";
import { ApiError } from "./api";

export function useSettingsError(onAccessError: (message: string) => void) {
  const [error, setError] = useState("");
  const reportError = useCallback(
    (failure: unknown) => {
      if (failure instanceof ApiError && [401, 403].includes(failure.status)) {
        onAccessError(failure.message);
      } else {
        setError(failure instanceof Error ? failure.message : String(failure));
      }
    },
    [onAccessError],
  );
  const clearError = useCallback(() => setError(""), []);
  return { error, reportError, clearError };
}
