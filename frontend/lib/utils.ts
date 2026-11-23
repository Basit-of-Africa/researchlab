import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmtDate(iso?: string | null) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function fmtDateTime(iso?: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function authorsShort(authors: (string | { name: string })[] | undefined, max = 3) {
  const names = (authors ?? []).map((a) => (typeof a === "string" ? a : a.name)).filter(Boolean);
  if (!names.length) return "Unknown author";
  return names.length > max ? `${names.slice(0, max).join(", ")} et al.` : names.join(", ");
}
