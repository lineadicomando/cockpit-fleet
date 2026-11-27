import "./dom.ts";
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, render } from "@testing-library/react";
import { usePolling } from "../src/lib/hooks.ts";

let hidden = false;
Object.defineProperty(document, "visibilityState", { configurable: true, get: () => hidden ? "hidden" : "visible" });

afterEach(() => {
    cleanup();
    hidden = false;
});

const wait = (ms: number) => act(() => new Promise<void>(resolve => setTimeout(resolve, ms)));

async function show(state: boolean) {
    hidden = !state;
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
}

function Poller({ ms, active, count }: { ms: number; active: boolean; count: { n: number } }) {
    usePolling(() => { count.n++; }, ms, active);
    return null;
}

test("a page polls while its tab is shown, not while it is hidden", async () => {
    const count = { n: 0 };
    render(<Poller ms={30} active count={count} />);
    await wait(100);
    assert.ok(count.n >= 2, `polled ${count.n} times`);

    await show(false);
    const before = count.n;
    await wait(100);
    assert.equal(count.n, before, "nothing asked while hidden");

    await show(true);
    await wait(5);
    assert.equal(count.n, before + 1, "asked at once when shown again");
});

test("a poll that is not active asks nothing, shown or not", async () => {
    const count = { n: 0 };
    render(<Poller ms={10} active={false} count={count} />);
    await show(false);
    await show(true);
    await wait(50);
    assert.equal(count.n, 0);
});

test("a long interval waits its whole time before the first call", async () => {
    const count = { n: 0 };
    render(<Poller ms={200} active count={count} />);
    await wait(50);
    assert.equal(count.n, 0);
});
