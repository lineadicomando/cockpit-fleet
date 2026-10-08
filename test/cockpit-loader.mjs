// Loaded with `node --import`: maps the bare "cockpit" module (provided by the
// Cockpit shell at runtime, external in the esbuild bundle) to a test double.
import { register } from "node:module";

register("./cockpit-resolve.mjs", import.meta.url);
