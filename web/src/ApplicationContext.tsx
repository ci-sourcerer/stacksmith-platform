import { createContext, useContext } from "react";
import type { Session } from "./types";
import type { WorkspaceSelection } from "./workspaceSelection";

export interface ApplicationState {
  session: Session;
  workspace: WorkspaceSelection;
  location: URL;
  navigate: (href: string, options?: { replace?: boolean }) => Promise<boolean>;
  applyWorkspace: (workspace: WorkspaceSelection) => Promise<boolean>;
  registerGuard: (guard: () => Promise<boolean>) => () => void;
  targetError: string;
}

export const ApplicationContext = createContext<ApplicationState | null>(null);

export function useApplication(): ApplicationState {
  const application = useContext(ApplicationContext);
  if (!application) throw new Error("Application shell is missing.");
  return application;
}
