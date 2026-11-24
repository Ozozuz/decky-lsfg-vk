import { Tabs } from "@decky/ui";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { FaCube, FaExternalLinkAlt, FaFileAlt, FaGamepad, FaSteam, FaTools } from "react-icons/fa";
import { useFlatpakConfiguration } from "../hooks/useFlatpakConfiguration";
import { useGameConfiguration } from "../hooks/useGameConfiguration";
import { useInstallation } from "../hooks/useLsfgHooks";
import { tabStyles } from "../styles";
import { targetsForSource } from "../utils/gameTargets";
import { resolveNowPlayingTarget, type NowPlayingTarget } from "../utils/nowPlaying";
import { ConfigFileTab } from "./ConfigFileTab";
import { ConfigurationTab } from "./ConfigurationTab";
import { FlatpakNowPlayingTab } from "./FlatpakNowPlayingTab";
import { FlatpakTab } from "./FlatpakTab";
import { NowPlayingTab } from "./NowPlayingTab";
import { SettingsTab } from "./SettingsTab";

const tabIcons = {
  nowPlaying: <FaGamepad size={18} />,
  steam: <FaSteam size={18} />,
  nonSteam: <FaExternalLinkAlt size={18} />,
  flatpak: <FaCube size={18} />,
  configFile: <FaFileAlt size={18} />,
  settings: <FaTools size={18} />,
};

const DEBUG_TAB_VISIBILITY_KEY = "lsfg-debug-tab-visible-v1";
type GameTabId = "Steam" | "NonSteam" | "Flatpak";

function tabForNowPlaying(target: NowPlayingTarget | null): GameTabId {
  if (!target) return "Steam";
  if (target.kind === "flatpak") return target.launcher?.source === "nonSteam" ? "NonSteam" : "Flatpak";
  return target.game.source === "nonSteam" ? "NonSteam" : "Steam";
}

function usePersistentBoolean(key: string, defaultValue: boolean) {
  const [value, setValue] = useState(() => {
    try {
      const stored = localStorage.getItem(key);
      return stored === null ? defaultValue : stored === "true";
    } catch {
      return defaultValue;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(key, String(value));
    } catch {}
  }, [key, value]);

  return [value, setValue] as const;
}

