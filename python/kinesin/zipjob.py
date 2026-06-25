"""Build a results .zip from a manifest: ``python -m kinesin.zipjob <manifest> <out>``.

The manifest is JSON ``{"entries": [...]}`` where each entry is either a single
file (``{"file", "arc"}``) or a directory glob (``{"glob", "arc_dir", "suffix"?}``).
Globs are expanded at zip time, so they pick up files written by *all* of a
sharded job's workers. Used by the runtime's ``/bundle`` route to build downloads
on demand (lazily, after every shard has finished).
"""

from __future__ import annotations

import glob
import json
import os
import sys
import zipfile


def main() -> None:
    if len(sys.argv) < 3:
        sys.stderr.write("usage: python -m kinesin.zipjob <manifest.json> <out.zip>\n")
        sys.exit(2)

    manifest_path, out_path = sys.argv[1], sys.argv[2]
    with open(manifest_path) as fh:
        manifest = json.load(fh)

    tmp = out_path + ".partial"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for entry in manifest.get("entries", []):
            if "file" in entry:
                if os.path.exists(entry["file"]):
                    z.write(entry["file"], entry["arc"])
            elif "glob" in entry:
                arc_dir = entry.get("arc_dir", "")
                suffix = entry.get("suffix", "")
                for path in sorted(glob.glob(entry["glob"])):
                    stem, ext = os.path.splitext(os.path.basename(path))
                    arc = os.path.join(arc_dir, f"{stem}{suffix}{ext}")
                    z.write(path, arc)

    os.replace(tmp, out_path)
    sys.stdout.write(out_path)


if __name__ == "__main__":
    main()
