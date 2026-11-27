import { ButtonItem, ConfirmModal, DialogButton, Field, Focusable, PanelSection, PanelSectionRow, gamepadDialogClasses, showModal } from "@decky/ui";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FaArrowLeft } from "react-icons/fa";
import type { FlatpakApp, LsfgConfig, RunningFlatpakApp, WorkaroundState } from "../api/lsfgApi";
import { CollapsibleItemGroup, collapsibleItemGroupStyles, usePersistentCollapsed } from "./CollapsibleItemGroup";
import { ConfigurationSection } from "./ConfigurationSection";
import { FlatpakWorkaroundsSection } from "./FlatpakWorkaroundsSection";
import { FpsMultiplierControl } from "./FpsMultiplierControl";
import { ProfileDetails } from "./ProfileDetails";

interface Props {
  apps: FlatpakApp[];
  runningApps: RunningFlatpakApp[];
  loading: boolean;
  busyAppId: string;
  onRefresh: () => Promise<void>;
  onEnable: (appId: string) => Promise<boolean>;
  onEnableAll: () => Promise<void>;
  onRemove: (appId: string) => Promise<boolean>;
  onRemoveAll: () => Promise<void>;
  onStop: (appId: string) => Promise<void>;
  onConfigChange: (appId: string, config: Partial<LsfgConfig>) => Promise<boolean>;
  onWorkaroundChange: (appId: string, field: keyof WorkaroundState, value: boolean | number) => Promise<boolean>;
}

const ENABLED_COLLAPSED_KEY = "lsfg-flatpak-enabled-collapsed-v2";
const AVAILABLE_COLLAPSED_KEY = "lsfg-flatpak-available-collapsed-v2";

