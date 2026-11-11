// The editor's completion for Ansible files: lib/ansible.ts decides what fits,
// this asks the backend what is installed and hands the answer to CodeMirror.

import { startCompletion, type Completion, type CompletionContext, type CompletionResult } from "@codemirror/autocomplete";
import { placeAt, resolveModule, suggest, type ModuleDoc, type Suggestion } from "./ansible.ts";
import { formatBytes } from "./checks.ts";
import { call } from "./fleet.ts";
import { storeChoices, storePathAt, type StoreList } from "./store.ts";

// Asked once per session: the list is large and changes with the packages of
// the controller, not while a file is edited.
let modules: Promise<Record<string, string>> | null = null;
const docs = new Map<string, Promise<ModuleDoc | null>>();

export function loadModules(): Promise<Record<string, string>> {
    modules ??= call<{ modules: Record<string, string> }>("module.list").then(
        data => data.modules,
        // Without the list the keywords are still suggested; asked again later.
        () => { modules = null; return {}; },
    );
    return modules;
}

function loadDoc(name: string): Promise<ModuleDoc | null> {
    let doc = docs.get(name);
    if (!doc) {
        doc = call<ModuleDoc>("module.doc", { name }).catch(() => null);
        docs.set(name, doc);
    }
    return doc;
}

const TYPES: Record<Suggestion["kind"], string> = { keyword: "keyword", module: "function", option: "property", value: "constant" };

function completion(item: Suggestion): Completion {
    return {
        label: item.label, type: TYPES[item.kind], detail: item.detail, info: item.info || undefined, apply: item.apply,
        boost: item.first ? 10 : item.label.startsWith("ansible.builtin.") ? 5 : 0,
    };
}

// The store, for paths written after {{ fleet_store }}/: asked again after a
// while, since files come and go while a playbook is written.
let storeList: { at: number; list: Promise<StoreList | null> } | null = null;
const STORE_AGE = 30000;

function loadStore(): Promise<StoreList | null> {
    if (!storeList || Date.now() - storeList.at > STORE_AGE) {
        storeList = { at: Date.now(), list: call<StoreList>("store.list").catch(() => null) };
    }
    return storeList.list;
}

// Files and folders of the store, a level at a time, after {{ fleet_store }}/.
async function storeCompletion(context: CompletionContext): Promise<CompletionResult | null | undefined> {
    const line = context.state.doc.lineAt(context.pos);
    const place = storePathAt(line.text.slice(0, context.pos - line.from));
    if (!place) return undefined;
    const list = await loadStore();
    if (!list || context.aborted) return null;
    const options = storeChoices(list, place.folder, place.partial).map((choice): Completion => ({
        label: choice.label, type: choice.folder ? "namespace" : "text", boost: choice.folder ? 1 : 0,
        detail: choice.size === undefined ? undefined : formatBytes(choice.size),
        // A folder chosen goes on to what is in it.
        apply: choice.folder
            ? (view, _completion, from, to) => {
                view.dispatch({ changes: { from, to, insert: choice.label }, selection: { anchor: from + choice.label.length } });
                startCompletion(view);
            }
            : undefined,
    }));
    if (options.length === 0) return null;
    return { from: context.pos - place.partial.length, options, validFor: /^[^\s"'{}/]*$/ };
}

// For tests: forget what was loaded.
export function resetModules(): void {
    modules = null;
    storeList = null;
    docs.clear();
}

export function ansibleSource(path: string) {
    return async (context: CompletionContext): Promise<CompletionResult | null> => {
        const inStore = await storeCompletion(context);
        if (inStore !== undefined) return inStore;
        const place = placeAt(context.state.sliceDoc(0, context.pos), path);
        // Nothing typed: suggestions come when asked for, not at every new line.
        if (!place || (place.kind === "key" && !place.prefix && !context.explicit)) return null;
        const known = place.scope === "task" || place.scope === "args" ? await loadModules() : {};
        const name = place.scope === "args" ? resolveModule(place.module, known) : null;
        const doc = name ? await loadDoc(name) : null;
        if (context.aborted) return null;
        const options = suggest(place, known, doc).map(completion);
        if (options.length === 0) return null;
        return { from: context.pos - place.prefix.length, options, validFor: /^[A-Za-z0-9_.]*$/ };
    };
}
