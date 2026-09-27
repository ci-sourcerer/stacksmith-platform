import {
  cloneElement,
  useId,
  type ButtonHTMLAttributes,
  type FormHTMLAttributes,
  type ReactElement,
  type ReactNode,
} from "react";

export function SettingsPageHeader({
  title,
  description,
  context,
}: {
  title: string;
  description: ReactNode;
  context?: ReactNode;
}) {
  return (
    <div className="settings-page-heading">
      <h2>{title}</h2>
      <p>{description}</p>
      {context && <div className="settings-context">{context}</div>}
    </div>
  );
}

export function SettingsPanel({
  title,
  description,
  toolbar,
  footer,
  children,
  className = "",
  form,
  busy,
}: {
  title: ReactNode;
  description?: ReactNode;
  toolbar?: ReactNode;
  footer?: ReactNode;
  children?: ReactNode;
  className?: string;
  form?: FormHTMLAttributes<HTMLFormElement>;
  busy?: boolean;
}) {
  const id = useId();
  const content = (
    <>
      <div className="settings-panel-heading">
        <div>
          <h3 id={id}>{title}</h3>
          {description && (
            <p className="settings-panel-description">{description}</p>
          )}
        </div>
        {toolbar && <div className="settings-toolbar">{toolbar}</div>}
      </div>
      {children && <div className="settings-panel-body">{children}</div>}
      {footer && <div className="settings-panel-footer">{footer}</div>}
    </>
  );
  return form ? (
    <form
      {...form}
      className={`settings-panel ${className}`}
      aria-labelledby={id}
      aria-busy={busy}
    >
      {content}
    </form>
  ) : (
    <section
      className={`settings-panel ${className}`}
      aria-labelledby={id}
      aria-busy={busy}
    >
      {content}
    </section>
  );
}

export function SettingsFields({
  children,
  wide = false,
  compact = false,
}: {
  children: ReactNode;
  wide?: boolean;
  compact?: boolean;
}) {
  return (
    <div
      className={`settings-fields${wide ? " settings-fields-wide" : ""}${compact ? " settings-fields-compact" : ""}`}
    >
      {children}
    </div>
  );
}

export function SettingsField({
  label,
  description,
  error,
  children,
}: {
  label: string;
  description?: ReactNode;
  error?: string;
  children: ReactElement<{
    id?: string;
    "aria-describedby"?: string;
    "aria-invalid"?: boolean;
  }>;
}) {
  const generated = useId();
  const id = children.props.id ?? generated;
  return (
    <div className="settings-field">
      <label htmlFor={id}>
        {label}
        {cloneElement(children, {
          id,
          "aria-describedby":
            [
              children.props["aria-describedby"],
              description ? `${id}-help` : "",
              error ? `${id}-error` : "",
            ]
              .filter(Boolean)
              .join(" ") || undefined,
          "aria-invalid": error ? true : children.props["aria-invalid"],
        })}
      </label>
      {description && <small id={`${id}-help`}>{description}</small>}
      {error && (
        <SettingsFeedback id={`${id}-error`} tone="error">
          {error}
        </SettingsFeedback>
      )}
    </div>
  );
}

export function SettingsActions({
  children,
  feedback,
}: {
  children: ReactNode;
  feedback?: ReactNode;
}) {
  return (
    <div className="settings-action-area">
      <div className="settings-actions">{children}</div>
      {feedback && <div className="settings-action-feedback">{feedback}</div>}
    </div>
  );
}

export function SettingsFeedback({
  children,
  tone = "info",
  id,
}: {
  children: ReactNode;
  tone?: "info" | "success" | "error";
  id?: string;
}) {
  return (
    <div
      id={id}
      className={`settings-feedback settings-feedback-${tone}`}
      role={tone === "error" ? "alert" : "status"}
    >
      {children}
    </div>
  );
}

export function SettingsButton({
  variant = "secondary",
  compact = false,
  className = "",
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "text";
  compact?: boolean;
}) {
  return (
    <button
      {...props}
      type={type}
      className={`settings-button settings-button-${variant}${compact ? " settings-button-compact" : ""} ${className}`}
    />
  );
}
