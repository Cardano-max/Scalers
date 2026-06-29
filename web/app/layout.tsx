import type { Metadata } from 'next';
import { hankenGrotesk, ibmPlexMono } from '@/lib/fonts';
import { Providers } from './providers';
import './globals.css';

export const metadata: Metadata = {
  title: 'Scalers · Operator Console',
  description:
    'Supervise the autonomous marketing harness — review escalations, browse auto-executed work, watch the live decision feed, inspect runs, and steer via command.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${hankenGrotesk.variable} ${ibmPlexMono.variable}`}>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
