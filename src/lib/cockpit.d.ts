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
        stream(handler: (data: string) => void): SpawnProcess;
        close(problem?: string): void;
    }

    // A channel of the bridge. Only what the store needs: a process spawned
    // with binary input, which acknowledges what it was sent.
    interface ChannelOptions {
        payload: "stream";
        spawn: string[];
        binary: true;
        "send-acks"?: "bytes";
        superuser?: "require" | "try";
        err?: "message" | "out" | "ignore";
        environ?: string[];
    }

    interface ControlMessage {
        command: string;
        bytes?: number;
        problem?: string;
        message?: string;
        "exit-status"?: number;
    }

    interface Channel {
        send(data: Uint8Array | ArrayBuffer): void;
        control(options: { command: string }): void;
        close(problem?: string): void;
        addEventListener(event: "message", handler: (event: unknown, data: Uint8Array) => void): void;
        addEventListener(event: "control" | "close", handler: (event: unknown, options: ControlMessage) => void): void;
    }

    interface Transport {
        csrf_token: string;
        // The URL of a path of Cockpit's web server, as this page reaches it.
        uri(suffix?: string): string;
    }

    interface Cockpit {
        location: CockpitLocation;
        channel(options: ChannelOptions): Channel;
        transport: Transport;
        spawn(args: string[], options?: CockpitSpawnOptions): SpawnProcess;
        // Shows another page of Cockpit, like its terminal.
        jump(path: string, host?: string): void;
        addEventListener(event: "locationchanged", handler: () => void): void;
        removeEventListener(event: "locationchanged", handler: () => void): void;
    }

    const cockpit: Cockpit;
    export default cockpit;
}
