// Entry point: load the exported run data, then mount each section.

import { bind, el, prefersReducedMotion, setText, tooltip } from "./dom.js";
import { mountExplorer } from "./explorer.js";
import { mountColophon, mountPassK, mountProbe, mountSelfEval } from "./panels.js";
import { mountSpecimen, mountVerdict } from "./verdict.js";

const DATA_URL = "data/showcase.json";

function validate(data) {
  const required = ["run", "gates", "probe", "audit", "self_eval", "stats"];
  const missing = required.filter((key) => !(key in data));
  if (missing.length) throw new Error(`showcase.json is missing: ${missing.join(", ")}`);
  if (!Array.isArray(data.gates) || data.gates.length === 0) throw new Error("showcase.json has no gate runs");
  return data;
}

function showError(error) {
  const box = bind("load-error");
  box.className = "load-error";
  box.append(
    el("strong", { text: "The run data could not be loaded. " }),
    `${error.message}. The raw output is still readable in `,
    el("a", { href: "https://github.com/patsypppe/meridian/tree/main/site/data/raw", text: "site/data/raw" }),
    ".",
  );
}

function revealOnScroll() {
  const nodes = document.querySelectorAll(".reveal");
  if (prefersReducedMotion() || !("IntersectionObserver" in window)) {
    nodes.forEach((node) => node.classList.add("is-in"));
    return;
  }
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-in");
          observer.unobserve(entry.target);
        }
      }
    },
    { rootMargin: "0px 0px -8% 0px" },
  );
  nodes.forEach((node) => observer.observe(node));
}

function wireCopyButtons() {
  for (const block of document.querySelectorAll(".codeblock")) {
    const code = block.querySelector("code");
    const button = el("button", { type: "button", class: "copy", text: "copy", "aria-label": "Copy command" });
    button.addEventListener("click", async () => {
      const text = [...code.childNodes]
        .map((node) => (node.nodeType === Node.ELEMENT_NODE && node.classList.contains("prompt") ? "" : node.textContent))
        .join("")
        .split("\n")
        .map((line) => line.trimEnd())
        .join("\n");
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = "copied";
      } catch {
        button.textContent = "select + copy";
      }
      setTimeout(() => {
        button.textContent = "copy";
      }, 1600);
    });
    block.append(button);
  }
}

function onResize(callbacks) {
  let lastWidth = window.innerWidth;
  let frame = 0;
  window.addEventListener("resize", () => {
    if (window.innerWidth === lastWidth) return;
    lastWidth = window.innerWidth;
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => callbacks.forEach((cb) => cb()));
  });
}

async function main() {
  wireCopyButtons();
  revealOnScroll();
  let data;
  try {
    const response = await fetch(DATA_URL, { cache: "no-cache" });
    if (!response.ok) throw new Error(`${DATA_URL} returned HTTP ${response.status}`);
    data = validate(await response.json());
  } catch (error) {
    showError(error);
    return;
  }

  setText("version", data.meridian_version);
  const tip = tooltip();
  mountSpecimen(data);
  const verdict = mountVerdict(data, tip);
  const explorer = mountExplorer(data);
  mountPassK(data);
  mountProbe(data);
  mountSelfEval(data);
  mountColophon(data);
  onResize([verdict.redraw, explorer.redraw]);
  document.addEventListener("scroll", () => tip.hide(), { passive: true });

  // A hook for verification scripts: the page's own sensitivity arithmetic.
  window.meridianShowcase = { data, sensitivity: explorer.compute };
  document.documentElement.dataset.ready = "true";
}

main();
