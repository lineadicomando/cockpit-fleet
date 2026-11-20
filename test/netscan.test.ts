import { test } from "node:test";
import assert from "node:assert/strict";
import { filterScanHosts, subnetSize } from "../src/lib/netscan.ts";
import type { ScanHost } from "../src/lib/types.ts";

function host(address: string, extra: Partial<ScanHost> = {}): ScanHost {
    return {
        address, mac: "", mac_source: "", dns_name: "", netbios_name: "", workgroup: "", ports: [],
        seen: ["tcp"], self: false, known: [], ...extra,
    };
}

const HOSTS = [
    host("192.168.10.7", { netbios_name: "PC101", mac: "52:54:00:aa:bb:07", ports: [22, 445] }),
    host("192.168.10.8", { dns_name: "printer.school.lan", ports: [80] }),
    host("192.168.10.9", { known: [{ inventory_id: "i1", inventory: "lab", host: "pc103", by: "address" }] }),
];

test("a filter matches addresses, names, MAC addresses, ports and hosts of inventories", () => {
    const addresses = (text: string) => filterScanHosts(HOSTS, text).map(item => item.address);
    assert.deepEqual(addresses(""), ["192.168.10.7", "192.168.10.8", "192.168.10.9"]);
    assert.deepEqual(addresses("pc101"), ["192.168.10.7"]);
    assert.deepEqual(addresses("AA:BB"), ["192.168.10.7"]);
    assert.deepEqual(addresses("printer"), ["192.168.10.8"]);
    assert.deepEqual(addresses("445"), ["192.168.10.7"]);
    assert.deepEqual(addresses("PC103"), ["192.168.10.9"]);
    assert.deepEqual(addresses("lab"), ["192.168.10.9"]);
    assert.deepEqual(addresses("10.9"), ["192.168.10.9"]);
});

test("the size of a subnet, or nothing for what is not one", () => {
    assert.equal(subnetSize("192.168.10.0/24"), 256);
    assert.equal(subnetSize(" 10.0.0.0/22 "), 1024);
    assert.equal(subnetSize("10.0.0.1/32"), 1);
    for (const text of ["", "10.0.0.0", "10.0.0.0/33", "300.0.0.0/24", "fd00::/64", "x/24"]) {
        assert.equal(subnetSize(text), null, text);
    }
});
