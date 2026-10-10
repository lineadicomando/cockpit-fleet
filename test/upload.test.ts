import { test } from "node:test";
import assert from "node:assert/strict";
import { calls, setSpawnHandler } from "./mocks/cockpit.ts";
import { base64Pieces, uploadArchive, uploadPieces } from "../src/lib/upload.ts";

function bytes(length: number): Uint8Array {
    return Uint8Array.from({ length }, (_value, index) => (index * 31 + 7) % 256);
}

test("the pieces of base64 join into the encoding of the whole", () => {
    for (const length of [0, 1, 2, 3, 4, 299, 300, 301, 20000]) {
        const data = bytes(length);
        const pieces = base64Pieces(data, 300);
        assert.equal(pieces.length, Math.ceil(length / 300));
        assert.equal(pieces.join(""), Buffer.from(data).toString("base64"));
    }
    // The size really used: more than one call to String.fromCharCode can take.
    const large = bytes(500000);
    assert.equal(base64Pieces(large).join(""), Buffer.from(large).toString("base64"));
});

test("the pieces are one JSON object, whatever the file is called", () => {
    const data = bytes(1000);
    const name = 'my "site"\\v1\n.tar.gz';
    const params = JSON.parse(uploadPieces("p1", name, data, 300).join(""));
    assert.deepEqual(Object.keys(params), ["id", "filename", "data"]);
    assert.equal(params.filename, name);
    assert.deepEqual(new Uint8Array(Buffer.from(params.data, "base64")), data);
});

test("an archive goes to project.upload on stdin, never in argv", async () => {
    setSpawnHandler(() => JSON.stringify({ ok: true, data: { run_id: "r1", filename: "a.zip", size: 5, sha256: "00" } }));
    const file = new File([new Uint8Array([80, 75, 3, 4, 0])], "a.zip");
    assert.equal((await uploadArchive("p1", file)).run_id, "r1");
    assert.equal(calls[0].args[calls[0].args.length - 1], "project.upload");
    assert.deepEqual(JSON.parse(calls[0].input ?? ""), { id: "p1", filename: "a.zip", data: "UEsDBAA=" });
});
