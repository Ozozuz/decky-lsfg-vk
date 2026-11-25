"""Replace an existing Decky plugin and reload it in the running Game Mode UI."""

import asyncio
import fcntl
import hashlib
import json
import os
import pwd
import stat
import sys
import tempfile
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import aiohttp


async def decky(session, expression):
    async with session.get("http://127.0.0.1:8080/json") as response:
        response.raise_for_status()
        tabs = await response.json(content_type=None)
    tab = next((tab for tab in tabs if tab["title"] in
                ("SharedJSContext", "Steam Shared Context presented by Valve™", "Steam", "SP")
                and tab["url"].startswith(("https://steamloopback.host/routes/", "https://steamloopback.host/index.html"))), None)
    if tab is None:
        raise RuntimeError("Open Game Mode before deploying")
    async with session.ws_connect(tab["webSocketDebuggerUrl"]) as socket:
        await socket.send_json({"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": expression, "awaitPromise": True, "returnByValue": True,
        }})
        while True:
            message = await socket.receive_json(timeout=20)
            if message.get("id") != 1:
                continue
            result = message.get("result", {})
            if "error" in message or "exceptionDetails" in result:
                raise RuntimeError(result.get("result", {}).get("description", "Decky request failed"))
            return result["result"].get("value")


async def reload_plugin(session, name, version):
    loaded = await decky(session, f"""
        (async () => {{
            const name = {json.dumps(name)};
            const version = {json.dumps(version)};
            const start = performance.now();
            await DeckyBackend.callable("loader/reload_plugin")(name);
            const plugins = DeckyBackend.callable("loader/get_plugins");
            for (let attempt = 0; attempt < 40; attempt++) {{
                const plugin = (await plugins()).find(item => item.name === name);
                const frontend = performance.getEntriesByType("resource").some(entry =>
                    entry.startTime >= start && entry.responseStatus === 200 &&
                    decodeURIComponent(new URL(entry.name).pathname) === "/plugins/" + name + "/dist/index.js");
                if (plugin?.version === version && !plugin.disabled && frontend) return true;
                await new Promise(resolve => setTimeout(resolve, 250));
            }}
            return false;
        }})()
    """)
    if not loaded:
        raise RuntimeError(f"Decky did not confirm the reload of {version}")


async def install(home, payload, name, version):
    homebrew = home / "homebrew"
    live = homebrew / "plugins" / name
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
        installed = await decky(session, f"""
            DeckyBackend.callable("loader/get_plugins")().then(plugins =>
                plugins.find(plugin => plugin.name === {json.dumps(name)} && !plugin.disabled))
        """)
        previous = json.loads((live / "package.json").read_text())["version"]
        if not installed or installed["version"] != previous:
            raise RuntimeError("The enabled plugin in Decky must match the installed folder before deploying")

        backup = Path(tempfile.mkdtemp(prefix=".lsfg-deploy-", dir=homebrew))
        with ZipFile(BytesIO(payload)) as archive:
            for entry in archive.infolist():
                path = Path(entry.filename)
                if not path.parts or path.parts[0] != name or ".." in path.parts or stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError(f"Unsafe ZIP entry {entry.filename}")
            archive.extractall(backup)
        candidate = backup / name
        if (json.loads((candidate / "plugin.json").read_text())["name"] != name
                or json.loads((candidate / "package.json").read_text())["version"] != version
                or not (candidate / "dist/index.js").stat().st_size):
            raise ValueError("Unexpected plugin content in ZIP")
        for path in (candidate, *candidate.rglob("*")):
            original = live / path.relative_to(candidate)
            attributes = (original if original.exists() else live).stat()
            os.chown(path, attributes.st_uid, attributes.st_gid)
            os.chmod(path, stat.S_IMODE(attributes.st_mode) if original.exists() else (0o755 if path.is_dir() else 0o644))

        saved = backup / "original"
        live.rename(saved)
        try:
            candidate.rename(live)
            await reload_plugin(session, name, version)
        except BaseException:
            if live.exists():
                live.rename(candidate)
            saved.rename(live)
            await reload_plugin(session, name, previous)
            raise
        print(f"Decky is running {version}. Backup: {saved}", flush=True)


def main(relative, name, version, expected_hash):
    home = Path(pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir)
    payload = (home / relative).read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_hash:
        raise ValueError("Transferred ZIP checksum mismatch")
    with (home / "homebrew/.lsfg-deploy.lock").open("ab") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(install(home, payload, name, version))


if __name__ == "__main__":
    main(*sys.argv[1:])
