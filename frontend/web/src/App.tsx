import React from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import HomePage from './components/HomePage';
import SetupModeChat from './components/SetupModeChat';
import BCALanding from './components/BCALanding';
import BCAChat from './components/BCAChat';
import KonsultanPsikologiLanding from './components/KonsultanPsikologiLanding';
import KonsultanPsikologiChat from './components/KonsultanPsikologiChat';
import GenericTenantLanding from './components/GenericTenantLanding';
import GenericTenantChat from './components/GenericTenantChat';
import './App.css';

function App() {
  return (
    <Router>
      <div className="App">
        <Routes>
          {/* Home & Setup Mode */}
          <Route path="/" element={<HomePage />} />
          <Route path="/chat" element={<SetupModeChat />} />
          
          {/* Specific tenants (priority routes) */}
          <Route path="/bca" element={<BCALanding />} />
          <Route path="/bca/chat" element={<BCAChat />} />
          <Route path="/konsultanpsikologi" element={<KonsultanPsikologiLanding />} />
          <Route path="/konsultanpsikologi/chat" element={<KonsultanPsikologiChat />} />
          
          {/* Generic tenant routes (wildcard - must be last) */}
          <Route path="/:tenantId" element={<GenericTenantLanding />} />
          <Route path="/:tenantId/chat" element={<GenericTenantChat />} />
        </Routes>
      </div>
    </Router>
  );
}

export default App;
