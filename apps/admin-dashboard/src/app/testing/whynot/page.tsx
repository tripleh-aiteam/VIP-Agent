"use client";
/* The gate-proof view lives INSIDE Menu 3 now (boss 2026-09-07: "the menu
   should be inside Menu 3 — please do not make separate"). This route only
   survives so old links and habits land in the right place. */
import { useEffect } from "react";

export default function WhyNotRedirect() {
  useEffect(() => {
    window.location.replace("/testing/approve#whynot");
  }, []);
  return (
    <div style={{ padding: 30, fontSize: 13, opacity: 0.7 }}>
      🚧 이 화면은 메뉴 3 안으로 들어갔습니다 — 이동 중… / moved inside Menu 3 — redirecting…
    </div>
  );
}
