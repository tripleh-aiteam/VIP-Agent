// 모의투자 (Paper Trading) — each trading mode is its OWN page/window (boss 2026-07-09):
// /testing/semi (default) · /testing/manual · /testing/auto
import { redirect } from "next/navigation";

export default function TestingIndex() {
  redirect("/testing/semi");
}
