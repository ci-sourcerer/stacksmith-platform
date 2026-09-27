import { CircleAlert, X } from "lucide-react";
import { createContext, useContext, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { rememberReturnRoute } from "../navigation";

const NotificationTarget = createContext<HTMLDivElement | null>(null);

export function NotificationProvider({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<HTMLDivElement | null>(null);
  return (
    <NotificationTarget.Provider value={target}>
      {children}
      <div
        className="notifications"
        aria-label="Notifications"
        ref={setTarget}
      />
    </NotificationTarget.Provider>
  );
}

export function ErrorBanner({
  message,
  signIn,
  onDismiss,
}: {
  message: string;
  signIn: boolean;
  onDismiss: () => void;
}) {
  const target = useContext(NotificationTarget);
  const notification = (
    <div
      className="error-banner notification-box"
      role="alert"
      aria-live="assertive"
    >
      <CircleAlert size={17} />
      <span>{message}</span>
      {signIn &&
        /sign.in|session|authenticat|access.*denied/i.test(message) && (
          <a
            className="text-button"
            href="/api/auth/github"
            onClick={rememberReturnRoute}
          >
            Sign in again
          </a>
        )}
      <button className="text-button" onClick={() => location.reload()}>
        Reload
      </button>
      <button
        className="icon-button"
        aria-label="Dismiss error"
        onClick={onDismiss}
      >
        <X size={16} />
      </button>
    </div>
  );
  return target ? createPortal(notification, target) : notification;
}
