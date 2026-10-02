// Presentation-only number/unit formatting shared by the workspace scripts.
// Mirrors display.py so server- and client-rendered values read the same way.
// Stored values and exports keep full precision.
(() => {
  "use strict";

  const SUPERSCRIPT = { "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹", "-": "⁻", "+": "⁺" };

  const isLogUnit = (unit) => {
    const text = String(unit || "").trim();
    return Boolean(text) && /^(p[A-Z]|log)/i.test(text) && !["ppm", "ppb"].includes(text.toLowerCase());
  };

  const unit = (value) => {
    let text = String(value || "").trim();
    if (!text) return "";
    text = text.replace(/\^(-?\+?\d+)/g, (_match, power) => power.split("").map((char) => SUPERSCRIPT[char] || char).join(""));
    text = text.replace(/(^|[^A-Za-z])u(?=(L|M|g|mol|m)\b)/g, "$1µ");
    return text;
  };

  const number = (value, unitText = "", digits = null) => {
    if (value === null || value === undefined || value === "") return "—";
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "—";
    if (digits !== null) return numeric.toFixed(digits);
    if (isLogUnit(unitText)) return numeric.toFixed(2);
    if (numeric === 0) return "0";
    const magnitude = Math.abs(numeric);
    if (magnitude >= 1000) return Math.round(numeric).toLocaleString("en-US");
    if (magnitude < 0.001) return numeric.toExponential(2);
    const decimals = Math.max(0, 2 - Math.floor(Math.log10(magnitude)));
    const text = numeric.toFixed(decimals);
    return text.includes(".") ? text.replace(/0+$/, "").replace(/\.$/, "") : text;
  };

  // " µM", or " × 10⁻⁶ cm/s" when the unit carries a power-of-ten scale (mirrors display._unit_suffix).
  const unitSuffix = (unitText) => {
    const u = unit(unitText);
    if (!u) return "";
    return /^10[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]/.test(u) ? ` × ${u}` : ` ${u}`;
  };

  const measure = (value, unitText = "", qualifier = "") => {
    if (value === null || value === undefined || value === "") return "—";
    const q = String(qualifier || "").trim();
    const prefix = q && q !== "=" ? `${q} ` : "";
    return `${prefix}${number(value, unitText)}${unitSuffix(unitText)}`;
  };

  const delta = (value, unitText = "") => {
    if (value === null || value === undefined || value === "") return "—";
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "—";
    const sign = numeric > 0 ? "+" : numeric < 0 ? "−" : "±";
    return `${sign}${number(Math.abs(numeric), unitText)}${unitSuffix(unitText)}`;
  };

  const LABELS = {
    no_chiral_centers_detected: "No stereocentres",
    fully_defined: "Stereo defined",
    undefined_stereocenters: "Undefined stereocentre(s)",
    observed: "Exact",
    observed_with_censored: "Exact + threshold values",
    observed_with_missing: "Exact + missing values",
    observed_with_censored_and_missing: "Exact + threshold + missing",
    censored: "Threshold only",
    censored_with_missing: "Threshold + missing",
    missing: "No result",
    unreviewed: "Not reviewed",
  };

  const humanize = (value) => {
    const text = String(value || "").trim();
    if (!text) return "—";
    if (LABELS[text]) return LABELS[text];
    const spaced = text.replaceAll("_", " ");
    return spaced.charAt(0).toUpperCase() + spaced.slice(1);
  };

  const endpointName = (key) => String(key || "").split(":", 1)[0] || "Endpoint";

  window.SARFormat = Object.freeze({ isLogUnit, unit, number, measure, delta, humanize, endpointName });
})();
