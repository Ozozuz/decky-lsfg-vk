"""Build an installable ZIP and, optionally, replace the plugin over SSH."""

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_archive():
    package = json.loads((ROOT / "package.json").read_text())
    name = json.loads((ROOT / "plugin.json").read_text())["name"]
    version = package["version"]
    if Path(name).name != name or name in ("", ".", ".."):
        raise ValueError("Invalid plugin folder name")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version):
        raise ValueError("Invalid package version")
    artifacts = ROOT / ".deploy"
    artifacts.mkdir(exist_ok=True)
    archive = artifacts / f"Decky.LSFG-VK-Ozozuz-{version}.zip"

    with tempfile.TemporaryDirectory(prefix=".build-", dir=artifacts) as temporary:
        stage = Path(temporary)
        frontend = ROOT / "dist"
        subprocess.run(["npm", "run", "build"], cwd=ROOT, check=True)
        files = {file: ROOT / file for file in ("LICENSE", "README.md", "plugin.json", "package.json", "main.py")}
        files.update({str(path.relative_to(ROOT)): path for path in (ROOT / "py_modules").rglob("*.py")})
        files.update({f"i18n/{path.name}": path for path in (ROOT / "defaults/i18n").glob("*.json")})
        files.update({f"dist/{path.relative_to(frontend)}": path for path in frontend.rglob("*") if path.is_file()})
        if "dist/index.js" not in files:
            raise ValueError("Frontend build did not produce dist/index.js")

        cache = artifacts / ".runtime"
        cache.mkdir(exist_ok=True)
        for binary in package.get("remote_binary", []):
            filename = binary["name"]
            if Path(filename).name != filename or filename in ("", ".", ".."):
                raise ValueError("Invalid runtime filename")
            runtime = cache / filename
            if not runtime.is_file() or digest(runtime) != binary["sha256hash"]:
                downloaded = stage / filename
                subprocess.run(["curl", "-fLsS", "--retry", "2", "-o", str(downloaded), binary["url"]], check=True)
                if digest(downloaded) != binary["sha256hash"]:
                    raise ValueError(f"Runtime checksum mismatch for {filename}")
                downloaded.replace(runtime)
            files[f"bin/{filename}"] = runtime

        built = stage / "plugin.zip"
        with ZipFile(built, "w", ZIP_DEFLATED) as output:
            for relative, source in sorted(files.items()):
                output.write(source, f"{name}/{relative}")
        with ZipFile(built) as output:
            if output.testzip() is not None:
                raise ValueError("Generated ZIP is corrupt")
        built.replace(archive)
    return archive, name, version


def deploy(archive, name, version, host, known_hosts):
    environment = os.environ.copy()
    password = environment.pop("DECK_PASSWORD", "")
    prefix = []
    if password:
        environment["SSHPASS"] = password
        prefix = ["sshpass", "-e"]
    options = ["-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=5"]
    if known_hosts:
        options += ["-o", f"UserKnownHostsFile={known_hosts}"]
    relative = f"Downloads/{archive.name}"
    subprocess.run(prefix + ["scp", *options, "--", str(archive), f"{host}:{relative}"], env=environment, check=True)

    remote = Path(__file__).with_name("deploy_on_deck.py").read_text()
    command = ["sudo", *(["-S", "-p", ""] if password else []),
               "python3", "-c", remote, relative, name, version, digest(archive)]
    # SSH needs a TTY only when sudo may prompt interactively.
    tty = ["-T"] if password else ["-tt"]
    subprocess.run(prefix + ["ssh", *tty, *options, "--", host, shlex.join(command)],
                   env=environment, input=f"{password}\n" if password else None, text=True, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", nargs="?", default=os.environ.get("DECK_HOST"), help="SSH target, such as deck@steamdeck")
    parser.add_argument("--known-hosts", type=Path, help="Use this verified SSH known_hosts file")
    parser.add_argument("--build-only", action="store_true", help="Create and validate the ZIP without connecting to the Deck")
    args = parser.parse_args()
    if not args.build_only and not args.host:
        parser.error("provide an SSH target or set DECK_HOST")
    try:
        archive, name, version = build_archive()
        print(f"{archive} ({digest(archive)})", flush=True)
        if not args.build_only:
            deploy(archive, name, version, args.host, args.known_hosts)
    except subprocess.CalledProcessError as error:
        sys.exit(f"Deploy command failed ({error.returncode})")
    except (OSError, ValueError) as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
