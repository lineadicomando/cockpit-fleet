import "./dom.ts";
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { FileManager, type FileOps, type FileWords } from "../src/components/FileManager.tsx";
import type { Loaded } from "../src/lib/hooks.ts";
import type { Listed, Listing } from "../src/lib/store.ts";

afterEach(() => {
    cleanup();
    window.localStorage.clear();
});

const WORDS: FileWords = {
    root: "Store", top: "the top of the store", searchAll: "Search the store", inAll: "In the whole store",
    listLabel: "Files", empty: "The store is empty", emptyFolder: "This folder is empty", emptyBody: "Upload files.",
    missing: "This folder is gone", hint: "Drop files here", incomplete: "Not everything is listed",
};

// A tree of COUNT files in docs/, named so that they sort as numbered.
function tree(count: number): Listing<Listed> {
    const files = Array.from({ length: count }, (_, i) => ({ path: `docs/f${String(i).padStart(4, "0")}.txt`, size: 10, updated_at: 0 }));
    return { files, directories: ["docs", "docs/sub"], complete: true };
}

function draw(data: Listing<Listed>, directory = "docs") {
    const done: { removed: string[][]; moved: unknown[] } = { removed: [], moved: [] };
    const ops: FileOps = {
        move: async moves => { done.moved.push(moves); },
        copy: async () => ({}),
        remove: async paths => { done.removed.push(paths); },
        mkdir: async () => {},
        upload: async () => null,
        download: async () => null,
    };
    const list: Loaded<Listing<Listed>> = { data, error: null, loading: false, reload: () => {} };
    render(
        <FileManager<Listed> kind="test" directory={directory} go={() => {}} list={list} data={data} reload={() => {}}
            can={{ upload: true, move: true, copy: true, delete: true }} ops={ops} words={WORDS} columns={[]}
            openFile={() => {}} deleteText={paths => `Delete ${paths.length}?`} />,
    );
    return done;
}

// The names in the rows of the list, in order.
function names(): string[] {
    const table = screen.getByRole("grid", { name: "Files" });
    return within(table).getAllByRole("row").slice(1).map(row => within(row).getAllByRole("button")[0].textContent ?? "");
}

test("a folder of few entries is drawn whole, without pages", () => {
    draw(tree(30));
    assert.equal(names().length, 31);
    assert.deepEqual(names().slice(0, 2), ["sub", "f0000.txt"]);
    assert.equal(screen.queryByRole("navigation", { name: "Pages" }), null);
});

test("a large folder is drawn a page at a time", async () => {
    draw(tree(150));
    const shown = names();
    assert.equal(shown.length, 100);
    assert.equal(shown[0], "sub");
    assert.equal(shown[99], "f0098.txt");
    const next = screen.getAllByRole("button", { name: "Go to the next page" })[0];
    await act(async () => fireEvent.click(next));
    assert.deepEqual(names().slice(0, 1), ["f0099.txt"]);
    assert.equal(names().length, 51);
});

test("selecting all selects the page shown", async () => {
    draw(tree(150));
    await act(async () => fireEvent.click(screen.getByRole("checkbox", { name: "Select all" })));
    assert.equal(screen.getByText("100 selected").tagName, "STRONG");
});

test("a filter narrows the folder and goes back to its first page", async () => {
    draw(tree(150));
    await act(async () => fireEvent.click(screen.getAllByRole("button", { name: "Go to the next page" })[0]));
    await act(async () => fireEvent.change(screen.getByPlaceholderText("Filter this folder"), { target: { value: "f001" } }));
    assert.deepEqual(names(), Array.from({ length: 10 }, (_, i) => `f001${i}.txt`));
});

test("deleting a selected file asks first, then removes it", async () => {
    const done = draw(tree(3));
    await act(async () => fireEvent.click(screen.getByRole("checkbox", { name: "Select f0001.txt" })));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Delete" })));
    assert.deepEqual(done.removed, []);
    const dialog = screen.getByRole("dialog");
    assert.ok(within(dialog).getByText("Delete 1?"));
    await act(async () => fireEvent.click(within(dialog).getByRole("button", { name: "Delete" })));
    assert.deepEqual(done.removed, [["docs/f0001.txt"]]);
});
