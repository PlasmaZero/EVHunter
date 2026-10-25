import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "EVHunter — Find your edge",
  description: "A private terminal for odds comparison, positive expected value, arbitrage, and disciplined bet tracking.",
  robots: { index: false, follow: false }
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
