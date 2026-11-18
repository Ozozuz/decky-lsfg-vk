import type { WorkaroundState } from "../api/lsfgApi";

type Changes = Partial<WorkaroundState>;
type Save = (changes: Changes) => Promise<boolean>;

interface PendingSave {
  changes: Changes;
  save: Save;
  timer: ReturnType<typeof setTimeout>;
  result: Promise<boolean>;
  complete: (success: boolean) => void;
}

const pending = new Map<string, PendingSave>();
const writing = new Map<string, Promise<boolean>>();

export function flushWorkaroundSaves(key: string): Promise<boolean> {
  const next = pending.get(key);
  if (!next) return writing.get(key) ?? Promise.resolve(true);
  pending.delete(key);
  clearTimeout(next.timer);
  const result = (writing.get(key) ?? Promise.resolve(true))
    .then(() => next.save(next.changes))
    .catch(() => false);
  writing.set(key, result);
  void result.then((success) => {
    if (writing.get(key) === result) writing.delete(key);
    next.complete(success);
  });
  return result;
}

export function queueWorkaroundSave(
  key: string,
  field: keyof WorkaroundState,
  value: number | boolean,
  save: Save,
): Promise<boolean> {
  const next = pending.get(key);
  const changes = { ...next?.changes, [field]: value };
  if (next) {
    clearTimeout(next.timer);
    next.changes = changes;
    next.save = save;
    if (field === "dxvkFrameRate") {
      next.timer = setTimeout(() => void flushWorkaroundSaves(key), 250);
    } else {
      void flushWorkaroundSaves(key);
    }
    return next.result;
  }

  let complete!: (success: boolean) => void;
  const result = new Promise<boolean>((resolve) => { complete = resolve; });
  const timer = setTimeout(() => void flushWorkaroundSaves(key), field === "dxvkFrameRate" ? 250 : 0);
  pending.set(key, { changes, save, timer, result, complete });
  if (field !== "dxvkFrameRate") void flushWorkaroundSaves(key);
  return result;
}
