import asyncio
import sys
import types
import unittest
from unittest.mock import Mock


class PluginMigrationTests(unittest.TestCase):
    def _load_plugin(self):
        decky = types.SimpleNamespace(
            DECKY_HOME="/decky",
            DECKY_USER_HOME="/home/deck",
            migrate_logs=Mock(),
            migrate_settings=Mock(),
            migrate_runtime=Mock(),
            logger=Mock(),
        )
        previous_decky = sys.modules.get("decky")
        previous_tomllib = sys.modules.get("tomllib")
        previous_plugin = sys.modules.pop("lsfg_vk.plugin", None)
        sys.modules["decky"] = decky
        sys.modules["tomllib"] = types.SimpleNamespace(loads=Mock())
        sys.path.insert(0, "py_modules")
        from lsfg_vk.plugin import Plugin
        return Plugin, decky, previous_decky, previous_tomllib, previous_plugin

    def _restore(self, previous_decky, previous_tomllib, previous_plugin):
        sys.path.remove("py_modules")
        if previous_decky is None:
            sys.modules.pop("decky", None)
        else:
            sys.modules["decky"] = previous_decky
        if previous_tomllib is None:
            sys.modules.pop("tomllib", None)
        else:
            sys.modules["tomllib"] = previous_tomllib
        if previous_plugin is None:
            sys.modules.pop("lsfg_vk.plugin", None)
        else:
            sys.modules["lsfg_vk.plugin"] = previous_plugin

    def test_migration_only_runs_decky_path_migrations(self):
        Plugin, decky, previous_decky, previous_tomllib, previous_plugin = self._load_plugin()
        try:
            plugin = Plugin.__new__(Plugin)
            plugin.installation_service = Mock()
            plugin.flatpak_service = Mock()

            asyncio.run(plugin._migration())

            decky.migrate_logs.assert_called_once()
            decky.migrate_settings.assert_called_once()
            decky.migrate_runtime.assert_called_once()
            plugin.installation_service.install.assert_not_called()
            plugin.flatpak_service.prepare_app.assert_not_called()
        finally:
            self._restore(previous_decky, previous_tomllib, previous_plugin)

    def test_decky_removal_or_upgrade_keeps_runtime_and_profiles(self):
        Plugin, _decky, previous_decky, previous_tomllib, previous_plugin = self._load_plugin()
        try:
            plugin = Plugin.__new__(Plugin)
            plugin.installation_service = Mock()
            plugin.flatpak_service = Mock()
            plugin.configuration_service = Mock()
            plugin.wrapper_service = Mock()

            asyncio.run(plugin._uninstall())

            self.assertFalse(plugin.installation_service.mock_calls)
            self.assertFalse(plugin.flatpak_service.mock_calls)
            self.assertFalse(plugin.configuration_service.mock_calls)
            self.assertFalse(plugin.wrapper_service.mock_calls)
        finally:
            self._restore(previous_decky, previous_tomllib, previous_plugin)

    def test_explicit_runtime_uninstall_keeps_profiles(self):
        Plugin, _decky, previous_decky, previous_tomllib, previous_plugin = self._load_plugin()
        try:
            plugin = Plugin.__new__(Plugin)
            plugin.installation_service = Mock()
            plugin.flatpak_service = Mock()
            plugin.configuration_service = Mock()
            plugin.wrapper_service = Mock()
            plugin.flatpak_service.remove_plugin_owned_environment.return_value = {"success": True}
            plugin.wrapper_service.purge.return_value = {"success": True}

            asyncio.run(plugin.uninstall_lsfg_vk())

            plugin.installation_service.uninstall.assert_called_once()
            self.assertFalse(plugin.configuration_service.mock_calls)
        finally:
            self._restore(previous_decky, previous_tomllib, previous_plugin)


if __name__ == "__main__":
    unittest.main()
