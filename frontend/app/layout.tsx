import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ResearchLab",
  description: "A personal, evidence-grounded academic research environment.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-GB">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
