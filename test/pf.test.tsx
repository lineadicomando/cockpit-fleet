import "./dom.ts";
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import i18n from "../src/i18n.ts";
import { Pagination, SearchInput } from "../src/components/pf.tsx";

afterEach(async () => {
    cleanup();
    await i18n.changeLanguage("en");
});

// PatternFly's own words are English: the wrappers give them in the page's language.
test("the wrappers of PatternFly speak the language of the page", async () => {
    await act(async () => { await i18n.changeLanguage("it"); });
    render(
        <>
            <SearchInput value="" onChange={() => {}} />
            <Pagination itemCount={300} page={2} perPage={100} onSetPage={() => {}} />
        </>,
    );
    assert.ok(screen.getByRole("textbox", { name: "Cerca" }));
    assert.ok(screen.getByRole("navigation", { name: "Pagine" }));
    assert.ok(screen.getByRole("button", { name: "Vai alla pagina successiva" }));
    assert.equal(screen.queryByRole("button", { name: /Go to/ }), null);
});
