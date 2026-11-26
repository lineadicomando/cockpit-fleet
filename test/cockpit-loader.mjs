// Loaded with `node --import`: maps the bare "cockpit" module (provided by the
// Cockpit shell at runtime, external in the esbuild bundle) to a test double,
// and loads what esbuild takes care of in the bundle (cockpit-resolve.mjs).
import { register, registerHooks } from "node:module";

register("./cockpit-resolve.mjs", import.meta.url);

// PatternFly's components, CommonJS for Node, require their style sheets:
// nothing for a test. require() goes through synchronous hooks only.
registerHooks({
    load(url, context, nextLoad) {
        if (/\.s?css$/.test(url)) return { format: "commonjs", source: "", shortCircuit: true };
        return nextLoad(url, context);
    },
});
