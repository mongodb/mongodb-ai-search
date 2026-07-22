import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Employee Support Copilot",
  description: "AI-powered employee support — IT Helpdesk + HR policies via SearchaaS",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
