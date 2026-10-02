// Built-in 2-D structure sketcher for substructure queries.
//
// Vanilla JS + SVG, so it runs under the page's script-src 'self' policy and
// needs no third-party editor. It draws atoms (including the query atoms
// A = any heavy atom, Q = heteroatom, * = any atom / attachment point), bonds
// (single, double, triple, aromatic, any), ring templates and charges, with
// undo / redo, and reads and writes MDL V2000 molfiles. Chemistry is left to
// the server: RDKit validates the drawing and turns it into a query.
(() => {
  "use strict";

  const BOND = 40; // drawing bond length (px)
  const MOLFILE_BOND = 1.5; // molfile bond length (Å)
  const WIDTH = 640;
  const HEIGHT = 340;
  const ATOM_HIT = 11;
  const BOND_HIT = 7;
  const MERGE = 7; // a new atom this close to an existing one reuses it
  const HISTORY_LIMIT = 60;
  const ELEMENTS = ["C", "N", "O", "S", "F", "Cl", "Br", "I", "P", "H"];
  const QUERY_ATOMS = [["A", "Any heavy atom"], ["Q", "Any heteroatom (not C or H)"], ["*", "Any atom or attachment point"]];
  const COLOURS = { N: "#2f5fd0", O: "#d03a2f", S: "#9a7d00", F: "#23884a", Cl: "#23884a", Br: "#8a4b22", I: "#6b2fa0", P: "#c56a00", H: "#4a4a4a", A: "#6f42c1", Q: "#6f42c1", "*": "#6f42c1" };
  const KEY_ELEMENTS = { c: "C", n: "N", o: "O", s: "S", f: "F", l: "Cl", b: "Br", i: "I", p: "P", h: "H", a: "A", q: "Q", "*": "*" };
  const ELEMENT_KEYS = Object.fromEntries(Object.entries(KEY_ELEMENTS).map(([key, element]) => [element, key.toUpperCase()]));
  const KEY_BONDS = { 1: 1, 2: 2, 3: 3, 4: 4, 5: 8 };
  const BOND_KEYS = { 1: "1", 2: "2", 3: "3", 4: "4", 8: "5" };
  const BOND_LABELS = { 1: "Single", 2: "Double", 3: "Triple", 4: "Aromatic", 8: "Any" };
  const CHARGE_CODES = { 1: 3, 2: 2, 3: 1, 5: -1, 6: -2, 7: -3 };
  const RINGS = [["ring:6:a", 6, true, "Benzene ring"], ["ring:6", 6, false, "Cyclohexane ring"], ["ring:5", 5, false, "Cyclopentane ring"], ["ring:4", 4, false, "Cyclobutane ring"], ["ring:3", 3, false, "Cyclopropane ring"]];

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const r1 = (value) => Math.round(value * 10) / 10;
  const icon = (body) => `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">${body}</svg>`;
  const stroke = 'stroke="currentColor" stroke-width="1.8" stroke-linecap="round"';
  const BOND_ICONS = {
    1: icon(`<line x1="5" y1="18" x2="19" y2="6" ${stroke}/>`),
    2: icon(`<line x1="4" y1="15" x2="16" y2="5" ${stroke}/><line x1="8" y1="19" x2="20" y2="9" ${stroke}/>`),
    3: icon(`<line x1="3" y1="14" x2="14" y2="4" ${stroke}/><line x1="6" y1="18" x2="18" y2="7" ${stroke}/><line x1="10" y1="21" x2="21" y2="11" ${stroke}/>`),
    4: icon(`<line x1="4" y1="15" x2="16" y2="5" ${stroke}/><line x1="8" y1="19" x2="20" y2="9" ${stroke} stroke-dasharray="2.5 2.5"/>`),
    8: icon(`<line x1="5" y1="18" x2="19" y2="6" ${stroke} stroke-dasharray="3 3"/>`),
  };
  const ringIcon = (size, aromatic) => {
    const points = [...Array(size)].map((_, k) => {
      const angle = -Math.PI / 2 + (k * 2 * Math.PI) / size;
      return `${r1(12 + 8.5 * Math.cos(angle))},${r1(12.5 + 8.5 * Math.sin(angle))}`;
    }).join(" ");
    return icon(`<polygon points="${points}" fill="none" stroke="currentColor" stroke-width="1.6"/>${aromatic ? '<circle cx="12" cy="12.5" r="4" fill="none" stroke="currentColor" stroke-width="1.3"/>' : ""}`);
  };

  const toolButton = (tool, content, title, key = "") => `<button type="button" class="sketcher__tool" data-sketch-tool="${esc(tool)}" aria-pressed="false" aria-label="${esc(title)}" title="${esc(title)}${key ? ` · key ${esc(key)}` : ""}">${content}</button>`;
  const actionButton = (action, text, title) => `<button type="button" class="sketcher__tool sketcher__tool--action" data-sketch-action="${action}" title="${esc(title)}">${esc(text)}</button>`;
  const toolbarHtml = () => `
    <div class="sketcher__group" role="group" aria-label="Atoms">${ELEMENTS.map((element) => toolButton(`atom:${element}`, esc(element), `${element} atom`, ELEMENT_KEYS[element])).join("")}</div>
    <div class="sketcher__group" role="group" aria-label="Query atoms">${QUERY_ATOMS.map(([element, title]) => toolButton(`atom:${element}`, esc(element), title, ELEMENT_KEYS[element])).join("")}</div>
    <div class="sketcher__group" role="group" aria-label="Bonds">${[1, 2, 3, 4, 8].map((order) => toolButton(`bond:${order}`, BOND_ICONS[order], `${BOND_LABELS[order]} bond`, BOND_KEYS[order])).join("")}</div>
    <div class="sketcher__group" role="group" aria-label="Rings">${RINGS.map(([tool, size, aromatic, title]) => toolButton(tool, ringIcon(size, aromatic), title)).join("")}</div>
    <div class="sketcher__group" role="group" aria-label="Edit">${toolButton("charge:1", "+", "Increase charge")}${toolButton("charge:-1", "−", "Decrease charge")}${toolButton("erase", "Erase", "Erase an atom or bond", "Delete")}${toolButton("move", "Move", "Move an atom, or drag empty space to move the drawing")}</div>
    <div class="sketcher__group" role="group" aria-label="History">${actionButton("undo", "Undo", "Undo (Ctrl/⌘ Z)")}${actionButton("redo", "Redo", "Redo (Ctrl/⌘ Shift Z)")}${actionButton("clear", "Clear", "Remove the whole drawing")}</div>`;

  let instances = 0;

  const create = (container, options = {}) => {
    const onChange = typeof options.onChange === "function" ? options.onChange : () => {};
    const statusId = `sketcherStatus${(instances += 1)}`;
    container.classList.add("sketcher");
    container.innerHTML = `<div class="sketcher__toolbar" role="toolbar" aria-label="Drawing tools">${toolbarHtml()}</div>
      <svg class="sketcher__canvas" viewBox="0 0 ${WIDTH} ${HEIGHT}" tabindex="0" role="application" aria-label="${esc(options.label || "Structure drawing canvas")}" aria-describedby="${statusId}">
        <g class="sketcher__bonds"></g><g class="sketcher__atoms"></g><g class="sketcher__hover"></g><g class="sketcher__preview"></g>
      </svg>
      <p class="sketcher__status" id="${statusId}" aria-live="polite"></p>`;
    const svg = container.querySelector(".sketcher__canvas");
    const layers = {
      bonds: svg.querySelector(".sketcher__bonds"),
      atoms: svg.querySelector(".sketcher__atoms"),
      hover: svg.querySelector(".sketcher__hover"),
      preview: svg.querySelector(".sketcher__preview"),
    };
    const status = container.querySelector(".sketcher__status");

    let atoms = []; // { id, x, y, el, charge }
    let bonds = []; // { id, a, b, order } with order 1, 2, 3, 4 (aromatic) or 8 (any)
    let nextId = 1;
    let tool = "bond:1";
    let hover = null; // { kind: "atom" | "bond", id }
    let drag = null;
    let history = [];
    let future = [];

    const atomById = (id) => atoms.find((atom) => atom.id === id) || null;
    const bondById = (id) => bonds.find((bond) => bond.id === id) || null;
    const bondBetween = (a, b) => bonds.find((bond) => (bond.a === a && bond.b === b) || (bond.a === b && bond.b === a)) || null;
    const neighbours = (id) => bonds.filter((bond) => bond.a === id || bond.b === id).map((bond) => (bond.a === id ? bond.b : bond.a));
    const atomNear = (x, y, radius = MERGE) => atoms.find((atom) => Math.hypot(atom.x - x, atom.y - y) < radius) || null;
    const addAtom = (x, y, el = "C") => {
      const atom = { id: nextId, x: r1(x), y: r1(y), el, charge: 0 };
      nextId += 1;
      atoms.push(atom);
      return atom.id;
    };
    const atomAt = (x, y, el = "C") => atomNear(x, y)?.id ?? addAtom(x, y, el);
    const addBond = (a, b, order = 1) => {
      if (a === b) return null;
      const existing = bondBetween(a, b);
      if (existing) {
        existing.order = order;
        return existing.id;
      }
      bonds.push({ id: nextId, a, b, order });
      nextId += 1;
      return nextId - 1;
    };
    const removeAtom = (id) => {
      atoms = atoms.filter((atom) => atom.id !== id);
      bonds = bonds.filter((bond) => bond.a !== id && bond.b !== id);
    };
    const removeBond = (id) => {
      bonds = bonds.filter((bond) => bond.id !== id);
    };

    const snapshot = () => JSON.stringify({ atoms, bonds, nextId });
    const restore = (text) => {
      const saved = JSON.parse(text);
      atoms = saved.atoms;
      bonds = saved.bonds;
      nextId = saved.nextId;
    };
    const changed = () => {
      hover = null;
      render();
      onChange(api);
    };
    // Every user action runs through edit() so it can be undone.
    const edit = (change) => {
      const before = snapshot();
      change();
      if (snapshot() === before) return render();
      history.push(before);
      if (history.length > HISTORY_LIMIT) history.shift();
      future = [];
      return changed();
    };
    const undo = () => {
      if (!history.length) return;
      future.push(snapshot());
      restore(history.pop());
      changed();
    };
    const redo = () => {
      if (!future.length) return;
      history.push(snapshot());
      restore(future.pop());
      changed();
    };

    // Direction for a new bond from an atom: zig-zag for chains, the widest gap otherwise.
    const freeAngle = (id) => {
      const atom = atomById(id);
      const angles = neighbours(id).map((other) => {
        const neighbour = atomById(other);
        return Math.atan2(neighbour.y - atom.y, neighbour.x - atom.x);
      });
      if (!angles.length) return -Math.PI / 6;
      if (angles.length === 1) {
        const options = [angles[0] + (2 * Math.PI) / 3, angles[0] - (2 * Math.PI) / 3];
        return options.sort((a, b) => Math.cos(b) - Math.cos(a) || Math.sin(a) - Math.sin(b))[0];
      }
      const sorted = angles.map((angle) => (angle + 2 * Math.PI) % (2 * Math.PI)).sort((a, b) => a - b);
      let widest = -1;
      let start = 0;
      sorted.forEach((angle, index) => {
        const next = index + 1 < sorted.length ? sorted[index + 1] : sorted[0] + 2 * Math.PI;
        if (next - angle > widest) {
          widest = next - angle;
          start = angle;
        }
      });
      return start + widest / 2;
    };
    const segmentDistance = (p, a, b) => {
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const length = dx * dx + dy * dy || 1;
      const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / length));
      return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
    };
    const hitTest = (p) => {
      let best = null;
      let distance = ATOM_HIT;
      atoms.forEach((atom) => {
        const d = Math.hypot(atom.x - p.x, atom.y - p.y);
        if (d < distance) [best, distance] = [{ kind: "atom", id: atom.id }, d];
      });
      if (best) return best;
      distance = BOND_HIT;
      bonds.forEach((bond) => {
        const d = segmentDistance(p, atomById(bond.a), atomById(bond.b));
        if (d < distance) [best, distance] = [{ kind: "bond", id: bond.id }, d];
      });
      return best;
    };
    const pointer = (event) => {
      const matrix = svg.getScreenCTM();
      if (!matrix) return { x: 0, y: 0 };
      const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
      return { x: point.x, y: point.y };
    };

    const grow = (id, order = 1, el = "C") => {
      const atom = atomById(id);
      const angle = freeAngle(id);
      addBond(id, atomAt(atom.x + BOND * Math.cos(angle), atom.y + BOND * Math.sin(angle), el), order);
    };
    const normalise = (angle) => Math.atan2(Math.sin(angle), Math.cos(angle));
    // Rings go on empty space, fuse onto a clicked bond, or hang off a clicked atom.
    const placeRing = (size, aromatic, hit, p) => {
      const radius = BOND / (2 * Math.sin(Math.PI / size));
      let centre;
      let start;
      let step = (2 * Math.PI) / size;
      const fixed = [];
      if (hit?.kind === "bond") {
        const bond = bondById(hit.id);
        const a = atomById(bond.a);
        const b = atomById(bond.b);
        const length = Math.hypot(b.x - a.x, b.y - a.y) || BOND;
        const middle = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
        let normal = { x: -(b.y - a.y) / length, y: (b.x - a.x) / length };
        const others = [...neighbours(a.id), ...neighbours(b.id)].filter((id) => id !== a.id && id !== b.id).map(atomById);
        const side = others.reduce((sum, atom) => sum + (atom.x - middle.x) * normal.x + (atom.y - middle.y) * normal.y, 0);
        if (side > 0) normal = { x: -normal.x, y: -normal.y };
        const apothem = length / (2 * Math.tan(Math.PI / size));
        centre = { x: middle.x + normal.x * apothem, y: middle.y + normal.y * apothem };
        start = Math.atan2(a.y - centre.y, a.x - centre.x);
        if (Math.abs(normalise(start + step - Math.atan2(b.y - centre.y, b.x - centre.x))) > 1e-3) step = -step;
        fixed.push(a.id, b.id);
      } else if (hit?.kind === "atom") {
        const atom = atomById(hit.id);
        const angle = neighbours(atom.id).length ? freeAngle(atom.id) : 0;
        let anchor = atom;
        if (neighbours(atom.id).length) {
          anchor = atomById(addAtom(atom.x + BOND * Math.cos(angle), atom.y + BOND * Math.sin(angle)));
          addBond(atom.id, anchor.id, 1);
        }
        centre = { x: anchor.x + radius * Math.cos(angle), y: anchor.y + radius * Math.sin(angle) };
        start = angle + Math.PI;
        fixed.push(anchor.id);
      } else {
        centre = p;
        start = -Math.PI / 2;
      }
      const ids = [...Array(size)].map((_, k) => fixed[k] ?? atomAt(centre.x + radius * Math.cos(start + k * step), centre.y + radius * Math.sin(start + k * step)));
      ids.forEach((id, k) => {
        const next = ids[(k + 1) % size];
        const existing = bondBetween(id, next);
        if (!existing) addBond(id, next, aromatic ? 4 : 1);
        else if (aromatic) existing.order = 4;
      });
    };

    const applyClick = (hit, p) => edit(() => {
      const [kind, value, flag] = tool.split(":");
      if (kind === "atom") {
        if (hit?.kind === "atom") atomById(hit.id).el = value;
        else if (!hit) addAtom(p.x, p.y, value);
      } else if (kind === "bond") {
        const order = Number(value);
        if (hit?.kind === "bond") {
          const bond = bondById(hit.id);
          // The single-bond tool cycles single → double → triple, like most sketchers.
          bond.order = order === 1 && bond.order < 3 ? bond.order + 1 : order === 1 && bond.order === 3 ? 1 : order;
        } else if (hit?.kind === "atom") {
          grow(hit.id, order);
        } else {
          const a = addAtom(p.x - BOND * 0.433, p.y + BOND * 0.25);
          addBond(a, addAtom(p.x + BOND * 0.433, p.y - BOND * 0.25), order);
        }
      } else if (kind === "ring") {
        placeRing(Number(value), flag === "a", hit, p);
      } else if (kind === "charge" && hit?.kind === "atom") {
        const atom = atomById(hit.id);
        atom.charge = Math.max(-3, Math.min(3, atom.charge + Number(value)));
      } else if (kind === "erase" && hit) {
        if (hit.kind === "atom") removeAtom(hit.id);
        else removeBond(hit.id);
      }
    });
    // Dragging draws a bond from the start atom (or a new atom) to the atom under the
    // pointer, or to a new atom one bond length away along the dragged direction.
    const dragEnd = (start, p) => {
      const angle = Math.round(Math.atan2(p.y - start.y, p.x - start.x) / (Math.PI / 12)) * (Math.PI / 12);
      return { x: start.x + BOND * Math.cos(angle), y: start.y + BOND * Math.sin(angle) };
    };
    const applyDrag = (from, p) => {
      const [kind, value] = tool.split(":");
      if (kind !== "atom" && kind !== "bond") return applyClick(from.hit, { x: from.x, y: from.y });
      return edit(() => {
        const element = kind === "atom" ? value : "C";
        const order = kind === "bond" ? Number(value) : 1;
        const start = from.hit?.kind === "atom" ? from.hit.id : addAtom(from.x, from.y, element);
        const target = hitTest(p);
        if (target?.kind === "atom" && target.id !== start) {
          addBond(start, target.id, order);
          return;
        }
        const end = dragEnd(atomById(start), p);
        addBond(start, atomAt(end.x, end.y, element), order);
      });
    };

    // ------------------------------------------------------------- drawing
    const labelled = (atom) => atom.el !== "C" || atom.charge !== 0 || neighbours(atom.id).length === 0;
    const line = (x1, y1, x2, y2, extra = "") => `<line x1="${r1(x1)}" y1="${r1(y1)}" x2="${r1(x2)}" y2="${r1(y2)}"${extra}></line>`;
    // Which side a second bond line goes: towards the ring / other substituents, or centred.
    const bondSide = (a, b, normal) => {
      const middle = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      const others = [...neighbours(a.id), ...neighbours(b.id)].filter((id) => id !== a.id && id !== b.id).map(atomById);
      const side = others.reduce((sum, atom) => sum + Math.sign((atom.x - middle.x) * normal.x + (atom.y - middle.y) * normal.y), 0);
      return Math.sign(side);
    };
    const bondSvg = (bond) => {
      const a = atomById(bond.a);
      const b = atomById(bond.b);
      const length = Math.hypot(b.x - a.x, b.y - a.y) || 1;
      const ux = (b.x - a.x) / length;
      const uy = (b.y - a.y) / length;
      const normal = { x: -uy, y: ux };
      const trimA = labelled(a) ? 9 : 0;
      const trimB = labelled(b) ? 9 : 0;
      const [x1, y1, x2, y2] = [a.x + ux * trimA, a.y + uy * trimA, b.x - ux * trimB, b.y - uy * trimB];
      const shifted = (offset, inset, extra = "") => line(x1 + normal.x * offset + ux * inset, y1 + normal.y * offset + uy * inset, x2 + normal.x * offset - ux * inset, y2 + normal.y * offset - uy * inset, extra);
      const side = bondSide(a, b, normal);
      const parts = [];
      if (bond.order === 2 && side) parts.push(line(x1, y1, x2, y2), shifted(6 * side, 6));
      else if (bond.order === 2) parts.push(shifted(2.8, 0), shifted(-2.8, 0));
      else if (bond.order === 3) parts.push(line(x1, y1, x2, y2), shifted(4.5, 0), shifted(-4.5, 0));
      else if (bond.order === 4) parts.push(line(x1, y1, x2, y2), shifted(6 * (side || 1), 6, ' stroke-dasharray="4 3"'));
      else if (bond.order === 8) parts.push(line(x1, y1, x2, y2, ' stroke-dasharray="4 3"'));
      else parts.push(line(x1, y1, x2, y2));
      return `<g class="sketcher__bond">${parts.join("")}</g>`;
    };
    const atomSvg = (atom) => {
      if (!labelled(atom)) return "";
      const size = Math.abs(atom.charge);
      const charge = atom.charge ? `${size > 1 ? size : ""}${atom.charge > 0 ? "+" : "−"}` : "";
      return `<g class="sketcher__atom"><circle cx="${atom.x}" cy="${atom.y}" r="${atom.el.length > 1 ? 11 : 9}"></circle><text x="${atom.x}" y="${r1(atom.y + 5)}" text-anchor="middle" fill="${COLOURS[atom.el] || "#1b2429"}">${esc(atom.el)}${charge ? `<tspan dy="-7" font-size="10">${esc(charge)}</tspan>` : ""}</text></g>`;
    };
    const renderHover = () => {
      let html = "";
      if (hover?.kind === "atom" && atomById(hover.id)) {
        const atom = atomById(hover.id);
        html = `<circle cx="${atom.x}" cy="${atom.y}" r="12"></circle>`;
      } else if (hover?.kind === "bond" && bondById(hover.id)) {
        const bond = bondById(hover.id);
        html = line(atomById(bond.a).x, atomById(bond.a).y, atomById(bond.b).x, atomById(bond.b).y);
      }
      layers.hover.innerHTML = html;
    };
    const renderPreview = (from, p) => {
      const kind = tool.split(":")[0];
      if (kind !== "atom" && kind !== "bond") return;
      const start = from.hit?.kind === "atom" ? atomById(from.hit.id) : { x: from.x, y: from.y };
      const target = hitTest(p);
      const end = target?.kind === "atom" ? atomById(target.id) : dragEnd(start, p);
      layers.preview.innerHTML = line(start.x, start.y, end.x, end.y);
    };
    const toolLabel = () => container.querySelector(`[data-sketch-tool="${tool}"]`)?.getAttribute("aria-label") || tool;
    const describe = () => (atoms.length
      ? `${atoms.length} atom${atoms.length === 1 ? "" : "s"}, ${bonds.length} bond${bonds.length === 1 ? "" : "s"}. Tool: ${toolLabel()}. Hover an atom and type a letter (N, O, S, Cl as L, Br as B, A, Q) to change it; Delete removes it.`
      : `Empty drawing. Tool: ${toolLabel()}. Click the canvas to start, or type SMILES / SMARTS instead.`);
    const syncToolbar = () => {
      container.querySelectorAll("[data-sketch-tool]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.sketchTool === tool)));
      const undoButton = container.querySelector('[data-sketch-action="undo"]');
      const redoButton = container.querySelector('[data-sketch-action="redo"]');
      const clearButton = container.querySelector('[data-sketch-action="clear"]');
      if (undoButton) undoButton.disabled = !history.length;
      if (redoButton) redoButton.disabled = !future.length;
      if (clearButton) clearButton.disabled = !atoms.length;
    };
    const render = () => {
      layers.bonds.innerHTML = bonds.map(bondSvg).join("");
      layers.atoms.innerHTML = atoms.length
        ? atoms.map(atomSvg).join("")
        : `<text class="sketcher__hint" x="${WIDTH / 2}" y="${HEIGHT / 2}" text-anchor="middle">Click to start drawing · drag from an atom to add a bond</text>`;
      renderHover();
      layers.preview.innerHTML = "";
      status.textContent = describe();
      syncToolbar();
    };
    const setTool = (next) => {
      if (!container.querySelector(`[data-sketch-tool="${next}"]`)) return;
      tool = next;
      syncToolbar();
      status.textContent = describe();
    };

    // ------------------------------------------------------- molfile V2000
    const pad = (value, width) => String(value).padStart(width);
    const toMolblock = () => {
      if (!atoms.length) return "";
      const index = new Map(atoms.map((atom, position) => [atom.id, position + 1]));
      const scale = MOLFILE_BOND / BOND;
      const lines = ["", "  SARWorkbench sketch", "", `${pad(atoms.length, 3)}${pad(bonds.length, 3)}  0  0  0  0  0  0  0  0999 V2000`];
      atoms.forEach((atom) => {
        lines.push(`${pad((atom.x * scale).toFixed(4), 10)}${pad((-atom.y * scale).toFixed(4), 10)}${pad("0.0000", 10)} ${atom.el.padEnd(3)} 0  0  0  0  0  0  0  0  0  0  0  0`);
      });
      bonds.forEach((bond) => lines.push(`${pad(index.get(bond.a), 3)}${pad(index.get(bond.b), 3)}${pad(bond.order, 3)}  0`));
      const charged = atoms.filter((atom) => atom.charge);
      for (let start = 0; start < charged.length; start += 8) {
        const chunk = charged.slice(start, start + 8);
        lines.push(`M  CHG${pad(chunk.length, 3)}${chunk.map((atom) => `${pad(index.get(atom.id), 4)}${pad(atom.charge, 4)}`).join("")}`);
      }
      lines.push("M  END");
      return lines.join("\n");
    };
    const parseMolblock = (text) => {
      const lines = String(text || "").split(/\r?\n/);
      const counts = lines[3] || "";
      const atomCount = parseInt(counts.slice(0, 3), 10);
      const bondCount = parseInt(counts.slice(3, 6), 10);
      if (!(atomCount >= 0 && atomCount <= 400 && bondCount >= 0) || counts.includes("V3000") || lines.length < 4 + atomCount + bondCount) return null;
      const parsedAtoms = [];
      for (let k = 0; k < atomCount; k += 1) {
        const row = lines[4 + k];
        const x = parseFloat(row.slice(0, 10));
        const y = parseFloat(row.slice(10, 20));
        if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
        parsedAtoms.push({ x, y, el: row.slice(31, 34).trim() || "C", charge: CHARGE_CODES[parseInt(row.slice(36, 39), 10)] || 0 });
      }
      const parsedBonds = [];
      for (let k = 0; k < bondCount; k += 1) {
        const row = lines[4 + atomCount + k];
        const a = parseInt(row.slice(0, 3), 10) - 1;
        const b = parseInt(row.slice(3, 6), 10) - 1;
        const type = parseInt(row.slice(6, 9), 10);
        if (!(a >= 0 && a < atomCount && b >= 0 && b < atomCount) || a === b) return null;
        // Molfile query bond types 5-7 (single/double, single/aromatic, double/aromatic) are drawn as "any".
        parsedBonds.push({ a, b, order: [1, 2, 3, 4, 8].includes(type) ? type : type >= 5 && type <= 7 ? 8 : 1 });
      }
      lines.slice(4 + atomCount + bondCount).filter((row) => row.startsWith("M  CHG")).forEach((row) => {
        const entries = parseInt(row.slice(6, 9), 10) || 0;
        for (let k = 0; k < entries; k += 1) {
          const atom = parseInt(row.slice(9 + k * 8, 13 + k * 8), 10) - 1;
          const value = parseInt(row.slice(13 + k * 8, 17 + k * 8), 10);
          if (parsedAtoms[atom] && Number.isFinite(value)) parsedAtoms[atom].charge = Math.max(-3, Math.min(3, value));
        }
      });
      return { atoms: parsedAtoms, bonds: parsedBonds };
    };
    // Scale molfile coordinates to the drawing bond length, then fit and centre them.
    const load = (parsed) => {
      const lengths = parsed.bonds
        .map((bond) => Math.hypot(parsed.atoms[bond.a].x - parsed.atoms[bond.b].x, parsed.atoms[bond.a].y - parsed.atoms[bond.b].y))
        .filter((length) => length > 1e-6);
      const average = lengths.length ? lengths.reduce((sum, length) => sum + length, 0) / lengths.length : MOLFILE_BOND;
      let scale = BOND / average;
      const span = (values) => (values.length ? Math.max(...values) - Math.min(...values) : 0);
      const fit = Math.min(1, (WIDTH - 70) / (span(parsed.atoms.map((atom) => atom.x * scale)) || 1), (HEIGHT - 70) / (span(parsed.atoms.map((atom) => atom.y * scale)) || 1));
      scale *= fit;
      const xs = parsed.atoms.map((atom) => atom.x * scale);
      const ys = parsed.atoms.map((atom) => -atom.y * scale);
      const centreX = xs.length ? (Math.max(...xs) + Math.min(...xs)) / 2 : 0;
      const centreY = ys.length ? (Math.max(...ys) + Math.min(...ys)) / 2 : 0;
      atoms = parsed.atoms.map((atom, k) => ({ id: k + 1, x: r1(WIDTH / 2 + xs[k] - centreX), y: r1(HEIGHT / 2 + ys[k] - centreY), el: atom.el, charge: atom.charge }));
      bonds = parsed.bonds.map((bond, k) => ({ id: atoms.length + k + 1, a: bond.a + 1, b: bond.b + 1, order: bond.order }));
      nextId = atoms.length + bonds.length + 1;
    };

    // ---------------------------------------------------------------- events
    container.querySelector(".sketcher__toolbar").addEventListener("click", (event) => {
      const button = event.target instanceof Element ? event.target.closest("button") : null;
      if (!button) return;
      if (button.dataset.sketchTool) setTool(button.dataset.sketchTool);
      if (button.dataset.sketchAction === "undo") undo();
      if (button.dataset.sketchAction === "redo") redo();
      if (button.dataset.sketchAction === "clear") edit(() => { atoms = []; bonds = []; });
    });
    svg.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      const p = pointer(event);
      drag = { x: p.x, y: p.y, last: p, hit: hitTest(p), moved: false, before: tool === "move" ? snapshot() : null };
      try { svg.setPointerCapture(event.pointerId); } catch (_error) { /* capture is optional */ }
      svg.focus({ preventScroll: true });
      event.preventDefault();
    });
    svg.addEventListener("pointermove", (event) => {
      const p = pointer(event);
      if (!drag) {
        const next = hitTest(p);
        if (JSON.stringify(next) !== JSON.stringify(hover)) {
          hover = next;
          renderHover();
        }
        return;
      }
      if (!drag.moved && Math.hypot(p.x - drag.x, p.y - drag.y) > 5) drag.moved = true;
      if (!drag.moved) return;
      if (tool !== "move") {
        renderPreview(drag, p);
        return;
      }
      const bond = drag.hit?.kind === "bond" ? bondById(drag.hit.id) : null;
      const moving = drag.hit?.kind === "atom" ? [atomById(drag.hit.id)] : bond ? [atomById(bond.a), atomById(bond.b)] : atoms;
      moving.forEach((atom) => {
        atom.x = r1(atom.x + p.x - drag.last.x);
        atom.y = r1(atom.y + p.y - drag.last.y);
      });
      drag.last = p;
      render();
    });
    svg.addEventListener("pointerup", (event) => {
      if (!drag) return;
      const from = drag;
      drag = null;
      try { svg.releasePointerCapture(event.pointerId); } catch (_error) { /* already released */ }
      if (tool === "move") {
        if (from.moved && from.before !== snapshot()) {
          history.push(from.before);
          if (history.length > HISTORY_LIMIT) history.shift();
          future = [];
          changed();
        }
        return;
      }
      if (from.moved) applyDrag(from, pointer(event));
      else applyClick(from.hit, { x: from.x, y: from.y });
    });
    svg.addEventListener("pointercancel", () => {
      drag = null;
      render();
    });
    svg.addEventListener("pointerleave", () => {
      if (drag || !hover) return;
      hover = null;
      renderHover();
    });
    // Keyboard: element letters and bond digits change the hovered atom / bond, or pick a tool.
    svg.addEventListener("keydown", (event) => {
      const key = event.key;
      const modifier = event.ctrlKey || event.metaKey;
      if (modifier && key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) redo();
        else undo();
        return;
      }
      if (modifier && key.toLowerCase() === "y") {
        event.preventDefault();
        redo();
        return;
      }
      if (modifier || event.altKey) return;
      const target = hover;
      if ((key === "Delete" || key === "Backspace") && target) {
        event.preventDefault();
        edit(() => (target.kind === "atom" ? removeAtom(target.id) : removeBond(target.id)));
        return;
      }
      const element = KEY_ELEMENTS[key.toLowerCase()] || KEY_ELEMENTS[key];
      if (element) {
        event.preventDefault();
        if (target?.kind === "atom") edit(() => { atomById(target.id).el = element; });
        else setTool(`atom:${element}`);
        return;
      }
      const order = KEY_BONDS[key];
      if (order) {
        event.preventDefault();
        if (target?.kind === "bond") edit(() => { bondById(target.id).order = order; });
        else setTool(`bond:${order}`);
      }
    });

    const api = {
      getMolblock: toMolblock,
      setMolblock: (text) => {
        const parsed = parseMolblock(text);
        if (!parsed) return false;
        edit(() => load(parsed));
        return true;
      },
      clear: () => edit(() => { atoms = []; bonds = []; }),
      isEmpty: () => atoms.length === 0,
      counts: () => ({ atoms: atoms.length, bonds: bonds.length }),
      setTool,
      focus: () => svg.focus(),
    };
    render();
    return api;
  };

  window.SARSketcher = Object.freeze({ create });
})();
