import "./dom.ts";
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { Table, Tbody, Td, Tr } from "@patternfly/react-table";
import { GroupedRows } from "../src/components/ListView.tsx";
import type { Group } from "../src/lib/listview.ts";

afterEach(() => cleanup());

// Rows are highlighted under the pointer (app.scss); the head of a group is a
// title, not an item, and carries the class that keeps it out.
test("the head of a group of rows is kept out of the highlight of rows", () => {
    const groups = [{ key: "lab", items: ["ping"] }] as unknown as Group<string>[];
    render(
        <Table aria-label="Templates">
            <GroupedRows groups={groups} grouped width={2} isOpen={() => true} onToggle={() => {}}
                title={() => "lab"} count={() => "1 template"}
                row={item => <Tr key={item}><Td /><Td>{item}</Td></Tr>} />
        </Table>,
    );
    const head = screen.getByText("lab").closest("tr");
    const item = screen.getByText("ping").closest("tr");
    assert.ok(head?.classList.contains("fleet-group-row"));
    assert.ok(!item?.classList.contains("fleet-group-row"));
});

test("rows without groups have no head", () => {
    const groups = [{ key: "", items: ["ping"] }] as unknown as Group<string>[];
    const { container } = render(
        <Table aria-label="Templates">
            <GroupedRows groups={groups} grouped={false} width={1} isOpen={() => true} onToggle={() => {}}
                title={() => ""} count={() => ""} row={item => <Tr key={item}><Td>{item}</Td></Tr>} />
        </Table>,
    );
    assert.equal(container.querySelectorAll(".fleet-group-row").length, 0);
});
