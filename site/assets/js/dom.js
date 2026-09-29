// Small DOM and SVG helpers. Text is always set with textContent, never innerHTML,
// so nothing from the data file is ever interpreted as markup.

const SVG_NS = "http://www.w3.org/2000/svg";

export function bind(name) {
  return document.querySelector(`[data-bind="${name}"]`);
}

export function setText(name, value) {
  const node = bind(name);
  if (node) node.textContent = value;
  return node;
}

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  applyAttrs(node, attrs);
  append(node, children);
  return node;
}

export function svg(tag, attrs = {}, children = []) {
  const node = document.createElementNS(SVG_NS, tag);
  applyAttrs(node, attrs);
  append(node, children);
  return node;
}

function applyAttrs(node, attrs) {
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "text") node.textContent = value;
    else if (key === "class") node.setAttribute("class", value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2), value);
    } else node.setAttribute(key, value === true ? "" : String(value));
  }
}

function append(node, children) {
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export const fmt3 = (value) => (value === null || value === undefined ? "—" : value.toFixed(3));
export const fmtPct = (value, places = 1) => `${(value * 100).toFixed(places)}%`;
export const fmtInt = (value) => Math.round(value).toLocaleString("en-US");

export function linear(domain, range) {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const scale = (value) => r0 + ((value - d0) / (d1 - d0)) * (r1 - r0);
  scale.invert = (px) => d0 + ((px - r0) / (r1 - r0)) * (d1 - d0);
  return scale;
}

export function logScale(domain, range) {
  const [d0, d1] = domain.map(Math.log);
  const [r0, r1] = range;
  const scale = (value) => r0 + ((Math.log(value) - d0) / (d1 - d0)) * (r1 - r0);
  scale.invert = (px) => Math.exp(d0 + ((px - r0) / (r1 - r0)) * (d1 - d0));
  return scale;
}

// One shared tooltip, positioned next to the pointer or the focused element.
export function tooltip() {
  const tip = bind("tooltip");
  return {
    show(lines, anchor) {
      clear(tip);
      lines.forEach((line, i) => {
        if (i) tip.append(el("br"));
        tip.append(document.createTextNode(line));
      });
      const box = anchor.getBoundingClientRect
        ? anchor.getBoundingClientRect()
        : { left: anchor.x, top: anchor.y, width: 0, height: 0 };
      tip.dataset.open = "true";
      const tipBox = tip.getBoundingClientRect();
      const left = Math.min(
        Math.max(8, box.left + box.width / 2 - tipBox.width / 2),
        window.innerWidth - tipBox.width - 8,
      );
      const above = box.top - tipBox.height - 10;
      const top = above > 8 ? above : box.top + box.height + 10;
      tip.style.left = `${left}px`;
      tip.style.top = `${top}px`;
    },
    hide() {
      tip.dataset.open = "false";
    },
  };
}

export const prefersReducedMotion = () =>
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;
