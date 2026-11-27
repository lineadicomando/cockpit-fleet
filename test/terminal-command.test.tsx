import "./dom.ts";
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { jumps } from "./mocks/cockpit.ts";
import { TerminalCommand } from "../src/components/common.tsx";

// The page as a frame of Cockpit's shell, which is at /fleet.
Object.defineProperty(window, "top", { configurable: true, get: () => ({ location: { pathname: "/fleet" } }) });

afterEach(() => {
    cleanup();
    jumps.length = 0;
});

test("a command's terminal opens in a new tab, so that leaving loses nothing", async () => {
    await act(async () => { render(<TerminalCommand text="fleet launch deploy" />); });
    const open = screen.getByRole("link", { name: /Open the terminal/ });
    assert.equal(open.getAttribute("href"), "/system/terminal");
    assert.equal(open.getAttribute("target"), "_blank");
});

test("while the chat is set up, the terminal opens in the same tab", async () => {
    await act(async () => { render(<TerminalCommand text="claude /login" here />); });
    assert.equal(screen.queryByRole("link", { name: /Open the terminal/ }), null);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Open the terminal" })));
    assert.deepEqual(jumps, ["/system/terminal"]);
});
