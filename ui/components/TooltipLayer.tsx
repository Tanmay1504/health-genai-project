"use client";

// TooltipLayer: one tooltip for the whole app.
//
// Any element with a `data-tip="..."` attribute shows that text on hover or
// keyboard focus. Mounted once in layout.tsx.
//
// WHY one global layer instead of a CSS ::after bubble on each element?
// Tables sit inside `overflow-x: auto` wrappers, which clip anything that
// sticks out. This layer renders a single `position: fixed` bubble at the
// page level, so it is never clipped and long texts can wrap freely.
import { useEffect, useState } from "react";

type Tip = { text: string; x: number; y: number; below: boolean };

const findTip = (t: EventTarget | null) =>
  (t instanceof Element ? t.closest<HTMLElement>("[data-tip]") : null);

export default function TooltipLayer() {
  const [tip, setTip] = useState<Tip | null>(null);

  useEffect(() => {
    const show = (e: Event) => {
      const el = findTip(e.target);
      const text = el?.dataset.tip;
      if (!el || !text) return;
      const r = el.getBoundingClientRect();
      const below = r.top < 140; // not enough room above: show underneath
      const half = Math.min(170, window.innerWidth * 0.45);
      const x = Math.min(Math.max(r.left + r.width / 2, half + 8), window.innerWidth - half - 8);
      setTip({ text, x, y: below ? r.bottom + 8 : r.top - 8, below });
    };
    const hide = (e: Event) => {
      const from = findTip(e.target);
      const to = (e as MouseEvent).relatedTarget;
      if (from && to instanceof Node && from.contains(to)) return; // still inside the same element
      setTip(null);
    };
    const clear = () => setTip(null);
    document.addEventListener("mouseover", show);
    document.addEventListener("mouseout", hide);
    document.addEventListener("focusin", show);
    document.addEventListener("focusout", clear);
    window.addEventListener("scroll", clear, true);
    return () => {
      document.removeEventListener("mouseover", show);
      document.removeEventListener("mouseout", hide);
      document.removeEventListener("focusin", show);
      document.removeEventListener("focusout", clear);
      window.removeEventListener("scroll", clear, true);
    };
  }, []);

  if (!tip) return null;
  return (
    <div role="tooltip" className="tooltip"
      style={{ left: tip.x, top: tip.y, transform: `translate(-50%, ${tip.below ? "0" : "-100%"})` }}>
      {tip.text}
    </div>
  );
}
