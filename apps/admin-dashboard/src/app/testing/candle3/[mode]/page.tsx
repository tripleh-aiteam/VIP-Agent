"use client";

// 🕯️ Algorithm 3 (candle trader) — one mode per window, same pattern as Algorithm 2.
import { notFound } from "next/navigation";
import Candle3Desk, { type C3Mode } from "../../candle3-desk";

export default function Candle3ModePage({ params }: { params: { mode: string } }) {
  const { mode } = params;
  if (mode !== "auto" && mode !== "semi" && mode !== "manual") notFound();
  return <Candle3Desk mode={mode as C3Mode} />;
}
