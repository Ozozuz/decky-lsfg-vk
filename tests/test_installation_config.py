import io
import os
import sys
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


sys.modules.setdefault(
    "decky",
    types.SimpleNamespace(DECKY_USER_HOME="/home/deck", logger=Mock()),
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py_modules"))

from lsfg_vk.config_schema import ConfigurationManager, UnsupportedConfigurationVersion
from lsfg_vk.installation import InstallationService


class InstallationConfigTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name) / "home" / "deck"
        self.home.mkdir(parents=True)
        self.steam = Mock()
        self.steam.find_lsfg_vk_dll.return_value = None
        self.service = InstallationService(
            logger=Mock(),
            runtime_service=Mock(),
            steam_service=self.steam,
        )
        self.service.user_home = self.home
        self.service.local_bin_dir = self.home / ".local/bin"
        self.service.local_lib_dir = self.home / ".local/lib"
        self.service.local_share_dir = self.home / ".local/share/vulkan/implicit_layer.d"
        self.service.config_dir = self.home / ".config/lsfg-vk"
        self.service.config_file_path = self.service.config_dir / "conf.toml"
        self.service.cli_file = self.service.local_bin_dir / "lsfg-vk-cli"
        self.service.lib_file = self.service.local_lib_dir / "liblsfg-vk-layer.so"
        self.service.lib_x86_file = self.service.local_lib_dir / "liblsfg-vk-layer.x86.so"
        self.service.json_file = self.service.local_share_dir / "VkLayer_LSFGVK_frame_generation.json"
        self.service.json_x86_file = self.service.local_share_dir / "VkLayer_LSFGVK_frame_generation.x86.json"
        self.service.legacy_lib_file = self.service.local_lib_dir / "legacy.so"
        self.service.legacy_json_file = self.service.local_share_dir / "legacy.json"

    def tearDown(self):
        self.tempdir.cleanup()

    def _write_config(self, dll=""):
        self.service.config_dir.mkdir(parents=True, exist_ok=True)
        self.service.config_file_path.write_text(
            ConfigurationManager.generate_toml_content_multi_profile({
                "profiles": {"Test game": {"active_in": ["123"]}},
                "global_config": {"dll": dll, "no_fp16": False},
            }),
            encoding="utf-8",
        )

    def _mark_installed(self):
        for path in (
            self.service.cli_file,
            self.service.lib_file,
            self.service.lib_x86_file,
            self.service.json_file,
            self.service.json_x86_file,
            self.service.config_file_path,
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("installed", encoding="utf-8")

    def _archive(self, *, omit=None):
        path = self.home / "payload.tar"
        with tarfile.open(path, "w") as archive:
            for name in self.service._payload_destinations():
                if name == omit:
                    continue
                member = tarfile.TarInfo(name)
                content = b"new payload"
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        return path

    def test_installation_backs_up_v1_profiles(self):
        self.service.config_dir.mkdir(parents=True)
        original = 'version = 1\n\n[[profile]]\nname = "Old Game"\n'
        self.service.config_file_path.write_text(original, encoding="utf-8")

        self.service._install_archive(self._archive())

        backup = self.service.config_file_path.with_name("conf.toml.v1.bak")
        self.assertEqual(backup.read_text(encoding="utf-8"), original)
        data = ConfigurationManager.parse_toml_content_multi_profile(
            self.service.config_file_path.read_text(encoding="utf-8")
        )
        self.assertEqual(data["profiles"], {})
        self.assertEqual(data["global_config"], {"dll": "", "no_fp16": False})
        self.service.log.warning.assert_called_once()

    def test_uninstall_removes_matching_installed_payload_only(self):
        self._write_config()
        self.service.cli_file.parent.mkdir(parents=True, exist_ok=True)
        self.service.cli_file.write_bytes(b"manual")
        self.service._install_archive(self._archive())
        self.service.lib_x86_file.write_bytes(b"user replacement")

        result = self.service.uninstall()

        self.assertTrue(result["success"])
        self.assertIn(str(self.service.lib_file), result["removed_files"])
        self.assertFalse(self.service.lib_file.exists())
        self.assertTrue(self.service.cli_file.exists())
        self.assertEqual(self.service.lib_x86_file.read_bytes(), b"user replacement")
        self.assertFalse(self.service.config_file_path.exists())

    def test_installation_backs_up_an_unversioned_configuration(self):
        self.service.config_dir.mkdir(parents=True)
        original = b'[global]\nallow_fp16 = false\n'
        self.service.config_file_path.write_bytes(original)
        self._mark_installed()
        self.assertFalse(self.service.check_installation()["installed"])
        self.assertEqual(self.service.config_file_path.read_bytes(), original)

        self.service._install_archive(self._archive())

        backup = self.service.config_file_path.with_name("conf.toml.unversioned.bak")
        self.assertEqual(backup.read_bytes(), original)

    def test_installation_does_not_overwrite_a_future_config_version(self):
        self.service.config_dir.mkdir(parents=True)
        original = "version = 3\n\n[global]\nallow_fp16 = true\n"
        self.service.config_file_path.write_text(original, encoding="utf-8")

        with self.assertRaises(UnsupportedConfigurationVersion):
            self.service._install_archive(self._archive())

        self.assertEqual(self.service.config_file_path.read_text(encoding="utf-8"), original)

    def test_installation_preserves_an_invalid_config(self):
        self.service.config_dir.mkdir(parents=True)
        original = "invalid = ["
        self.service.config_file_path.write_text(original, encoding="utf-8")

        with self.assertRaises(ValueError):
            self.service._install_archive(self._archive())

        self.assertEqual(self.service.config_file_path.read_text(encoding="utf-8"), original)
        self.assertFalse(self.service.cli_file.exists())

    def test_missing_payload_preserves_installed_and_legacy_files(self):
        self._write_config()
        self._mark_installed()
        self.service.legacy_lib_file.write_bytes(b"legacy")
        original_config = self.service.config_file_path.read_bytes()
        missing = next(name for name, (destination, _) in self.service._payload_destinations().items()
                       if destination == self.service.lib_x86_file)

        with self.assertRaisesRegex(OSError, "Archive is missing required files"):
            self.service._install_archive(self._archive(omit=missing))

        self.assertEqual(self.service.cli_file.read_bytes(), b"installed")
        self.assertEqual(self.service.config_file_path.read_bytes(), original_config)
        self.assertEqual(self.service.legacy_lib_file.read_bytes(), b"legacy")

    def test_validates_with_staged_cli_before_replacing_files(self):
        self._write_config()
        self._mark_installed()

        def reject(_content, *, cli_path):
            self.assertEqual(cli_path.read_bytes(), b"new payload")
            self.assertEqual(self.service.cli_file.read_bytes(), b"installed")
            raise ValueError("CLI rejected configuration")

        self.service.runtime_service.validate_config_content.side_effect = reject
        with self.assertRaisesRegex(ValueError, "CLI rejected"):
            self.service._install_archive(self._archive())
        self.assertEqual(self.service.cli_file.read_bytes(), b"installed")

    def test_failed_commit_restores_the_previous_payload(self):
        self._write_config()
        self._mark_installed()
        original_config = self.service.config_file_path.read_bytes()
        replace = os.replace

        def fail_late(source, destination):
            if destination == self.service.config_file_path:
                raise OSError("could not replace config")
            return replace(source, destination)

        with patch("lsfg_vk.installation.os.replace", side_effect=fail_late):
            with self.assertRaisesRegex(OSError, "could not replace config"):
                self.service._install_archive(self._archive())

        self.assertEqual(self.service.cli_file.read_bytes(), b"installed")
        self.assertEqual(self.service.lib_file.read_bytes(), b"installed")
        self.assertEqual(self.service.config_file_path.read_bytes(), original_config)

    def test_check_installation_repairs_dll_path_after_branch_becomes_available(self):
        dll_path = self.home / "steamapps/common/Lossless Scaling/lsfg-vk.dll"
        dll_path.parent.mkdir(parents=True)
        dll_path.write_text("dll", encoding="utf-8")
        self.steam.find_lsfg_vk_dll.return_value = str(dll_path)
        self.service.runtime_service.check_lossless_scaling.return_value = {
            "installed": True,
            "status": "Lossless Scaling detected by lsfg-vk",
        }
        self._write_config()
        self._mark_installed()

        result = self.service.check_installation()

        self.assertTrue(result["installed"])
        self.assertTrue(result["lossless_scaling_installed"])
        data = ConfigurationManager.parse_toml_content_multi_profile(
            self.service.config_file_path.read_text(encoding="utf-8")
        )
        self.assertEqual(data["global_config"]["dll"], str(dll_path))
        self.assertEqual(data["profiles"]["Test game"]["active_in"], ["123"])
        self.service.runtime_service.validate_config_content.assert_called_once()

    def test_check_installation_does_not_write_dll_path_on_wrong_branch(self):
        self.service.runtime_service.check_lossless_scaling.return_value = {
            "installed": False,
            "status": "Lossless Scaling's lsfg-vk.dll is not configured",
        }
        self._write_config()
        self._mark_installed()

        result = self.service.check_installation()

        self.assertTrue(result["installed"])
        self.assertFalse(result["lossless_scaling_installed"])
        data = ConfigurationManager.parse_toml_content_multi_profile(
            self.service.config_file_path.read_text(encoding="utf-8")
        )
        self.assertEqual(data["global_config"]["dll"], "")
        self.service.runtime_service.validate_config_content.assert_not_called()


if __name__ == "__main__":
    unittest.main()
