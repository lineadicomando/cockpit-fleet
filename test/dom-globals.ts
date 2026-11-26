// A document for React to draw in, before anything that reads it is loaded:
// test/dom.ts imports this first.
import { GlobalRegistrator } from "@happy-dom/global-registrator";

GlobalRegistrator.register({ url: "https://localhost:9090/cockpit/@localhost/fleet/index.html" });
// React's act() warns unless it is told the environment expects it.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
