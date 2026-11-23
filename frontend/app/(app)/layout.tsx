"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { BookOpen, BrainCircuit, FileText, FlaskConical, FolderKanban, LayoutDashboard, Library, LogOut, Menu, Settings, UserRound } from "lucide-react";
import { api, useApi } from "@/lib/api";
import { Spinner } from "@/components/ui";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/projects", label: "Research Projects", icon: FolderKanban },
  { href: "/knowledge", label: "Knowledge Centre", icon: Library },
  { href: "/literature", label: "Literature Discovery", icon: BookOpen },
  { href: "/workspace", label: "Research Workspace", icon: BrainCircuit },
  { href: "/analysis", label: "Data Analysis", icon: FlaskConical },
  { href: "/manuscripts", label: "Manuscripts", icon: FileText },
  { href: "/profile", label: "Researcher Profile", icon: UserRound },
  { href: "/settings", label: "Settings", icon: Settings },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const { data, error } = useApi<{ user: { display_name: string; email: string } }>("/auth/me", { shouldRetryOnError: false });
  const [open, setOpen] = useState(false);

  if (error) {
    if (typeof window !== "undefined") router.replace("/login");
    return null;
  }
  if (!data) return <div className="flex h-screen items-center justify-center text-muted"><Spinner /></div>;

  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));
  const logout = async () => {
    await api("/auth/logout", { method: "POST" });
    router.replace("/login");
  };

  return (
    <div className="flex min-h-screen">
      <aside className={cn("fixed inset-y-0 left-0 z-40 w-60 shrink-0 border-r border-border bg-surface transition-transform md:sticky md:top-0 md:h-screen md:translate-x-0",
        open ? "translate-x-0" : "-translate-x-full")}>
        <div className="flex h-14 items-center px-4">
          <Link href="/" className="font-semibold tracking-tight">ResearchLab</Link>
        </div>
        <nav className="space-y-0.5 px-2">
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link key={href} href={href} onClick={() => setOpen(false)}
              className={cn("flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm", active(href) ? "bg-accent-soft text-accent font-medium" : "text-muted hover:bg-surface-2 hover:text-text")}>
              <Icon size={16} strokeWidth={1.75} /> {label}
            </Link>
          ))}
        </nav>
        <div className="absolute inset-x-0 bottom-0 border-t border-border p-3 text-xs">
          <div className="truncate font-medium">{data.user.display_name || data.user.email}</div>
          <button onClick={logout} className="mt-1 flex items-center gap-1.5 text-muted hover:text-text"><LogOut size={12} /> Sign out</button>
        </div>
      </aside>
      {open && <div className="fixed inset-0 z-30 bg-black/20 md:hidden" onClick={() => setOpen(false)} />}
      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-20 flex h-12 items-center border-b border-border bg-bg/90 px-4 backdrop-blur md:hidden">
          <button onClick={() => setOpen(true)} aria-label="Open navigation"><Menu size={18} /></button>
          <span className="ml-3 font-semibold">ResearchLab</span>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-6 md:px-8">{children}</main>
      </div>
    </div>
  );
}
