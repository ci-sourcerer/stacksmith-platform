import { LoaderCircle } from "lucide-react";

export function ManagedConnectionLoading({
  hasPrevious,
}: {
  hasPrevious: boolean;
}) {
  return (
    <div
      className={`managed-connection-loading ${hasPrevious ? "is-refreshing" : "is-initial"}`}
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <LoaderCircle size={20} className="spin" aria-hidden="true" />
      <div>
        <strong>
          {hasPrevious
            ? "Updating managed connections"
            : "Loading managed connections"}
        </strong>
        <p>
          {hasPrevious
            ? "Previous connections stay visible while we check your changes."
            : "Checking the stack’s managed rules. Connections will appear when ready."}
        </p>
      </div>
    </div>
  );
}
