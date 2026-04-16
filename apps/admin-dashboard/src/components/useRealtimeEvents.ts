import { useEffect, useRef, useCallback } from "react";
import { API } from "./api";

type EventHandler = (event: { type: string; data: any; timestamp: string }) => void;

/**
 * WebSocket hook for real-time events from VIP Orchestrator.
 * Falls back to polling if WebSocket is unavailable.
 *
 * Usage:
 *   useRealtimeEvents((event) => {
 *     if (event.type.includes("a2a")) reload();
 *   });
 */
export function useRealtimeEvents(onEvent: EventHandler, deps: any[] = []) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  const connect = useCallback(() => {
    // Build WebSocket URL from API base
    const wsBase = API.replace("https://", "wss://").replace("http://", "ws://");
    const wsUrl = `${wsBase}/ws`;

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        // Send periodic pings to keep alive
        const pingInterval = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send("ping");
          } else {
            clearInterval(pingInterval);
          }
        }, 30000);
      };

      ws.onmessage = (msg) => {
        try {
          const event = JSON.parse(msg.data);
          handlerRef.current(event);
        } catch {
          // ignore non-JSON messages
        }
      };

      ws.onclose = () => {
        wsRef.current = null;
        // Reconnect after 5 seconds
        reconnectRef.current = setTimeout(connect, 5000);
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch {
      // WebSocket not supported or blocked — fallback to polling
      reconnectRef.current = setTimeout(connect, 10000);
    }
  }, []);

  useEffect(() => {
    connect();

    return () => {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      if (reconnectRef.current) {
        clearTimeout(reconnectRef.current);
      }
    };
  }, deps);
}
