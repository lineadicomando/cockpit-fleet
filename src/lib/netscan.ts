import type { ScanHost } from "./types.ts";


// The hosts of a scan that a text matches: in the address, a name, the MAC
// address, an open port or a host of an inventory, whatever its case.
export function filterScanHosts(hosts: ScanHost[], text: string): ScanHost[] {
    const wanted = text.trim().toLowerCase();
    if (!wanted) return hosts;
    return hosts.filter(host => [
        host.address, host.dns_name, host.netbios_name, host.workgroup, host.mac,
        ...host.ports.map(String), ...host.known.map(known => known.host), ...host.known.map(known => known.inventory),
    ].some(value => value.toLowerCase().includes(wanted)));
}

// How many addresses a subnet written as address/prefix has, or null when it
// is not one: a hint only, the backend decides.
export function subnetSize(text: string): number | null {
    const match = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/.exec(text.trim());
    if (!match) return null;
    const octets = match.slice(1, 5).map(Number);
    const prefix = Number(match[5]);
    if (octets.some(octet => octet > 255) || prefix > 32) return null;
    return 2 ** (32 - prefix);
}

// The hosts of an inventory that the chosen hosts of a scan may be: by
// address, or also by name when asked.
export function inventoryHosts(hosts: ScanHost[], inventoryId: string, byName: boolean): string[] {
    const names = hosts.flatMap(host => host.mac
        ? host.known.filter(known => known.inventory_id === inventoryId && (byName || known.by === "address")).map(known => known.host)
        : []);
    return [...new Set(names)].sort();
}

// The inventories the hosts of a scan were found in, with how many each.
export function inventoriesFound(hosts: ScanHost[]): Map<string, number> {
    const found = new Map<string, number>();
    for (const host of hosts) {
        for (const id of new Set(host.known.map(known => known.inventory_id))) {
            found.set(id, (found.get(id) ?? 0) + 1);
        }
    }
    return found;
}
