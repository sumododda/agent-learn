import './globals.css';
import type { Metadata } from 'next';
import { AuthProvider } from '@/context/AuthContext';
import { AuthHeader } from '@/components/AuthHeader';

export const metadata: Metadata = {
  title: 'agent-learn',
  description: 'AI-powered personalized course generation',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-gray-950 text-gray-100 min-h-screen">
        <AuthProvider>
          <AuthHeader />
          <main className="max-w-7xl mx-auto px-6 py-8">
            {children}
          </main>
        </AuthProvider>
      </body>
    </html>
  );
}
