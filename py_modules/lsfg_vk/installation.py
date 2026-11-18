import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Dict

from .base_service import BaseService
from .config_schema import ConfigurationManager, ProfileData, UnsupportedConfigurationVersion
from .constants import (
    ARCHIVE_FILENAME,
    BIN_DIR,
    CLI_FILENAME,
    JSON_FILENAME,
    JSON_X86_FILENAME,
    LEGACY_JSON_FILENAME,
    LEGACY_LIB_FILENAME,
    LIB_FILENAME,
    LIB_X86_FILENAME,
    LOCAL_SHARE,
    UI_DESKTOP_FILENAME,
    UI_FILENAME,
    UI_ICON_FILENAME,
)
from .runtime_service import RuntimeService
from .steam_service import SteamService
from .types import InstallationCheckResponse, InstallationResponse, UninstallationResponse


class InstallationService(BaseService):
    def __init__(
        self,
        logger=None,
        runtime_service: RuntimeService = None,
        steam_service: SteamService = None,
    ):
        super().__init__(logger)
        self.runtime_service = runtime_service or RuntimeService(logger=self.log)
        self.steam_service = steam_service or SteamService(logger=self.log)
        self.lib_file = self.local_lib_dir / LIB_FILENAME
        self.lib_x86_file = self.local_lib_dir / LIB_X86_FILENAME
        self.json_file = self.local_share_dir / JSON_FILENAME
        self.json_x86_file = self.local_share_dir / JSON_X86_FILENAME
        self.cli_file = self.local_bin_dir / CLI_FILENAME
        self.legacy_lib_file = self.local_lib_dir / LEGACY_LIB_FILENAME
        self.legacy_json_file = self.local_share_dir / LEGACY_JSON_FILENAME

    def install(self) -> InstallationResponse:
        try:
            archive_path = Path(__file__).parent.parent.parent / BIN_DIR / ARCHIVE_FILENAME
            if not archive_path.exists():
                raise FileNotFoundError(f"{ARCHIVE_FILENAME} not found at {archive_path}")
            self._ensure_directories()
            self._install_archive(archive_path)
            return self._success_response(InstallationResponse, "lsfg-vk 2.0.0 installed successfully")
        except Exception as error:
            self.log.error(f"Error installing lsfg-vk: {error}")
            return self._error_response(InstallationResponse, str(error))

    def _payload_destinations(self) -> Dict[str, tuple[Path, int]]:
        return {
            f"bin/{CLI_FILENAME}": (self.cli_file, 0o755),
            f"bin/{UI_FILENAME}": (self.local_bin_dir / UI_FILENAME, 0o755),
            f"lib/{LIB_FILENAME}": (self.lib_file, 0o644),
            f"lib/{LIB_X86_FILENAME}": (self.lib_x86_file, 0o644),
            f"share/vulkan/implicit_layer.d/{JSON_FILENAME}": (self.json_file, 0o644),
            f"share/vulkan/implicit_layer.d/{JSON_X86_FILENAME}": (self.json_x86_file, 0o644),
            f"share/applications/{UI_DESKTOP_FILENAME}": (
                self.user_home / LOCAL_SHARE / "applications" / UI_DESKTOP_FILENAME,
                0o644,
            ),
            f"share/icons/hicolor/256x256/apps/{UI_ICON_FILENAME}": (
                self.user_home / LOCAL_SHARE / "icons/hicolor/256x256/apps" / UI_ICON_FILENAME,
                0o644,
            ),
        }

    @property
    def _ownership_path(self) -> Path:
        return self.config_dir / "installation_state.json"

    @staticmethod
    def _payload_hash(path: Path) -> str | None:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as content:
            return hashlib.file_digest(content, "sha256").hexdigest()

    def _owned_payload(self) -> dict[str, str]:
        if self._ownership_path.is_symlink():
            raise ValueError("LSFG-VK installation metadata is a symlink")
        if not self._ownership_path.exists():
            return {}
        data = json.loads(self._ownership_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or any(
            not isinstance(digest, str) or len(digest) != 64 for digest in data.values()
        ):
            raise ValueError("Invalid LSFG-VK installation metadata")
        return data

    def _install_archive(self, archive_path: Path) -> None:
        original_config = self.config_file_path.read_bytes() if self.config_file_path.exists() else None
        profile_data, legacy_version = self._prepare_config(original_config.decode("utf-8") if original_config is not None else None)
        content = ConfigurationManager.generate_toml_content_multi_profile(profile_data)
        destinations = self._payload_destinations()
        previous_ownership = self._owned_payload()
        owned = {}
        with ExitStack() as staging, tarfile.open(archive_path, "r:*") as archive:
            members = {
                member.name.removeprefix("./"): member
                for member in archive.getmembers()
                if member.isfile()
            }
            missing = sorted(set(destinations) - members.keys())
            if missing:
                raise OSError("Archive is missing required files: " + ", ".join(missing))

            directories: dict[Path, Path] = {}
            keep_staging = False

            def clean_stage(directory: Path) -> None:
                if not keep_staging:
                    shutil.rmtree(directory)


            def stage_path(destination: Path) -> Path:
                parent = destination.parent
                if parent not in directories:
                    parent.mkdir(parents=True, exist_ok=True)
                    directories[parent] = Path(tempfile.mkdtemp(dir=parent, prefix=".lsfg-install-"))
                    staging.callback(clean_stage, directories[parent])
                return directories[parent] / destination.name

            staged: dict[Path, Path | None] = {}
            for source_path, (destination, mode) in destinations.items():
                path = stage_path(destination)
                source = archive.extractfile(members[source_path])
                if source is None:
                    raise OSError(f"Could not read archive member {source_path}")
                with source, path.open("wb") as target:
                    shutil.copyfileobj(source, target)
                    target.flush()
                    os.fsync(target.fileno())
                path.chmod(mode)
                staged[destination] = path
                existing = destination.exists() or destination.is_symlink()
                if not existing or (
                    source_path in previous_ownership
                    and self._payload_hash(destination) == previous_ownership[source_path]
                ):
                    owned[source_path] = self._payload_hash(path)

            self.runtime_service.validate_config_content(content, cli_path=staged[self.cli_file])
            config = stage_path(self.config_file_path)
            self._write_file(config, content)
            staged[self.config_file_path] = config
            if legacy_version is not None:
                backup = self.config_file_path.with_name(f"{self.config_file_path.name}.{legacy_version}.bak")
                if backup.exists() or backup.is_symlink():
                    raise FileExistsError(f"Move the existing legacy configuration backup before reinstalling: {backup}")
                backup_stage = stage_path(backup)
                backup_stage.write_bytes(original_config)
                backup_stage.chmod(0o600)
                staged[backup] = stage_path(backup)

            current_config = self.config_file_path.read_bytes() if self.config_file_path.exists() else None
            if current_config != original_config:
                raise RuntimeError("LSFG-VK configuration changed during installation. Retry the operation.")
            ownership = stage_path(self._ownership_path)
            self._write_file(ownership, json.dumps(owned, indent=2) + "\n", 0o600)
            staged[self._ownership_path] = ownership
            staged.update({self.legacy_lib_file: None, self.legacy_json_file: None})
            backups = {}
            for destination in staged:
                if destination.exists() or destination.is_symlink():
                    path = stage_path(destination).with_name(f"backup-{destination.name}")
                    os.link(destination, path, follow_symlinks=False)
                    backups[destination] = path

            applied = []
            try:
                for destination, path in staged.items():
                    if path is None:
                        destination.unlink(missing_ok=True)
                    else:
                        os.replace(path, destination)
                    applied.append(destination)
            except OSError as error:
                rollback_errors = []
                for destination in reversed(applied):
                    try:
                        if destination in backups:
                            os.replace(backups[destination], destination)
                        else:
                            destination.unlink(missing_ok=True)
                    except OSError as rollback_error:
                        rollback_errors.append(str(rollback_error))
                if rollback_errors:
                    keep_staging = True
                    locations = ", ".join(str(directory) for directory in directories.values())
                    raise OSError(f"{error}. Rollback failed: {', '.join(rollback_errors)}. Backups: {locations}") from error
                raise
            if legacy_version is not None:
                self.log.warning(f"Backed up legacy lsfg-vk configuration at {backup}")

    def _default_config(self) -> ProfileData:
        defaults = ConfigurationManager.get_defaults()
        return ProfileData(
            profiles={},
            global_config={"dll": defaults["dll"], "no_fp16": defaults["no_fp16"]},
        )

    def _prepare_config(self, content: str | None) -> tuple[ProfileData, str | None]:
        legacy_version = None
        try:
            profile_data = (
                ConfigurationManager.parse_toml_content_multi_profile(content)
                if content is not None
                else self._default_config()
            )
        except UnsupportedConfigurationVersion as error:
            if not ConfigurationManager.is_discardable_legacy_version(error.version):
                raise
            legacy_version = "unversioned" if error.version is None else f"v{error.version}"
            profile_data = self._default_config()
        self._resolve_dll_path(profile_data)
        defaults = ConfigurationManager.get_defaults()
        for name, profile in profile_data["profiles"].items():
            profile_data["profiles"][name] = ConfigurationManager.validate_config(
                {**defaults, **profile, **profile_data["global_config"]}
            )
        return profile_data, legacy_version

    def _resolve_dll_path(self, profile_data: ProfileData) -> bool:
        current_path = str(profile_data["global_config"].get("dll") or "")
        if current_path and Path(current_path).is_file():
            return False
        dll_path = self.steam_service.find_lsfg_vk_dll()
        if dll_path and current_path != dll_path:
            profile_data["global_config"]["dll"] = dll_path
            return True
        return False

    def _repair_dll_path(self, profile_data: ProfileData) -> None:
        try:
            if not self._resolve_dll_path(profile_data):
                return
            content = ConfigurationManager.generate_toml_content_multi_profile(profile_data)
            self.runtime_service.validate_config_content(content)
            self._write_file(self.config_file_path, content, 0o644)
            self.log.info("Repaired lsfg-vk DLL path")
        except Exception as error:
            self.log.warning(f"Could not repair lsfg-vk DLL path: {error}")

    def check_installation(self) -> InstallationCheckResponse:
        try:
            installed = all(
                path.is_file()
                for path in (
                    self.cli_file,
                    self.lib_file,
                    self.lib_x86_file,
                    self.json_file,
                    self.json_x86_file,
                    self.config_file_path,
                )
            )
            if installed:
                try:
                    profile_data = ConfigurationManager.parse_toml_content_multi_profile(
                        self.config_file_path.read_text(encoding="utf-8")
                    )
                    self._repair_dll_path(profile_data)
                except UnsupportedConfigurationVersion as error:
                    if ConfigurationManager.is_discardable_legacy_version(error.version):
                        return {
                            "installed": False,
                            "lossless_scaling_installed": False,
                            "lossless_scaling_status": "Select Install LSFG-VK to back up and upgrade the existing configuration.",
                            "error": None,
                        }
                except ValueError:
                    pass
            lossless_scaling = self.runtime_service.check_lossless_scaling()
            return {
                "installed": installed,
                "lossless_scaling_installed": bool(lossless_scaling["installed"]),
                "lossless_scaling_status": str(lossless_scaling["status"]),
                "error": None,
            }
        except Exception as error:
            return {
                "installed": False,
                "lossless_scaling_installed": False,
                "lossless_scaling_status": str(error),
                "error": str(error),
            }

    def uninstall(self) -> UninstallationResponse:
        try:
            owned = self._owned_payload()
            removed = []
            preserved = []
            for source, (path, _) in self._payload_destinations().items():
                if source in owned and self._payload_hash(path) == owned[source]:
                    self._remove_if_exists(path)
                    removed.append(str(path))
                elif path.exists() or path.is_symlink():
                    preserved.append(path)
            self._ownership_path.unlink(missing_ok=True)
            message = (
                "Only plugin-owned LSFG-VK files were removed. Other installation files and profiles were kept."
                if preserved else "LSFG-VK plugin files removed. Profiles were kept."
            )
            return self._success_response(UninstallationResponse, message, removed_files=removed or None)
        except Exception as error:
            return self._error_response(UninstallationResponse, str(error), removed_files=None)
