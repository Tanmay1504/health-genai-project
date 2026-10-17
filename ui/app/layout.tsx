// Root layout: shared top bar + navigation around every page.
import type { Metadata } from "next";
import Nav from "@/components/Nav";
import TooltipLayer from "@/components/TooltipLayer";
import "./globals.css";

export const metadata: Metadata = {
  title: "HEALTH_GEN_AI — Claims Intelligence",
  description: "Pre-auth, fraud rings, denial prediction, auto-appeal and audit on synthetic data.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <div className="brand">
            HEALTH_GEN_AI<small>synthetic data only</small>
          </div>
          <Nav />
        </header>
        <main>{children}</main>
        <TooltipLayer />
      </body>
    </html>
  );
}
