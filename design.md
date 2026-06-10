# Clasp Dashboard Design System

This document defines the user interface specifications, typography, layout, and color palette for the **Clasp Industrial Gateway Dashboard**, optimized for high-contrast visibility and a professional, minimal aesthetic.

---

## 1. Typography & Theme

We use a high-contrast, technical design inspired by high-end aviation panels and developer consoles. 

* **Primary UI Font:** Apple SF Pro (`SF Pro Display`, `SF Pro Text`, `-apple-system`, `BlinkMacSystemFont`)
* **Technical/Console Font:** SF Mono (`SFMono-Regular`, `Consolas`, monospace)
* **General Style:** Flat, sharp, high-contrast, avoiding unnecessary gradients, glow, or glassmorphism.

---

## 2. Color Palette

The color system uses a pitch-black background and varying shades of gray to establish visual hierarchy, with clean, professional accents for functional indicators.

| Element | Hex Code | Visual Use |
| :--- | :--- | :--- |
| **Primary Background** | `#000000` | Entire application background |
| **Panel Background** | `#0C0C0C` | Dashboard container panels and cards |
| **Primary Text** | `#FFFFFF` | Headings, active values, and critical labels |
| **Secondary Text** | `#8E8E93` | Muted labels, units of measurement, timestamps |
| **Border / Grid lines** | `#2C2C2E` | 1px solid separator lines and panel borders |
| **Normal Causal Links** | `#2F80ED` | Steel Blue for normal telemetry routes and active states |
| **Anomaly / Warning** | `#FF9500` | Industrial Amber for safety alerts and propagation paths |
| **Critical Alarm** | `#FF3B30` | Red (used sparingly for emergency shutdowns only) |

---

## 3. Layout Grid

The interface is structured in a clean, non-overlapping grid to present real-time telemetry efficiently.

```
+-------------------------------------------------------------------------+
| [Clasp logo]  System Status: Online  (10,240 tags)    Active Leases: 0  |
+----------------------------------------+--------------------------------+
|                                        |                                |
|                                        |  Telemetry Trends              |
|  Causal Network Topology               |  - Pressures (SF Pro Line)     |
|  - Nodes: SF Pro                       |  - Flows (SF Pro Line)         |
|  - Links: Steel Blue (#2F80ED)         |  - Temperatures (SF Pro Line)  |
|  - Alerts: Amber Path (#FF9500)        |                                |
|                                        |                                |
+----------------------------------------+--------------------------------+
|  Root-Cause Investigation Console                                       |
|  - Monospaced white logs on black background (SF Mono)                  |
+-------------------------------------------------------------------------+
```

---

## 4. Key Panel Specifications

### A. Causal Graph Panel (Left)
* **Aesthetic:** Rendered on a pure black background. Node shapes (circles/boxes) are outlined in light gray (`#8E8E93`) with white labels. 
* **State Colors:**
  * Standard connections are drawn as solid Steel Blue (`#2F80ED`) lines.
  * Active anomalies/fault chains turn Industrial Amber (`#FF9500`), highlighting the exact path from downstream symptom to upstream cause.

### B. Telemetry Charts (Right)
* **Aesthetic:** Transparent grid lines (`#2C2C2E`) over a dark background.
* **Plot Lines:** Thin, crisp Steel Blue (`#2F80ED`) data lines with no fill area below the curve to maintain a minimal, uncluttered design.

### C. Agent Reasoning Console (Bottom)
* **Aesthetic:** A full-width terminal block styled in SF Mono font.
* **Colors:** Muted gray system notifications (`#8E8E93`), crisp white text for standard agent processes, and highlighted amber alerts for diagnostic conclusions.
