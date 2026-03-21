'use client';

import { useState, useRef, useEffect, useCallback, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { useAuth } from '@/context/AuthContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

function VerifyForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const email = searchParams.get('email');
  const { verifyOtp, resendOtp } = useAuth();

  const [otp, setOtp] = useState<string[]>(Array(6).fill(''));
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [resendCooldown, setResendCooldown] = useState(0);
  const [shake, setShake] = useState(false);
  const inputRefs = useRef<(HTMLInputElement | null)[]>([]);

  useEffect(() => {
    if (!email) {
      router.push('/register');
    }
  }, [email, router]);

  useEffect(() => {
    if (resendCooldown <= 0) return;
    const timer = setTimeout(() => setResendCooldown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [resendCooldown]);

  const handleSubmit = useCallback(async (code: string) => {
    if (!email || code.length !== 6) return;
    setError(null);
    setLoading(true);
    try {
      await verifyOtp(email, code);
      router.push('/');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Verification failed';
      if (message.includes('expired') || message.includes('not found')) {
        router.push('/register');
        return;
      }
      setError(message);
      setShake(true);
      setTimeout(() => setShake(false), 500);
      setOtp(Array(6).fill(''));
      inputRefs.current[0]?.focus();
    } finally {
      setLoading(false);
    }
  }, [email, verifyOtp, router]);

  function handleChange(index: number, value: string) {
    if (!/^\d?$/.test(value)) return;
    const newOtp = [...otp];
    newOtp[index] = value;
    setOtp(newOtp);

    if (value && index < 5) {
      inputRefs.current[index + 1]?.focus();
    }

    const code = newOtp.join('');
    if (code.length === 6 && newOtp.every((d) => d !== '')) {
      handleSubmit(code);
    }
  }

  function handleKeyDown(index: number, e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Backspace' && !otp[index] && index > 0) {
      inputRefs.current[index - 1]?.focus();
    }
  }

  function handlePaste(e: React.ClipboardEvent) {
    e.preventDefault();
    const pasted = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, 6);
    if (pasted.length === 0) return;
    const newOtp = Array(6).fill('');
    for (let i = 0; i < pasted.length; i++) {
      newOtp[i] = pasted[i];
    }
    setOtp(newOtp);
    const nextIndex = Math.min(pasted.length, 5);
    inputRefs.current[nextIndex]?.focus();

    if (pasted.length === 6) {
      handleSubmit(pasted);
    }
  }

  async function handleResend() {
    if (!email || resendCooldown > 0) return;
    try {
      await resendOtp(email);
      setResendCooldown(30);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to resend code');
    }
  }

  if (!email) return null;

  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-4">
      <div className="text-sm font-semibold text-foreground mb-8">agent-learn</div>

      <Card className="w-full max-w-[380px]">
        <CardHeader>
          <CardTitle className="text-xl">Verify your email</CardTitle>
          <CardDescription>
            Enter the 6-digit code sent to <span className="text-foreground">{email}</span>
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {error && (
            <div className="text-sm text-destructive bg-destructive/10 border border-destructive/20 rounded-md px-3 py-2">
              {error}
            </div>
          )}

          <div
            className={`flex justify-center gap-3 ${shake ? 'animate-shake' : ''}`}
            onPaste={handlePaste}
          >
            {otp.map((digit, index) => (
              <Input
                key={index}
                ref={(el) => { inputRefs.current[index] = el; }}
                type="text"
                inputMode="numeric"
                maxLength={1}
                value={digit}
                onChange={(e) => handleChange(index, e.target.value)}
                onKeyDown={(e) => handleKeyDown(index, e)}
                disabled={loading}
                className="w-10 h-10 text-center text-lg p-0"
                autoFocus={index === 0}
              />
            ))}
          </div>

          {loading && (
            <p className="text-sm text-muted-foreground text-center">Verifying...</p>
          )}

          <div className="text-center">
            <Button
              variant="ghost"
              size="sm"
              onClick={handleResend}
              disabled={resendCooldown > 0}
            >
              {resendCooldown > 0 ? `Resend code in ${resendCooldown}s` : 'Resend code'}
            </Button>
          </div>

          <p className="text-center text-sm text-muted-foreground">
            Wrong email?{' '}
            <Link href="/register" className="text-primary hover:underline">
              Go back
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

export default function VerifyPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-muted-foreground">Loading...</p>
      </div>
    }>
      <VerifyForm />
    </Suspense>
  );
}
