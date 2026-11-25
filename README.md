# Decky LSFG-VK - Ozozuz

A community edition of [Decky LSFG-VK](https://github.com/xXJSONDeruloXx/decky-lsfg-vk), maintained by [Ozozuz](https://github.com/Ozozuz). It brings Lossless Scaling frame generation to Steam Deck through Decky Loader and the upstream [lsfg-vk Vulkan layer](https://lsfg-vk.dev/).

## What's different

- **Flatpak support** for Freedesktop 24.08, 25.08, and 26.08, including compatible GNOME and KDE runtimes. The plugin sets up user-level Flathub when needed, reuses existing extensions, and preserves unrelated Flatpak overrides.
- **Base FPS Cap** sits next to the frame generation multiplier. It limits the game's original FPS in DXVK and VKD3D-Proton titles. Restart the game after changing it, or restart the launcher for Flatpak titles.
- **Now Playing** shows current Gamescope FPS and a 15-second frame-time graph for Steam, non-Steam and Flatpak games in Gaming Mode.
- **Deck navigation and profiles** make Flatpak profiles easier to browse and keep edits tied to the profile you opened. Flatpak apps can also be enabled in bulk.
- **Safer installation** validates the bundled files and configuration before replacing an existing installation.

## Install

1. In Steam, install [Lossless Scaling](https://store.steampowered.com/app/993090/Lossless_Scaling/) and select the **lsfg-vk** branch under **Properties → Game Versions & Betas**.
2. Download the plugin ZIP from [Ozozuz's GitHub Releases](https://github.com/Ozozuz/decky-lsfg-vk/releases).
3. In Decky, enable **Developer Mode** and choose **Developer → Install Plugin from ZIP**.
4. Open **Decky LSFG-VK - Ozozuz** and select **Install lsfg-vk** if setup is needed. Use the **Steam**, **Non-Steam**, or **Flatpak** tab to enable frame generation for the next launch.

**Uninstall LSFG-VK** inside the plugin removes the plugin-managed runtime and deletes the active profile configuration in `~/.config/lsfg-vk/conf.toml`. Installation files whose ownership the plugin cannot verify are preserved.

### Deploying a test build

With the project dependencies installed and the target Deck in Game Mode with SSH enabled, run `./scripts/deploy-to-deck.sh deck@steamdeck`. The script builds the ZIP, copies it to `~/Downloads` and reloads the existing plugin. It backs up the replaced plugin under `~/homebrew/.lsfg-deploy-*/original` and restores it if the reload fails.

The host key must be trusted by SSH. Use `--known-hosts FILE` if the verified key is stored elsewhere. For password authentication, export `DECK_PASSWORD` in the current shell to use it with `sshpass` and sudo. Use `--build-only` to create the ZIP under `.deploy/` without connecting to the Deck.

## TODO

- Measure and display base FPS before frame generation.

## Feedback and credits

Report fork-specific bugs in [GitHub Issues](https://github.com/Ozozuz/decky-lsfg-vk/issues). The [upstream Discord](https://discord.gg/TwvHdVucC3) hosts discussion about the original plugin.

- [Kurt Himebauch (xXJSONDeruloXx)](https://github.com/xXJSONDeruloXx) created the original Decky plugin.
- [PancakeTAS](https://lsfg-vk.dev/) created the lsfg-vk Vulkan layer.
- The Lossless Scaling developers created the original frame generation technology.
- [Deck Wizard](https://www.youtube.com/@DeckWizard), the Decky Loader team, and community contributors provided testing, guides, and support.
