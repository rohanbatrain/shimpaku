import { createContext, useContext } from "react";

export type Who = {
  mode: "off" | "local";
  demo: boolean;
  authenticated: boolean;
  user: string | null;
  kind: string | null;
  can_approve: boolean;
  can_propose: boolean;
};

export const SessionCtx = createContext<{ who: Who; refresh: () => void; openAssistant: (q?: string) => void }>(null as any);
export const useSession = () => useContext(SessionCtx);
