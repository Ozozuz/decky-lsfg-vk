import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuickAccessVisible } from "@decky/api";
import { Router } from "@decky/ui";
import { getGameConfigs, getInstalledGames, getWorkaroundApps, getWorkaroundState, removeWorkaroundState, resetGameConfig, resetGameConfigs, setWorkaroundState, updateGameConfig, updateGlobalConfig as saveGlobalConfig, type GameConfigEntry, type GlobalConfig, type InstalledGame, type WorkaroundApp, type WorkaroundState } from "../api/lsfgApi";
import { ConfigurationData, getDefaults } from "../config/configSchema";
import { getDefaultWrapperPath, installWrapperIntegration, removeWrapperIntegration } from "../utils/steamLaunchOptions";
import { getTargetSource, isBulkGameTarget, mergeGameTargets, type GameTarget, type KnownGameSource } from "../utils/gameTargets";
import { flushWorkaroundSaves } from "../utils/workaroundSaves";
import { showErrorToast } from "../utils/toastUtils";

export type { GameSource, GameTarget, KnownGameSource } from "../utils/gameTargets";

async function getSteamShortcuts(): Promise<InstalledGame[]> {
  const apps = (globalThis as any).SteamClient?.Apps;
  if (typeof apps?.GetAllShortcuts !== "function") return [];

  try {
    const shortcuts = await apps.GetAllShortcuts();
    if (!Array.isArray(shortcuts)) return [];
    return shortcuts.flatMap((shortcut: any) => {
      const appid = Number(shortcut?.appid);
      const name = shortcut?.data?.strAppName;
      if (!Number.isInteger(appid) || appid === 0 || typeof name !== "string" || !name) return [];
      return [{
        appid: String(appid >>> 0),
        name,
        nonSteam: true,
      }];
    });
  } catch {
    return [];
  }
}

function mergeInstalledGames(backendGames: InstalledGame[], shortcutGames: InstalledGame[]) {
  const games = new Map(backendGames.map((game) => [game.appid, game]));
  for (const game of shortcutGames) {
    const existing = games.get(game.appid);
    games.set(game.appid, existing ? { ...existing, name: game.name, nonSteam: true } : game);
  }
  return Array.from(games.values());
}

const DEFAULT_WORKAROUND_STATE: WorkaroundState = {
  dxvkFrameRate: 0,
  disableGamescopeWsi: true,
  disableHdr: true,
  disableSteamdeckMode: false,
  disableVkbasalt: false,
  enableZink: false,
};

function asError(error: unknown): Error {
  return error instanceof Error ? error : new Error(String(error));
}