export function FlatpakTab({
  apps,
  runningApps,
  loading,
  busyAppId,
  onRefresh,
  onEnable,
  onEnableAll,
  onRemove,
  onRemoveAll,
  onStop,
  onConfigChange,
  onWorkaroundChange,
}: Props) {
  const [selectedAppId, setSelectedAppId] = useState<string | null>(null);
  const selected = useMemo(
    () => selectedAppId ? apps.find((app) => app.app_id === selectedAppId) || null : null,
    [apps, selectedAppId],
  );
  const backRef = useRef<HTMLDivElement>(null);
  const enabledToggleRef = useRef<HTMLDivElement>(null);
  const availableToggleRef = useRef<HTMLDivElement>(null);
  const refreshRef = useRef<HTMLDivElement>(null);
  const focusApp = useRef<string | null>(null);
  const open = (appId: string) => {
    focusApp.current = appId;
    setSelectedAppId(appId);
  };
  const close = useCallback(() => {
    focusApp.current = selectedAppId;
    setSelectedAppId(null);
  }, [selectedAppId]);
  const [enabledCollapsed, toggleEnabled] = usePersistentCollapsed(ENABLED_COLLAPSED_KEY);
  const [availableCollapsed, toggleAvailable] = usePersistentCollapsed(AVAILABLE_COLLAPSED_KEY);

  const enabledApps = useMemo(
    () => apps.filter((app) => app.enabled).sort((a, b) => a.app_name.localeCompare(b.app_name)),
    [apps],
  );
  const availableApps = useMemo(
    () => apps.filter((app) => !app.enabled).sort((a, b) => a.app_name.localeCompare(b.app_name)),
    [apps],
  );
  useEffect(() => {
    if (focusApp.current === null) return;
    const frame = requestAnimationFrame(() => {
      const appId = focusApp.current;
      focusApp.current = null;
      const wasEnabled = apps.find((app) => app.app_id === appId)?.enabled;
      const targets = selectedAppId
        ? [backRef]
        : wasEnabled
          ? [enabledToggleRef, availableToggleRef, refreshRef]
          : [availableToggleRef, enabledToggleRef, refreshRef];
      for (const target of targets) {
        const button = target.current?.querySelector<HTMLElement>('button:not(:disabled), [role="button"]:not([aria-disabled="true"])');
        if (button) {
          button.focus();
          break;
        }
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [selectedAppId, apps]);
  const enableableApps = useMemo(
    () => availableApps.filter((app) => !(app.prepared && !app.owned) && !app.error),
    [availableApps],
  );
  const confirmEnableAll = () => {
    showModal(
      <ConfirmModal
        strTitle="Enable all available Flatpaks?"
        strDescription="Create individual LSFG-VK profiles for every Flatpak app this plugin can manage. Apps prepared externally or unavailable will be skipped."
        strOKButtonText="Enable all"
        strCancelButtonText="Cancel"
        onOK={() => void onEnableAll()}
        onCancel={() => {}}
      />,
    );
  };
  const confirmRemoveAll = () => {
    showModal(
      <ConfirmModal
        strTitle="Remove all Flatpak profiles?"
        strOKButtonText="Remove all"
        strCancelButtonText="Cancel"
        onOK={() => void onRemoveAll()}
        onCancel={() => {}}
      />,
    );
  };
  const runningAppIds = new Set(runningApps.map(({ app_id }) => app_id));
  const itemFor = (app: FlatpakApp) => ({
    id: app.app_id,
    label: app.app_name,
    description: `${app.app_id} · ${app.prepared && !app.owned ? "Prepared externally" : "Available"}`,
  });

  if (!selectedAppId) {
    return (
      <PanelSection title="Flatpak">
        <style>{collapsibleItemGroupStyles}</style>
        <CollapsibleItemGroup
          title="Enabled"
          items={enabledApps.map((app) => ({
            id: app.app_id,
            label: app.app_name,
            description: `${app.app_id}${runningAppIds.has(app.app_id) ? " · Running" : ""}`,
          }))}
          collapsed={enabledCollapsed}
          onToggle={toggleEnabled}
          onSelect={open}
          toggleRef={enabledToggleRef}
        />
        <CollapsibleItemGroup
          title="Available"
          items={availableApps.map(itemFor)}
          collapsed={availableCollapsed}
          onToggle={toggleAvailable}
          onSelect={open}
          toggleRef={availableToggleRef}
        />
        {enableableApps.length > 0 && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={loading || Boolean(busyAppId)}
              onClick={confirmEnableAll}
            >
              Enable all available Flatpaks
            </ButtonItem>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={loading || Boolean(busyAppId) || enabledApps.length === 0}
            onClick={confirmRemoveAll}
          >
            Remove all profiles
          </ButtonItem>
        </PanelSectionRow>
        {apps.length === 0 && !loading && (
          <PanelSectionRow>
            <Field label="No Flatpak applications found" />
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <div ref={refreshRef}>
            <ButtonItem layout="below" disabled={loading || Boolean(busyAppId)} onClick={() => void onRefresh()}>
              {loading ? "Refreshing..." : "Refresh Flatpaks"}
            </ButtonItem>
          </div>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  if (!selected) {
    return (
      <Focusable onCancelButton={close}>
        <PanelSection title="Flatpak">
          <PanelSectionRow>
            <div ref={backRef}>
              <ButtonItem layout="below" onClick={close}>Back</ButtonItem>
            </div>
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label="Flatpak application is no longer installed" />
          </PanelSectionRow>
        </PanelSection>
      </Focusable>
    );
  }

  const busy = busyAppId === selected.app_id;
  const config = selected.config;
  const needsRuntime = selected.enabled && !selected.runtime_ready;
  const external = selected.prepared && !selected.owned;
  const profileDescription = [
    selected.app_id,
    selected.runtime_branch ? `runtime ${selected.runtime_branch}` : null,
    selected.enabled ? `profile ${selected.profile}` : null,
    runningAppIds.has(selected.app_id) ? "Running" : null,
  ].filter(Boolean).join(" · ");

  const changeConfig = async (
    field: keyof LsfgConfig,
    value: boolean | number | string | string[],
  ) => {
    if (!config) return;
    await onConfigChange(selected.app_id, { [field]: value });
  };

  return (
    <Focusable onCancelButton={close}>
      <PanelSection>
        <PanelSectionRow>
          <div style={{ display: "flex", alignItems: "center", width: "100%" }}>
            <Focusable ref={backRef} noFocusRing style={{ flex: "none" }}>
              <DialogButton
                aria-label="Back to Flatpaks"
                onClick={close}
                style={{
                  width: "48px",
                  minWidth: "48px",
                  height: "24px",
                  minHeight: "24px",
                  padding: 0,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <FaArrowLeft />
              </DialogButton>
            </Focusable>
            <div
              className={gamepadDialogClasses.FieldLabel}
              style={{ flex: 1, minWidth: 0, marginLeft: "8px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            >
              {selected.app_name}
            </div>
          </div>
        </PanelSectionRow>
      </PanelSection>
      {(!selected.enabled || needsRuntime) && (
        <PanelSection>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={busy || external || Boolean(selected.error)}
              onClick={() => void onEnable(selected.app_id)}
            >
              {busy
                ? needsRuntime ? "Repairing runtime..." : "Enabling..."
                : external ? "Prepared externally" : needsRuntime ? "Repair runtime" : "Enable LSFG-VK"}
            </ButtonItem>
          </PanelSectionRow>
          {needsRuntime && (
            <PanelSectionRow>
              <Field label="Runtime extension missing" description="Repair installs the extension for this app's current runtime and keeps your profile." />
            </PanelSectionRow>
          )}
          {selected.error && (
            <PanelSectionRow>
              <Field label="Unavailable" description={selected.error} />
            </PanelSectionRow>
          )}
        </PanelSection>
      )}
      {selected.enabled && config && (
        <>
          <FpsMultiplierControl config={config} onConfigChange={changeConfig} />
          <FlatpakWorkaroundsSection
            state={selected.workarounds}
            disabled={busy}
            onChange={(field, value) => onWorkaroundChange(selected.app_id, field, value)}
          >
            <ConfigurationSection config={config} onConfigChange={changeConfig} />
          </FlatpakWorkaroundsSection>
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy} onClick={() => void onRemove(selected.app_id)}>
              {busy ? "Removing..." : "Remove Flatpak profile"}
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}
      {runningAppIds.has(selected.app_id) && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={Boolean(busyAppId)}
            onClick={() => showModal(
              <ConfirmModal
                strTitle={`Force close ${selected.app_name}?`}
                strDescription="Save and exit any games first. This also closes games running through this app."
                strOKButtonText="Force close"
                strCancelButtonText="Cancel"
                onOK={() => void onStop(selected.app_id)}
                onCancel={() => {}}
              />,
            )}
          >
            Force close Flatpak app
          </ButtonItem>
        </PanelSectionRow>
      )}
      <ProfileDetails description={profileDescription} />
    </Focusable>
  );
}
