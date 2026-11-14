import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock


sys.modules.setdefault(
    "decky",
    types.SimpleNamespace(DECKY_USER_HOME="/home/deck", logger=Mock()),
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py_modules"))

from lsfg_vk.flatpak_service import FlatpakService


class FlatpakServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name) / "home" / "deck"
        self.home.mkdir(parents=True)
        self.service = FlatpakService()
        self.service.user_home = self.home
        self.service.config_dir = self.home / ".config/lsfg-vk"
        self.service.config_file_path = self.service.config_dir / "conf.toml"
        self.service.ownership_path.parent.mkdir(parents=True, exist_ok=True)
        self.service.check_flatpak_available = Mock(return_value=True)
        self.service._run_flatpak_command = Mock(side_effect=self._run_flatpak_command)
        self.runtime_ref = "org.freedesktop.Platform/x86_64/24.08"
        self.runtime_metadata = ""
        self.user_branches = set()
        self.system_branches = set()
        self.user_extension_origin = "flathub"
        self.user_remotes = {"flathub"}
        self.apps = {"com.example.Game": "Example Game"}
        self.dll_dir = self.home / ".local/share/Steam/steamapps/common/Lossless Scaling"
        self.service._dll_directory = Mock(return_value=self.dll_dir)

    def tearDown(self):
        self.tempdir.cleanup()

    @staticmethod
    def _result(stdout="", returncode=0, stderr=""):
        return types.SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)

    @staticmethod
    def _extension_line(branch):
        return f"org.freedesktop.Platform.VulkanLayer.lsfgvk\tx86_64\t{branch}\n"

    @staticmethod
    def _parse_override(content):
        section = None
        filesystems = []
        unset_environment = []
        environment = {}
        other = []
        for raw in content.splitlines():
            line = raw.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                continue
            key, separator, value = line.partition("=")
            if not separator:
                continue
            if section == "Context" and key == "filesystems":
                filesystems.extend(item for item in value.split(";") if item)
            elif section == "Context" and key == "unset-environment":
                unset_environment.extend(item for item in value.split(";") if item)
            elif section == "Environment":
                environment[key] = value
            else:
                other.append((section, key, value))
        return filesystems, unset_environment, environment, other

    @staticmethod
    def _serialize_override(filesystems, unset_environment, environment):
        lines = ["[Context]"]
        if filesystems:
            lines.append("filesystems=" + ";".join(filesystems) + ";")
        if unset_environment:
            lines.append("unset-environment=" + ";".join(unset_environment) + ";")
        if environment:
            lines.append("")
            lines.append("[Environment]")
            lines.extend(f"{key}={value}" for key, value in environment.items())
        return "\n".join(lines) + "\n"

    def _apply_override(self, args):
        app_id = args[-1]
        path = self.service._override_path(app_id)
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        filesystems, unset_environment, environment, _ = self._parse_override(content)
        for arg in args[2:-1]:
            if arg.startswith("--filesystem="):
                value = arg.split("=", 1)[1]
                if value not in filesystems:
                    filesystems.append(value)
            elif arg.startswith("--env="):
                key, value = arg.split("=", 1)[1].split("=", 1)
                environment[key] = value
                if key in unset_environment:
                    unset_environment.remove(key)
            elif arg.startswith("--unset-env="):
                key = arg.split("=", 1)[1]
                environment.pop(key, None)
                if key not in unset_environment:
                    unset_environment.append(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            self._serialize_override(filesystems, unset_environment, environment),
            encoding="utf-8",
        )
        return self._result()

    def _run_flatpak_command(self, args, **_kwargs):
        if args == ["remotes", "--user", "--columns=name"]:
            return self._result("\n".join(sorted(self.user_remotes)))
        if args[0] == "remote-add":
            self.user_remotes.add(args[-2])
            return self._result()
        if args[:2] == ["info", "--show-runtime"]:
            return self._result(self.runtime_ref + "\n")
        if args[:2] == ["info", "--show-metadata"]:
            return self._result(self.runtime_metadata)
        if args[:3] == ["info", "--user", "--show-origin"]:
            return self._result(self.user_extension_origin)
        if args[:2] == ["list", "--app"]:
            return self._result("".join(f"{name}\t{app_id}\n" for app_id, name in self.apps.items()))
        if args[0] == "list":
            branches = self.user_branches if "--user" in args else self.system_branches
            return self._result("".join(self._extension_line(branch) for branch in sorted(branches)))
        if args[0] == "install":
            self.user_branches.add(args[-1].rsplit("/", 1)[-1])
            return self._result()
        if args[0] == "uninstall":
            self.user_branches.discard(args[-1].rsplit("/", 1)[-1])
            return self._result()
        if args[:3] == ["override", "--user", "--show"]:
            path = self.service._override_path(args[-1])
            return self._result(path.read_text(encoding="utf-8") if path.exists() else "")
        if args[:3] == ["override", "--user", "--reset"]:
            self.service._override_path(args[-1]).unlink(missing_ok=True)
            return self._result()
        if args[:2] == ["override", "--user"]:
            return self._apply_override(args)
        raise AssertionError(f"Unexpected Flatpak command: {args}")

    def test_resolves_freedesktop_and_derived_runtimes(self):
        runtime, branch = self.service._resolve_runtime("com.example.Game")
        self.assertEqual(runtime, self.runtime_ref)
        self.assertEqual(branch, "24.08")

        self.runtime_ref = "org.kde.Platform/x86_64/6.10"
        self.runtime_metadata = "[Extension org.freedesktop.Platform.GL]\nversions=25.08;25.08-extra;1.4\n"
        runtime, branch = self.service._resolve_runtime("com.example.Game")
        self.assertEqual(runtime, self.runtime_ref)
        self.assertEqual(branch, "25.08")

    def test_resolves_freedesktop_26_08_runtime(self):
        self.runtime_ref = "org.freedesktop.Platform/x86_64/26.08"

        runtime, branch = self.service._resolve_runtime("com.example.Game")

        self.assertEqual(runtime, self.runtime_ref)
        self.assertEqual(branch, "26.08")

    def test_resolves_derived_runtimes_with_26_08_metadata(self):
        for runtime_id in ("org.gnome.Platform", "org.kde.Platform"):
            for versions in ("26.08;26.08-extra;1.4", "26.08-extra;1.4"):
                with self.subTest(runtime_id=runtime_id, versions=versions):
                    self.runtime_ref = f"{runtime_id}/x86_64/test"
                    self.runtime_metadata = (
                        "[Extension org.freedesktop.Platform.GL]\n"
                        f"versions={versions}\n"
                    )

                    runtime, branch = self.service._resolve_runtime("com.example.Game")

                    self.assertEqual(runtime, self.runtime_ref)
                    self.assertEqual(branch, "26.08")

    def test_rejects_unsupported_runtime_branches(self):
        for branch in ("23.08", "27.08"):
            with self.subTest(branch=branch):
                self.runtime_ref = f"org.freedesktop.Platform/x86_64/{branch}"
                with self.assertRaisesRegex(ValueError, "Unsupported Flatpak runtime branch"):
                    self.service._resolve_runtime("com.example.Game")
                with self.assertRaisesRegex(ValueError, "supported Freedesktop base runtime"):
                    self.service.runtime_branch_from_metadata(
                        f"[Extension org.freedesktop.Platform.GL]\nversions={branch};1.4\n"
                    )

        self.service._run_flatpak_command.reset_mock()
        response = self.service.install_extension("27.08")
        self.assertFalse(response["success"])
        self.assertIn("Unsupported Flatpak runtime branch 27.08", response["error"])
        self.service._run_flatpak_command.assert_not_called()

    def test_extension_status_lists_26_08_as_supported(self):
        response = self.service.get_extension_status()

        self.assertTrue(response["success"])
        self.assertEqual(response["supported_branches"], ["24.08", "25.08", "26.08"])

    def test_prepare_and_cleanup_26_08_preserve_existing_extensions(self):
        self.runtime_ref = "org.freedesktop.Platform/x86_64/26.08"
        self.user_branches = {"24.08"}
        self.system_branches = {"25.08"}

        response = self.service.prepare_app("com.example.Game")

        self.assertTrue(response["success"], response)
        self.assertTrue(response["prepared"])
        self.assertEqual(response["runtime_branch"], "26.08")
        self.service._run_flatpak_command.assert_any_call(
            [
                "install", "--user", "--noninteractive", "--or-update", "flathub",
                "org.freedesktop.Platform.VulkanLayer.lsfgvk//26.08",
            ],
            capture_output=True, text=True,
        )
        self.assertEqual(self.user_branches, {"24.08", "26.08"})
        state = json.loads(self.service.ownership_path.read_text(encoding="utf-8"))
        self.assertEqual(state["plugin_owned_branches"], ["26.08"])

        response = self.service.remove_plugin_owned_environment()

        self.assertTrue(response["success"], response)
        self.assertEqual(response["removed_branches"], ["26.08"])
        self.assertEqual(self.user_branches, {"24.08"})
        self.assertEqual(self.system_branches, {"25.08"})
        self.assertFalse(self.service.ownership_path.exists())
        self.assertFalse(self.service._override_path("com.example.Game").exists())

    def test_prepare_26_08_install_failure_does_not_create_app_override(self):
        self.runtime_ref = "org.freedesktop.Platform/x86_64/26.08"

        def fail_install(args, **kwargs):
            if args[0] == "install":
                return self._result(returncode=1, stderr="Extension unavailable")
            return self._run_flatpak_command(args, **kwargs)

        self.service._run_flatpak_command.side_effect = fail_install

        response = self.service.prepare_app("com.example.Game")

        self.assertFalse(response["success"])
        self.assertFalse(response["prepared"])
        self.assertIn("Extension unavailable", response["error"])
        self.assertFalse(self.service.ownership_path.exists())
        self.assertFalse(self.service._override_path("com.example.Game").exists())

    def test_clean_env_targets_deck_user_session_bus(self):
        env = self.service._clean_env()
        user_id = self.home.stat().st_uid
        self.assertEqual(env["XDG_RUNTIME_DIR"], f"/run/user/{user_id}")
        self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], f"unix:path=/run/user/{user_id}/bus")

    def test_prepare_adds_missing_user_flathub_before_installing_extension(self):
        self.runtime_ref = "org.freedesktop.Platform/x86_64/26.08"
        # A system-wide Flathub, as shipped on Steam Deck, is not visible to --user installs.
        self.user_remotes = {"flathub-beta"}

        response = self.service.prepare_app("com.example.Game")

        self.assertTrue(response["success"], response)
        self.assertEqual(self.user_remotes, {"flathub-beta", "flathub"})
        commands = [call.args[0] for call in self.service._run_flatpak_command.call_args_list]
        add = [
            "remote-add", "--user", "--if-not-exists", "flathub",
            "https://dl.flathub.org/repo/flathub.flatpakrepo",
        ]
        self.assertEqual(commands.count(add), 1)
        self.assertLess(commands.index(add), next(i for i, args in enumerate(commands) if args[0] == "install"))
        self.assertTrue(self.service.prepare_app("com.example.Game")["success"])
        self.assertTrue(self.service.remove_plugin_owned_environment()["success"])
        self.assertEqual(self.user_remotes, {"flathub-beta", "flathub"})
        commands = [call.args[0] for call in self.service._run_flatpak_command.call_args_list]
        self.assertEqual(commands.count(add), 1)

    def test_existing_user_flathub_is_preserved(self):
        response = self.service.prepare_app("com.example.Game")

        self.assertTrue(response["success"], response)
        commands = [call.args[0] for call in self.service._run_flatpak_command.call_args_list]
        self.assertFalse(any(args[0] in ("remote-add", "remote-modify", "remote-delete") for args in commands))

    def test_system_extension_does_not_require_user_remote(self):
        self.system_branches = {"24.08"}
        self.user_remotes = set()

        response = self.service.prepare_app("com.example.Game")

        self.assertTrue(response["success"], response)
        self.assertEqual(self.user_remotes, set())
        commands = [call.args[0] for call in self.service._run_flatpak_command.call_args_list]
        self.assertFalse(any(args[0] in ("remotes", "remote-add", "install") for args in commands))

    def test_remote_setup_failure_preserves_existing_extension_and_overrides(self):
        self.user_remotes = set()
        self.user_branches = {"24.08"}
        self.user_extension_origin = "lsfgvk-origin"
        path = self.service._override_path("com.example.Game")
        path.parent.mkdir(parents=True, exist_ok=True)
        original = "[Environment]\nFOO=bar\n"
        path.write_text(original, encoding="utf-8")

        def fail_remote_add(args, **kwargs):
            if args[0] == "remote-add":
                return self._result(returncode=1, stderr="Network unavailable")
            return self._run_flatpak_command(args, **kwargs)

        self.service._run_flatpak_command.side_effect = fail_remote_add

        response = self.service.prepare_app("com.example.Game")

        self.assertFalse(response["success"])
        self.assertIn("Could not configure Flathub", response["error"])
        self.assertIn("Network unavailable", response["error"])
        self.assertEqual(self.user_branches, {"24.08"})
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertFalse(self.service.ownership_path.exists())
        commands = [call.args[0] for call in self.service._run_flatpak_command.call_args_list]
        self.assertFalse(any(args[0] in ("install", "uninstall", "override") for args in commands))

    def test_remote_inspection_failure_does_not_change_configuration(self):
        def fail_remotes(args, **kwargs):
            if args[0] == "remotes":
                return self._result(returncode=1, stderr="Cannot read remote configuration")
            return self._run_flatpak_command(args, **kwargs)

        self.service._run_flatpak_command.side_effect = fail_remotes

        response = self.service.prepare_app("com.example.Game")

        self.assertFalse(response["success"])
        self.assertIn("Cannot read remote configuration", response["error"])
        commands = [call.args[0] for call in self.service._run_flatpak_command.call_args_list]
        self.assertFalse(any(args[0] in ("remote-add", "install", "uninstall", "override") for args in commands))

    def test_prepare_app_installs_runtime_and_persists_narrow_override(self):
        response = self.service.prepare_app("com.example.Game")

        self.assertTrue(response["success"])
        self.assertTrue(response["prepared"])
        self.assertTrue(response["owned"])
        self.assertEqual(response["runtime_branch"], "24.08")
        self.assertEqual(self.user_branches, {"24.08"})
        install_calls = [
            call.args[0]
            for call in self.service._run_flatpak_command.call_args_list
            if call.args[0][0] == "install"
        ]
        self.assertEqual(
            install_calls,
            [[
                "install",
                "--user",
                "--noninteractive",
                "--or-update",
                "flathub",
                "org.freedesktop.Platform.VulkanLayer.lsfgvk//24.08",
            ]],
        )
        status = self.service._app_override_status("com.example.Game")
        self.assertTrue(status["prepared"])
        content = self.service._override_path("com.example.Game").read_text(encoding="utf-8")
        self.assertIn(str(self.service.config_dir) + ":ro", content)
        self.assertIn(str(self.dll_dir) + ":ro", content)
        self.assertIn("LSFGVK_CONFIG=" + str(self.service.config_file_path), content)
        self.assertIn("LSFGVK_FLATPAK=1", content)
        self.assertNotIn("ENABLE_GAMESCOPE_WSI", content)
        state = json.loads(self.service.ownership_path.read_text(encoding="utf-8"))
        self.assertEqual(state["plugin_owned_branches"], ["24.08"])
        self.assertEqual(state["prepared_apps"]["com.example.Game"], {})

    def test_prepare_is_idempotent(self):
        first = self.service.prepare_app("com.example.Game")
        first_content = self.service._override_path("com.example.Game").read_bytes()
        second = self.service.prepare_app("com.example.Game")

        self.assertTrue(first["success"])
        self.assertTrue(second["success"])
        self.assertEqual(first_content, self.service._override_path("com.example.Game").read_bytes())
        install_calls = [call for call in self.service._run_flatpak_command.call_args_list if call.args[0][0] == "install"]
        self.assertEqual(len(install_calls), 1)

    def test_replaces_extension_from_another_remote(self):
        self.user_branches = {"24.08"}
        self.user_extension_origin = "lsfgvk-origin"

        response = self.service.install_extension("24.08")

        self.assertTrue(response["success"])
        commands = [call.args[0] for call in self.service._run_flatpak_command.call_args_list]
        self.assertIn(
            [
                "uninstall",
                "--user",
                "--noninteractive",
                "org.freedesktop.Platform.VulkanLayer.lsfgvk/x86_64/24.08",
            ],
            commands,
        )
        self.assertEqual(self.user_branches, {"24.08"})

    def test_preinstalled_runtime_is_not_owned(self):
        self.system_branches = {"24.08"}
        response = self.service.prepare_app("com.example.Game")

        self.assertTrue(response["success"])
        state = json.loads(self.service.ownership_path.read_text(encoding="utf-8"))
        self.assertEqual(state["plugin_owned_branches"], [])
        self.assertIn("com.example.Game", state["prepared_apps"])

    def test_prepare_resets_existing_override(self):
        self.system_branches = {"24.08"}
        path = self.service._override_path("com.example.Game")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[Context]\nfilesystems=~/Documents;\n\n[Environment]\nFOO=bar\n", encoding="utf-8")

        response = self.service.prepare_app("com.example.Game")
        content = path.read_text(encoding="utf-8")

        self.assertTrue(response["success"])
        self.assertTrue(response["owned"])
        self.assertNotIn("~/Documents", content)
        self.assertNotIn("FOO=bar", content)
        self.assertIn("LSFGVK_CONFIG=", content)

    def test_remove_deletes_override_created_by_plugin(self):
        self.system_branches = {"24.08"}
        self.assertTrue(self.service.prepare_app("com.example.Game")["success"])
        path = self.service._override_path("com.example.Game")
        self.assertTrue(path.exists())

        response = self.service.remove_app_override("com.example.Game")

        self.assertTrue(response["success"])
        self.assertFalse(path.exists())
        self.assertFalse(self.service.ownership_path.exists())

    def test_remove_resets_external_changes(self):
        self.system_branches = {"24.08"}
        self.assertTrue(self.service.prepare_app("com.example.Game")["success"])
        path = self.service._override_path("com.example.Game")
        with path.open("a", encoding="utf-8") as handle:
            handle.write("EXTERNAL=1\n")

        response = self.service.remove_app_override("com.example.Game")

        self.assertTrue(response["success"])
        self.assertFalse(path.exists())
        self.assertFalse(self.service.ownership_path.exists())

    def test_legacy_ownership_fields_are_accepted(self):
        self.service.ownership_path.write_text(
            json.dumps({
                "version": 2,
                "plugin_owned_branches": [],
                "prepared_apps": {"com.example.Game": {"override_existed": True, "managed_sha256": "old"}},
            }),
            encoding="utf-8",
        )

        self.assertIn("com.example.Game", self.service._read_state()["prepared_apps"])

    def test_full_cleanup_removes_only_owned_state(self):
        self.system_branches = {"23.08"}
        self.assertTrue(self.service.prepare_app("com.example.Game")["success"])
        self.assertEqual(self.user_branches, {"24.08"})

        response = self.service.remove_plugin_owned_environment()

        self.assertTrue(response["success"])
        self.assertEqual(response["removed_apps"], ["com.example.Game"])
        self.assertEqual(response["removed_branches"], ["24.08"])
        self.assertEqual(self.user_branches, set())
        self.assertEqual(self.system_branches, {"23.08"})
        self.assertFalse(self.service.ownership_path.exists())

    def test_corrupt_ownership_metadata_fails_closed(self):
        self.service.ownership_path.write_text("{not-json", encoding="utf-8")

        response = self.service.remove_plugin_owned_environment()

        self.assertFalse(response["success"])
        self.assertEqual(self.service._run_flatpak_command.call_count, 0)


if __name__ == "__main__":
    unittest.main()
