// Copyright (c) 2026 openyfai (YF)
// Licensed under the Business Source License 1.1 (BSL 1.1)
// See LICENSE file in the project root for full license terms.

import { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { Settings2, Box, Droplets, Database, Activity } from 'lucide-react';
import { createRoot } from 'react-dom/client';

export function CausalNetwork({ graphData, anomalyPath }) {
  const containerRef = useRef(null);

  useEffect(() => {
    if (!graphData || !graphData.nodes || !containerRef.current) return;
    
    // Clear previous
    containerRef.current.innerHTML = '';
    
    const width = containerRef.current.clientWidth;
    const height = containerRef.current.clientHeight;

    const svg = d3.select(containerRef.current)
      .append("svg")
      .attr("width", width)
      .attr("height", height)
      .attr("viewBox", [0, 0, width, height]);

    // Defs for glows and gradients
    const defs = svg.append("defs");
    
    const filterCyan = defs.append("filter").attr("id", "glowCyan");
    filterCyan.append("feGaussianBlur").attr("stdDeviation", "4").attr("result", "coloredBlur");
    const feMergeCyan = filterCyan.append("feMerge");
    feMergeCyan.append("feMergeNode").attr("in", "coloredBlur");
    feMergeCyan.append("feMergeNode").attr("in", "SourceGraphic");

    const filterOrange = defs.append("filter").attr("id", "glowOrange");
    filterOrange.append("feGaussianBlur").attr("stdDeviation", "5").attr("result", "coloredBlur");
    const feMergeOrange = filterOrange.append("feMerge");
    feMergeOrange.append("feMergeNode").attr("in", "coloredBlur");
    feMergeOrange.append("feMergeNode").attr("in", "SourceGraphic");

    // Arrow marker
    defs.append("marker")
      .attr("id", "arrowCyan")
      .attr("viewBox", "0 -5 10 10")
      .attr("refX", 32)
      .attr("refY", 0)
      .attr("markerWidth", 6)
      .attr("markerHeight", 6)
      .attr("orient", "auto")
      .append("path")
      .attr("fill", "var(--color-cyan)")
      .attr("d", "M0,-5L10,0L0,5");

    defs.append("marker")
      .attr("id", "arrowOrange")
      .attr("viewBox", "0 -5 10 10")
      .attr("refX", 32)
      .attr("refY", 0)
      .attr("markerWidth", 6)
      .attr("markerHeight", 6)
      .attr("orient", "auto")
      .append("path")
      .attr("fill", "var(--color-amber)")
      .attr("d", "M0,-5L10,0L0,5");

    // Force simulation
    const simulation = d3.forceSimulation(graphData.nodes)
      .force("link", d3.forceLink(graphData.links).id(d => d.id).distance(120))
      .force("charge", d3.forceManyBody().strength(-400))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("x", d3.forceX(width / 2).strength(0.05))
      .force("y", d3.forceY(height / 2).strength(0.05));

    // Links
    const link = svg.append("g")
      .attr("stroke-opacity", 0.6)
      .selectAll("line")
      .data(graphData.links)
      .join("line")
      .attr("stroke-width", 2)
      .attr("stroke", d => {
        // Is this link part of the anomaly path?
        const isAnomalous = anomalyPath?.some((step, i) => {
          if (i === 0) return false;
          const prev = anomalyPath[i-1];
          return (prev.id === d.source.id && step.id === d.target.id) || 
                 (prev.id === d.target.id && step.id === d.source.id);
        });
        return isAnomalous ? "var(--color-amber)" : "var(--color-cyan)";
      })
      .attr("marker-end", d => {
        const isAnomalous = anomalyPath?.some((step, i) => {
          if (i === 0) return false;
          const prev = anomalyPath[i-1];
          return (prev.id === d.source.id && step.id === d.target.id) || 
                 (prev.id === d.target.id && step.id === d.source.id);
        });
        return isAnomalous ? "url(#arrowOrange)" : "url(#arrowCyan)";
      });

    // Nodes
    const nodeGroup = svg.append("g")
      .selectAll("g")
      .data(graphData.nodes)
      .join("g")
      .call(d3.drag()
        .on("start", dragstarted)
        .on("drag", dragged)
        .on("end", dragended));

    // Determine anomalous status
    const isNodeAnomalous = (id) => anomalyPath?.some(step => step.id === id);

    // Node Box
    nodeGroup.append("rect")
      .attr("width", 50)
      .attr("height", 50)
      .attr("x", -25)
      .attr("y", -25)
      .attr("rx", 12)
      .attr("fill", d => isNodeAnomalous(d.id) ? "rgba(255, 149, 0, 0.15)" : "rgba(0, 229, 255, 0.05)")
      .attr("stroke", d => isNodeAnomalous(d.id) ? "var(--color-amber)" : "var(--color-cyan)")
      .attr("stroke-width", 2)
      .style("filter", d => isNodeAnomalous(d.id) ? "url(#glowOrange)" : "url(#glowCyan)");

    // Top right indicator dot
    nodeGroup.append("circle")
      .attr("r", 4)
      .attr("cx", 18)
      .attr("cy", -18)
      .attr("fill", d => isNodeAnomalous(d.id) ? "var(--color-amber)" : "var(--color-green)")
      .style("filter", d => isNodeAnomalous(d.id) ? "url(#glowOrange)" : "url(#glowCyan)");

    // Label
    nodeGroup.append("text")
      .attr("dy", 40)
      .attr("text-anchor", "middle")
      .attr("fill", "var(--text-secondary)")
      .attr("font-size", "11px")
      .text(d => {
        // Extract label from something like "XMV(10) Valve" -> "Valve"
        const label = d.label || d.id;
        const match = label.match(/\((.*?)\)\s*(.*)/);
        if (match) return match[2];
        return label;
      });

    nodeGroup.append("text")
      .attr("dy", 55)
      .attr("text-anchor", "middle")
      .attr("fill", "var(--text-primary)")
      .attr("font-size", "12px")
      .text(d => {
        const label = d.label || d.id;
        const match = label.match(/^(.*?)\(/);
        if (match) return match[1];
        if (label.includes("R101")) return "R101";
        if (label.includes("V102")) return "V102";
        return label.substring(0, 8);
      });

    // Icons inside SVG using a foreignObject (React Icons are hard to map directly to d3 SVG without this)
    nodeGroup.append("foreignObject")
      .attr("width", 24)
      .attr("height", 24)
      .attr("x", -12)
      .attr("y", -12)
      .each(function(d) {
        // Render react icon
        let Icon = Settings2;
        if (d.label?.toLowerCase().includes("reactor") || d.id === 'r101') Icon = Box;
        if (d.label?.toLowerCase().includes("pump") || d.id === 'rx103') Icon = Droplets;
        if (d.label?.toLowerCase().includes("heat") || d.id === 'hx104') Icon = Database;

        const color = isNodeAnomalous(d.id) ? "var(--color-amber)" : "var(--color-cyan)";
        
        // Since d3 manipulates the DOM outside React's lifecycle, we use a micro-root
        const root = createRoot(this);
        root.render(<Icon color={color} size={24} />);
      });

    simulation.on("tick", () => {
      // Keep nodes within bounds
      nodeGroup.attr("transform", d => {
        d.x = Math.max(25, Math.min(width - 25, d.x));
        d.y = Math.max(25, Math.min(height - 60, d.y)); // extra padding for bottom label
        return `translate(${d.x},${d.y})`;
      });

      link
        .attr("x1", d => d.source.x)
        .attr("y1", d => d.source.y)
        .attr("x2", d => d.target.x)
        .attr("y2", d => d.target.y);
    });

    function dragstarted(event) {
      if (!event.active) simulation.alphaTarget(0.3).restart();
      event.subject.fx = event.subject.x;
      event.subject.fy = event.subject.y;
    }
    
    function dragged(event) {
      event.subject.fx = event.x;
      event.subject.fy = event.y;
    }
    
    function dragended(event) {
      if (!event.active) simulation.alphaTarget(0);
      event.subject.fx = null;
      event.subject.fy = null;
    }

    return () => {
      simulation.stop();
    };
  }, [graphData, anomalyPath]);

  return (
    <div className="panel graph-panel">
      <div style={{ position: 'absolute', top: '16px', left: '16px', zIndex: 10 }}>
        <h2 style={{ fontSize: '16px', fontWeight: 500, color: 'var(--text-primary)' }}>Interactive 2D Causal Network</h2>
        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>Sensor line of normal causal relationships</div>
      </div>
      
      {anomalyPath && anomalyPath.length > 0 && (
        <div style={{ position: 'absolute', top: '16px', right: '16px', zIndex: 10, textAlign: 'right' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--color-amber)', fontSize: '13px' }}>
            <Activity size={16} /> <span>Flow Failure detected;</span>
          </div>
          <div style={{ color: 'var(--color-red)', fontSize: '12px', marginTop: '4px', opacity: 0.8 }}>
            {anomalyPath.map(p => p.shortLabel).join(" > ")}
          </div>
        </div>
      )}
      
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
    </div>
  );
}
