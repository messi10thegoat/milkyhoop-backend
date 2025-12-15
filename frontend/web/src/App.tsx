import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import HomePage from './components/HomePage';
import SetupModeChat from './components/SetupModeChat';
import BCALanding from './components/BCALanding';
import BCAChat from './components/BCAChat';
import KonsultanPsikologiLanding from './components/KonsultanPsikologiLanding';
import KonsultanPsikologiChat from './components/KonsultanPsikologiChat';
import GenericTenantLanding from './components/GenericTenantLanding';
import GenericTenantChat from './components/GenericTenantChat';
import QRLoginPage from './components/QRLoginPage';
import { isDesktopBrowser } from './utils/device';
import { isAuthenticated } from './utils/auth';
import './App.css';

// Desktop login guard - redirects desktop users to QR login if not authenticated
const DesktopLoginGuard: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  // Only enforce QR login for desktop browsers
  if (isDesktopBrowser() && !isAuthenticated()) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
};

// Login page guard - redirects authenticated users away from login
const LoginPageGuard: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  // If already authenticated, redirect to main app
  if (isAuthenticated()) {
    return <Navigate to="/" replace />;
  }
  // Only show QR login on desktop
  if (!isDesktopBrowser()) {
    return <Navigate to="/" replace />;
  }
  return <>{children}</>;
};

// Home route - shows dashboard for authenticated desktop users, landing for others
const HomeRoute: React.FC = () => {
  // Authenticated desktop users see dashboard directly
  if (isDesktopBrowser() && isAuthenticated()) {
    return <SetupModeChat />;
  }
  // Non-authenticated desktop users get redirected to login
  if (isDesktopBrowser() && !isAuthenticated()) {
    return <Navigate to="/login" replace />;
  }
  // Mobile users see landing page
  return <HomePage />;
};

function App() {
  return (
    <Router>
      <div className="App">
        <Routes>
          {/* QR Login Page (desktop only) */}
          <Route path="/login" element={
            <LoginPageGuard>
              <QRLoginPage />
            </LoginPageGuard>
          } />

          {/* Home - shows dashboard for authenticated desktop, landing for others */}
          <Route path="/" element={<HomeRoute />} />
          <Route path="/chat" element={
            <DesktopLoginGuard>
              <SetupModeChat />
            </DesktopLoginGuard>
          } />

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
