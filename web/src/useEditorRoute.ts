import { useEffect, useState } from "react";
import { useApplication } from "./ApplicationContext";
import {
  editorHash,
  initialEditorRoute,
  parseEditorRoute,
  sameStack,
  type EditorRoute,
} from "./navigation";

export function useEditorRoute() {
  const [route, setRoute] = useState(initialEditorRoute);
  const { navigate: applicationNavigate } = useApplication();

  function navigate(
    next: EditorRoute,
    replace = false,
    synchronize = false,
  ): Promise<boolean> {
    if (
      editorHash(route) !== editorHash(next) ||
      window.location.hash !== editorHash(next)
    ) {
      if (window.location.pathname === "/" && !synchronize)
        return applicationNavigate(`/${editorHash(next)}`, { replace });
      setRoute(next);
    }
    return Promise.resolve(true);
  }

  useEffect(() => {
    function followLocation() {
      if (window.location.pathname !== "/") return;
      const next = parseEditorRoute(window.location.hash);
      // Keep the stack identity stable when only the view or mode changes.
      setRoute(
        sameStack(route.stack, next.stack)
          ? { ...next, stack: route.stack }
          : next,
      );
    }
    window.addEventListener("platform:navigate", followLocation);
    return () => {
      window.removeEventListener("platform:navigate", followLocation);
    };
  }, [route]);

  return { route, navigate };
}
