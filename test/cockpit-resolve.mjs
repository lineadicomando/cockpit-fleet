export async function resolve(specifier, context, nextResolve) {
    if (specifier === "cockpit") {
        return { url: new URL("./mocks/cockpit.ts", import.meta.url).href, shortCircuit: true };
    }
    return nextResolve(specifier, context);
}
