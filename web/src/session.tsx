import { createContext, useContext } from "react";

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
};

export const SessionCtx = createContext<{ who: Who; refresh: () => void; openAssistant: (q?: string) => void }>(null as any);
export const useSession = () => useContext(SessionCtx);
