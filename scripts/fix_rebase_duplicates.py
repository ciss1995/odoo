"""
Automatically find and remove near-duplicate const/let/var/import lines
at module level in JS source files. These are common rebase artifacts.

Run standalone:  python3 scripts/fix_rebase_duplicates.py
"""
import os
import re

ADDONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "addons")
SKIP_DIRS = {"tests", "test", "lib", "node_modules", "__pycache__"}
DECL_RE = re.compile(r"^(const|let|var|import)\s+")

def normalize(line):
    """Normalize a declaration line for comparison (strip whitespace, trailing semicolons)."""
    s = line.strip().rstrip(";").rstrip()
    s = re.sub(r"\s+", " ", s)
    return s

def get_decl_name(line):
    """Extract the declared identifier from a const/let/var declaration."""
    stripped = line.strip()
    m = re.match(r"(?:const|let|var)\s+(\w+)\s*=", stripped)
    if m:
        return m.group(1)
    m = re.match(r"(?:const|let|var)\s*\{([^}]+)\}", stripped)
    if m:
        return frozenset(x.strip().split(" as ")[0].strip() for x in m.group(1).split(",") if x.strip())
    return None

def fix_file(filepath):
    """Remove near-duplicate module-level declarations."""
    try:
        with open(filepath) as f:
            lines = f.readlines()
    except Exception:
        return []

    fixes = []
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if DECL_RE.match(stripped):
            # Look ahead up to 5 lines for a near-duplicate
            found_dup = False
            for j in range(i + 1, min(i + 6, len(lines))):
                next_stripped = lines[j].strip()
                if not next_stripped:
                    continue
                if not DECL_RE.match(next_stripped):
                    break

                name_i = get_decl_name(stripped)
                name_j = get_decl_name(next_stripped)

                if name_i and name_j and name_i == name_j:
                    # Keep the second (usually more complete) version, skip the first
                    # But also skip any blank lines between them
                    fixes.append(f"{filepath}:{i+1}: removed duplicate decl of '{name_i}'")
                    # Skip current line and any blank lines before the duplicate
                    i += 1
                    while i < j:
                        if not lines[i].strip():
                            i += 1
                        else:
                            break
                    found_dup = True
                    break

            if found_dup:
                continue

        new_lines.append(line)
        i += 1

    if fixes:
        with open(filepath, "w") as f:
            f.writelines(new_lines)

    return fixes

def main():
    all_fixes = []
    for root, dirs, files in os.walk(ADDONS_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".js"):
                path = os.path.join(root, f)
                all_fixes.extend(fix_file(path))

    for fix in all_fixes:
        print(fix)
    print(f"\nTotal fixes: {len(all_fixes)}")

if __name__ == "__main__":
    main()
