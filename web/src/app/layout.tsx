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
      <head>
        {/*
          The map starts fetching tiles the moment MapLibre initialises, and the
          API is called on first interaction. Opening the TCP and TLS
          connections during initial parse takes that handshake off the critical
          path — worth roughly a round trip each on a cold connection.

          The API host is not known at build time (it is written into
          config.json at deploy), so this preconnects to the API Gateway
          endpoint pattern for the deployment region.
        */}
        <link rel="preconnect" href="https://tile.openstreetmap.org" crossOrigin="anonymous" />
        <link rel="dns-prefetch" href="https://tile.openstreetmap.org" />
        <link rel="dns-prefetch" href="https://execute-api.us-east-1.amazonaws.com" />
      </head>
      <body className="antialiased">{children}</body>
    </html>
  );
}
