import { useQuickAccessVisible } from "@decky/api";
import { useEffect, useState } from "react";
import { getGameFrametime } from "../api/lsfgApi";
import t from "../i18n/i18n";

const WINDOW_MS = 15_000;
const SAMPLE_INTERVAL_MS = 500;
const WIDTH = 240;
const HEIGHT = 28;

type Sample = { time: number; frametime: number | null };

export function NowPlayingPerformance({ appId }: { appId: string }) {
  const visible = useQuickAccessVisible();
  return visible ? <LiveFrametime key={appId} appId={appId} /> : null;
}

function LiveFrametime({ appId }: { appId: string }) {
  const [samples, setSamples] = useState<Sample[]>([]);

  useEffect(() => {
    if (!/^-?\d+$/.test(appId)) return;
    const parsed = Number(appId);
    if (!Number.isInteger(parsed) || parsed < -0x80000000 || parsed > 0xffffffff || parsed === 0) return;
    const id = parsed >>> 0;
    let stopped = false;
    let timer: number | undefined;
    const poll = async () => {
      const frametime = await getGameFrametime(id).catch(() => null);
      if (stopped) return;
      const time = performance.now();
      setSamples((previous) => [
        ...previous.filter((sample) => time - sample.time < WINDOW_MS),
        { time, frametime: frametime !== null && Number.isFinite(frametime) && frametime > 0 ? frametime : null },
      ]);
      timer = window.setTimeout(() => void poll(), SAMPLE_INTERVAL_MS);
    };
    void poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [appId]);

  const latest = samples[samples.length - 1];
  const frametime = latest?.frametime;
  const maximum = Math.max(66.67, ...samples.map((sample) => sample.frametime ?? 0));
  let hasPreviousPoint = false;
  const path = samples.map((sample) => {
    if (sample.frametime === null) {
      hasPreviousPoint = false;
      return "";
    }
    const x = 1 + (WIDTH - 2) * (1 - (latest.time - sample.time) / WINDOW_MS);
    const y = HEIGHT - 2 - ((HEIGHT - 4) * sample.frametime / maximum);
    const command = hasPreviousPoint ? "L" : "M";
    hasPreviousPoint = true;
    return `${command}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");

  return (
    <div title={t("NOW_PLAYING_GAMESCOPE_PERFORMANCE", "Gamescope performance")}>
      <div style={{ fontSize: "13px", lineHeight: "18px", fontVariantNumeric: "tabular-nums" }}>
        {frametime
          ? t("NOW_PLAYING_GAMESCOPE_SAMPLE", "{fps} FPS · {ms} ms frame time")
              .replace("{fps}", String(Math.round(1000 / frametime)))
              .replace("{ms}", frametime.toFixed(1))
          : t("NOW_PLAYING_GAMESCOPE_NO_SAMPLE", "No presentation sample available")}
      </div>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="none"
        style={{ width: "100%", height: HEIGHT, display: "block", background: "rgba(0, 0, 0, 0.2)" }}
        role="img"
        aria-label={t("NOW_PLAYING_FRAMETIME_HISTORY", "Frame time history over the last 15 seconds")}
      >
        <path d={path} fill="none" stroke="#72e079" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    </div>
  );
}
