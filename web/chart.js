// Minimal SVG line chart for the scenario projection. Values come straight from the API's projection series.
const SVG = "http://www.w3.org/2000/svg";

export function balanceChart(projection, status, breakMonth) {
  const width = 300;
  const height = 90;
  const pad = 4;
  const values = projection.map((p) => Number(p.balance));
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const span = max - min || 1;
  const x = (i) => pad + (i / Math.max(1, values.length - 1)) * (width - pad * 2);
  const y = (v) => pad + (1 - (v - min) / span) * (height - pad * 2);

  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("class", "chart");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("role", "img");

  const zero = document.createElementNS(SVG, "line");
  zero.setAttribute("x1", pad);
  zero.setAttribute("x2", width - pad);
  zero.setAttribute("y1", y(0));
  zero.setAttribute("y2", y(0));
  zero.setAttribute("class", "zero");
  svg.appendChild(zero);

  const line = document.createElementNS(SVG, "polyline");
  line.setAttribute("points", values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" "));
  line.setAttribute("class", `line ${status}`);
  svg.appendChild(line);

  if (breakMonth !== null && breakMonth !== undefined) {
    const idx = projection.findIndex((p) => p.month === breakMonth);
    if (idx >= 0) {
      const dot = document.createElementNS(SVG, "circle");
      dot.setAttribute("cx", x(idx));
      dot.setAttribute("cy", y(values[idx]));
      dot.setAttribute("r", 3.5);
      dot.setAttribute("class", "marker");
      svg.appendChild(dot);
    }
  }
  return svg;
}
