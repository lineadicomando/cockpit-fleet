import { beforeEach, test } from "node:test";
import assert from "node:assert/strict";
import { calls, setSpawnHandler } from "./mocks/cockpit.ts";
import { AccessError, FleetError, call, deleteSecret, resetAccess, setSecret } from "../src/lib/fleet.ts";

const ok = (data: unknown) => JSON.stringify({ ok: true, data });
const refused = { problem: null, exit_status: 1, message: "sudo: a password is required" };

beforeEach(() => resetAccess());

test("call sends the command in argv and the parameters on stdin", async () => {
    setSpawnHandler(() => ok({ user: "anna" }));
    const data = await call<{ user: string }>("whoami", { limit: 5 });
    assert.deepEqual(data, { user: "anna" });
    assert.deepEqual(calls[0].args, [
        "sudo", "-n", "-u", "cockpit-fleet", "/usr/libexec/cockpit-fleet/fleetctl", "whoami",
    ]);
    assert.equal(calls[0].input, '{"limit":5}');
    assert.equal(calls[0].options.superuser, undefined);
});

test("parameters never reach argv", async () => {
    setSpawnHandler(() => ok(null));
    await call("x", { name: "--evil", token: "s3cret" });
    assert.ok(!calls[0].args.join(" ").includes("evil"));
    assert.ok(!calls[0].args.join(" ").includes("s3cret"));
});

test("an error answered by the backend becomes a FleetError with its code", async () => {
    setSpawnHandler(() => JSON.stringify({ ok: false, error: { code: "forbidden", message: "requires admin" } }));
    await assert.rejects(call("audit.list"), (ex: unknown) =>
        ex instanceof FleetError && ex.code === "forbidden" && ex.message === "requires admin");
});

test("a user in no role group gets no-access", async () => {
    setSpawnHandler(() => { throw refused; });
    await assert.rejects(call("whoami"), (ex: unknown) =>
        ex instanceof AccessError && ex.problem === "no-access");
    // Tried plainly first, then once more with administrative access.
    assert.deepEqual(calls.map(c => c.options.superuser), [undefined, "try"]);
});

test("administrative access is used when sudo refuses, and remembered", async () => {
    setSpawnHandler(c => {
        if (c.options.superuser !== "try") throw refused;
        return ok({ role: "admin" });
    });
    assert.deepEqual(await call("whoami"), { role: "admin" });
    await call("selfcheck");
    assert.deepEqual(calls.map(c => c.options.superuser), [undefined, "try", "try"]);
});

test("a missing backend is told apart from a refusal", async () => {
    setSpawnHandler(() => { throw { problem: "not-found", message: "" }; });
    await assert.rejects(call("whoami"), (ex: unknown) =>
        ex instanceof AccessError && ex.problem === "not-installed");
    assert.equal(calls.length, 1);

    setSpawnHandler(() => { throw { problem: null, message: "sudo: /usr/libexec/cockpit-fleet/fleetctl: command not found" }; });
    await assert.rejects(call("whoami"), (ex: unknown) =>
        ex instanceof AccessError && ex.problem === "not-installed");
});

test("any other failure is reported with its message", async () => {
    setSpawnHandler(() => { throw { problem: null, message: "fleetctl: must run as cockpit-fleet" }; });
    await assert.rejects(call("whoami"), (ex: unknown) =>
        ex instanceof AccessError && ex.problem === "failed" && ex.message.includes("must run as"));
});

test("output that is not JSON is a failure, not a crash", async () => {
    setSpawnHandler(() => "Traceback (most recent call last)");
    await assert.rejects(call("whoami"), (ex: unknown) =>
        ex instanceof AccessError && ex.problem === "failed");
});

test("a secret goes to the root helper on stdin, its name in argv", async () => {
    setSpawnHandler(() => "");
    await setSecret("c-abc-password", "hunter2");
    assert.deepEqual(calls[0].args, ["sudo", "-n", "/usr/libexec/cockpit-fleet/fleet-secret", "set", "c-abc-password"]);
    assert.equal(calls[0].input, "hunter2");
    assert.ok(!calls[0].args.join(" ").includes("hunter2"));

    await deleteSecret("c-abc-password");
    assert.deepEqual(calls[1].args.slice(-2), ["delete", "c-abc-password"]);
});
