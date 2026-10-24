import { createContext, useContext } from "react";
import { api } from "./api";

export type Who = {
  mode: "off" | "local";
  demo: boolean;
  replay: { taken_at: number; version?: string } | null;
  authenticated: boolean;
  user: string | null;
  kind: string | null;
  can_approve: boolean;
  can_propose: boolean;
  role?: string | null;
  lease_id?: string | null;
  public_view?: boolean;
  totp_required?: boolean;
  operator_idle_min?: number | null;
};

/** Sign out and leave nothing behind for the next person at this browser: drop the server
 * session (or every operator session), clear the storage keys this app could use, then a full
 * load of the read-only view so no React state or cached response survives. */
export async function signOutAndClear(everywhere = false): Promise<void> {
  try {
    await api(everywhere ? "/api/logout-all" : "/api/logout", { method: "POST" });
  } catch {
    if (everywhere) await api("/api/logout", { method: "POST" }).catch(() => {});
  }
  try { sessionStorage.clear(); } catch {}
  try {
    for (const k of Object.keys(localStorage)) if (k.startsWith("shimpaku")) localStorage.removeItem(k);
  } catch {}
  location.replace("/");
}

export const SessionCtx = createContext<{ who: Who; refresh: () => void; openAssistant: (q?: string) => void }>(null as any);
export const useSession = () => useContext(SessionCtx);
