/**
 * Fonts via next/font (self-hosted at build; no runtime Google Fonts request).
 * Hanken Grotesk = UI/body; IBM Plex Mono = IDs, idempotency keys, timestamps,
 * metric values, UPPERCASE labels. Exposed as CSS variables consumed by
 * globals.css (`--font-hanken`, `--font-plex-mono`).
 */
import { Hanken_Grotesk, IBM_Plex_Mono } from 'next/font/google';

export const hankenGrotesk = Hanken_Grotesk({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-hanken',
  display: 'swap',
});

export const ibmPlexMono = IBM_Plex_Mono({
  subsets: ['latin'],
  weight: ['400', '500', '600'],
  variable: '--font-plex-mono',
  display: 'swap',
});
