// What the pages of the inventory of hosts compute from what the backend
// sends: a filter, the hosts by system, and the export as CSV.

import type { CmdbHost } from "./types.ts";

// Whether a host matches what is typed in the filter: every word somewhere
// in its name, inventory, groups, system, hardware or addresses.
export function matches(host: CmdbHost, text: string): boolean {
    const words = text.toLowerCase().split(/\s+/).filter(Boolean);
    if (words.length === 0) return true;
    const hardware = host.hardware;
    const haystack = [
        host.name, host.inventory, ...host.groups, host.os, host.kernel, host.arch,
        hardware.vendor, hardware.model, hardware.serial, hardware.cpu_model, hardware.virtual,
        ...(hardware.interfaces ?? []).flatMap(nic => [nic.mac, ...nic.addresses]),
    ].filter(Boolean).join(" ").toLowerCase();
    return words.every(word => haystack.includes(word));
}

// How many hosts run each system, the most common first; "" for those not known yet.
export function bySystem(hosts: CmdbHost[]): [string, number][] {
    const counts = new Map<string, number>();
    for (const host of hosts)
        counts.set(host.os, (counts.get(host.os) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

// A cell of CSV. What a node wrote could start a formula in a spreadsheet:
// such a cell is opened by an apostrophe.
export function csvCell(value: unknown): string {
    let text = value === null || value === undefined ? "" : String(value);
    if (/^[=+\-@\t\r]/.test(text)) text = "'" + text;
    return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv(rows: unknown[][]): string {
    return rows.map(row => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
}

export const CSV_COLUMNS = [
    "name", "inventory", "groups", "system", "kernel", "arch", "vendor", "model", "serial", "virtual",
    "cpu_model", "cpus", "memory_bytes", "disks", "addresses", "packages", "services", "updates",
    "security_updates", "compliant", "collected_at",
];

export function hostRows(hosts: CmdbHost[]): unknown[][] {
    return [CSV_COLUMNS, ...hosts.map(host => {
        const hardware = host.hardware;
        return [
            host.name, host.inventory, host.groups.join(" "), host.os, host.kernel, host.arch,
            hardware.vendor, hardware.model, hardware.serial, hardware.virtual, hardware.cpu_model,
            hardware.cpus, hardware.memory,
            (hardware.disks ?? []).map(disk => `${disk.name}:${disk.size}`).join(" "),
            (hardware.interfaces ?? []).flatMap(nic => nic.addresses).join(" "),
            host.packages, host.services, host.updates, host.security_updates,
            host.compliant === null ? "" : host.compliant ? "yes" : "no",
            hardware.collected_at ? new Date(hardware.collected_at * 1000).toISOString() : "",
        ];
    })];
}

// Save text as a file the browser downloads.
export function download(text: string, name: string, type: string): void {
    const link = window.document.createElement("a");
    link.href = URL.createObjectURL(new Blob([text], { type }));
    link.download = name;
    link.click();
    URL.revokeObjectURL(link.href);
}
