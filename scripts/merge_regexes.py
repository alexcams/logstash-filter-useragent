#!/usr/bin/env python3
"""Merge upstream regexes.yaml with local overrides.

Usage:
    python3 scripts/merge_regexes.py <upstream> <overrides> <output>

The script:
  1. Reads the upstream regexes.yaml.
  2. Reads regexes-overrides.yaml (additions + replacements).
  3. Applies replacements: swaps matching regex strings in the specified section.
  4. Prepends additions: inserts custom entries at the top of each section.
  5. Writes the merged result to <output>.
"""

import sys
import re


PARSER_SECTIONS = ['user_agent_parsers', 'os_parsers', 'device_parsers']

# Regex that matches a YAML list entry starting with "- regex:"
ENTRY_START_RE = re.compile(r'^(\s*)- regex:\s')


def read_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def write_file(path, content):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


def parse_overrides(text):
    """Minimal YAML-like parser for regexes-overrides.yaml.

    We avoid importing PyYAML so this script has zero dependencies and can
    run in any CI environment with just Python 3.
    """
    additions = {s: [] for s in PARSER_SECTIONS}
    replacements = []

    current_section = None
    in_replacements = False
    current_replacement = {}

    for line in text.splitlines():
        stripped = line.strip()

        # skip blank lines and comments
        if not stripped or stripped.startswith('#'):
            continue

        # top-level section detection
        for section in PARSER_SECTIONS:
            if stripped == f'{section}:' or stripped == f'{section}: []':
                current_section = section
                in_replacements = False
                continue

        if stripped == 'replacements:':
            in_replacements = True
            current_section = None
            continue

        if in_replacements:
            if stripped.startswith('- section:'):
                # save previous replacement if any
                if current_replacement:
                    replacements.append(current_replacement)
                current_replacement = {'section': stripped.split(':', 1)[1].strip()}
            elif stripped.startswith('original:'):
                val = stripped.split(':', 1)[1].strip().strip("'\"")
                current_replacement['original'] = val
            elif stripped.startswith('replacement:'):
                val = stripped.split(':', 1)[1].strip().strip("'\"")
                current_replacement['replacement'] = val
        elif current_section and stripped.startswith('- regex:'):
            # Start of a new multi-line entry (regex + optional family_replacement, etc.)
            additions[current_section].append(line)
        elif current_section and not stripped.startswith('-') and additions[current_section]:
            # Continuation line of the current entry (e.g. family_replacement)
            additions[current_section].append(line)

    # save last replacement
    if current_replacement:
        replacements.append(current_replacement)

    return additions, replacements


def apply_replacements(upstream_lines, replacements):
    """Replace specific regex strings in the upstream content."""
    result = list(upstream_lines)
    for repl in replacements:
        original = repl['original']
        replacement = repl['replacement']
        found = False
        for i, line in enumerate(result):
            if original in line:
                result[i] = line.replace(original, replacement)
                found = True
                break
        if not found:
            print(f"WARNING: replacement target not found in upstream "
                  f"(may have been fixed upstream): {original!r}",
                  file=sys.stderr)
    return result


def find_section_start(lines, section_name):
    """Find the line index of the first entry after a section header."""
    pattern = re.compile(rf'^{re.escape(section_name)}:')
    for i, line in enumerate(lines):
        if pattern.match(line):
            # Find the first "- regex:" line after the section header
            for j in range(i + 1, len(lines)):
                if ENTRY_START_RE.match(lines[j]):
                    return j
            return i + 1
    return -1


def prepend_additions(lines, additions):
    """Prepend custom entries at the top of each parser section."""
    # Process sections in reverse order so line indices stay valid
    insertions = []
    for section in PARSER_SECTIONS:
        entries = additions.get(section, [])
        if not entries:
            continue
        idx = find_section_start(lines, section)
        if idx < 0:
            print(f"WARNING: section '{section}' not found in upstream",
                  file=sys.stderr)
            continue
        insertions.append((idx, entries))

    # Sort by index descending so earlier insertions don't shift later ones
    insertions.sort(key=lambda x: x[0], reverse=True)

    for idx, entries in insertions:
        # Detect indentation from existing entries
        indent = '  '
        if idx < len(lines):
            m = re.match(r'^(\s*)', lines[idx])
            if m:
                indent = m.group(1)

        # Determine base indentation from the override entries (first - regex: line)
        base_override_indent = 0
        for entry in entries:
            if entry.lstrip().startswith('- regex:'):
                base_override_indent = len(entry) - len(entry.lstrip())
                break

        block = [indent + '# --- Custom overrides (from regexes-overrides.yaml) ---']
        for entry in entries:
            entry_indent = len(entry) - len(entry.lstrip())
            relative_indent = entry_indent - base_override_indent
            block.append(indent + ' ' * relative_indent + entry.strip())
        block.append(indent + '# --- End custom overrides ---')
        block.append('')

        for i, b in enumerate(block):
            lines.insert(idx + i, b)

    return lines


def main():
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <upstream> <overrides> <output>",
              file=sys.stderr)
        sys.exit(1)

    upstream_path, overrides_path, output_path = sys.argv[1:4]

    upstream_text = read_file(upstream_path)
    overrides_text = read_file(overrides_path)

    additions, replacements = parse_overrides(overrides_text)

    lines = upstream_text.splitlines()

    # Step 1: Apply replacements
    lines = apply_replacements(lines, replacements)

    # Step 2: Prepend additions
    lines = prepend_additions(lines, additions)

    write_file(output_path, '\n'.join(lines) + '\n')

    # Summary
    n_additions = sum(len(v) for v in additions.values())
    n_replacements = len(replacements)
    print(f"Merged: {n_additions} addition(s), {n_replacements} replacement(s) "
          f"-> {output_path}")


if __name__ == '__main__':
    main()
