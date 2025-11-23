import React, { createContext, useContext, useState, ReactNode } from 'react';

interface User {
  id: string;
  username: string;
  email: string;
  tenant_id: string;
}

interface AuthContextType {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<boolean>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

interface AuthProviderProps {
  children: ReactNode;
}

export const AuthProvider: React.FC<AuthProviderProps> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);

  const login = async (email: string, password: string): Promise<boolean> => {
    try {
      console.log('🔐 Attempting login for:', email);
      
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });

      if (response.ok) {
        const data = await response.json();
        console.log('✅ Login successful:', data);
        
        // Simple mock user data for initial testing
        const mockUser: User = {
          id: 'user_123',
          username: email.split('@')[0],
          email: email,
          tenant_id: 'konsultanpsikologi' // Keep existing tenant for compatibility
        };
        
        setUser(mockUser);
        setToken('mock_token_' + Date.now());
        
        return true;
      } else {
        console.error('❌ Login failed:', response.status);
        return false;
      }
    } catch (error) {
      console.error('💥 Login error:', error);
      return false;
    }
  };

  const logout = () => {
    console.log('👋 Logging out');
    setUser(null);
    setToken(null);
  };

  return (
    <AuthContext.Provider value={{
      user,
      token,
      isAuthenticated: !!user && !!token,
      login,
      logout,
    }}>
      {children}
    </AuthContext.Provider>
  );
};
