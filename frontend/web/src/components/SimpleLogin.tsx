import React, { useState } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { useNavigate } from 'react-router-dom';

const SimpleLogin: React.FC = () => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState('');
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setMessage('Testing login...');
    
    const success = await login(email, password);
    
    if (success) {
      setMessage('✅ Login successful! Redirecting...');
      setTimeout(() => navigate('/chat'), 1000);
    } else {
      setMessage('❌ Login failed. Check backend logs.');
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="max-w-md w-full space-y-8 p-6">
        <h2 className="text-2xl font-bold text-center">Test Login</h2>
        
        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded"
            required
          />
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded"
            required
          />
          <button
            type="submit"
            className="w-full bg-blue-600 text-white py-2 rounded hover:bg-blue-700"
          >
            Test Login
          </button>
        </form>
        
        {message && (
          <div className="text-center text-sm p-2 bg-gray-100 rounded">
            {message}
          </div>
        )}
        
        <div className="text-xs text-gray-500 text-center">
          Testing Phase 3 Authentication Integration
        </div>
      </div>
    </div>
  );
};

export default SimpleLogin;
