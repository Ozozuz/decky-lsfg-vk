import { useQuickAccessVisible } from "@decky/api";
import { useEffect, useRef, useState } from "react";
import t from "../i18n/i18n";

const WINDOW_MS = 15_000;
const SAMPLE_INTERVAL_MS = 500;
const WIDTH = 260;
const HEIGHT = 28;

type Sample = { time: number; frametime: number | null };

interface Props {
  active: boolean;
  appId: string;
  readFrametime: (appId: string) => Promise<number | null>;
}

export function NowPlayingPerformance({ active, appId, readFrametime }: Props) {
  const visible = useQuickAccessVisible();
  const [samples, setSamples] = useState<Sample[]>([]);
  const chart = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    setSamples([]);
    if (!active || !visible) return;
    let stopped = false;
    let timer: number | undefined;
    const poll = async () => {
      const frametime = await readFrametime(appId).catch(() => null);
      if (stopped) return;
      const time = performance.now();
      setSamples((previous) => [
        ...previous.filter((sample) => time - sample.time <= WINDOW_MS),
        { time, frametime: frametime !== null && Number.isFinite(frametime) && frametime > 0 ? frametime : null },
      ]);
      timer = window.setTimeout(() => void poll(), SAMPLE_INTERVAL_MS);
    };
    void poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [active, appId, readFrametime, visible]);

  useEffect(() => {
    const context = chart.current?.getContext("2d");
    if (!context) return;
    context.clearRect(0, 0, WIDTH, HEIGHT);
    const latest = samples[samples.length - 1];
    if (!latest) return;
    const maximum = Math.max(50, ...samples.map(({ frametime }) => frametime ?? 0));
    context.strokeStyle = "#72e079";
    context.lineWidth = 1.5;
    context.lineCap = "round";
    context.beginPath();
    let connected = false;
    for (const { time, frametime } of samples) {
      if (frametime === null) {
        connected = false;
        continue;
      }
      const x = 1 + (WIDTH - 3) * (1 - (latest.time - time) / WINDOW_MS);
      const y = HEIGHT - 2 - (HEIGHT - 4) * frametime / maximum;
      if (connected) {
        context.lineTo(x, y);
      } else {
        context.moveTo(x, y);
        context.lineTo(x + 0.5, y);
      }
      connected = true;
    }
    context.stroke();
  }, [samples]);

  if (!active || !visible) return null;
  const frametime = samples[samples.length - 1]?.frametime;
  return (
    <div style={{ padding: "0 0 6px", fontSize: 13, lineHeight: "18px", fontVariantNumeric: "tabular-nums" }}>
      <div>
        {frametime
          ? t("PERFORMANCE_CURRENT_SAMPLE", "{fps} FPS · {ms} ms")
              .replace("{fps}", String(Math.round(1000 / frametime)))
              .replace("{ms}", frametime.toFixed(1))
          : t("PERFORMANCE_CURRENT_WAITING", "Waiting for Gamescope data…")}
      </div>
      <canvas
        ref={chart}
        width={WIDTH}
        height={HEIGHT}
        style={{ display: "block", width: "100%", height: HEIGHT, background: "rgba(0, 0, 0, 0.2)" }}
        role="img"
        aria-label={t("PERFORMANCE_CURRENT_HISTORY", "Frame time over the last 15 seconds")}
      />
    </div>
  );
}
