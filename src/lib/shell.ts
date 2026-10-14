// The address, in Cockpit's shell, of another page of the same host, built
// from the shell's own path ("/fleet", "/@host/fleet", "/root/fleet") and the
// path of this frame ("…/cockpit/@localhost/fleet/index.html"). It lets that
// page open in a new tab, where cockpit.jump would replace this one. Empty
// when this frame is not inside the shell.
export function shellPage(shellPath: string, framePath: string, page: string): string {
    const parts = framePath.split("/");
    const pkg = parts[parts.length - 2];
    if (!pkg) return "";
    const path = shellPath.replace(/\/$/, "");
    const end = `/${pkg}`;
    return path.endsWith(end) ? path.slice(0, -end.length) + page : "";
}
