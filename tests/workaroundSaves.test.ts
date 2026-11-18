import assert from "node:assert/strict";
import { test } from "node:test";
import { flushWorkaroundSaves, queueWorkaroundSave } from "../src/utils/workaroundSaves.ts";

test("leaving the slider still saves the last FPS cap", async () => {
  const writes: unknown[] = [];
  const save = async (changes: unknown) => { writes.push(changes); return true; };
  const first = queueWorkaroundSave("steam:100", "dxvkFrameRate", 29, save);
  const last = queueWorkaroundSave("steam:100", "dxvkFrameRate", 30, save);

  assert.equal(await first, true);
  assert.equal(await last, true);
  assert.deepEqual(writes, [{ dxvkFrameRate: 30 }]);
});

test("a toggle flushes the pending cap and saves in order after an active write", async () => {
  const writes: unknown[] = [];
  let finish!: (success: boolean) => void;
  const active = new Promise<boolean>((resolve) => { finish = resolve; });
  const key = "flatpak:org.example.Game";
  const first = queueWorkaroundSave(key, "disableSteamdeckMode", true, async (changes) => {
    writes.push(changes);
    return active;
  });
  const save = async (changes: unknown) => { writes.push(changes); return true; };
  const slider = queueWorkaroundSave(key, "dxvkFrameRate", 36, save);
  const toggle = queueWorkaroundSave(key, "disableHdr", false, save);
  await Promise.resolve();
  assert.deepEqual(writes, [{ disableSteamdeckMode: true }]);

  finish(true);
  assert.equal(await flushWorkaroundSaves(key), true);
  assert.deepEqual(await Promise.all([first, slider, toggle]), [true, true, true]);
  assert.deepEqual(writes, [
    { disableSteamdeckMode: true },
    { dxvkFrameRate: 36, disableHdr: false },
  ]);
});
