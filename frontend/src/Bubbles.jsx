import React, { useEffect, useRef } from "react";
import * as d3 from "d3";

export const color = d3.scaleLinear().domain([0, 50, 100]).range(["#00e5ff", "#ffb300", "#ff3d71"]).clamp(true);

/** Sender-group bubbles: size = mail count, colour = importance, label = name + count, click = drill down. */
export default function Bubbles({ groups, onPick }) {
  const ref = useRef();
  useEffect(() => {
    const svg = d3.select(ref.current);
    svg.selectAll("*").remove();
    if (!groups.length) return;
    const W = 900, H = 520;
    svg.attr("viewBox", `0 0 ${W} ${H}`);
    const root = d3.hierarchy({ children: groups }).sum((d) => d.count);
    const nodes = d3.pack().size([W, H]).padding(4)(root).leaves();
    const g = svg.selectAll("g").data(nodes).join("g").attr("transform", (d) => `translate(${d.x},${d.y})`);
    g.append("circle").attr("r", (d) => d.r).attr("fill", (d) => color(d.data.importance)).attr("stroke", (d) => color(d.data.importance)).style("color", (d) => color(d.data.importance))
      .on("click", (_, d) => onPick(d.data))
      .append("title").text((d) => `${d.data.label}\n${d.data.count} mails · importance ${d.data.importance}`);
    const fit = (s, r) => (s.length * 6.5 > r * 1.8 ? s.slice(0, Math.max(2, Math.floor((r * 1.8) / 6.5) - 1)) + "…" : s);
    g.filter((d) => d.r > 18).append("text").attr("dy", "-0.2em").text((d) => fit(d.data.label, d.r));
    g.filter((d) => d.r > 18).append("text").attr("dy", "1em").style("font-weight", 700).text((d) => d.data.count);
  }, [groups, onPick]);
  return <svg ref={ref} className="bubbles" role="img" aria-label="Sender groups" />;
}
