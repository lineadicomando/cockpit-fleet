// The editor's completion for Ansible files: lib/ansible.ts decides what fits,
// this asks the backend what is installed and hands the answer to CodeMirror.

import type { Completion, CompletionContext, CompletionResult } from "@codemirror/autocomplete";
import { placeAt, resolveModule, suggest, type ModuleDoc, type Suggestion } from "./ansible.ts";
import { call } from "./fleet.ts";

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

// For tests: forget what was loaded.
export function resetModules(): void {
    modules = null;
    docs.clear();
}

export function ansibleSource(path: string) {
    return async (context: CompletionContext): Promise<CompletionResult | null> => {
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
