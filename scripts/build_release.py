"""Build a verified, reproducible HACS archive from a release commit."""

import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

INTEGRATION = "custom_components/echonet_lite"


def committed(revision: str, path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{revision}:{path}"])


def main() -> None:
    tag = os.environ["RELEASE_TAG"]
    revision = os.environ["RELEASE_SHA"]
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("Invalid release tag")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid release commit")
    resolved = subprocess.check_output(
        ["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"], text=True
    ).strip()
    if resolved != revision:
        raise ValueError("Release tag does not point to the release commit")
    manifest = json.loads(committed(revision, f"{INTEGRATION}/manifest.json"))
    if manifest["version"] != tag.removeprefix("v"):
        raise ValueError("Release tag and manifest versions disagree")
    if committed(revision, "version.txt").decode().strip() != manifest["version"]:
        raise ValueError("Release version files disagree")
    hacs = json.loads(committed(revision, "hacs.json"))
    if hacs.get("filename") != "echonet_lite.zip" or not hacs.get("zip_release"):
        raise ValueError("HACS archive configuration does not match")
    license_data = committed(revision, "LICENSE")

    output = Path("dist/echonet_lite.zip")
    output.parent.mkdir(exist_ok=True)
    temporary = output.with_name(".echonet_lite.zip.tmp")
    if temporary.exists():
        raise FileExistsError(f"Temporary archive already exists: {temporary}")
    try:
        subprocess.run(
            [
                "git",
                "archive",
                "--format=zip",
                "--mtime=1980-01-01T00:00:00Z",
                f"--output={temporary}",
                f"{revision}:{INTEGRATION}",
            ],
            check=True,
        )
        with ZipFile(temporary, "a", compression=ZIP_DEFLATED) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or "LICENSE" in names:
                raise ValueError("Duplicate archive member")
            for info in archive.infolist():
                path = PurePosixPath(info.filename)
                if (
                    path.is_absolute()
                    or "\\" in info.filename
                    or (path.parts and path.parts[0].endswith(":"))
                    or ".." in path.parts
                    or "__pycache__" in path.parts
                    or path.suffix in (".pyc", ".pyo")
                    or (info.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    raise ValueError(f"Unexpected archive member: {info.filename}")
            if not {"manifest.json", "__init__.py"} <= set(names):
                raise ValueError("Required integration archive members are missing")
            license_info = ZipInfo("LICENSE")
            license_info.compress_type = ZIP_DEFLATED
            license_info.external_attr = 0o100644 << 16
            archive.writestr(license_info, license_data)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
