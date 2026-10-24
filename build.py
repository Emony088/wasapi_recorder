"""Build the single-file exe (PyInstaller) and verify its manifest.

Usage:
    python build.py            # build
    python build.py --check    # only inspect an existing dist/WasapiRecorder.exe
"""

from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
EXE = os.path.join(DIST, "WasapiRecorder.exe")


def check_manifest(path: str) -> bool:
    """Read the PE resources and confirm requestedExecutionLevel=requireAdministrator."""

    try:
        import pefile
    except ImportError:
        print("pefile not installed; skipped manifest check")
        return True
    pe = pefile.PE(path)
    result = False
    for entry in getattr(pe, "DIRECTORY_ENTRY_RESOURCE", []).entries:
        if entry.id != 24:  # RT_MANIFEST
            continue
        for res in entry.directory.entries:
            for lang in res.directory.entries:
                data = pe.get_data(lang.data.struct.OffsetToData, lang.data.struct.Size)
                text = data.decode("utf-8", "ignore")
                if "requireAdministrator" in text:
                    result = True
                print("manifest level:", "requireAdministrator" if "requireAdministrator" in text
                      else ("asInvoker" if "asInvoker" in text else "unknown"))
    pe.close()
    return result


def main() -> int:
    if "--check" not in sys.argv:
        cmd = [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean",
            "--distpath", DIST,
            "--workpath", os.path.join(HERE, "build"),
            "build.spec",
        ]
        env = dict(os.environ)
        env.setdefault("PYINSTALLER_CONFIG_DIR", os.path.join(HERE, ".pyinstaller"))
        print("running:", " ".join(cmd))
        code = subprocess.call(cmd, cwd=HERE, env=env)
        if code != 0:
            return code

    if not os.path.exists(EXE):
        print("missing", EXE)
        return 1
    size = os.path.getsize(EXE) / (1024 * 1024)
    print("built %s (%.1f MB)" % (EXE, size))
    ok = check_manifest(EXE)
    print("manifest requires admin:", ok)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
