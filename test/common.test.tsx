import "./dom.ts";
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { TextInput } from "@patternfly/react-core";
import { FormModal, ListState, StartedRuns, announceRun } from "../src/components/common.tsx";

afterEach(() => cleanup());

function form(guardClose: boolean, closed: { count: number }) {
    return (
        <FormModal title="New thing" submitLabel="Save" onSubmit={async () => {}} onClose={() => { closed.count += 1; }} guardClose={guardClose}>
            <TextInput aria-label="Name" value="" onChange={() => {}} />
        </FormModal>
    );
}

// A long form typed into asks before it is closed without saving.
test("a guarded form asks before closing once typed into", () => {
    const closed = { count: 0 };
    render(form(true, closed));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    assert.equal(closed.count, 1, "nothing typed: it closes at once");
    cleanup();

    render(form(true, closed));
    fireEvent.input(screen.getByRole("textbox", { name: "Name" }), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    assert.equal(closed.count, 1, "typed into: it does not close yet");
    assert.equal(screen.queryAllByText("Discard the changes?").length, 1);
    fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    assert.equal(closed.count, 1);
    assert.equal(screen.queryAllByText("Discard the changes?").length, 0);
    fireEvent.click(screen.getAllByRole("button", { name: "Close" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    assert.equal(closed.count, 2);
});

test("a form not guarded closes even when typed into", () => {
    const closed = { count: 0 };
    render(form(false, closed));
    fireEvent.input(screen.getByRole("textbox", { name: "Name" }), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    assert.equal(closed.count, 1);
});

// A refresh that fails keeps the list in sight, said to be maybe out of date.
test("a list that cannot be refreshed stays, with a warning and a way to try again", () => {
    let reloads = 0;
    const loaded = { data: ["a"], error: new Error("timed out"), loading: false, reload: () => { reloads += 1; } };
    render(<ListState loaded={loaded} isEmpty={data => data.length === 0} emptyTitle="None" emptyBody="">{data => <p>{data.join()}</p>}</ListState>);
    assert.equal(screen.queryAllByText("a").length, 1);
    assert.equal(screen.queryAllByText("Could not refresh: what is shown may be out of date.").length, 1);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    assert.equal(reloads, 1);
    cleanup();

    render(<ListState loaded={{ ...loaded, data: null }} isEmpty={() => false} emptyTitle="None" emptyBody="">{() => <p>a</p>}</ListState>);
    assert.equal(screen.queryAllByText("timed out").length, 1);
    assert.equal(screen.queryAllByText("a").length, 0);
});

test("a run that waits for an approval is not announced as started", () => {
    render(<StartedRuns />);
    act(() => {
        announceRun("r1", "update", "pending_approval");
        announceRun("r2", "backup", "queued");
    });
    assert.equal(screen.queryAllByText("Waiting for an approval: update").length, 1);
    assert.equal(screen.queryAllByText("Started: backup").length, 1);
});
