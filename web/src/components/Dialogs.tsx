import { AlertDialog, Dialog } from "radix-ui";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { X } from "lucide-react";
import "./Dialogs.css";

interface Confirmation {
  title: string;
  description: ReactNode;
  actionLabel: string;
  destructive?: boolean;
}

interface Confirmations {
  confirm: (options: Confirmation) => Promise<boolean>;
  cancel: () => void;
}

const ConfirmationContext = createContext<Confirmations | null>(null);

function returnFocusTarget(): HTMLElement | null {
  if (!(document.activeElement instanceof HTMLElement)) return null;
  return (
    document.activeElement.closest("details")?.querySelector("summary") ??
    document.activeElement
  );
}

function restoreFocus(target: HTMLElement | null) {
  if (
    target?.isConnected &&
    target !== document.body &&
    !target.closest("[hidden]")
  )
    target.focus();
  else
    document
      .querySelector<HTMLElement>("[data-dialog-focus-fallback]")
      ?.focus();
}

export function Modal({
  title,
  description,
  children,
  onCancel,
}: {
  title: string;
  description: string;
  children: ReactNode;
  onCancel: () => void;
}) {
  const [returnTarget] = useState(returnFocusTarget);
  return (
    <Dialog.Root
      open
      onOpenChange={(open) => {
        if (!open) onCancel();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content
          className="app-dialog"
          data-keyboard-owner="true"
          onOpenAutoFocus={(event) => {
            if (!(event.target instanceof HTMLElement)) return;
            const initial = event.target.querySelector<HTMLElement>(
              "[data-dialog-initial-focus]",
            );
            if (initial) {
              event.preventDefault();
              initial.focus();
            }
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            restoreFocus(returnTarget);
          }}
        >
          <div className="dialog-heading">
            <Dialog.Title>{title}</Dialog.Title>
            <Dialog.Close asChild>
              <button className="icon-button" aria-label="Close dialog">
                <X size={18} />
              </button>
            </Dialog.Close>
          </div>
          <Dialog.Description>{description}</Dialog.Description>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function ConfirmationProvider({ children }: { children: ReactNode }) {
  const [options, setOptions] = useState<Confirmation>();
  const pending = useRef<{
    resolve: (accepted: boolean) => void;
    target: HTMLElement | null;
  } | null>(null);
  const lastTarget = useRef<HTMLElement | null>(null);
  const finish = useCallback((accepted: boolean) => {
    pending.current?.resolve(accepted);
    pending.current = null;
    setOptions(undefined);
  }, []);
  const cancel = useCallback(() => finish(false), [finish]);
  const confirm = useCallback(
    (next: Confirmation) =>
      new Promise<boolean>((resolve) => {
        pending.current?.resolve(false);
        pending.current = { resolve, target: returnFocusTarget() };
        lastTarget.current = pending.current.target;
        setOptions(next);
      }),
    [],
  );
  useEffect(
    () => () => {
      pending.current?.resolve(false);
      pending.current = null;
    },
    [],
  );
  return (
    <ConfirmationContext.Provider value={{ confirm, cancel }}>
      {children}
      <AlertDialog.Root
        open={Boolean(options)}
        onOpenChange={(open) => {
          if (!open) finish(false);
        }}
      >
        <AlertDialog.Portal>
          <AlertDialog.Overlay className="dialog-overlay" />
          <AlertDialog.Content
            className="app-dialog"
            data-keyboard-owner="true"
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              restoreFocus(lastTarget.current);
            }}
          >
            <AlertDialog.Title>{options?.title}</AlertDialog.Title>
            <AlertDialog.Description asChild>
              <div className="dialog-description">{options?.description}</div>
            </AlertDialog.Description>
            <div className="dialog-footer">
              <AlertDialog.Cancel asChild>
                <button className="button" onClick={() => finish(false)}>
                  Cancel
                </button>
              </AlertDialog.Cancel>
              <AlertDialog.Action asChild>
                <button
                  className={`button ${options?.destructive ? "danger" : "primary"}`}
                  onClick={() => finish(true)}
                >
                  {options?.actionLabel}
                </button>
              </AlertDialog.Action>
            </div>
          </AlertDialog.Content>
        </AlertDialog.Portal>
      </AlertDialog.Root>
    </ConfirmationContext.Provider>
  );
}

export function useConfirmation(): Confirmations {
  const context = useContext(ConfirmationContext);
  if (!context) throw new Error("Confirmation provider is missing.");
  return context;
}
