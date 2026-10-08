import { createContext, useContext } from "react";
import type { Session } from "./types.ts";

export const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
    const session = useContext(SessionContext);
    if (!session) throw new Error("useSession outside SessionContext");
    return session;
}

// Whether to offer an action. The backend decides again when it is used.
export function useCan(): (command: string) => boolean {
    const session = useSession();
    return command => session.commands.includes(command);
}
