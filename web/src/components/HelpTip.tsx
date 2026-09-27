import { Tooltip } from "radix-ui";
import { CircleHelp } from "lucide-react";
import type { ReactElement, ReactNode } from "react";

export function ActionTooltip({
  description,
  children,
}: {
  description: ReactNode;
  children: ReactElement;
}) {
  return (
    <Tooltip.Provider delayDuration={250}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content className="help-tooltip" sideOffset={6}>
            {description}
            <Tooltip.Arrow />
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}

export function HelpTip({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <ActionTooltip description={children}>
      <button type="button" className="help-tip" aria-label={label}>
        <CircleHelp size={16} aria-hidden="true" />
      </button>
    </ActionTooltip>
  );
}
