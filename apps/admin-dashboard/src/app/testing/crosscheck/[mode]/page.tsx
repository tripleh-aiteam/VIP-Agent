"use client";

// 🔀 Algorithm 4 (Cross-Check) — one mode per window, same pattern as Algorithm 3.
import { notFound } from "next/navigation";
import CrossCheckDesk, { type CCMode } from "../../crosscheck-desk";

export default function CrossCheckModePage({ params }: { params: { mode: string } }) {
  const { mode } = params;
  if (mode !== "auto" && mode !== "semi" && mode !== "manual") notFound();
  return <CrossCheckDesk mode={mode as CCMode} />;
}
