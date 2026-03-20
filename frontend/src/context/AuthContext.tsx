'use client';

import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface AuthContextValue {
  getToken: () => Promise<string | null>;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => void;
  isSignedIn: boolean;
  isLoaded: boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function decodePayload(token: string): { sub?: string; exp?: number } | null {
  try {
    const base64 = token.split('.')[1];
    if (!base64) return null;
    const json = atob(base64.replace(/-/g, '+').replace(/_/g, '/'));
    return JSON.parse(json);
  } catch {
    return null;
  }
}

function isTokenExpired(token: string): boolean {
  const payload = decodePayload(token);
  if (!payload?.exp) return true;
  // Consider expired if within 30 seconds of expiry
  return payload.exp * 1000 <= Date.now() + 30_000;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [isLoaded, setIsLoaded] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem('token');
    if (stored && !isTokenExpired(stored)) {
      setToken(stored);
    } else if (stored) {
      localStorage.removeItem('token');
    }
    setIsLoaded(true);
  }, []);

  const getToken = useCallback(async (): Promise<string | null> => {
    if (!token) return null;
    if (isTokenExpired(token)) {
      localStorage.removeItem('token');
      setToken(null);
      return null;
    }
    return token;
  }, [token]);

  const login = useCallback(async (email: string, password: string): Promise<void> => {
    const res = await fetch(`${API_BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Login failed' }));
      throw new Error(err.detail || 'Login failed');
    }
    const data: { token: string; user_id: string } = await res.json();
    localStorage.setItem('token', data.token);
    setToken(data.token);
  }, []);

  const register = useCallback(async (email: string, password: string): Promise<void> => {
    const res = await fetch(`${API_BASE}/api/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Registration failed' }));
      throw new Error(err.detail || 'Registration failed');
    }
    const data: { token: string; user_id: string } = await res.json();
    localStorage.setItem('token', data.token);
    setToken(data.token);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem('token');
    setToken(null);
  }, []);

  const isSignedIn = token !== null && !isTokenExpired(token);

  return (
    <AuthContext.Provider value={{ getToken, login, register, logout, isSignedIn, isLoaded }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return ctx;
}
