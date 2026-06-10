// Copyright (c) 2026 openyfai (YF)
// Licensed under the Business Source License 1.1 (BSL 1.1)
// See LICENSE file in the project root for full license terms.

import { useEffect, useRef } from 'react';

export function AgentLog({ logs }) {
  const scrollRef = useRef(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs]);

  return (
    <div className="panel log-panel">
      <div className="panel-header" style={{ marginBottom: '8px', borderBottom: '1px solid var(--border-color)', paddingBottom: '8px' }}>
        <span>Agent Activity Log</span>
        <span style={{ color: 'var(--text-muted)' }}>Inter Mono</span>
      </div>
      <div ref={scrollRef} style={{ flex: 1, overflowY: 'auto' }}>
        {logs.map((log, i) => {
          // Format based on message type
          let colorClass = 'log-info';
          let prefix = '';
          if (log.msg.includes('CRITICAL:')) {
            colorClass = 'log-critical';
            prefix = 'CRITICAL: ';
          } else if (log.msg.includes('ROOT CAUSE:')) {
            colorClass = 'log-warning';
            prefix = 'ROOT CAUSE: ';
          } else if (log.msg.includes('Log:')) {
            colorClass = 'log-info';
            prefix = 'Log: ';
          }

          const cleanMsg = log.msg.replace(/^(CRITICAL:|ROOT CAUSE:|Log:)\s*/, '');

          return (
            <div key={i} className="log-line">
              <span className="log-timestamp">{log.time}</span>
              <span>- </span>
              {prefix && <span className={colorClass}>{prefix}</span>}
              <span style={{ color: 'var(--text-primary)' }}>{cleanMsg}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