export function useGameConfiguration() {
  const [games, setGames] = useState<GameConfigEntry[]>([]);
  const [globalConfig, setGlobalConfig] = useState<GlobalConfig>({ dll: "", no_fp16: false });
  const [installedGames, setInstalledGames] = useState<InstalledGame[]>([]);
  const [workaroundApps, setWorkaroundApps] = useState<WorkaroundApp[]>([]);
  const [configsLoaded, setConfigsLoaded] = useState(false);
  const [runningGame, setRunningGame] = useState<GameTarget | null>(null);
  const [bulkOperationBusy, setBulkOperationBusy] = useState(false);
  const bulkOperationLock = useRef(false);
  const profileOperations = useRef<Promise<unknown>>(Promise.resolve());
  const enqueueProfileOperation = useCallback(<T,>(operation: () => Promise<T>): Promise<T> => {
    const next = profileOperations.current.then(operation, operation);
    profileOperations.current = next;
    return next;
  }, []);
  const quickAccessVisible = useQuickAccessVisible();

  const load = useCallback(async () => {
    const [result, installed, shortcuts, workaroundResult] = await Promise.all([
      getGameConfigs(),
      getInstalledGames(),
      getSteamShortcuts(),
      getWorkaroundApps(),
    ]);
    if (result.success) {
      setGlobalConfig(result.global_config || { dll: "", no_fp16: false });
      setGames(result.games || []);
    }
    setInstalledGames(mergeInstalledGames(installed.success ? installed.games || [] : [], shortcuts));
    setWorkaroundApps(workaroundResult.success ? workaroundResult.apps || [] : []);
    setConfigsLoaded(true);
  }, []);

  useEffect(() => {
    if (quickAccessVisible) void load();
  }, [load, quickAccessVisible]);

  useEffect(() => {
    if (!quickAccessVisible || !configsLoaded) return;
    const poll = () => {
      const app = Router.MainRunningApp as any;
      if (!app?.appid) return setRunningGame(null);
      const appid = String(app.appid);
      const installed = installedGames.find((game) => game.appid === appid);
      const name = app.display_name || installed?.name;
      if (!name) return setRunningGame(null);
      const source = getTargetSource(appid, installedGames, workaroundApps);
      const next: GameTarget = {
        ...(installed || { appid, name, nonSteam: source === "nonSteam" }),
        name,
        nonSteam: source === "nonSteam",
        source,
        configured: games.some((game) => game.appid === appid),
      };
      setRunningGame((current) => (
        current?.appid === next.appid
        && current.name === next.name
        && current.nonSteam === next.nonSteam
        && current.source === next.source
        && current.configured === next.configured
        && current.isFlatpakShortcut === next.isFlatpakShortcut
        && current.flatpakAppId === next.flatpakAppId
          ? current
          : next
      ));
    };
    poll();
    const interval = window.setInterval(poll, 2000);
    return () => window.clearInterval(interval);
  }, [configsLoaded, games, installedGames, quickAccessVisible, workaroundApps]);

  const targets = useMemo<GameTarget[]>(() => {
    return mergeGameTargets(games, installedGames, workaroundApps, runningGame);
  }, [games, installedGames, runningGame, workaroundApps]);
  const template = useMemo(() => ({ ...getDefaults(), ...globalConfig }), [globalConfig]);
  const configFor = (appid: string) => games.find((game) => game.appid === appid)?.config || template;
  const runningConfig = runningGame ? configFor(runningGame.appid) : template;

  const ensureTargetWorkarounds = useCallback(async (target: GameTarget): Promise<boolean> => {
    if (target.source === "unknown") {
      showErrorToast("Could not initialize workarounds", "The target source is unknown; re-discover the game before enabling it");
      return false;
    }
    if (!installedGames.some((game) => game.appid === target.appid)) return true;
    const appId = Number(target.appid);
    let integration: Awaited<ReturnType<typeof installWrapperIntegration>> | null = null;
    let newState = false;
    let stateWriteAttempted = false;
    let wrapperPath = getDefaultWrapperPath();
    try {
      const existing = await getWorkaroundState(target.appid);
      if (!existing.success) throw new Error(existing.error || "Could not read workaround state");
      wrapperPath = existing.wrapper_path || getDefaultWrapperPath();
      const state = existing.state || { ...DEFAULT_WORKAROUND_STATE };
      const commandTokenAdded = existing.command_token_added === true;
      newState = !existing.state;
      integration = await installWrapperIntegration(
        appId,
        target.nonSteam,
        wrapperPath,
        commandTokenAdded,
      );
      stateWriteAttempted = true;
      const saved = await setWorkaroundState(
        target.appid,
        state,
        integration.commandTokenAdded,
        target.nonSteam,
        true,
      );
      if (!saved.success) throw new Error(saved.error || "Could not save workaround state");
      return true;
    } catch (error) {
      let rollbackSucceeded = true;
      if (integration?.changed) {
        try {
          await removeWrapperIntegration(
            appId,
            target.nonSteam,
            wrapperPath,
            integration.commandTokenAdded,
          );
        } catch (rollbackError) {
          showErrorToast("Workaround rollback failed", asError(rollbackError).message);
          rollbackSucceeded = false;
        }
      }
      if (rollbackSucceeded && newState && stateWriteAttempted) {
        const restored = await removeWorkaroundState(target.appid);
        if (!restored.success) {
          showErrorToast("Workaround rollback failed", restored.error || "Could not roll back workaround state");
          rollbackSucceeded = false;
        }
      }
      showErrorToast("Could not initialize workarounds", asError(error).message);
      return false;
    }
  }, [installedGames]);

  const removeTargetWorkarounds = useCallback(async (target: GameTarget): Promise<boolean> => {
    const installed = installedGames.some((game) => game.appid === target.appid);
    if (target.source === "unknown" && installed) return true;
    const appId = Number(target.appid);
    try {
      await flushWorkaroundSaves(`steam:${target.appid}`);
      const existing = await getWorkaroundState(target.appid);
      if (!existing.success) throw new Error(existing.error || "Could not read workaround state");
      const wrapperPath = existing.wrapper_path || getDefaultWrapperPath();
      if (installed) {
        await removeWrapperIntegration(
          appId,
          target.nonSteam,
          wrapperPath,
          existing.command_token_added === true,
        );
      }
      const removed = await removeWorkaroundState(target.appid);
      if (!removed.success) throw new Error(removed.error || "Could not remove workaround state");
      return true;
    } catch (error) {
      showErrorToast("Could not clean up game workarounds", asError(error).message);
      return false;
    }
  }, [installedGames]);

  const enqueueBulkProfileOperation = useCallback(async (operation: () => Promise<void>) => {
    if (bulkOperationLock.current) return;
    bulkOperationLock.current = true;
    setBulkOperationBusy(true);
    try {
      await enqueueProfileOperation(operation);
    } finally {
      bulkOperationLock.current = false;
      setBulkOperationBusy(false);
    }
  }, [enqueueProfileOperation]);

  const cleanupAllWorkarounds = useCallback(async (): Promise<boolean> => {
    try {
      await profileOperations.current.catch(() => {});
      const result = await getWorkaroundApps();
      if (!result.success) throw new Error(result.error || "Could not read workaround state");
      const cleaned = new Set<string>();
      const wrapperPath = result.wrapper_path || getDefaultWrapperPath();

      for (const entry of result.apps || []) {
        await flushWorkaroundSaves(`steam:${entry.appid}`);
        await removeWrapperIntegration(
          Number(entry.appid),
          entry.non_steam,
          wrapperPath,
          entry.command_token_added,
        );
        const removed = await removeWorkaroundState(entry.appid);
        if (!removed.success) throw new Error(removed.error || "Could not remove workaround state");
        cleaned.add(entry.appid);
      }

      // Also clean configured targets whose sidecar entry was lost. This
      // removes an old wrapper and only the plugin-managed launch pieces.
      for (const target of targets.filter((item) => item.configured && installedGames.some((game) => game.appid === item.appid))) {
        if (!cleaned.has(target.appid) && !(await removeTargetWorkarounds(target))) return false;
      }
      return true;
    } catch (error) {
      showErrorToast("Could not clean up game launch options", asError(error).message);
      return false;
    }
  }, [installedGames, removeTargetWorkarounds, targets]);

  const saveFor = useCallback((appid: string, changes: Partial<ConfigurationData>) => (
    enqueueProfileOperation(async () => {
      const target = targets.find((item) => item.appid === appid);
      if (!target?.name) return false;
      const result = await updateGameConfig(appid, target.name, changes, true);
      const config = result.config;
      if (!result.success || !config) throw new Error(result.error || "Could not save game profile");
      setGames((games) => games.map((game) => game.appid === appid
        ? { ...game, config, profile: result.profile || game.profile }
        : game));
      return true;
    }).catch((error) => {
      showErrorToast("Could not save game profile", asError(error).message);
      return false;
    })
  ), [enqueueProfileOperation, targets]);

  const updateGlobal = useCallback(async (next: GlobalConfig): Promise<boolean> => {
    const result = await saveGlobalConfig(next);
    if (!result.success) return false;
    setGlobalConfig(result.global_config || next);
    return true;
  }, []);

  const enable = useCallback((appid: string) => enqueueProfileOperation(async () => {
    const target = targets.find((item) => item.appid === appid);
    if (!target?.name) return false;
    if (!(await ensureTargetWorkarounds(target))) return false;
    const result = await updateGameConfig(appid, target.name, template);
    if (result.success) await load();
    else await removeTargetWorkarounds(target);
    return result.success;
  }), [enqueueProfileOperation, ensureTargetWorkarounds, load, removeTargetWorkarounds, targets, template]);

  const enableAll = useCallback((source: KnownGameSource): Promise<void> => enqueueBulkProfileOperation(async () => {
    const available = targets.filter((target) => isBulkGameTarget(target, source) && !target.configured && target.name);
    if (available.length === 0) return;
    for (const target of available) {
      if (!(await ensureTargetWorkarounds(target))) {
        await load();
        return;
      }
      const result = await updateGameConfig(target.appid, target.name, template);
      if (!result.success) {
        await removeTargetWorkarounds(target);
        showErrorToast(
          "Could not enable all games",
          result.error || `Could not create a profile for ${target.name}`,
        );
        await load();
        return;
      }
    }
    await load();
  }), [enqueueBulkProfileOperation, ensureTargetWorkarounds, load, removeTargetWorkarounds, targets, template]);

  const repair = useCallback((appid: string): Promise<boolean> => enqueueProfileOperation(async () => {
    const target = targets.find((item) => item.appid === appid);
    if (!target) return false;
    const profiles = await getGameConfigs();
    if (!profiles.success || !profiles.games?.some((game) => game.appid === appid)) {
      showErrorToast("Could not repair profile", profiles.error || "The game profile no longer exists");
      return false;
    }
    const success = await ensureTargetWorkarounds(target);
    if (success) await load();
    return success;
  }).catch((error) => {
    showErrorToast("Could not repair profile", asError(error).message);
    return false;
  }), [enqueueProfileOperation, ensureTargetWorkarounds, load, targets]);

  const resetFor = useCallback((appid: string): Promise<boolean> => enqueueProfileOperation(async () => {
    const target = targets.find((item) => item.appid === appid);
    if (target && !(await removeTargetWorkarounds(target))) return false;
    const result = await resetGameConfig(appid);
    if (!result.success) {
      showErrorToast("Could not remove profile", result.error || "Could not remove the selected profile");
      return false;
    }
    setRunningGame((current) => current?.appid === appid ? { ...current, configured: false } : current);
    await load();
    return true;
  }), [enqueueProfileOperation, load, removeTargetWorkarounds, targets]);

  const resetAll = useCallback((source: KnownGameSource): Promise<void> => enqueueBulkProfileOperation(async () => {
    const selectedTargets = targets.filter((item) => item.configured && isBulkGameTarget(item, source));
    if (selectedTargets.length === 0) return;
    for (const target of selectedTargets) {
      if (!(await removeTargetWorkarounds(target))) {
        await load();
        return;
      }
    }
    const result = await resetGameConfigs(selectedTargets.map((target) => target.appid));
    if (!result.success) {
      showErrorToast("Could not remove all profiles", result.error || "Could not remove the selected profiles");
      await load();
      return;
    }
    setRunningGame((current) => current && selectedTargets.some((target) => target.appid === current.appid)
      ? { ...current, configured: false } : current);
    await load();
  }), [enqueueBulkProfileOperation, load, removeTargetWorkarounds, targets]);

  return { configFor, runningConfig, globalConfig, targets, runningGame, saveFor, updateGlobal, enable, enableAll, repair, resetFor, resetAll, bulkOperationBusy, cleanupAllWorkarounds, reload: load };
}
