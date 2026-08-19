import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "StepWise — walking routes scored for your body",
  description:
    "Enter your age, sex, weight and address; get walking routes with real elevation, " +
    "honest time estimates and the health return, built on Overture Maps open data.",
};

export const viewport: Viewport = {
  themeColor: "#0a1d22",
  width: "device-width",
  initialScale: 1,
  // The map needs pinch-zoom; locking it would break the primary interaction.
  maximumScale: 5,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
