import sys
import tempfile
import tomllib
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
from lsfg_vk.configuration import ConfigurationService


class ConfigurationProfileTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name) / "home" / "deck"
        self.home.mkdir(parents=True)
        self.runtime = Mock()
        self.service = ConfigurationService(runtime_service=self.runtime)
        self.service.user_home = self.home
        self.service.config_dir = self.home / ".config/lsfg-vk"
        self.service.config_file_path = self.service.config_dir / "conf.toml"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_selector_only_profile_survives_round_trip(self):
        content = """version = 2

[global]
allow_fp16 = true

[[profile]]
name = "flatpak:org.example.Game"
pacing_mode = "vsync"
multiplier = 3
flow_scale = 0.8
performance_mode = false
override_present_mode = true
preserve_swapchain_image_count = false
"""
        parsed = ConfigurationManager.parse_toml_content_multi_profile(content)
        self.assertIn("flatpak:org.example.Game", parsed["profiles"])
        self.assertEqual(parsed["profiles"]["flatpak:org.example.Game"]["active_in"], [])
        rendered = ConfigurationManager.generate_toml_content_multi_profile(parsed)
        reparsed = ConfigurationManager.parse_toml_content_multi_profile(rendered)
        self.assertEqual(reparsed["profiles"]["flatpak:org.example.Game"]["multiplier"], 3)

    def test_legacy_config_is_preserved_on_read_and_edit(self):
        self.service.config_dir.mkdir(parents=True)
        for original in ('version = 1\n\n[global]\nallow_fp16 = false\n', '[global]\nallow_fp16 = false\n'):
            with self.subTest(original=original):
                self.service.config_file_path.write_text(original, encoding="utf-8")
                self.assertFalse(self.service.get_game_configs()["success"])
                self.assertFalse(self.service.update_global_config({"no_fp16": True})["success"])
                with self.assertRaises(UnsupportedConfigurationVersion):
                    self.service._get_profile_data()
                self.assertEqual(self.service.config_file_path.read_text(encoding="utf-8"), original)
        self.runtime.validate_config_content.assert_not_called()

    def test_patch_preserves_other_profile_fields(self):
        self.assertTrue(self.service.update_game_config("123", "Steam Game", {"multiplier": 3})["success"])
        steam = self.service.update_game_config("123", "Steam Game", {"flow_scale": 0.65}, require_profile=True)
        self.assertEqual(steam["config"]["multiplier"], 3)
        self.assertEqual(steam["config"]["flow_scale"], 0.65)

        app = "org.example.Game"
        self.assertTrue(self.service.update_flatpak_config(app, {"multiplier": 4})["success"])
        flatpak = self.service.update_flatpak_config(app, {"performance_mode": True}, require_profile=True)
        self.assertEqual(flatpak["config"]["multiplier"], 4)
        self.assertTrue(flatpak["config"]["performance_mode"])
        self.service.reset_flatpak_config(app)
        self.assertFalse(self.service.update_flatpak_config(app, {"multiplier": 2}, require_profile=True)["success"])

    def test_future_config_version_is_preserved(self):
        self.service.config_dir.mkdir(parents=True)
        original = "version = 3\n\n[global]\nallow_fp16 = true\n"
        self.service.config_file_path.write_text(original, encoding="utf-8")

        result = self.service.get_game_configs()

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "unsupported lsfg-vk configuration version")
        self.assertEqual(self.service.config_file_path.read_text(encoding="utf-8"), original)

    def test_game_reset_all_preserves_flatpak_profiles(self):
        self.service.update_game_config("123", "Steam Game", {"multiplier": 2})
        self.service.update_flatpak_config("org.example.Game", {"multiplier": 3})

        result = self.service.reset_all_game_configs()
        data = self.service._get_profile_data()

        self.assertTrue(result["success"])
        self.assertNotIn("Steam Game", data["profiles"])
        self.assertIn("flatpak:org.example.Game", data["profiles"])
        self.assertEqual(data["profiles"]["flatpak:org.example.Game"]["multiplier"], 3)

    def test_scoped_game_reset_is_one_write_and_preserves_other_profiles(self):
        self.service.update_game_config("123", "Steam Game", {"multiplier": 2})
        self.service.update_game_config("456", "Non-Steam Game", {"multiplier": 3})
        self.service.update_flatpak_config("org.example.Game", {"multiplier": 4})

        with patch.object(self.service, "_save_profile_data", wraps=self.service._save_profile_data) as save:
            result = self.service.reset_game_configs(["123"])

        data = self.service._get_profile_data()
        self.assertTrue(result["success"])
        self.assertEqual(save.call_count, 1)
        self.assertNotIn("Steam Game", data["profiles"])
        self.assertIn("Non-Steam Game", data["profiles"])
        self.assertIn("flatpak:org.example.Game", data["profiles"])

    def test_global_config_update_does_not_change_profile_values(self):
        self.service.update_game_config("123", "Steam Game", {"multiplier": 2})

        result = self.service.update_global_config({"no_fp16": True})
        data = self.service._get_profile_data()

        self.assertTrue(result["success"])
        self.assertTrue(result["global_config"]["no_fp16"])
        self.assertTrue(data["global_config"]["no_fp16"])
        self.assertEqual(data["profiles"]["Steam Game"]["multiplier"], 2)

    def test_profile_and_global_edits_preserve_logging_settings(self):
        self.service.config_file_path.parent.mkdir(parents=True)
        content = (
            'version = 2\n[global]\nlog_level = "debug"\nlog_file = "/tmp/lsfg 🎮.log"\n'
        )
        self.service.config_file_path.write_text(content, encoding="utf-8")

        for result in (
            self.service.update_game_config("123", "Steam Game", {"multiplier": 3}),
            self.service.update_global_config({"no_fp16": True}),
        ):
            self.assertTrue(result["success"])
        saved = tomllib.loads(self.service.config_file_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["global"]["log_level"], "debug")
        self.assertEqual(saved["global"]["log_file"], "/tmp/lsfg 🎮.log")

    def test_profile_update_cannot_overwrite_global_fp16_setting(self):
        self.service.update_global_config({"no_fp16": True})

        self.service.update_game_config("123", "Steam Game", {"multiplier": 3, "no_fp16": False})

        data = self.service._get_profile_data()
        self.assertTrue(data["global_config"]["no_fp16"])


if __name__ == "__main__":
    unittest.main()
