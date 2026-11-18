"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/", label: "홈" },
  { href: "/me", label: "마이페이지" },
];

export default function BottomNav() {
  const pathname = usePathname();
  return (
    <nav
      aria-label="주요 메뉴"
      className="fixed inset-x-0 bottom-0 mx-auto flex max-w-md border-t border-black/10 bg-background"
    >
      {TABS.map((tab) => {
        const active = tab.href === "/" ? pathname === "/" : pathname.startsWith(tab.href);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={active ? "page" : undefined}
            className={`flex-1 py-3 text-center text-sm ${active ? "font-bold" : "opacity-60"}`}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
