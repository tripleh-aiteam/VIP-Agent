"use client";

import { useEffect, useRef, useState } from "react";

// ₩ price that flashes on change — red tick when it rises, blue when it falls
// (Korean convention), fading out over ~0.4s. Text color follows today's % change.
const RED = "#d32f2f";
const BLUE = "#1565c0";
const fmt = (n?: number | null) => (n == null ? "-" : Number(n).toLocaleString());

export default function FlashPrice({
  price, chg, className, style,
}: {
  price?: number | null;
  chg?: number | null;
  className?: string;
  style?: React.CSSProperties;
}) {
  const [flash, setFlash] = useState<0 | 1 | -1>(0);
  const last = useRef<number | null | undefined>(price);
  useEffect(() => {
    if (price != null && last.current != null && price !== last.current) {
      setFlash(price > last.current ? 1 : -1);
      const id = setTimeout(() => setFlash(0), 450);
      last.current = price;
      return () => clearTimeout(id);
    }
    last.current = price;
  }, [price]);
  const col = (chg ?? 0) >= 0 ? RED : BLUE;
  const bg = flash === 1 ? "rgba(211,47,47,0.22)" : flash === -1 ? "rgba(21,101,192,0.22)" : "transparent";
  return (
    <span className={className} style={{ color: col, background: bg, transition: "background 0.4s ease-out", borderRadius: 4, padding: "0 3px", ...style }}>
      ₩{fmt(price)}
    </span>
  );
}