export function Content() {
  const {
    configFor,
    runningConfig,
    globalConfig,
    targets,
    runningGame,
    saveFor,
    updateGlobal,
    enable,
    enableAll,
    bulkOperationBusy,
    repair,
    resetFor,
    resetAll,
    cleanupAllWorkarounds,
    reload,
  } = useGameConfiguration();
  const {
    isInstalled,
    setupComplete,
    installationStatus,
    losslessScalingInstalled,
    losslessScalingStatus,
    steamBranchStatus,
    isInstalling,
    isUninstalling,
    install,
    uninstall,
  } = useInstallation(reload, cleanupAllWorkarounds);
  const flatpak = useFlatpakConfiguration(setupComplete, runningGame?.flatpakAppId);
  const [tab, setTab] = useState("Settings");
  const [showDebugTab, setShowDebugTab] = usePersistentBoolean(DEBUG_TAB_VISIBILITY_KEY, false);
  const previousRunningWorkload = useRef<string | null>(null);
  const previousNowPlayingTab = useRef<GameTabId>("Steam");
  const runningFlatpak = flatpak.runningApp;
  const nowPlayingTarget = resolveNowPlayingTarget(runningGame, runningFlatpak);
  const hasNowPlaying = Boolean(nowPlayingTarget);
  const runningWorkload = nowPlayingTarget
    ? nowPlayingTarget.kind === "flatpak"
      ? `flatpak:${nowPlayingTarget.app.app_id}:${nowPlayingTarget.launcher?.appid ?? ""}`
      : `${nowPlayingTarget.game.source}:${nowPlayingTarget.game.appid}`
    : null;
  const steamTargets = targetsForSource(targets, "steam");
  const nonSteamTargets = targetsForSource(targets, "nonSteam");

  useEffect(() => {
    if (!setupComplete) {
      setTab("Settings");
      return;
    }
    setTab((current) => current === "Settings" ? (hasNowPlaying ? "NowPlaying" : "Steam") : current);
  }, [hasNowPlaying, setupComplete]);

  useEffect(() => {
    if (!setupComplete) return;
    const previous = previousRunningWorkload.current;
    previousRunningWorkload.current = runningWorkload;
    if (runningWorkload && runningWorkload !== previous) {
      previousNowPlayingTab.current = tabForNowPlaying(nowPlayingTarget);
      setTab((current) => current === "Flatpak" ? current : "NowPlaying");
    }
    else if (!runningWorkload && previous) {
      setTab((current) => current === "NowPlaying" ? previousNowPlayingTab.current : current);
    }
  }, [runningWorkload, setupComplete]);

  useEffect(() => {
    if (isInstalled) void reload();
  }, [isInstalled, reload]);

  useEffect(() => {
    if (!showDebugTab && tab === "ConfigFile") setTab(setupComplete ? "Steam" : "Settings");
  }, [setupComplete, showDebugTab, tab]);

  const settings = (
    <SettingsTab
      isInstalled={isInstalled}
      setupComplete={setupComplete}
      installationStatus={installationStatus}
      losslessScalingInstalled={losslessScalingInstalled}
      losslessScalingStatus={losslessScalingStatus}
      steamBranchStatus={steamBranchStatus}
      isInstalling={isInstalling}
      isUninstalling={isUninstalling}
      globalConfig={globalConfig}
      showDebugTab={showDebugTab}
      onGlobalConfigChange={updateGlobal}
      onShowDebugTabChange={setShowDebugTab}
      onInstall={() => void install()}
      onUninstall={() => void uninstall()}
    />
  );

  const tabContent = (content: ReactNode) => (
    <div className="lsfg-vk-tab-content">{content}</div>
  );

  const nowPlaying = nowPlayingTarget?.kind === "flatpak" ? (
    <FlatpakNowPlayingTab
      app={nowPlayingTarget.app}
      launcher={nowPlayingTarget.launcher}
      showPerformance={tab === "NowPlaying"}
      onConfigChange={flatpak.updateConfig}
    />
  ) : nowPlayingTarget ? (
    <NowPlayingTab
      game={nowPlayingTarget.game}
      config={runningConfig}
      showPerformance={tab === "NowPlaying"}
      onConfigChange={async (field, value) => {
        await saveFor(nowPlayingTarget.game.appid, { [field]: value });
      }}
    />
  ) : null;

  const tabs = setupComplete
    ? [
        ...(nowPlaying ? [{ id: "NowPlaying", title: tabIcons.nowPlaying, content: tabContent(nowPlaying) }] : []),
        {
          id: "Steam",
          title: tabIcons.steam,
          content: tabContent(
            <ConfigurationTab
              title="Steam games"
              source="steam"
              configFor={configFor}
              targets={steamTargets}
              runningGame={runningGame}
              onSave={saveFor}
              onEnable={enable}
              onEnableAll={enableAll}
              bulkOperationBusy={bulkOperationBusy}
              onRepair={repair}
              onReset={resetFor}
              onResetAll={resetAll}
            />,
          ),
        },
        {
          id: "NonSteam",
          title: tabIcons.nonSteam,
          content: tabContent(
            <ConfigurationTab
              title="Non-Steam games"
              source="nonSteam"
              configFor={configFor}
              targets={nonSteamTargets}
              runningGame={runningGame}
              onSave={saveFor}
              onEnable={enable}
              onEnableAll={enableAll}
              bulkOperationBusy={bulkOperationBusy}
              onRepair={repair}
              onReset={resetFor}
              onResetAll={resetAll}
            />
          ),
        },
        {
          id: "Flatpak",
          title: tabIcons.flatpak,
          content: tabContent(
            <FlatpakTab
              apps={flatpak.apps}
              runningApp={runningFlatpak}
              loading={flatpak.loading}
              busyAppId={flatpak.busyAppId}
              onRefresh={flatpak.reload}
              onEnable={flatpak.enableApp}
              onEnableAll={flatpak.enableAll}
              onRemove={flatpak.removeApp}
              onRemoveAll={flatpak.removeAll}
              onConfigChange={flatpak.updateConfig}
              onWorkaroundChange={flatpak.updateWorkarounds}
            />,
          ),
        },
        ...(showDebugTab ? [{ id: "ConfigFile", title: tabIcons.configFile, content: tabContent(<ConfigFileTab />) }] : []),
        { id: "Settings", title: tabIcons.settings, content: tabContent(settings) },
      ]
    : [
        ...(isInstalled && showDebugTab
          ? [{ id: "ConfigFile", title: tabIcons.configFile, content: tabContent(<ConfigFileTab />) }]
          : []),
        { id: "Settings", title: tabIcons.settings, content: tabContent(settings) },
      ];

  const availableTabIds = new Set(tabs.map(({ id }) => id));
  const activeTab = availableTabIds.has(tab) ? tab : setupComplete ? "Steam" : "Settings";

  return (
    <div
      className="lsfg-vk-tabs"
      style={{ height: "95%", width: "300px", position: "fixed", marginTop: "-12px", overflow: "hidden" }}
    >
      <style>{tabStyles}</style>
      <Tabs
        activeTab={activeTab}
        autoFocusContents={false}
        onShowTab={(nextTab: string) => {
          if (!availableTabIds.has(nextTab)) return;
          setTab(nextTab);
        }}
        tabs={tabs}
      />
    </div>
  );
}
