import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MachineRead - AI & Search Readiness Audit",
  description:
    "Audit public signals that help AI agents, retrieval systems, and search crawlers access and understand your site.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <main>{children}</main>
      </body>
    </html>
  );
}
