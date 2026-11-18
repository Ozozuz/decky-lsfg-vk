import { ButtonItem, PanelSectionRow, SliderField, ToggleField } from "@decky/ui";
import { type ReactNode, useEffect, useState } from "react";
import { RiArrowDownSFill, RiArrowUpSFill } from "react-icons/ri";
import type { WorkaroundState } from "../api/lsfgApi";
import t from "../i18n/i18n";

interface Props {
  children: ReactNode;
  state: WorkaroundState;
  disabled?: boolean;
  onChange: (field: keyof WorkaroundState, value: boolean | number) => Promise<boolean>;
}

const WORKAROUNDS_COLLAPSED_KEY = "lsfg-flatpak-workarounds-collapsed-v1";

export function FlatpakWorkaroundsSection({ children, state, disabled = false, onChange }: Props) {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(WORKAROUNDS_COLLAPSED_KEY) !== "false";
    } catch {
      return true;
    }
  });
  const [fpsValue, setFpsValue] = useState(state.dxvkFrameRate);

  useEffect(() => {
    try {
      localStorage.setItem(WORKAROUNDS_COLLAPSED_KEY, String(collapsed));
    } catch {}
  }, [collapsed]);

  useEffect(() => {
    setFpsValue(state.dxvkFrameRate);
  }, [state.dxvkFrameRate]);

  const update = (field: keyof WorkaroundState, value: boolean | number) => {
    void onChange(field, value);
  };

  const fpsLabel = fpsValue > 0 ? `${fpsValue} FPS` : t("CONFIG_BASE_FPS_CAP_OFF", "Off");

  return (
    <>
      <PanelSectionRow>
        <SliderField
          label={`${t("CONFIG_BASE_FPS_CAP", "Base FPS Cap")} (${fpsLabel})`}
          description={t("CONFIG_BASE_FPS_CAP_DESC", "Limits real FPS before frame generation in DXVK and VKD3D-Proton games. 0 disables the plugin cap. Restart the Flatpak app to apply.")}
          value={fpsValue}
          min={0}
          max={60}
          step={1}
          onChange={(value) => {
            setFpsValue(value);
            void onChange("dxvkFrameRate", value).then((success) => {
              if (!success) setFpsValue((current) => current === value ? state.dxvkFrameRate : current);
            });
          }}
          disabled={disabled}
        />
      </PanelSectionRow>
      {children}
      <PanelSectionRow>
        <div
          style={{
            fontSize: "14px",
            fontWeight: "bold",
            marginTop: "8px",
            marginBottom: "6px",
            borderBottom: "1px solid rgba(255, 255, 255, 0.2)",
            paddingBottom: "3px",
            color: "white",
          }}
        >
          {t("CONFIG_WORKAROUNDS_TITLE", "Workarounds")}
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          bottomSeparator={collapsed ? "standard" : "none"}
          onClick={() => setCollapsed((value) => !value)}
        >
          {collapsed ? <RiArrowDownSFill /> : <RiArrowUpSFill />}
        </ButtonItem>
      </PanelSectionRow>
      {!collapsed && (
        <>
          <PanelSectionRow>
            <ToggleField
              label={t("CONFIG_DISABLE_STEAMDECK_MODE", "Disable Steam Deck Mode")}
              description={t("CONFIG_DISABLE_STEAMDECK_MODE_DESC", "Disables a game-specific Steam Deck compatibility switch. Requires app restart to apply.")}
              checked={state.disableSteamdeckMode}
              onChange={(value) => update("disableSteamdeckMode", value)}
              disabled={disabled}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField
              label={t("CONFIG_DISABLE_GAMESCOPE_WSI", "Disable Gamescope WSI")}
              description={t("CONFIG_DISABLE_GAMESCOPE_WSI_DESC", "Adds ENABLE_GAMESCOPE_WSI=0. Requires app restart to apply.")}
              checked={state.disableGamescopeWsi}
              onChange={(value) => update("disableGamescopeWsi", value)}
              disabled={disabled}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField
              label={t("CONFIG_DISABLE_HDR", "Disable HDR")}
              description={t("CONFIG_DISABLE_HDR_DESC", "Prevents DXVK from exposing HDR to the app. Requires app restart to apply.")}
              checked={state.disableHdr}
              onChange={(value) => update("disableHdr", value)}
              disabled={disabled}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField
              label={t("CONFIG_DISABLE_VKBASALT", "Disable vkBasalt")}
              description={t("CONFIG_DISABLE_VKBASALT_DESC", "Disables vkBasalt which can conflict with LSFG-VK.")}
              checked={state.disableVkbasalt}
              onChange={(value) => update("disableVkbasalt", value)}
              disabled={disabled}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField
              label={t("CONFIG_ENABLE_ZINK", "Force Zink for OpenGL Games")}
              description={t("CONFIG_ENABLE_ZINK_DESC", "Uses Mesa's Zink OpenGL-to-Vulkan driver. May cause crashes or freezes. Requires app restart to apply.")}
              checked={state.enableZink}
              onChange={(value) => update("enableZink", value)}
              disabled={disabled}
            />
          </PanelSectionRow>
        </>
      )}
    </>
  );
}
