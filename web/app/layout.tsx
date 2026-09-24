import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "XC Data — NVJCYO Cross Country",
  description: "Cross-country race results, analytics, and administration.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-bg font-sans text-fg antialiased">
        {children}
      </body>
    </html>
  );
}
