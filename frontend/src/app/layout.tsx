import type { Metadata } from "next";
import type { ReactNode } from "react";

import { Shell } from "@/components/Shell";

import "./globals.css";

export const metadata: Metadata = {
  title: "Sparky_rATS",
  description: "Local reverse ATS. Nothing leaves this machine on your behalf.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
