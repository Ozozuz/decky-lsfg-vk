from __future__ import annotations

from typing import Any, Dict

from .configuration import ConfigurationService
from .flatpak_service import FlatpakService


class FlatpakProfileService:
    STATE_FIELDS = (
        "dxvkFrameRate",
        "disableGamescopeWsi",
        "disableHdr",
        "disableSteamdeckMode",
        "disableVkbasalt",
        "enableZink",
    )
    BOOLEAN_FIELDS = STATE_FIELDS[1:]

    def __init__(
        self,
        flatpak_service: FlatpakService,
        configuration_service: ConfigurationService,
    ):
        self.flatpak_service = flatpak_service
        self.configuration_service = configuration_service

    @classmethod
    def default_state(cls) -> Dict[str, Any]:
        return {
            "dxvkFrameRate": 0,
            "disableGamescopeWsi": True,
            "disableHdr": True,
            "disableSteamdeckMode": False,
            "disableVkbasalt": False,
            "enableZink": False,
        }

    @classmethod
    def _validate_state(cls, raw: Any) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("Workaround state must be an object")
        missing = [field for field in cls.STATE_FIELDS if field not in raw]
        if missing:
            raise ValueError("Workaround state is missing: " + ", ".join(missing))
        state = {field: raw[field] for field in cls.STATE_FIELDS}
        frame_rate = state["dxvkFrameRate"]
        if isinstance(frame_rate, bool) or not isinstance(frame_rate, int) or not 0 <= frame_rate <= 60:
            raise ValueError("Base FPS Cap must be an integer from 0 to 60")
        for field in cls.BOOLEAN_FIELDS:
            if type(state[field]) is not bool:
                raise ValueError(f"{field} must be a boolean")
        return state

    def _state_entry(self, app_id: str) -> tuple[Dict[str, object], Dict[str, Any]]:
        state = self.flatpak_service._read_state()
        entry = state["prepared_apps"].get(app_id)
        if not isinstance(entry, dict):
            raise RuntimeError("Flatpak application is not owned by this plugin")
        return state, entry

    def enable_app(self, app_id: str) -> Dict[str, Any]:
        created_profile = False
        with self.flatpak_service._lock:
            try:
                self.flatpak_service._validate_app_id(app_id)
                existing = self.configuration_service.get_flatpak_config(app_id)
                if not existing.get("success"):
                    raise RuntimeError(existing.get("error") or "Could not read Flatpak profile")
                state = self.flatpak_service._read_state()
                entry = state["prepared_apps"].get(app_id, {})
                workarounds = self._validate_state(entry.get("workaround_state", self.default_state()))
                if not existing.get("exists"):
                    config = {
                        **self.configuration_service._public_config({}),
                        **(existing.get("global_config") or {}),
                        "active_in": [],
                    }
                    saved = self.configuration_service.update_flatpak_config(app_id, config)
                    if not saved.get("success"):
                        raise RuntimeError(saved.get("error") or "Could not create Flatpak profile")
                    created_profile = True
                prepared = self.flatpak_service.prepare_app(app_id, workarounds)
                if not prepared.get("success") or not prepared.get("owned"):
                    raise RuntimeError(prepared.get("error") or "Could not prepare Flatpak application")
                return {**prepared, "enabled": True, "workarounds": workarounds}
            except Exception as error:
                if created_profile:
                    reset = self.configuration_service.reset_flatpak_config(app_id)
                    if not reset.get("success"):
                        error = RuntimeError(f"{error}. Could not roll back Flatpak profile: {reset.get('error')}")
                return {
                    "success": False,
                    "message": "",
                    "error": str(error),
                    "app_id": str(app_id),
                    "enabled": False,
                }

    def update_config(self, app_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
        try:
            self._state_entry(app_id)
            result = self.configuration_service.update_flatpak_config(app_id, config)
            if not result.get("success"):
                raise RuntimeError(result.get("error") or "Could not update Flatpak profile")
            return result
        except Exception as error:
            return {
                "success": False,
                "message": "",
                "error": str(error),
                "app_id": str(app_id),
                "enabled": False,
            }

    def get_workaround_state(self, app_id: str) -> Dict[str, Any]:
        try:
            _, entry = self._state_entry(app_id)
            state = self._validate_state(entry.get("workaround_state", self.default_state()))
            return {
                "success": True,
                "message": "",
                "error": None,
                "app_id": app_id,
                "state": state,
            }
        except Exception as error:
            return {
                "success": False,
                "message": "",
                "error": str(error),
                "app_id": str(app_id),
                "state": None,
            }

    def set_workaround_state(self, app_id: str, workaround_state: Dict[str, Any]) -> Dict[str, Any]:
        try:
            self.flatpak_service._validate_app_id(app_id)
            with self.flatpak_service._lock:
                if not isinstance(workaround_state, dict) or any(key not in self.STATE_FIELDS for key in workaround_state):
                    raise ValueError("Invalid workaround state")
                ownership, entry = self._state_entry(app_id)
                state = self._validate_state({
                    **entry.get("workaround_state", self.default_state()),
                    **workaround_state,
                })
                self.flatpak_service._update_app_override(app_id, ownership, workarounds=state)
            return {
                "success": True,
                "message": "",
                "error": None,
                "app_id": app_id,
                "state": state,
            }
        except Exception as error:
            return {
                "success": False,
                "message": "",
                "error": str(error),
                "app_id": str(app_id),
                "state": None,
            }

    def remove_app(self, app_id: str) -> Dict[str, Any]:
        with self.flatpak_service._lock:
            previous = None
            try:
                self.flatpak_service._validate_app_id(app_id)
                config = self.configuration_service.get_flatpak_config(app_id)
                if not config.get("success"):
                    raise RuntimeError(config.get("error") or "Could not read Flatpak profile")
                if config.get("exists"):
                    reset = self.configuration_service.reset_flatpak_config(app_id)
                    if not reset.get("success"):
                        raise RuntimeError(reset.get("error") or "Could not remove Flatpak profile")
                    previous = config["config"]
                removed = self.flatpak_service.remove_app_override(app_id)
                if not removed.get("success"):
                    raise RuntimeError(removed.get("error") or "Could not remove Flatpak preparation")
                return {
                    "success": True,
                    "message": "Flatpak profile removed",
                    "error": None,
                    "app_id": app_id,
                    "enabled": False,
                }
            except Exception as error:
                if previous is not None:
                    restored = self.configuration_service.update_flatpak_config(app_id, previous)
                    if not restored.get("success"):
                        error = RuntimeError(f"{error}. Could not restore Flatpak profile: {restored.get('error')}")
                return {
                    "success": False,
                    "message": "",
                    "error": str(error),
                    "app_id": str(app_id),
                    "enabled": True,
                }

    def get_apps(self) -> Dict[str, Any]:
        try:
            result = self.flatpak_service.get_flatpak_apps()
            if not result.get("success"):
                raise RuntimeError(result.get("error") or "Could not list Flatpak applications")
            ownership = self.flatpak_service._read_state()
            apps = []
            for item in result.get("apps", []):
                app_id = item["app_id"]
                config_result = self.configuration_service.get_flatpak_config(app_id)
                entry = ownership["prepared_apps"].get(app_id)
                workarounds = self.default_state()
                if isinstance(entry, dict):
                    workarounds = self._validate_state(entry.get("workaround_state", workarounds))
                profile = self.configuration_service.flatpak_profile_name(app_id)
                selector_ready = item.pop("profile_selector", None) == profile and item.get("prepared")
                apps.append({
                    **item,
                    "profile": profile,
                    "enabled": bool(item.get("owned") and config_result.get("exists") and selector_ready),
                    "config": config_result.get("config"),
                    "workarounds": workarounds,
                })
            return {
                "success": True,
                "message": result.get("message", ""),
                "error": None,
                "apps": apps,
            }
        except Exception as error:
            return {"success": False, "message": "", "error": str(error), "apps": []}

    def get_running_apps(self) -> Dict[str, Any]:
        try:
            state = self.flatpak_service._read_state()
            owned = state["prepared_apps"]
            if not owned:
                return {"success": True, "message": "", "error": None, "apps": []}
            result = self.flatpak_service._run_flatpak_command(
                ["ps", "--columns=application,active,pid"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise OSError(result.stderr.strip() or "Could not inspect running Flatpak applications")
            running = []
            enabled = {}
            for line in result.stdout.splitlines():
                fields = line.split("\t") if "\t" in line else line.split()
                if not fields or fields[0] not in owned:
                    continue
                app_id = fields[0]
                if app_id not in enabled:
                    config = self.configuration_service.get_flatpak_config(app_id)
                    enabled[app_id] = bool(
                        config.get("exists")
                        and self.flatpak_service._app_override_status(app_id)["profile_selector"]
                        == self.configuration_service.flatpak_profile_name(app_id)
                    )
                if not enabled[app_id]:
                    continue
                active = len(fields) > 1 and fields[1].strip().lower() in {"1", "true", "yes", "active"}
                running.append({
                    "app_id": fields[0],
                    "active": active,
                    "pid": fields[2].strip() if len(fields) > 2 else "",
                    "start_time": self.flatpak_service._process_start_time(
                        fields[2].strip() if len(fields) > 2 else ""
                    ),
                })
            return {"success": True, "message": "", "error": None, "apps": running}
        except Exception as error:
            return {"success": False, "message": "", "error": str(error), "apps": []}
