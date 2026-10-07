import type { ReactNode } from "react";
import Link from "next/link";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/projects", label: "Projects" },
  { href: "/bundles", label: "Bundles" },
  { href: "/settings", label: "Settings" },
];

export function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen md:grid md:grid-cols-[240px_1fr]">
      <aside className="border-b border-line bg-[#ebe4d6] px-5 py-6 md:border-b-0 md:border-r">
        <Link href="/" className="block">
          <span className="font-serif text-2xl tracking-tight">Sparky_rATS</span>
          <span className="mt-1 block text-xs uppercase tracking-[0.16em] text-muted">
            Reverse ATS
          </span>
        </Link>
        <nav className="mt-8 flex gap-4 md:flex-col md:gap-2">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="text-sm text-ink underline-offset-4 hover:text-spark hover:underline"
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <p className="mt-8 hidden text-xs leading-5 text-muted md:block">
          One candidate, one machine. Nothing is sent on your behalf.
        </p>
      </aside>
      <main className="px-5 py-8 md:px-10">{children}</main>
    </div>
  );
}
