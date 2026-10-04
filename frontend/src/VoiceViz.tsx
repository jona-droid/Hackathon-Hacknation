import { useEffect, useRef } from "react";

const BARS = 36;

/** Live waveform: the AI's voice (violet), the pilot's microphone (red), or a calm idle line. */
export function VoiceViz({ mode, getLevel }: { mode: "idle" | "speaking" | "listening"; getLevel: () => number }) {
  const bars = useRef<HTMLDivElement[]>([]);
  const history = useRef<number[]>(Array(BARS).fill(0));
  const getLevelRef = useRef(getLevel);
  getLevelRef.current = getLevel;
  const modeRef = useRef(mode);
  modeRef.current = mode;

  useEffect(() => {
    let raf = 0;
    let t = 0;
    const draw = () => {
      t += 1;
      const m = modeRef.current;
      const level = m === "idle" ? 0.04 + 0.03 * Math.sin(t / 18) : Math.max(0.05, getLevelRef.current());
      const h = history.current;
      h.shift();
      h.push(level);
      bars.current.forEach((el, i) => {
        if (!el) return;
        // mirror the newest samples around the centre for a symmetric waveform
        const dist = Math.abs(i - BARS / 2) / (BARS / 2);
        const v = h[Math.floor((1 - dist) * (BARS - 1))] ?? 0;
        const jitter = m === "idle" ? 0 : 0.25 * Math.random();
        el.style.height = `${Math.max(3, Math.min(38, (v + jitter * v) * 40))}px`;
      });
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div className={`voice-viz ${mode}`}>
      {Array.from({ length: BARS }, (_, i) => (
        <div key={i} className="viz-bar" ref={(el) => { if (el) bars.current[i] = el; }} />
      ))}
    </div>
  );
}
