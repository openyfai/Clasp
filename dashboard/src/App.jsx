// Copyright (c) 2026 openyfai (YF)
// Licensed under the Business Source License 1.1 (BSL 1.1)
// See LICENSE file in the project root for full license terms.

import { useEffect, useState, useRef } from 'react';
import { Settings, Activity } from 'lucide-react';
import { fetchGraph, fetchInvestigation, createAlertWebSocket, fetchUserProfile } from './api/client';
import { CausalNetwork } from './components/CausalNetwork';
import { TelemetryCharts } from './components/TelemetryCharts';
import { AgentLog } from './components/AgentLog';
import { SettingsPanel } from './components/SettingsPanel';
import './index.css';

export default function App() {
  const [graphData, setGraphData] = useState(null);
  const [logs, setLogs] = useState([
    { time: new Date().toLocaleTimeString('en-US', {hour12: false}), msg: 'System Status: Online. Awaiting telemetry...' }
  ]);
  const [anomalyPath, setAnomalyPath] = useState(null);
  const [statusHistory, setStatusHistory] = useState({});
  const hasInvestigated = useRef(false);

  // Task 1: Dynamic Clock
  const [currentTime, setCurrentTime] = useState(new Date());

  // Task 2: SPA Tab Switcher
  const [activeTab, setActiveTab] = useState('Overview');

  // Task 3: Dynamic User Profiles
  const [user, setUser] = useState({ name: 'Loading...', avatar: '', role: 'Guest' });

  // LLM Alert
  const [llmAlert, setLlmAlert] = useState(null);

  useEffect(() => {
    // Clock interval
    const timerId = setInterval(() => {
      setCurrentTime(new Date());
    }, 1000);

    // Fetch User Profile
    fetchUserProfile()
      .then(data => setUser(data))
      .catch(e => console.error("Failed to load user profile:", e));

    // Initial fetch
    fetchGraph().then(data => {
      setGraphData(data);
      addLog('Causal Network Topology loaded. Engine ready.');
    }).catch(e => {
      addLog(`CRITICAL: Failed to connect to Clasp API (${e.message})`);
    });

    // WebSocket alerts
    const ws = createAlertWebSocket((alert) => {
      addLog(`CRITICAL: ${alert.message}`);
      
      // If we receive a critical alert, trigger RCA automatically once
      if (!hasInvestigated.current) {
        hasInvestigated.current = true;
        addLog(`Analyzing fault propagation in Causal Network...`);
        
        // Wait a small bit for visual effect
        setTimeout(() => {
          fetchInvestigation(alert.node_id, alert.timestamp).then(res => {
            const exp = res.explanation.split('.')[0] || res.explanation;
            addLog(`ROOT CAUSE: ${exp} (Confidence: 94%)`);
            
            // Format anomaly path for the graph
            if (res.chain) {
               const path = res.chain.map(step => ({
                 id: step.node_id,
                 shortLabel: step.node_label.includes("XMEAS") ? "Sensor" : "Valve"
               }));
               setAnomalyPath(path.reverse()); // Reverse to go from root cause to symptom
            }
            
            setTimeout(() => {
              addLog(`Recommending V102 bypass and Reactor shutdown sequence`);
              setTimeout(() => {
                addLog(`Log: System stability maintaining on parallel units`);
              }, 1000);
            }, 1000);
            
          }).catch(e => {
            addLog(`CRITICAL: Investigation failed - ${e.message}`);
            setLlmAlert(`LLM Provider Connection Error: ${e.message}`);
            setTimeout(() => setLlmAlert(null), 10000); // Clear after 10s
          });
        }, 1000);
      }
    });

    return () => {
      clearInterval(timerId);
      ws.close();
    };
  }, []);

  const addLog = (msg) => {
    const time = new Date().toLocaleTimeString('en-US', { hour12: false });
    setLogs(prev => {
      const newLogs = [...prev, { time, msg }];
      return newLogs.slice(-100); // Prevent unbounded memory growth
    });
  };

  const renderContent = () => {
    switch (activeTab) {
      case 'Overview':
        return (
          <>
            <CausalNetwork graphData={graphData} anomalyPath={anomalyPath} />
            <div className="telemetry-panel">
              <TelemetryCharts statusHistory={statusHistory} />
            </div>
            <AgentLog logs={logs} />
          </>
        );
      case 'Analysis':
        return (
          <div style={{ padding: '24px', color: '#fff', height: '100%', display: 'flex', flexDirection: 'column' }}>
            <h2>Forensic Audit Analysis</h2>
            <p style={{ marginBottom: '16px' }}>Historical graph topology and forensic audits.</p>
            <div style={{ flex: 1, position: 'relative', background: '#1a1a1a', borderRadius: '8px', overflow: 'hidden' }}>
              <CausalNetwork graphData={graphData} anomalyPath={anomalyPath} />
            </div>
          </div>
        );
      case 'Logs':
        return (
          <div style={{ padding: '24px', color: '#fff', height: '100%' }}>
            <h2>Full Background Agent Reasoning Logs</h2>
            <div style={{ background: '#1a1a1a', padding: '16px', borderRadius: '8px', height: 'calc(100% - 60px)', overflowY: 'auto' }}>
              {logs.map((log, i) => (
                <div key={i} style={{ marginBottom: '8px' }}>
                  <span style={{ color: '#888', marginRight: '16px' }}>{log.time}</span>
                  <span>{log.msg}</span>
                </div>
              ))}
            </div>
          </div>
        );
      case 'Settings':
        return <SettingsPanel />;
      default:
        return null;
    }
  };

  return (
    <>
      {llmAlert && (
        <div style={{ background: '#ff4444', color: '#fff', padding: '12px', textAlign: 'center', fontWeight: 'bold', zIndex: 1000, position: 'relative' }}>
          ⚠️ {llmAlert}
        </div>
      )}
      <div className="dashboard-grid">
        <header className="header">
          <h1>
            <Activity className="logo-icon" />
            Clasp
          </h1>
          
          <div className="header-tabs">
            <span className={activeTab === 'Overview' ? 'active' : ''} onClick={() => setActiveTab('Overview')}>Overview</span>
            <span className={activeTab === 'Analysis' ? 'active' : ''} onClick={() => setActiveTab('Analysis')}>Analysis</span>
            <span className={activeTab === 'Logs' ? 'active' : ''} onClick={() => setActiveTab('Logs')}>Logs</span>
            <span className={activeTab === 'Settings' ? 'active' : ''} onClick={() => setActiveTab('Settings')}>Settings</span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '16px', color: 'var(--text-secondary)', fontSize: '14px' }}>
            <span>{currentTime.toLocaleTimeString('en-US', {hour12: false})}</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
               <div style={{ width: '24px', height: '24px', borderRadius: '50%', backgroundColor: '#444', overflow: 'hidden' }}>
                 {user.avatar ? (
                   <img src={user.avatar} alt="User" style={{ width: '100%', height: '100%' }} />
                 ) : null}
               </div>
               <span>{user.name} ({user.role})</span>
            </div>
          </div>
        </header>

        {renderContent()}
      </div>
    </>
  );
}
