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
