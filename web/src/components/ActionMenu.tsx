import { useEffect, useRef, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";

export function ActionMenu({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  const menu = useRef<HTMLDetailsElement>(null);

  useEffect(() => {
    function dismiss(event: PointerEvent) {
      if (event.target instanceof Node && !menu.current?.contains(event.target))
        menu.current?.removeAttribute("open");
    }

    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, []);

  return (
    <details
      className="action-menu"
      ref={menu}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget))
          event.currentTarget.open = false;
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.currentTarget.open = false;
          event.currentTarget.querySelector("summary")?.focus();
        }
      }}
    >
      <summary className="button">
        {label}
        <ChevronDown size={14} />
      </summary>
      <div
        className="action-menu-content"
        onClick={(event) => {
          if (
            event.target instanceof Element &&
            event.target.closest("button")
          ) {
            menu.current?.removeAttribute("open");
            menu.current?.querySelector("summary")?.focus();
          }
        }}
      >
        {children}
      </div>
    </details>
  );
}
