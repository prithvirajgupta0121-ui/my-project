import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

class RO { observe() {} unobserve() {} disconnect() {} }
(globalThis as any).ResizeObserver = RO;
(window as any).matchMedia = (q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} });
// jsdom has no canvas: provide a no-op 2D context so Leaflet's canvas renderer can mount.
const ctxStub: any = new Proxy({}, {
  get: (_t, k) => (k === "canvas" ? document.createElement("canvas") : k === "measureText" ? () => ({ width: 0 }) : () => undefined),
  set: () => true,
});
HTMLCanvasElement.prototype.getContext = (() => ctxStub) as any;
afterEach(() => { cleanup(); vi.restoreAllMocks(); window.location.hash = ""; });
