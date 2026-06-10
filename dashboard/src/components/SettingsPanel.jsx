// Copyright (c) 2026 openyfai (YF)
// Licensed under the Business Source License 1.1 (BSL 1.1)
// See LICENSE file in the project root for full license terms.

import { useState, useEffect } from 'react';
import { fetchSettings, updateSettings } from '../api/client';

export function SettingsPanel() {
  const [settings, setSettings] = useState({
    safe_mode: true,
    system_prompt: "",
    llm_provider: "",
    llm_model: ""
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [saveStatus, setSaveStatus] = useState("");

  useEffect(() => {
    fetchSettings()
      .then(data => {
        setSettings(data);
        setLoading(false);
      })
      .catch(e => {
        setError(e.message);
        setLoading(false);
      });
  }, []);

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;
    setSettings(prev => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value
    }));
  };

  const handleSave = async (e) => {
    e.preventDefault();
    setSaveStatus("Saving...");
    try {
      await updateSettings(settings);
      setSaveStatus("Saved successfully!");
      setTimeout(() => setSaveStatus(""), 3000);
    } catch (e) {
      setSaveStatus("Error saving.");
      setError(e.message);
    }
  };

  if (loading) return <div style={{ padding: '24px', color: '#fff' }}>Loading settings...</div>;
  if (error) return <div style={{ padding: '24px', color: '#ff4444' }}>Error: {error}</div>;

  return (
    <div style={{ padding: '24px', color: '#fff', height: '100%', overflowY: 'auto' }}>
      <h2>Settings Configuration</h2>
      <form onSubmit={handleSave} style={{ display: 'flex', flexDirection: 'column', gap: '20px', maxWidth: '600px', marginTop: '24px' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '12px', fontSize: '16px' }}>
            <input 
              type="checkbox" 
              name="safe_mode" 
              checked={settings.safe_mode} 
              onChange={handleChange} 
              style={{ width: '20px', height: '20px' }}
            />
            Enable Safe Mode (Prevent automated control changes)
          </label>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <label style={{ fontSize: '14px', color: '#ccc' }}>System Prompt</label>
          <textarea 
            name="system_prompt" 
            value={settings.system_prompt} 
            onChange={handleChange} 
            rows={4}
            style={{ padding: '12px', background: '#3f3f3f', border: '1px solid #555', color: '#fff', borderRadius: '4px', resize: 'vertical' }}
          />
        </div>

        <div style={{ display: 'flex', gap: '16px' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', flex: 1 }}>
            <label style={{ fontSize: '14px', color: '#ccc' }}>LLM Provider</label>
            <select 
              name="llm_provider" 
              value={settings.llm_provider} 
              onChange={handleChange} 
              style={{ padding: '12px', background: '#3f3f3f', border: '1px solid #555', color: '#fff', borderRadius: '4px' }}
            >
              <option value="gemini">Google Gemini</option>
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic</option>
              <option value="local">Local Model (Ollama/LM Studio)</option>
            </select>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', flex: 1 }}>
            <label style={{ fontSize: '14px', color: '#ccc' }}>LLM Model Name</label>
            <input 
              type="text" 
              name="llm_model" 
              value={settings.llm_model} 
              onChange={handleChange} 
              placeholder="e.g. gemini-2.0-flash"
              style={{ padding: '12px', background: '#3f3f3f', border: '1px solid #555', color: '#fff', borderRadius: '4px' }}
            />
          </div>
        </div>

        <div>
          <button type="submit" style={{ padding: '10px 24px', background: '#4a90e2', color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer', fontSize: '16px' }}>
            Save Settings
          </button>
          {saveStatus && <span style={{ marginLeft: '16px', color: saveStatus.includes('Error') ? '#ff4444' : '#4caf50' }}>{saveStatus}</span>}
        </div>
      </form>
    </div>
  );
}
