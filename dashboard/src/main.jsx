// Copyright (c) 2026 openyfai (YF)
// Licensed under the Business Source License 1.1 (BSL 1.1)
// See LICENSE file in the project root for full license terms.

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import './index.css'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
