from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import threading
from pathlib import Path
from typing import Dict, Optional, Set

from .base_service import BaseService
from .configuration import ConfigurationService
from .constants import FLATPAK_APP_ID_PATTERN


class FlatpakService(BaseService):
    EXTENSION_ID = "org.freedesktop.Platform.VulkanLayer.lsfgvk"
    FLATHUB_REMOTE = "flathub"
    FLATHUB_REPO_URL = "https://dl.flathub.org/repo/flathub.flatpakrepo"
    SUPPORTED_RUNTIMES = ("24.08", "25.08", "26.08")
    DERIVED_RUNTIME_IDS = {"org.gnome.Platform", "org.kde.Platform"}
    RUNTIME_METADATA_SECTION = "Extension org.freedesktop.Platform.GL"
    OWNERSHIP_FILENAME = "flatpak_state.json"
    OWNERSHIP_VERSION = 3
    def __init__(self, logger=None):
        super().__init__(logger)
        self.flatpak_command: Optional[str] = None
        self._lock = threading.RLock()

    @property
    def ownership_path(self) -> Path:
        return self.config_dir / self.OWNERSHIP_FILENAME

    def _clean_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        env.pop("LD_LIBRARY_PATH", None)
        env["HOME"] = str(self.user_home)
        try:
            user_id = self.user_home.stat().st_uid
        except OSError:
            user_id = None
        if user_id is not None:
            env["XDG_RUNTIME_DIR"] = f"/run/user/{user_id}"
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{user_id}/bus"
        path = [entry for entry in env.get("PATH", "").split(":") if entry]
        for entry in ("/usr/bin", "/usr/local/bin", "/bin"):
            if entry not in path:
                path.insert(0, entry)
        env["PATH"] = ":".join(path)
        return env

    def check_flatpak_available(self) -> bool:
        env = self._clean_env()
        self.flatpak_command = shutil.which("flatpak", path=env["PATH"])
        return self.flatpak_command is not None

    def _run_flatpak_command(self, args, **kwargs):
        if self.flatpak_command is None and not self.check_flatpak_available():
            raise FileNotFoundError("Flatpak command not available")
        env = self._clean_env()
        command = [self.flatpak_command, *args]
        try:
            user = pwd.getpwuid(self.user_home.stat().st_uid)
        except (KeyError, OSError) as error:
            raise RuntimeError(f"Unable to resolve Flatpak user for {self.user_home}") from error
        if os.geteuid() != user.pw_uid:
            runuser = shutil.which("runuser", path=env["PATH"])
            if runuser is None:
                raise FileNotFoundError("runuser command not available")
            command = [runuser, "--user", user.pw_name, "--", *command]
        kwargs.setdefault("timeout", 300 if args and args[0] in ("install", "uninstall") else 30)
        return subprocess.run(command, env=env, **kwargs)

    @classmethod
    def _validate_app_id(cls, app_id: str) -> str:
        if not isinstance(app_id, str) or not FLATPAK_APP_ID_PATTERN.fullmatch(app_id):
            raise ValueError("Invalid Flatpak application ID")
        return app_id

    @classmethod
    def _validate_runtime(cls, branch: str) -> str:
        if branch not in cls.SUPPORTED_RUNTIMES:
            raise ValueError(
                f"Unsupported Flatpak runtime branch {branch}; supported branches are "
                + ", ".join(cls.SUPPORTED_RUNTIMES)
            )
        return branch

    @classmethod
    def runtime_branch_from_metadata(cls, metadata: str) -> str:
        section = None
        versions = []
        for raw_line in metadata.splitlines() if isinstance(metadata, str) else []:
            line = raw_line.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip()
                continue
            if section != cls.RUNTIME_METADATA_SECTION:
                continue
            key, separator, value = line.partition("=")
            if separator and key.strip() == "versions":
                versions.extend(part.strip() for part in value.split(";"))
        for value in versions:
            for branch in cls.SUPPORTED_RUNTIMES:
                if value == branch or value.startswith(f"{branch}-"):
                    return branch
        raise ValueError("Could not determine a supported Freedesktop base runtime from Flatpak metadata")

    @classmethod
    def _extension_ref(cls, branch: str) -> str:
        return f"{cls.EXTENSION_ID}/x86_64/{cls._validate_runtime(branch)}"

    def _installed_extension_branches(self, scope: Optional[str] = None) -> Set[str]:
        scopes = ("user", "system") if scope is None else (scope,)
        installed = set()
        for item in scopes:
            result = self._run_flatpak_command(
                ["list", f"--{item}", "--runtime", "--columns=application,arch,branch"],
                capture_output=True,
                text=True,
                check=True,
            )
            for line in result.stdout.splitlines():
                fields = line.split("\t") if "\t" in line else line.split()
                if len(fields) >= 3 and fields[0] == self.EXTENSION_ID and fields[1] == "x86_64":
                    installed.add(fields[2])
        return installed

    def _extension_identity(self, branch: str) -> list[int] | None:
        result = self._run_flatpak_command(
            ["info", "--user", "--show-origin", "--show-location", self._extension_ref(branch)],
            capture_output=True,
            text=True,
            check=True,
        )
        origin, _, location = result.stdout.strip().partition(" ")
        if origin != self.FLATHUB_REMOTE or not Path(location).is_absolute():
            return None
        deployed = Path(location).stat()
        return [deployed.st_dev, deployed.st_ino, deployed.st_ctime_ns]

    def _ensure_user_flathub_remote(self) -> None:
        result = self._run_flatpak_command(
            ["remotes", "--user", "--columns=name"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise OSError(result.stderr.strip() or "Could not inspect user Flatpak remotes")
        if self.FLATHUB_REMOTE in {line.strip() for line in result.stdout.splitlines()}:
            return
        result = self._run_flatpak_command(
            ["remote-add", "--user", "--if-not-exists", self.FLATHUB_REMOTE, self.FLATHUB_REPO_URL],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise OSError(
                "Could not configure Flathub for the current user: "
                + (result.stderr.strip() or "Flatpak remote-add failed")
            )

    def _empty_state(self) -> Dict[str, object]:
        return {
            "version": self.OWNERSHIP_VERSION,
            "plugin_owned_branches": {},
            "prepared_apps": {},
        }

    def _read_state(self) -> Dict[str, object]:
        if not self.ownership_path.exists():
            return self._empty_state()
        if self.ownership_path.is_symlink() or not self.ownership_path.is_file():
            raise RuntimeError("Flatpak ownership metadata is not a regular file")
        try:
            data = json.loads(self.ownership_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Could not read Flatpak ownership metadata: {error}") from error
        if data.get("version") not in (2, self.OWNERSHIP_VERSION):
            raise RuntimeError("Unsupported Flatpak ownership metadata version")
        branches = data.get("plugin_owned_branches")
        apps = data.get("prepared_apps")
        expected_type = list if data["version"] == 2 else dict
        if not isinstance(branches, expected_type) or not isinstance(apps, dict):
            raise RuntimeError("Invalid Flatpak ownership metadata")
        for branch in branches:
            self._validate_runtime(branch)
            if isinstance(branches, dict):
                identity = branches[branch]
                if not isinstance(identity, list) or len(identity) != 3 or any(type(part) is not int for part in identity):
                    raise RuntimeError("Invalid Flatpak extension ownership metadata")
        for app_id, entry in apps.items():
            self._validate_app_id(app_id)
            if not isinstance(entry, dict):
                raise RuntimeError("Invalid Flatpak app ownership metadata")
        if data["version"] == 2:
            # Older versions also claimed pre-installed user extensions.
            data["plugin_owned_branches"] = {}
            data["version"] = self.OWNERSHIP_VERSION
        return data

    def _write_state(self, state: Dict[str, object]) -> None:
        branches = state.get("plugin_owned_branches", {})
        apps = state.get("prepared_apps", {})
        if not branches and not apps:
            self.ownership_path.unlink(missing_ok=True)
            return
        self._write_file(
            self.ownership_path,
            json.dumps(state, indent=2, sort_keys=True) + "\n",
        )

    def _override_path(self, app_id: str) -> Path:
        env = self._clean_env()
        data_home = Path(env.get("XDG_DATA_HOME") or self.user_home / ".local/share")
        if not data_home.is_absolute():
            data_home = self.user_home / ".local/share"
        user_dir = Path(env.get("FLATPAK_USER_DIR") or data_home / "flatpak")
        return user_dir / "overrides" / self._validate_app_id(app_id)

    @staticmethod
    def _override_config(content: str) -> dict[str, dict[str, str]]:
        config = {}
        group = None
        for raw in content.split("\n"):
            line = raw.lstrip()
            if not line or line.startswith("#"):
                continue
            header = line.rstrip()
            if header.startswith("[") and header.endswith("]"):
                group = config.setdefault(header[1:-1], {})
                continue
            key, separator, value = line.partition("=")
            if group is None or not separator or not key.rstrip():
                raise ValueError("Invalid Flatpak override metadata")
            group[key.rstrip()] = value.lstrip(" \t")
        return config

    @staticmethod
    def _list_value(value: str) -> list[str]:
        return re.findall(r"(?:\\.|[^;\\])+", value)

    @staticmethod
    def _escape_value(value: str) -> str:
        return value.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")

    @staticmethod
    def _filesystem_key(entry: str) -> str:
        return re.sub(r":(?:ro|rw|create)$", "", entry.removeprefix("!"))

    def _filesystem_aliases(self, host_path: Path) -> set[str]:
        paths = {str(host_path)}
        if host_path.is_relative_to(self.user_home):
            paths.add(f"~/{host_path.relative_to(self.user_home).as_posix()}")
        return {self._escape_value(path).replace(";", "\\;") for path in paths}

    def _launch_environment(self, app_id: str, workarounds: Optional[dict]) -> dict:
        env = {
            "LSFGVK_CONFIG": str(self.config_file_path),
            "LSFGVK_FLATPAK": "1",
            "DISABLE_LSFGVK": None,
            "DISABLE_LSFG": None,
        }
        if workarounds is None:
            return env
        env["LSFGVK_PROFILE"] = ConfigurationService.flatpak_profile_name(app_id)
        if workarounds.get("disableGamescopeWsi"):
            env.update(ENABLE_GAMESCOPE_WSI="0", DISABLE_GAMESCOPE_WSI=None)
        if workarounds.get("disableHdr"):
            env["DXVK_HDR"] = "0"
        if workarounds.get("disableSteamdeckMode"):
            env["SteamDeck"] = "0"
        if workarounds.get("disableVkbasalt"):
            env.update(DISABLE_VKBASALT="1", ENABLE_VKBASALT=None)
        if workarounds.get("enableZink"):
            env.update(__GLX_VENDOR_LIBRARY_NAME="mesa", MESA_LOADER_DRIVER_OVERRIDE="zink", GALLIUM_DRIVER="zink")
        frame_rate = workarounds.get("dxvkFrameRate", 0)
        if frame_rate:
            env.update(DXVK_CONFIG=f"dxvk.maxFrameRate = {frame_rate}", VKD3D_FRAME_RATE=str(frame_rate))
        return env

    @classmethod
    def _override_env_value(cls, value: Optional[str]) -> list:
        return ["", True] if value is None else [cls._escape_value(value), False]

    @staticmethod
    def _managed_value(current, desired, previous):
        original, applied = previous
        if current != applied:
            original = current
        if desired is None:
            return original, None
        return desired, [original, desired] if original != desired else None

    def _write_override(self, path: Path, content: str, mode: int) -> None:
        home_stat = self.user_home.stat()
        owner = (home_stat.st_uid, home_stat.st_gid)
        if os.geteuid() == 0 and owner[0] != 0:
            missing = []
            parent = path.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            if parent.stat().st_uid != owner[0]:
                raise PermissionError(f"Flatpak override directory is not owned by the current user: {parent}")
            for directory in reversed(missing):
                try:
                    directory.mkdir()
                except FileExistsError:
                    if directory.stat().st_uid != owner[0]:
                        raise
                else:
                    os.chown(directory, *owner, follow_symlinks=False)
        if content:
            self._write_file(path, content, mode, owner=owner if os.geteuid() == 0 else None)
        else:
            path.unlink(missing_ok=True)

    def _update_app_override(
        self, app_id: str, state: dict, *, workarounds: Optional[dict] = None, remove: bool = False,
    ) -> None:
        path = self._override_path(app_id)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise RuntimeError(f"Flatpak override is not a regular file: {path}")
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        config = self._override_config(before)
        context = config.setdefault("Context", {})
        environment = config.setdefault("Environment", {})
        before_context = context.copy()
        before_environment = environment.copy()
        unset = self._list_value(context.get("unset-environment", ""))
        filesystems = self._list_value(context.get("filesystems", ""))
        paths = {str(p): self._filesystem_aliases(p) for p in (self.config_dir, self._dll_directory())}

        apps = state["prepared_apps"]
        entry = apps.get(app_id, {})
        changes = entry.get("override_changes", {})
        old_env = changes.get("environment", {})
        old_fs = changes.get("filesystems", {})
        desired_env = {} if remove else self._launch_environment(app_id, workarounds)
        if app_id in apps and "override_changes" not in entry:
            # Legacy overrides only recorded the settings the plugin wrote.
            for key, value in self._launch_environment(app_id, entry.get("workaround_state")).items():
                current = [environment.get(key), key in unset]
                expected = self._override_env_value(value)
                if current == expected or (value is None and current == [None, True]):
                    old_env[key] = [[None, False], current]
            for host, aliases in paths.items():
                matching = [item for item in filesystems if self._filesystem_key(item) in aliases]
                if len(matching) == 1 and not matching[0].startswith("!") and matching[0].endswith(":ro"):
                    old_fs[host] = [[], matching]

        next_env = {}
        for key in sorted(old_env.keys() | desired_env.keys()):
            current = [environment.get(key), key in unset]
            desired = self._override_env_value(desired_env[key]) if key in desired_env else None
            previous = old_env.get(key, [current, current])
            if key == "DXVK_CONFIG" and desired is not None:
                baseline = previous[0] if current == previous[1] else current
                if baseline[0] and not baseline[1]:
                    options = [
                        segment for segment in baseline[0].split(";")
                        if re.match(r"(?:[ \t]|\\[nrts])*dxvk\.maxFrameRate[ \t]*=", segment) is None
                    ]
                    options.append(desired[0])
                    desired = ["; ".join(option.strip() for option in options if option.strip()), False]
            target, change = self._managed_value(current, desired, previous)
            if change is not None:
                next_env[key] = change
            value, is_unset = target
            if value is None:
                environment.pop(key, None)
            else:
                environment[key] = value
            if is_unset and key not in unset:
                unset.append(key)
            if not is_unset and key in unset:
                unset.remove(key)

        next_fs = {}
        for host in sorted(old_fs.keys() | paths.keys()):
            aliases = self._filesystem_aliases(Path(host))
            current = [item for item in filesystems if self._filesystem_key(item) in aliases]
            desired = None
            if not remove and host in paths:
                desired = current if current and all(not item.startswith("!") for item in current) else [
                    self._escape_value(host).replace(";", "\\;") + ":ro"
                ]
            target, change = self._managed_value(current, desired, old_fs.get(host, [current, current]))
            if change is not None:
                next_fs[host] = change
            if target != current:
                filesystems = [item for item in filesystems if item not in current] + target

        for key, values in (("filesystems", filesystems), ("unset-environment", unset)):
            if values:
                context[key] = ";".join(values) + ";"
            else:
                context.pop(key, None)
        after = before
        if context != before_context or environment != before_environment:
            for section in ("Context", "Environment"):
                if not config[section]:
                    del config[section]
            after = "\n".join(
                f"[{group}]\n" + "".join(f"{key}={value}\n" for key, value in entries.items())
                for group, entries in config.items()
            )

        if remove:
            apps.pop(app_id)
        else:
            apps[app_id] = {
                **entry,
                "override_changes": {"environment": next_env, "filesystems": next_fs},
                **({"workaround_state": workarounds} if workarounds is not None else {}),
            }
        if after != before:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if current != before:
                raise RuntimeError(f"Flatpak override changed during the update for {app_id}. Retry the operation.")
            self._write_override(path, after, mode)
        try:
            self._write_state(state)
        except Exception:
            if after != before and (path.read_text(encoding="utf-8") if path.exists() else "") == after:
                self._write_override(path, before, mode)
            raise

    @staticmethod
    def _parse_process_start_time(stat_content: str) -> Optional[int]:
        closing_command = stat_content.rfind(")")
        if closing_command < 0:
            return None
        fields = stat_content[closing_command + 2:].split()
        if len(fields) <= 19:
            return None
        try:
            return int(fields[19])
        except (TypeError, ValueError):
            return None

    @classmethod
    def _process_start_time(cls, pid: str) -> Optional[int]:
        if not isinstance(pid, str) or re.fullmatch(r"[0-9]+", pid) is None:
            return None
        try:
            stat_content = (Path("/proc") / pid / "stat").read_text(encoding="utf-8")
        except OSError:
            return None
        return cls._parse_process_start_time(stat_content)

    def _resolve_runtime(self, app_id: str) -> tuple[str, str]:
        self._validate_app_id(app_id)
        if not self.check_flatpak_available():
            raise FileNotFoundError("Flatpak is not available on this system")
        result = self._run_flatpak_command(
            ["info", "--show-runtime", app_id],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise OSError(result.stderr.strip() or f"Could not inspect Flatpak app {app_id}")
        runtime = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        parts = runtime.split("/")
        if len(parts) != 3:
            raise ValueError(f"Unsupported Flatpak runtime reference: {runtime}")
        if parts[0] == "org.freedesktop.Platform":
            return runtime, self._validate_runtime(parts[2])
        if parts[0] not in self.DERIVED_RUNTIME_IDS:
            raise ValueError(f"Unsupported Flatpak runtime reference: {runtime}")
        metadata_result = self._run_flatpak_command(
            ["info", "--show-metadata", runtime],
            capture_output=True,
            text=True,
        )
        if metadata_result.returncode != 0:
            raise OSError(metadata_result.stderr.strip() or f"Could not inspect Flatpak runtime {runtime}")
        return runtime, self.runtime_branch_from_metadata(metadata_result.stdout)

    def _dll_directory(self) -> Path:
        if self.config_file_path.exists():
            try:
                content = self.config_file_path.read_text(encoding="utf-8")
                match = re.search(
                    r'(?m)^[ \t]*dll[ \t]*=[ \t]*"((?:\\.|[^"\\])*)"',
                    content,
                )
                if match:
                    configured_dll = json.loads('"' + match.group(1) + '"')
                    if configured_dll:
                        return Path(configured_dll).parent
            except Exception:
                pass
        return self.user_home / ".local/share/Steam/steamapps/common/Lossless Scaling"

    def _filesystem_present(self, entries: str, host_path: Path) -> bool:
        accepted = self._filesystem_aliases(host_path)
        matching = [item for item in self._list_value(entries) if self._filesystem_key(item) in accepted]
        return bool(matching) and all(not item.startswith("!") for item in matching)

    def _app_override_status(self, app_id: str) -> Dict[str, object]:
        result = self._run_flatpak_command(
            ["override", "--user", "--show", self._validate_app_id(app_id)],
            capture_output=True,
            text=True,
            check=True,
        )
        config = self._override_config(result.stdout)
        context = config.get("Context", {})
        environment = config.get("Environment", {})
        filesystems = context.get("filesystems", "")
        unset = self._list_value(context.get("unset-environment", ""))
        config_ready = self._filesystem_present(filesystems, self.config_dir)
        dll_ready = self._filesystem_present(filesystems, self._dll_directory())
        env_ready = (
            environment.get("LSFGVK_CONFIG") == self._escape_value(str(self.config_file_path))
            and environment.get("LSFGVK_FLATPAK") == "1"
            and "DISABLE_LSFGVK" in unset
            and "DISABLE_LSFG" in unset
        )
        return {
            "profile_selector": environment.get("LSFGVK_PROFILE") if "LSFGVK_PROFILE" not in unset else None,
            "filesystem_ready": config_ready and dll_ready,
            "environment_ready": env_ready,
            "prepared": config_ready and dll_ready and env_ready,
        }

    def get_extension_status(self):
        try:
            available = self.check_flatpak_available()
            installed = self._installed_extension_branches() if available else set()
            return self._success_response(
                dict,
                "Flatpak runtime extension status retrieved" if available else "Flatpak is not available",
                available=available,
                extension_id=self.EXTENSION_ID,
                supported_branches=list(self.SUPPORTED_RUNTIMES),
                installed_branches=sorted(installed),
            )
        except Exception as error:
            return self._error_response(
                dict,
                str(error),
                available=False,
                extension_id=self.EXTENSION_ID,
                supported_branches=list(self.SUPPORTED_RUNTIMES),
                installed_branches=[],
            )

    get_flatpak_support_status = get_extension_status

    def install_extension(self, branch: str):
        try:
            branch = self._validate_runtime(branch)
            if not self.check_flatpak_available():
                raise FileNotFoundError("Flatpak is not available on this system")
            with self._lock:
                if any(branch in self._installed_extension_branches(scope) for scope in ("user", "system")):
                    return self._extension_result(branch, True, False, "is ready")
                state = self._read_state()
                self._ensure_user_flathub_remote()
                result = self._run_flatpak_command(
                    [
                        "install",
                        "--user",
                        "--noninteractive",
                        self.FLATHUB_REMOTE,
                        f"{self.EXTENSION_ID}//{branch}",
                    ],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    raise OSError(result.stderr.strip() or "Flatpak installation failed")
                if branch not in self._installed_extension_branches("user"):
                    raise RuntimeError(f"Flatpak install completed but {self._extension_ref(branch)} was not visible afterwards")
                identity = self._extension_identity(branch)
                if identity is not None:
                    state["plugin_owned_branches"][branch] = identity
                self._write_state(state)
                return self._extension_result(branch, True, False, "installed")
        except Exception as error:
            return self._error_response(dict, str(error), runtime_branch=branch, installed=False, enabled=False)

    def _remove_extension(self, branch: str) -> None:
        result = self._run_flatpak_command(
            ["uninstall", "--user", "--noninteractive", self._extension_ref(branch)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise OSError(result.stderr.strip() or "Flatpak uninstall failed")

    def _extension_result(self, branch: str, installed: bool, removed: bool, verb: str):
        return self._success_response(
            dict,
            f"lsfg-vk {branch} runtime extension {verb}",
            runtime_branch=branch,
            installed=installed,
            enabled=installed,
            removed=removed,
        )

    def uninstall_extension(self, branch: str):
        try:
            branch = self._validate_runtime(branch)
            if not self.check_flatpak_available():
                raise FileNotFoundError("Flatpak is not available on this system")
            with self._lock:
                state = self._read_state()
                owned = state["plugin_owned_branches"]
                removed = (
                    branch in owned
                    and branch in self._installed_extension_branches("user")
                    and self._extension_identity(branch) == owned[branch]
                )
                if removed:
                    self._remove_extension(branch)
                if branch in owned:
                    del owned[branch]
                    self._write_state(state)
                installed = branch in self._installed_extension_branches()
                return self._extension_result(
                    branch, installed, removed, "uninstalled" if removed else "preserved (not plugin-owned)"
                )
        except Exception as error:
            return self._error_response(dict, str(error), runtime_branch=branch, removed=False, installed=False, enabled=False)

    def ensure_extension(self, branch: str):
        return self.install_extension(branch)

    def set_extension_enabled(self, branch: str, enabled: bool):
        if type(enabled) is not bool:
            return self._error_response(dict, "enabled must be a boolean", runtime_branch=branch, installed=False, enabled=False)
        return self.install_extension(branch) if enabled else self.uninstall_extension(branch)

    def get_flatpak_apps(self):
        try:
            if not self.check_flatpak_available():
                raise FileNotFoundError("Flatpak is not available on this system")
            installed_extensions = self._installed_extension_branches()
            state = self._read_state()
            owned_apps = state["prepared_apps"]
            result = self._run_flatpak_command(
                ["list", "--app", "--columns=name,application"],
                capture_output=True,
                text=True,
                check=True,
            )
            apps = []
            for line in result.stdout.splitlines():
                fields = line.split("\t")
                if len(fields) < 2:
                    continue
                name, app_id = fields[0].strip(), fields[1].strip()
                if not app_id:
                    continue
                item = {
                    "app_id": app_id,
                    "app_name": name or app_id,
                    "runtime": None,
                    "runtime_branch": None,
                    "runtime_ready": False,
                    "prepared": False,
                    "owned": app_id in owned_apps,
                    "profile_selector": None,
                    "error": None,
                }
                try:
                    runtime, branch = self._resolve_runtime(app_id)
                    status = self._app_override_status(app_id)
                    item.update({
                        "runtime": runtime,
                        "runtime_branch": branch,
                        "runtime_ready": branch in installed_extensions,
                        "prepared": status["prepared"],
                        "profile_selector": status["profile_selector"],
                    })
                except Exception as error:
                    item["error"] = str(error)
                apps.append(item)
            apps.sort(key=lambda item: str(item["app_name"]).lower())
            return self._success_response(dict, f"Found {len(apps)} Flatpak applications", apps=apps)
        except Exception as error:
            return self._error_response(dict, str(error), apps=[])

    def prepare_app(self, app_id: str, workarounds: Optional[dict] = None):
        try:
            app_id = self._validate_app_id(app_id)
            with self._lock:
                runtime, branch = self._resolve_runtime(app_id)
                extension = self.ensure_extension(branch)
                if not extension.get("success") or not extension.get("installed"):
                    raise RuntimeError(extension.get("error") or f"Could not install Flatpak runtime {branch}")
                state = self._read_state()
                entry = state["prepared_apps"].get(app_id, {})
                if workarounds is None:
                    workarounds = entry.get("workaround_state")
                self._update_app_override(app_id, state, workarounds=workarounds)
                return self._success_response(
                    dict,
                    "Flatpak application prepared for lsfg-vk",
                    app_id=app_id,
                    runtime=runtime,
                    runtime_branch=branch,
                    prepared=True,
                    owned=True,
                )
        except Exception as error:
            return self._error_response(dict, str(error), app_id=app_id, prepared=False, owned=False)

    def remove_app_override(self, app_id: str):
        try:
            app_id = self._validate_app_id(app_id)
            with self._lock:
                state = self._read_state()
                apps = state["prepared_apps"]
                if app_id not in apps:
                    return self._success_response(
                        dict,
                        "Flatpak application is not plugin-owned; existing overrides were preserved",
                        app_id=app_id,
                        prepared=self._app_override_status(app_id)["prepared"],
                        owned=False,
                    )
                self._update_app_override(app_id, state, remove=True)
                return self._success_response(
                    dict,
                    "Plugin-owned Flatpak override removed",
                    app_id=app_id,
                    prepared=False,
                    owned=False,
                )
        except Exception as error:
            return self._error_response(dict, str(error), app_id=app_id, prepared=False, owned=True)

    def remove_plugin_owned_environment(self):
        try:
            with self._lock:
                state = self._read_state()
                failures = []
                removed_apps = []
                for app_id in list(state["prepared_apps"]):
                    result = self.remove_app_override(app_id)
                    if result.get("success"):
                        removed_apps.append(app_id)
                    else:
                        failures.append(f"{app_id}: {result.get('error')}")
                if failures:
                    return self._error_response(
                        dict,
                        "; ".join(failures),
                        removed_apps=removed_apps,
                        removed_branches=[],
                    )
                state = self._read_state()
                removed_branches = []
                for branch in sorted(state["plugin_owned_branches"]):
                    result = self.uninstall_extension(branch)
                    if result.get("removed"):
                        removed_branches.append(branch)
                    if not result.get("success"):
                        failures.append(f"{branch}: {result.get('error')}")
                if failures:
                    return self._error_response(
                        dict,
                        "; ".join(failures),
                        removed_apps=removed_apps,
                        removed_branches=removed_branches,
                    )
                return self._success_response(
                    dict,
                    "Plugin-owned Flatpak state removed",
                    removed_apps=removed_apps,
                    removed_branches=removed_branches,
                )
        except Exception as error:
            return self._error_response(dict, str(error), removed_apps=[], removed_branches=[])
