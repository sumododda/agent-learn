'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { Eye, EyeOff } from 'lucide-react';
import { useAuth } from '@/context/AuthContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

type Step = 'request' | 'confirm';

export default function ForgotPasswordPage() {
  const router = useRouter();
  const { requestPasswordReset, confirmPasswordReset } = useAuth();

  const [step, setStep] = useState<Step>('request');
  const [email, setEmail] = useState('');
  const [otp, setOtp] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleRequest(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);
    setLoading(true);
    try {
      const data = await requestPasswordReset(email);
      setInfo(data.message);
      setStep('confirm');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send reset code');
    } finally {
      setLoading(false);
    }
  }

  async function handleConfirm(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);
    setLoading(true);
    try {
      const data = await confirmPasswordReset(email, otp, newPassword);
      router.push('/login?reset=success');
      setInfo(data.message);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reset password');
    } finally {
      setLoading(false);
    }
  }

  async function handleResend() {
    setError(null);
    setInfo(null);
    setLoading(true);
    try {
      const data = await requestPasswordReset(email);
      setInfo(data.message);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send reset code');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-4">
      <div className="text-sm font-semibold text-foreground mb-8">agent-learn</div>

      <Card className="w-full max-w-[380px]">
        <CardHeader>
          <CardTitle className="text-xl">Forgot password</CardTitle>
          <CardDescription>
            {step === 'request'
              ? 'We will email you a 6-digit reset code.'
              : 'Enter the reset code and choose a new password.'}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={step === 'request' ? handleRequest : handleConfirm} className="space-y-4">
            {info && (
              <div className="text-sm text-green-700 bg-green-500/10 border border-green-500/20 rounded-md px-3 py-2">
                {info}
              </div>
            )}

            {error && (
              <div className="text-sm text-destructive bg-destructive/10 border border-destructive/20 rounded-md px-3 py-2">
                {error}
              </div>
            )}

            <div className="space-y-1">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                disabled={loading || step === 'confirm'}
              />
            </div>

            {step === 'confirm' && (
              <>
                <div className="space-y-1">
                  <Label htmlFor="otp">Reset code</Label>
                  <Input
                    id="otp"
                    inputMode="numeric"
                    maxLength={6}
                    value={otp}
                    onChange={(e) => setOtp(e.target.value.replace(/\D/g, '').slice(0, 6))}
                    required
                  />
                </div>

                <div className="space-y-1">
                  <Label htmlFor="new-password">New password</Label>
                  <div className="relative">
                    <Input
                      id="new-password"
                      type={showPassword ? 'text' : 'password'}
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      required
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword(!showPassword)}
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                    >
                      {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    At least 8 characters with uppercase, lowercase, and a digit.
                  </p>
                </div>
              </>
            )}

            <Button type="submit" className="w-full" disabled={loading}>
              {loading
                ? step === 'request'
                  ? 'Sending code...'
                  : 'Resetting password...'
                : step === 'request'
                  ? 'Send reset code'
                  : 'Reset password'}
            </Button>

            {step === 'confirm' && (
              <Button type="button" variant="outline" className="w-full" onClick={handleResend} disabled={loading}>
                Send another code
              </Button>
            )}

            <p className="text-center text-sm text-muted-foreground">
              Remembered your password?{' '}
              <Link href="/login" className="text-primary hover:underline">
                Sign in
              </Link>
            </p>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
