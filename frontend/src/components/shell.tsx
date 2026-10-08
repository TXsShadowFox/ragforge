"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { api } from "@/lib/api-client";
import type { Me } from "@/lib/types";

const LINKS = [
  { href: "/documents", label: "Documents" },
  { href: "/playground", label: "Playground" },
  { href: "/api-keys", label: "API keys" },
  { href: "/analytics", label: "Analytics" },
];

/** The frame of every dashboard page: navigation, who is logged in, and "Log out". */
export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    let active = true;
    api<Me>("/me").then(
      (data) => active && setMe(data),
      () => undefined, // a lost login sends the browser to /login (see api())
    );
    return () => {
      active = false;
    };
  }, []);

  async function logOut() {
    await fetch("/api/session", { method: "DELETE" });
    // A full page load, not router.push(): it clears every bit of this user's data from
    // memory (Next.js keeps visited pages alive in the background for the Back button).
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign("/login");
  }

  return (
    <div className="min-h-screen">
      <header className="border-b border-gray-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <Link href="/documents" className="text-lg font-semibold text-indigo-600">
            RAGForge
          </Link>
          <nav aria-label="Main" className="flex flex-wrap gap-1">
            {LINKS.map((link) => {
              const current = pathname === link.href;
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  aria-current={current ? "page" : undefined}
                  className={`rounded-lg px-3 py-1.5 text-sm font-medium ${
                    current ? "bg-indigo-50 text-indigo-700" : "text-gray-600 hover:bg-gray-100"
                  }`}
                >
                  {link.label}
                </Link>
              );
            })}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm text-gray-600">
            {me && (
              <span className="hidden md:inline">
                {me.tenant_name} &middot; {me.email}
                <span className="ml-2 rounded-full bg-gray-100 px-2 py-0.5 text-xs uppercase">
                  {me.plan}
                </span>
              </span>
            )}
            <button
              type="button"
              onClick={logOut}
              className="rounded-lg px-3 py-1.5 font-medium text-gray-600 hover:bg-gray-100"
            >
              Log out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
    </div>
  );
}
