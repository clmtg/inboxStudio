"""Extract one version's Markdown notes before publishing a release."""
import argparse
from pathlib import Path
import re


def extract_notes(changelog, version):
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('Use a release version such as 0.2.2')
    headings = list(re.finditer(r'^##[ \t]+(.+?)\s*$', changelog, re.MULTILINE))
    matches = []
    for index, heading in enumerate(headings):
        if re.fullmatch(re.escape(version) + r'(?: - \d{4}-\d{2}-\d{2})?', heading.group(1)):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(changelog)
            matches.append(changelog[heading.end():end].strip())
    if len(matches) != 1:
        raise ValueError(f'CHANGELOG.md must contain exactly one "## {version}" section before releasing')
    if not matches[0]:
        raise ValueError(f'CHANGELOG.md section {version} must include release notes')
    return matches[0] + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('version')
    parser.add_argument('--changelog', type=Path, default=Path('CHANGELOG.md'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        notes = extract_notes(args.changelog.read_text(encoding='utf-8'), args.version)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Release notes error: {error}\n')
    args.output.write_text(notes, encoding='utf-8')


if __name__ == '__main__':
    main()
