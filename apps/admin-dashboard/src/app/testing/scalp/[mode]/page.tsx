"use client";

// ⚡ Algorithm 2 (ripple scalper) — one mode per window, same pattern as Algorithm 1:
// /testing/scalp/auto = the machine ripples · /testing/scalp/manual = 호가창 + his hands
import { notFound } from "next/navigation";
import ScalpDesk, { type ScalpMode } from "../../scalp-desk";

export default function ScalpModePage({ params }: { params: { mode: string } }) {
  const { mode } = params;
  if (mode !== "auto" && mode !== "semi" && mode !== "manual") notFound();
  return <ScalpDesk mode={mode as ScalpMode} />;
}
