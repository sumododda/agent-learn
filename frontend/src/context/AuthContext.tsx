'use client';

import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || '';

interface AuthContextValue {
  getToken: () => Promise<string | null>;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, turnstileToken: string) => Promise<{ email: string; message: string }>;
  verifyOtp: (email: string, otp: string) => Promise<void>;
  resendOtp: (email: string) => Promise<{ message: string }>;
  requestPasswordReset: (email: string) => Promise<{ message: string }>;
  confirmPasswordReset: (email: string, otp: string, newPassword: string) => Promise<{ message: string }>;
  logout: () => void;
  isSignedIn: boolean;
  isLoaded: boolean;
  providerKeysLoaded: number;
  userEmail: string | null;
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
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [isLoaded, setIsLoaded] = useState(false);
  const [providerKeysLoaded, setProviderKeysLoaded] = useState(0);
  const [userEmail, setUserEmail] = useState<string | null>(null);

  useEffect(() => {
    const stored = localStorage.getItem('token');
    if (stored && !isTokenExpired(stored)) {
      setToken(stored);
      setUserEmail(localStorage.getItem('userEmail'));
    } else if (stored) {
      localStorage.removeItem('token');
      localStorage.removeItem('userEmail');
    }
    setIsLoaded(true);
  }, []);

  const getToken = useCallback(async (): Promise<string | null> => {
    let resolvedToken = token;

    // On a hard refresh, route effects can run before the hydration effect above
    // has copied the token from localStorage into React state.
    if (!resolvedToken && typeof window !== 'undefined') {
      const stored = localStorage.getItem('token');
      if (stored && !isTokenExpired(stored)) {
        resolvedToken = stored;
        setToken(stored);
        setUserEmail(localStorage.getItem('userEmail'));
      }
    }

    if (!resolvedToken) return null;
    if (isTokenExpired(resolvedToken)) {
      localStorage.removeItem('token');
      localStorage.removeItem('userEmail');
      setToken(null);
      setUserEmail(null);
      return null;
    }
    return resolvedToken;
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
    const data: { token: string; user_id: string; provider_keys_loaded?: number } = await res.json();
    localStorage.setItem('token', data.token);
    localStorage.setItem('userEmail', email);
    setToken(data.token);
    setUserEmail(email);
    if (typeof data.provider_keys_loaded === 'number') {
      setProviderKeysLoaded(data.provider_keys_loaded);
    }
  }, []);

  const register = useCallback(async (email: string, password: string, turnstileToken: string): Promise<{ email: string; message: string }> => {
    const res = await fetch(`${API_BASE}/api/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, turnstile_token: turnstileToken }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Registration failed' }));
      const detail = err.detail;
      const message = Array.isArray(detail) ? detail.map((e: { msg?: string }) => e.msg).join(', ') : detail || 'Registration failed';
      throw new Error(message);
    }
    const data: { email: string; message: string } = await res.json();
    return { email: data.email, message: data.message };
  }, []);

  const verifyOtp = useCallback(async (email: string, otp: string): Promise<void> => {
    const res = await fetch(`${API_BASE}/api/auth/verify-otp`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, otp }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Verification failed' }));
      throw new Error(err.detail || 'Verification failed');
    }
    const data: { token: string; user_id: string; provider_keys_loaded?: number } = await res.json();
    localStorage.setItem('token', data.token);
    localStorage.setItem('userEmail', email);
    setToken(data.token);
    setUserEmail(email);
    if (typeof data.provider_keys_loaded === 'number') {
      setProviderKeysLoaded(data.provider_keys_loaded);
    }
  }, []);

  const resendOtp = useCallback(async (email: string): Promise<{ message: string }> => {
    const res = await fetch(`${API_BASE}/api/auth/resend-otp`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Resend failed' }));
      throw new Error(err.detail || 'Resend failed');
    }
    return res.json();
  }, []);

  const requestPasswordReset = useCallback(async (email: string): Promise<{ message: string }> => {
    const res = await fetch(`${API_BASE}/api/auth/forgot-password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to send reset code' }));
      throw new Error(err.detail || 'Failed to send reset code');
    }
    return res.json();
  }, []);

  const confirmPasswordReset = useCallback(async (
    email: string,
    otp: string,
    newPassword: string,
  ): Promise<{ message: string }> => {
    const res = await fetch(`${API_BASE}/api/auth/forgot-password/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, otp, new_password: newPassword }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to reset password' }));
      throw new Error(err.detail || 'Failed to reset password');
    }
    return res.json();
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem('token');
    localStorage.removeItem('userEmail');
    setToken(null);
    setUserEmail(null);
    router.replace('/');
  }, [router]);

  const isSignedIn = token !== null && !isTokenExpired(token);

  return (
    <AuthContext.Provider
      value={{
        getToken,
        login,
        register,
        verifyOtp,
        resendOtp,
        requestPasswordReset,
        confirmPasswordReset,
        logout,
        isSignedIn,
        isLoaded,
        providerKeysLoaded,
        userEmail,
      }}
    >
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
