// Shown only in the static GitHub Pages build (NEXT_PUBLIC_DEMO=1). Tells
// visitors honestly that AI results were pre-generated, and where the code is.
import { DEMO } from "@/lib/api";

export default function DemoBanner() {
  if (!DEMO) return null;
  return (
    <div className="demo-banner">
      <b>Live demo</b> on synthetic data. Model scores, fraud rings and the Groq-written appeals and pre-auths were
      pre-generated with the real pipeline, so this site needs no backend.{" "}
      <a href="https://github.com/Tanmay1504/health-genai-project">Code &amp; run it fully live →</a>
    </div>
  );
}
