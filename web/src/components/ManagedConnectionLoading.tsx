import { LoaderCircle } from "lucide-react";

export function ManagedConnectionLoading() {
  return (
    <div
      className="managed-connection-loading"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <LoaderCircle size={20} className="spin" aria-hidden="true" />
      <div>
        <strong>Loading managed connections</strong>
        <p>
          Checking the stack’s managed rules. Connections will appear when
          ready.
        </p>
      </div>
    </div>
  );
}
