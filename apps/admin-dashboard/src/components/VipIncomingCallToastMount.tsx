"use client";

/**
 * VipIncomingCallToastMount — Next.js wrapper around the framework-agnostic
 * <IncomingCallToast /> from `@triple-h/chatbot/voice-ui`.
 *
 * The package's toast component doesn't know about Next.js (intentional —
 * Real Estate's frontend stack hasn't been picked). This thin wrapper:
 *
 *   1. Suppresses the toast on the /calls page itself (host owns pathnames)
 *   2. Wires the "Watch live →" button to Next.js router.push
 *   3. Simulates a mock incoming call 8 seconds after mount so the demo
 *      flow still works while the backend is mid-build
 *
 * Once `voice-client.ts` is wired (Step 16), the mock subscription gets
 * replaced with `subscribeToCalls(vipConfig, { onCallStarted: setCall })`.
 */

import { useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { IncomingCallToast } from "@triple-h/chatbot/voice-ui";
import type { CallEvent } from "@triple-h/chatbot/voice-ui";

export default function VipIncomingCallToastMount() {
  const router = useRouter();
  const pathname = usePathname();
  const [call, setCall] = useState<CallEvent | null>(null);

  // Mock: fake a call 8 seconds after mount so the demo flow still works
  // while the backend is mid-build. Replace with subscribeToCalls() in Step 16.
  useEffect(() => {
    if (pathname === "/calls") return;
    const timer = setTimeout(() => {
      setCall({
        id: "call_mock_toast",
        direction: "inbound",
        status: "active",
        urgency: "medium",
        caller: { number: "+82-10-5234-7891", name: "김민호" },
        startedAt: Date.now(),
        transcript: [],
      });
    }, 8000);
    return () => clearTimeout(timer);
  }, [pathname]);

  return (
    <IncomingCallToast
      call={call}
      suppressed={pathname === "/calls"}
      onWatchLive={() => router.push("/calls?tab=live")}
    />
  );
}
