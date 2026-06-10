// Copyright (c) 2026 openyfai (YF)
// Licensed under the Business Source License 1.1 (BSL 1.1)
// See LICENSE file in the project root for full license terms.

import { AreaChart, Area, XAxis, YAxis, CartesianGrid, ResponsiveContainer, Tooltip } from 'recharts';

export function TelemetryCharts({ statusHistory }) {
  // Mock data for display based on the image style
  const generateMockData = (base, vol) => {
    return Array.from({ length: 60 }, (_, i) => ({
      time: i,
      val1: base + Math.sin(i * 0.5) * vol + Math.random() * (vol / 2),
      val2: base - Math.cos(i * 0.3) * vol + Math.random() * (vol / 2) - (i > 35 ? vol * 2 : 0) // simulate fault at min 35
    }));
  };

  const pressureData = generateMockData(25, 5);
  const flowData = generateMockData(90, 5);
  const tempData = Array.from({ length: 60 }, (_, i) => ({
    time: i,
    val1: i > 35 ? 160 + Math.random() * 10 : 25 + Math.random() * 5
  }));

  return (
    <>
      <div className="telemetry-card">
        <div className="panel-header">
          <span>Pressure (bar)</span>
          <div style={{ display: 'flex', gap: '12px', fontSize: '11px' }}>
            <span style={{ color: 'var(--color-cyan)' }}>— Reactor R101</span>
            <span style={{ color: 'var(--color-red)' }}>— Boiler B205</span>
          </div>
        </div>
        <div style={{ flex: 1, width: '100%', height: '100%' }}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={pressureData} margin={{ top: 5, right: 0, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="colorCyan" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--color-cyan)" stopOpacity={0.3}/>
                  <stop offset="95%" stopColor="var(--color-cyan)" stopOpacity={0}/>
                </linearGradient>
                <linearGradient id="colorRed" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--color-red)" stopOpacity={0.3}/>
                  <stop offset="95%" stopColor="var(--color-red)" stopOpacity={0}/>
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="time" tick={{fontSize: 10}} tickFormatter={(v) => v % 20 === 0 && v > 0 ? `${v}min` : ''} />
              <YAxis tick={{fontSize: 10}} domain={[0, 40]} />
              <Tooltip contentStyle={{backgroundColor: 'var(--bg-panel)', border: 'none', borderRadius: '4px'}} />
              <Area type="monotone" dataKey="val1" stroke="var(--color-cyan)" strokeWidth={2} fillOpacity={1} fill="url(#colorCyan)" />
              <Area type="monotone" dataKey="val2" stroke="var(--color-red)" strokeWidth={2} fillOpacity={1} fill="url(#colorRed)" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="telemetry-card">
        <div className="panel-header">
          <span>Flow Rate (m³/h)</span>
          <div style={{ display: 'flex', gap: '12px', fontSize: '11px' }}>
            <span style={{ color: 'var(--color-red)' }}>— V102</span>
            <span style={{ color: 'var(--text-secondary)' }}>— Target</span>
          </div>
        </div>
        <div style={{ flex: 1, width: '100%', height: '100%' }}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={flowData} margin={{ top: 5, right: 0, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="colorRedFlow" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--color-red)" stopOpacity={0.3}/>
                  <stop offset="95%" stopColor="var(--color-red)" stopOpacity={0}/>
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="time" tick={{fontSize: 10}} tickFormatter={(v) => v % 20 === 0 && v > 0 ? `${v}min` : ''} />
              <YAxis tick={{fontSize: 10}} domain={[0, 120]} />
              <Tooltip contentStyle={{backgroundColor: 'var(--bg-panel)', border: 'none', borderRadius: '4px'}} />
              <Area type="step" dataKey="val2" stroke="var(--color-red)" strokeWidth={2} fillOpacity={1} fill="url(#colorRedFlow)" />
              <Area type="step" dataKey="time" stroke="var(--text-secondary)" strokeWidth={1} fill="none" activeDot={false} strokeDasharray="5 5" yAxisId={0} data={flowData.map(d => ({...d, target: 100}))} dataKey="target" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="telemetry-card">
        <div className="panel-header">
          <span>Temperature (°C)</span>
          <div style={{ display: 'flex', gap: '12px', fontSize: '11px' }}>
            <span style={{ color: 'var(--color-amber)' }}>R101: 140°C ↑</span>
            <span style={{ color: 'var(--color-cyan)' }}>T1: 130°C ↑</span>
          </div>
        </div>
        <div style={{ flex: 1, width: '100%', height: '100%' }}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={tempData} margin={{ top: 5, right: 0, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="colorAmber" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--color-amber)" stopOpacity={0.3}/>
                  <stop offset="95%" stopColor="var(--color-amber)" stopOpacity={0}/>
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="time" tick={{fontSize: 10}} tickFormatter={(v) => v % 20 === 0 && v > 0 ? `${v}min` : ''} />
              <YAxis tick={{fontSize: 10}} domain={[0, 200]} />
              <Tooltip contentStyle={{backgroundColor: 'var(--bg-panel)', border: 'none', borderRadius: '4px'}} />
              <Area type="monotone" dataKey="val1" stroke="var(--color-amber)" strokeWidth={2} fillOpacity={1} fill="url(#colorAmber)" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>
    </>
  );
}
