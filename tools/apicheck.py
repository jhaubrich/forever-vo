"""Cross-checks every global function the addon calls against the captured
Forever API surface (docs/forever_api.json, from Thunderz96/forever-addon-kit).

    ./tools/run.sh tools/apicheck.py

Prints names that are not client functions, not defined in the addon, and not
known frames. Anything printed is either a comment word or a real bug: the
Forever client removed globals such as MouseIsOver, SetDesaturation and
InterfaceOptions_AddCategory that older addons still call.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "docs" / "forever_api.json"
LUA_BUILTINS = {
    "assert", "error", "ipairs", "pairs", "next", "pcall", "print", "rawget", "rawset", "rawequal", "select",
    "setmetatable", "getmetatable", "tonumber", "tostring", "tostringall", "type", "unpack", "xpcall", "format",
    "strsplit", "strjoin", "strtrim", "wipe", "Mixin", "CreateFromMixins", "hooksecurefunc", "CreateFrame",
    "PlaySound", "CreateColor", "CopyTable", "time", "date",
}


def main() -> int:
    api = json.loads(API.read_text())
    functions, frames, namespaces = set(api["functions"]), set(api.get("frames", [])), api.get("namespaces", {})
    files = sorted((ROOT / "ForeverVO").glob("**/*.lua"))
    all_text = "".join(f.read_text() for f in files)
    defined = set(re.findall(r"function\s+(?:[\w.]+[:.])?([A-Za-z_]\w*)\s*\(", all_text))
    defined |= set(re.findall(r"local\s+(?:function\s+)?([A-Za-z_]\w*)", all_text))
    defined |= set(re.findall(r"^([A-Za-z_]\w*)\s*=", all_text, re.MULTILINE))
    problems = 0
    for path in files:
        text = path.read_text()
        for m in re.finditer(r"(?<![.:\w])([A-Z][A-Za-z0-9_]*)\s*\(", text):
            name = m.group(1)
            if name in functions or name in LUA_BUILTINS or name in defined or name in frames:
                continue
            print(f"{path.relative_to(ROOT)}:{text.count(chr(10), 0, m.start()) + 1}: {name}")
            problems += 1
        for m in re.finditer(r"\b(C_\w+)\.(\w+)", text):
            ns, fn = m.groups()
            if ns in namespaces and fn not in namespaces[ns]:
                print(f"{path.relative_to(ROOT)}: {ns}.{fn} is not in the Forever API")
                problems += 1
    print(f"{len(files)} files checked, {problems} names to look at")
    return 0


if __name__ == "__main__":
    sys.exit(main())
