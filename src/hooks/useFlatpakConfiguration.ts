import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  enableFlatpakApp,
  getFlatpakApps,
  getRunningFlatpakApps,
  removeFlatpakApp,
  setFlatpakWorkaroundState,
  updateFlatpakConfig,
  type FlatpakApp,
  type LsfgConfig,
  type RunningFlatpakApp,
  type WorkaroundState,
} from "../api/lsfgApi";
import { selectMostRecentRunningFlatpak } from "../utils/nowPlaying";
import { flushWorkaroundSaves, queueWorkaroundSave } from "../utils/workaroundSaves";
import { showErrorToast } from "../utils/toastUtils";

type FlatpakOperationResult = {
  success: boolean;
  error?: string | null;
  config?: LsfgConfig | null;
  state?: WorkaroundState | null;
};

export function useFlatpakConfiguration(enabled: boolean, runningShortcutAppId?: string) {
  const [apps, setApps] = useState<FlatpakApp[]>([]);
  const [runningApps, setRunningApps] = useState<RunningFlatpakApp[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyAppId, setBusyAppId] = useState("");
  const operations = useRef(Promise.resolve<FlatpakOperationResult>({ success: true }));

  const reload = useCallback(async () => {
    if (!enabled) {
      setApps([]);
      return;
    }
    setLoading(true);
    try {
      const result = await getFlatpakApps();
      if (!result.success) throw new Error(result.error || "Could not list Flatpak applications");
      setApps(result.apps || []);
    } catch (error) {
      showErrorToast("Flatpak unavailable", error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [enabled]);

  const pollRunning = useCallback(async () => {
    if (!enabled) {
      setRunningApps([]);
      return;
    }
    try {
      const result = await getRunningFlatpakApps();
      if (result.success) setRunningApps(result.apps || []);
    } catch {}
  }, [enabled]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    const poll = async () => {
      await pollRunning();
      if (enabled && !stopped) timer = window.setTimeout(() => void poll(), 2000);
    };
    void poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [enabled, pollRunning]);

  const operate = useCallback((
    appId: string,
    operation: () => Promise<FlatpakOperationResult>,
    refresh = true,
  ): Promise<FlatpakOperationResult> => {
    const next = operations.current.then(async () => {
      setBusyAppId(appId);
      try {
        const result = await operation();
        if (!result.success) throw new Error(result.error || "Flatpak operation failed");
        return result;
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        showErrorToast("Flatpak operation failed", message);
        return { success: false, error: message };
      } finally {
        if (refresh) {
          await reload();
          await pollRunning();
        }
        setBusyAppId("");
      }
    });
    operations.current = next;
    return next;
  }, [pollRunning, reload]);

  const enableApp = useCallback(async (appId: string) => (
    await operate(appId, () => enableFlatpakApp(appId))
  ).success, [operate]);

  const removeApp = useCallback(async (appId: string) => {
    await flushWorkaroundSaves("flatpak:" + appId);
    return (await operate(appId, () => removeFlatpakApp(appId))).success;
  }, [operate]);

  const enableAll = useCallback(async (): Promise<void> => {
    const available = apps.filter((app) => (
      !app.enabled && !(app.prepared && !app.owned) && !app.error
    ));
    if (!available.length) return;
    await operate(available[0].app_id, async () => {
      for (const app of available) {
        const result = await enableFlatpakApp(app.app_id);
        if (!result.success) return result;
      }
      return { success: true };
    });
  }, [apps, operate]);

  const removeAll = useCallback(async (): Promise<void> => {
    const configured = apps.filter((app) => app.enabled);
    if (!configured.length) return;
    await Promise.all(configured.map((app) => flushWorkaroundSaves("flatpak:" + app.app_id)));
    await operate(configured[0].app_id, async () => {
      for (const app of configured) {
        const result = await removeFlatpakApp(app.app_id);
        if (!result.success) return result;
      }
      return { success: true };
    });
  }, [apps, operate]);

  const updateConfig = useCallback(
    async (appId: string, changes: Partial<LsfgConfig>) => (
      await operate(appId, async () => {
        const result = await updateFlatpakConfig(appId, changes);
        const config = result.config;
        if (result.success && config) {
          setApps((current) => current.map((app) => (
            app.app_id === appId ? { ...app, config } : app
          )));
        }
        return result;
      }, false)
    ).success,
    [operate],
  );
  const updateWorkarounds = useCallback(
    (appId: string, field: keyof WorkaroundState, value: boolean | number) => (
      queueWorkaroundSave("flatpak:" + appId, field, value, async (changes) => (
        await operate(appId, async () => {
          const result = await setFlatpakWorkaroundState(appId, changes);
          const state = result.state;
          if (result.success && state) {
            setApps((current) => current.map((app) => (
              app.app_id === appId ? { ...app, workarounds: state } : app
            )));
          }
          return result;
        }, false)
      ).success)
    ),
    [operate],
  );

  const runningApp = useMemo(() => selectMostRecentRunningFlatpak(apps, runningApps, runningShortcutAppId), [apps, runningApps, runningShortcutAppId]);

  return {
    apps,
    runningApps,
    runningApp,
    loading,
    busyAppId,
    reload,
    enableApp,
    enableAll,
    removeApp,
    removeAll,
    updateConfig,
    updateWorkarounds,
  };
}
