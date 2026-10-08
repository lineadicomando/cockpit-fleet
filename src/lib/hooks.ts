import { useCallback, useEffect, useRef, useState } from "react";
import cockpit from "cockpit";

export function useCockpitLocation() {
    const [location, setLocation] = useState(cockpit.location);
    useEffect(() => {
        function sync() {
            setLocation(cockpit.location);
        }
        cockpit.addEventListener("locationchanged", sync);
        return () => cockpit.removeEventListener("locationchanged", sync);
    }, []);
    return location;
}

export interface Loaded<T> {
    data: T | null;
    error: Error | null;
    loading: boolean;
    reload: () => void;
}

// Loads once on mount and again on reload(). A slow answer to an earlier
// request never overwrites the result of a later one.
export function useLoad<T>(load: () => Promise<T>): Loaded<T> {
    const [data, setData] = useState<T | null>(null);
    const [error, setError] = useState<Error | null>(null);
    const [loading, setLoading] = useState(true);
    const latest = useRef(0);
    const loadRef = useRef(load);
    loadRef.current = load;

    const reload = useCallback(() => {
        const request = ++latest.current;
        setLoading(true);
        loadRef.current().then(
            result => {
                if (request !== latest.current) return;
                setData(result);
                setError(null);
                setLoading(false);
            },
            ex => {
                if (request !== latest.current) return;
                setError(ex instanceof Error ? ex : new Error(String(ex)));
                setLoading(false);
            },
        );
    }, []);

    useEffect(reload, [reload]);
    return { data, error, loading, reload };
}
