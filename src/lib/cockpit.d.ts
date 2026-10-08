declare module "cockpit" {
    interface CockpitLocation {
        readonly path: string[];
        readonly options: Record<string, string>;
        go(path: string | string[], options?: Record<string, string>): void;
    }

    interface CockpitSpawnOptions {
        superuser?: "require" | "try";
        err?: "message" | "out" | "ignore";
        environ?: string[];
    }

    interface SpawnProcess extends Promise<string> {
        input(data: string, stream?: boolean): SpawnProcess;
    }

    interface Cockpit {
        location: CockpitLocation;
        spawn(args: string[], options?: CockpitSpawnOptions): SpawnProcess;
        addEventListener(event: "locationchanged", handler: () => void): void;
        removeEventListener(event: "locationchanged", handler: () => void): void;
    }

    const cockpit: Cockpit;
    export default cockpit;
}
