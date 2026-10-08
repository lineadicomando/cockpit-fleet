import * as esbuild from "esbuild";
import { sassPlugin } from "esbuild-sass-plugin";
import { cpSync, mkdirSync } from "fs";

const isWatch = process.argv.includes("--watch");
const outdir = "dist";

const cockpitExternalPlugin = {
    name: "cockpit-external",
    setup(build) {
        build.onResolve({ filter: /^cockpit$/ }, () => ({
            path: "cockpit",
            namespace: "cockpit-external",
        }));
        build.onLoad({ filter: /.*/, namespace: "cockpit-external" }, () => ({
            contents: "export default window.cockpit;",
            loader: "js",
        }));
        build.onResolve({ filter: /^cockpit-dark-theme$/ }, () => ({
            path: "cockpit-dark-theme",
            namespace: "cockpit-noop",
        }));
        build.onLoad({ filter: /.*/, namespace: "cockpit-noop" }, () => ({
            contents: "",
            loader: "js",
        }));
    },
};

const ctx = await esbuild.context({
    entryPoints: ["src/index.tsx"],
    bundle: true,
    outdir,
    format: "esm",
    platform: "browser",
    target: ["es2020"],
    plugins: [cockpitExternalPlugin, sassPlugin()],
    loader: {
        ".css": "css",
        ".woff2": "file",
        ".woff": "file",
        ".ttf": "file",
        ".eot": "file",
        ".svg": "file",
        ".png": "file",
    },
    minify: !isWatch,
    sourcemap: isWatch ? "inline" : false,
});

mkdirSync(outdir, { recursive: true });
cpSync("src/index.html", `${outdir}/index.html`);
cpSync("src/manifest.json", `${outdir}/manifest.json`);

if (isWatch) {
    await ctx.watch();
    console.log("Watching for changes...");
} else {
    await ctx.rebuild();
    await ctx.dispose();
    console.log("Build complete.");
}
