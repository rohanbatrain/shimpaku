import { useEffect } from "react";
import { useApi, type Loadable } from "../api";

/** GET a resource and refresh it every `ms` while the tab is visible (keeps the last data on error). */
export function usePoll<T>(path: string | null, ms = 10000): Loadable<T> {
  const q = useApi<T>(path);
  const { reload } = q;
  useEffect(() => {
    if (!path) return;
    const id = window.setInterval(() => document.visibilityState === "visible" && reload(), ms);
    return () => window.clearInterval(id);
  }, [path, ms, reload]);
  return q;
}
