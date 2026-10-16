import { test } from "node:test";
import assert from "node:assert/strict";
import { bySystem, csvCell, hostRows, matches, toCsv } from "../src/lib/cmdb.ts";
import type { CmdbHost } from "../src/lib/types.ts";

function host(name: string, os: string, extra: Partial<CmdbHost> = {}): CmdbHost {
    return {
        id: name, name, inventory_id: "i1", inventory: "lab", groups: ["web"], platform: "linux", os,
        os_id: "", os_version: "", kernel: "6.12", arch: "x86_64", reachable: true,
        hardware: {
            vendor: "QEMU", model: "Standard PC", cpu_model: "AMD EPYC", cpus: 2, memory: 2 ** 31,
            disks: [{ name: "vda", size: 100, model: "" }],
            interfaces: [{ name: "eth0", mac: "52:54:00:aa:bb:cc", addresses: ["10.0.0.7/24"] }],
            collected_at: 1_700_000_000,
        },
        packages: 400, services: 80, updates: 3, security_updates: 1, compliant: null, ...extra,
    };
}

test("the filter looks at every word in what a host is", () => {
    const web = host("web01", "Debian GNU/Linux 13 (trixie)");
    assert.equal(matches(web, ""), true);
    assert.equal(matches(web, "debian epyc"), true);
    assert.equal(matches(web, "10.0.0.7"), true);
    assert.equal(matches(web, "52:54:00:AA"), true);
    assert.equal(matches(web, "debian windows"), false);
    assert.equal(matches(host("pc01", "", { hardware: {} }), "pc"), true);
});

test("hosts are counted by system, the most common first", () => {
    const hosts = [host("a", "Debian 13"), host("b", "Windows 11"), host("c", "Debian 13"), host("d", "")];
    assert.deepEqual(bySystem(hosts), [["Debian 13", 2], ["", 1], ["Windows 11", 1]]);
});

test("a cell of CSV is quoted when it must be and never starts a formula", () => {
    assert.equal(csvCell("plain"), "plain");
    assert.equal(csvCell('say "hi", twice'), '"say ""hi"", twice"');
    assert.equal(csvCell("=HYPERLINK(\"x\")"), "\"'=HYPERLINK(\"\"x\"\")\"");
    assert.equal(csvCell("-1"), "'-1");
    assert.equal(csvCell(null), "");
    assert.equal(csvCell(4), "4");
    assert.equal(toCsv([["a", "b"], [1, "x\ny"]]), 'a,b\r\n1,"x\ny"\r\n');
});

test("the export has a row per host under a heading", () => {
    const rows = hostRows([host("web01", "Debian 13", { compliant: false })]);
    assert.equal(rows.length, 2);
    const row = Object.fromEntries((rows[0] as string[]).map((column, index) => [column, rows[1][index]]));
    assert.equal(row.name, "web01");
    assert.equal(row.disks, "vda:100");
    assert.equal(row.addresses, "10.0.0.7/24");
    assert.equal(row.compliant, "no");
    assert.equal(row.collected_at, "2023-11-14T22:13:20.000Z");
});
