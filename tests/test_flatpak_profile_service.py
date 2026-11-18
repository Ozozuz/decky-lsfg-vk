import sys
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

from lsfg_vk.configuration import ConfigurationService
from lsfg_vk.flatpak_service import FlatpakService
from lsfg_vk.flatpak_profile_service import FlatpakProfileService


class FakeFlatpakService(FlatpakService):
    def __init__(self, home: Path):
        super().__init__()
        self.user_home = home
        self.config_dir = home / ".config/lsfg-vk"
        self.config_file_path = self.config_dir / "conf.toml"
        self.commands = []
        self.running = ""
        self.start_times = {}

    def _process_start_time(self, pid):
        return self.start_times.get(pid)

    def check_flatpak_available(self):
        return True

    def _run_flatpak_command(self, args, **_kwargs):
        self.commands.append(args)
        if args[:3] == ["override", "--user", "--show"]:
            path = self._override_path(args[3])
            return types.SimpleNamespace(
                returncode=0,
                stdout=path.read_text(encoding="utf-8") if path.exists() else "",
                stderr="",
            )
        if args[:2] == ["info", "--show-runtime"]:
            return types.SimpleNamespace(returncode=0, stdout="org.freedesktop.Platform/x86_64/24.08", stderr="")
        if args[:2] == ["list", "--app"]:
            return types.SimpleNamespace(returncode=0, stdout="Example Game\torg.example.Game\n", stderr="")
        if args[:2] == ["list", "--system"]:
            return types.SimpleNamespace(returncode=0, stdout=f"{self.EXTENSION_ID}\tx86_64\t24.08\n", stderr="")
        if args[:2] == ["list", "--user"]:
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[0] == "ps":
            return types.SimpleNamespace(returncode=0, stdout=self.running, stderr="")
        raise AssertionError(args)


class FlatpakProfileServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name) / "home" / "deck"
        self.home.mkdir(parents=True)
        self.flatpak = FakeFlatpakService(self.home)
        self.runtime = Mock()
        self.configuration = ConfigurationService(runtime_service=self.runtime)
        self.configuration.user_home = self.home
        self.configuration.config_dir = self.flatpak.config_dir
        self.configuration.config_file_path = self.flatpak.config_file_path
        self.service = FlatpakProfileService(self.flatpak, self.configuration)
        self.app_id = "org.example.Game"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_enable_creates_selector_profile_and_default_workarounds(self):
        result = self.service.enable_app(self.app_id)
        config = self.configuration.get_flatpak_config(self.app_id)
        content = self.flatpak._override_path(self.app_id).read_text(encoding="utf-8")

        self.assertTrue(result["success"])
        self.assertTrue(config["exists"])
        self.assertEqual(config["profile"], "flatpak:org.example.Game")
        self.assertEqual(config["config"]["active_in"], [])
        self.assertIn("LSFGVK_PROFILE=flatpak:org.example.Game", content)
        self.assertIn("ENABLE_GAMESCOPE_WSI=0", content)
        self.assertIn("DXVK_HDR=0", content)

    def test_workaround_updates_restore_overlapping_existing_values(self):
        path = self.flatpak._override_path(self.app_id)
        self.flatpak._write_file(
            path,
            "[Context]\nfilesystems=~/Documents;\nsockets=wayland;\n"
            "[Environment]\nDXVK_CONFIG=dxgi.syncInterval = 0\nKEEP=yes\n"
            "[Session Bus Policy]\norg.example.Service=talk\n",
        )
        self.assertTrue(self.service.enable_app(self.app_id)["success"])

        self.assertTrue(self.service.set_workaround_state(self.app_id, {"dxvkFrameRate": 30})["success"])
        result = self.service.set_workaround_state(self.app_id, {"disableHdr": False, "enableZink": True})
        self.assertTrue(result["success"])
        self.assertEqual(result["state"]["dxvkFrameRate"], 30)
        content = path.read_text(encoding="utf-8")
        self.assertIn("DXVK_CONFIG=dxvk.maxFrameRate = 30", content)
        self.assertIn("VKD3D_FRAME_RATE=30", content)
        self.assertIn("KEEP=yes", content)
        self.assertIn("sockets=wayland", content)
        self.assertIn("org.example.Service=talk", content)
        self.assertNotIn("DXVK_HDR=0", content)
        self.assertIn("MESA_LOADER_DRIVER_OVERRIDE=zink", content)

        self.assertTrue(self.service.set_workaround_state(self.app_id, {"dxvkFrameRate": 0})["success"])
        content = path.read_text(encoding="utf-8")
        self.assertNotIn("VKD3D_FRAME_RATE=", content)
        self.assertIn("DXVK_CONFIG=dxgi.syncInterval = 0", content)

    def test_ownership_write_failure_restores_override_and_keeps_profile(self):
        self.assertTrue(self.service.enable_app(self.app_id)["success"])
        path = self.flatpak._override_path(self.app_id)
        before = path.read_bytes()
        with patch.object(self.flatpak, "_write_state", side_effect=OSError("ownership write failed")):
            result = self.service.set_workaround_state(self.app_id, {"dxvkFrameRate": 30})
        self.assertFalse(result["success"])
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(self.configuration.get_flatpak_config(self.app_id)["exists"])
        self.assertEqual(self.service.get_workaround_state(self.app_id)["state"]["dxvkFrameRate"], 0)

    def test_config_update_returns_without_relisting_flatpaks(self):
        self.assertTrue(self.service.enable_app(self.app_id)["success"])
        self.flatpak.get_flatpak_apps = Mock(side_effect=AssertionError("config updates must not relist Flatpaks"))

        result = self.service.update_config(self.app_id, {"multiplier": 4})

        self.assertTrue(result["success"])
        self.assertEqual(result["app_id"], self.app_id)
        self.assertEqual(result["config"]["multiplier"], 4)
        self.flatpak.get_flatpak_apps.assert_not_called()

    def test_remove_preserves_other_override_settings(self):
        self.flatpak._write_file(self.flatpak._override_path(self.app_id), "[Environment]\nKEEP=yes\n")
        self.assertTrue(self.service.enable_app(self.app_id)["success"])

        removed = self.service.remove_app(self.app_id)

        self.assertTrue(removed["success"])
        content = self.flatpak._override_path(self.app_id).read_text(encoding="utf-8")
        self.assertIn("KEEP=yes", content)
        self.assertNotIn("LSFGVK_PROFILE", content)
        self.assertFalse(self.configuration.get_flatpak_config(self.app_id)["exists"])

    def test_failed_override_removal_restores_the_profile(self):
        self.assertTrue(self.service.enable_app(self.app_id)["success"])
        self.assertTrue(self.service.update_config(self.app_id, {"multiplier": 4})["success"])
        with patch.object(self.flatpak, "_write_state", side_effect=OSError("ownership write failed")):
            result = self.service.remove_app(self.app_id)
        self.assertFalse(result["success"])
        self.assertIn("LSFGVK_PROFILE=", self.flatpak._override_path(self.app_id).read_text())
        self.assertEqual(self.configuration.get_flatpak_config(self.app_id)["config"]["multiplier"], 4)

    def test_external_override_change_survives_update(self):
        self.assertTrue(self.service.enable_app(self.app_id)["success"])
        path = self.flatpak._override_path(self.app_id)
        path.write_text(path.read_text(encoding="utf-8") + "EXTERNAL=yes\n", encoding="utf-8")

        result = self.service.set_workaround_state(self.app_id, self.service.default_state())

        self.assertTrue(result["success"])
        self.assertIn("EXTERNAL=yes", path.read_text(encoding="utf-8"))

    def test_enabling_does_not_list_all_flatpak_apps(self):
        self.assertTrue(self.service.enable_app(self.app_id)["success"])
        self.assertNotIn(["list", "--app", "--columns=name,application"], self.flatpak.commands)

    def test_poll_without_running_apps_skips_config_and_override_checks(self):
        self.assertTrue(self.service.enable_app(self.app_id)["success"])
        self.flatpak.commands.clear()
        with patch.object(self.configuration, "get_flatpak_config") as get_config:
            self.assertEqual(self.service.get_running_apps()["apps"], [])
            get_config.assert_not_called()
        self.assertEqual(self.flatpak.commands, [["ps", "--columns=application,active,pid"]])

    def test_running_detection_uses_owned_selector_state(self):
        self.assertTrue(self.service.enable_app(self.app_id)["success"])
        self.flatpak.running = "org.example.Game\ttrue\t1234\norg.other.App\ttrue\t9999\n"
        self.flatpak.start_times["1234"] = 200

        result = self.service.get_running_apps()

        self.assertTrue(result["success"])
        self.assertEqual(result["apps"], [{"app_id": self.app_id, "active": True, "pid": "1234", "start_time": 200}])

    def test_process_start_time_parser_handles_parentheses_in_command_name(self):
        fields = ["S"] + ["0"] * 18 + ["4242"]
        stat = "1234 (retro)arch) " + " ".join(fields)

        self.assertEqual(FlatpakService._parse_process_start_time(stat), 4242)


if __name__ == "__main__":
    unittest.main()
