"use client";

// Top navigation. A client component only because it highlights the active
// route via usePathname().
import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Claims" },
  { href: "/preauth", label: "Pre-auth" },
  { href: "/fraud", label: "Fraud rings" },
  { href: "/denial", label: "Denial risk" },
  { href: "/appeal", label: "Appeal" },
  { href: "/audit", label: "Audit log" },
];

export default function Nav() {
  const path = usePathname();
  return (
    <nav className="nav">
      {LINKS.map((l) => (
        <Link key={l.href} href={l.href} className={path === l.href ? "active" : ""}>
          {l.label}
        </Link>
      ))}
    </nav>
  );
}
