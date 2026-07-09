"use client";

// One trading mode per window (boss): semi = engine predicts + HE decides (focus
// stocks only) · manual = quiet desk + focus popups · auto = the machine's control room.
import { notFound } from "next/navigation";
import Desk, { type TradeMode } from "../desk";

export default function TestingModePage({ params }: { params: { mode: string } }) {
  const { mode } = params;
  if (mode !== "semi" && mode !== "manual" && mode !== "auto") notFound();
  return <Desk mode={mode as TradeMode} />;
}
