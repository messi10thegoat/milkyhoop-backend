import React from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import './App.css';

// TODO: Extract these components from container build analysis
function HomePage() {
  return (
    <div className="home-page">
      <h1>MilkyHoop 3.0</h1>
      <p>Social AI Platform - Instagram for AI Chatbots</p>
    </div>
  );
}

function BCALanding() {
  return (
    <div className="bca-landing">
      <h1>BCA Customer Service</h1>
      <a href="/bca/chat">Start Chat</a>
    </div>
  );
}

function BCAChat() {
  // TODO: Implement BCA customer chat component
  return (
    <div className="bca-chat">
      <h1>BCA Customer Chat</h1>
      {/* Chat interface implementation */}
    </div>
  );
}

function SetupModeChat() {
  // TODO: Implement setup mode chat component
  return (
    <div className="setup-chat">
      <h1>Setup Mode - Business Owner Chat</h1>
      {/* Setup chat interface implementation */}
    </div>
  );
}

function App() {
  return (
    <Router>
      <div className="App">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/bca" element={<BCALanding />} />
          <Route path="/bca/chat" element={<BCAChat />} />
          <Route path="/chat" element={<SetupModeChat />} />
        </Routes>
      </div>
    </Router>
  );
}

export default App;
