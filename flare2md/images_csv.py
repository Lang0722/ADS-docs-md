"""Lists the images of flare2md or sphinx2md output in a CSV file.

Usage:
    python images_csv.py MD_DIR CSV_FILE

The CSV file has one row for each reference to a local image file in a
.md file: Markdown images, links to images, and src / href in the <img>,
<a> and <source> tags of HTML (tables that Markdown cannot show).
References in code, escaped text and the YAML header are no references.
An image file that no page uses has one row with an empty page.

Columns:
  image     the image path relative to MD_DIR, with "/"
  file      the absolute path of the image file
  exists    1, or 0 if the page names a file that is not in MD_DIR
  bytes, sha256, mime   of the file (empty if exists is 0)
  page      the .md file, relative to MD_DIR
  line, column          where ref starts in the page (1-based, in
                        characters)
  kind      markdown or html
  ref       the text of the reference in the page, exactly as written
            (percent-encoded, with a query or fragment if it has one).
            Replace this text at line/column to change the reference.
            In html, ref is HTML-escaped ("&amp;").

The rows are sorted by image, page, line and column.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import mimetypes
import pathlib
import posixpath
import re
import sys
import urllib.parse
from typing import Optional

from markdown_it import MarkdownIt

IMAGE_EXT = frozenset(['.png', '.gif', '.jpg', '.jpeg', '.svg', '.bmp',
                       '.webp', '.tif', '.tiff', '.ico', '.avif'])
# The destination after "](": "<...>" or text without space or parentheses
# (the converters percent-encode these).
MD_DEST = re.compile(r'\]\((?:<([^<>\n]*)>|([^\s()<>]+))')
HTML_TAG = re.compile(r'<(?:img|a|source)\b[^>]*>', re.I)
HTML_ATTR = re.compile(
    r'\s(?:src|href)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', re.I)
SCHEME = re.compile(r'[A-Za-z][A-Za-z0-9+.-]*:')
FRONT_MATTER = re.compile(r'---\n.*?\n---\n', re.S)
COLUMNS = ['image', 'file', 'exists', 'bytes', 'sha256', 'mime', 'page',
           'line', 'column', 'kind', 'ref']


def resolve(dest: str, page_dir: str) -> Optional[str]:
    """The file that a local destination names, relative to the tree root.
    None for a URL, an anchor or a path outside the tree."""
    if not dest or dest.startswith(('#', '/')) or SCHEME.match(dest):
        return None
    path = posixpath.normpath(posixpath.join(
        page_dir, urllib.parse.unquote(re.match(r'[^?#]*', dest).group())))
    if path == '..' or path.startswith('../'):
        return None
    return path


def code_spans(text: str) -> list[tuple[int, int]]:
    """The character ranges of inline code spans (CommonMark rules)."""
    spans, i = [], 0
    while i < len(text):
        c = text[i]
        if c == '\\':
            i += 2
            continue
        if c != '`':
            i += 1
            continue
        n = len(re.match(r'`+', text[i:]).group())
        close = re.compile(r'(?<!`)`{%d}(?!`)' % n).search(text, i + n)
        if close is None:
            i += n
            continue
        spans.append((i, close.end()))
        i = close.end()
    return spans


def visible(md: MarkdownIt, text: str) -> str:
    """text with the YAML header and all code replaced by spaces. The
    offsets and line ends stay the same."""
    def blank(s):
        return re.sub(r'[^\n]', ' ', s)
    m = FRONT_MATTER.match(text)
    head = blank(m.group()) if m else ''
    lines = text[len(head):].splitlines(keepends=True)
    for t in md.parse(text[len(head):]):
        if t.type in ('fence', 'code_block') and t.map:
            for i in range(*t.map):
                lines[i] = blank(lines[i])
    body = ''.join(lines)
    for start, end in code_spans(body):
        body = body[:start] + blank(body[start:end]) + body[end:]
    return head + body


def references(md: MarkdownIt, text: str):
    """(offset, kind, ref) of each link or image destination of a page,
    outside code."""
    view = visible(md, text)
    for m in MD_DEST.finditer(view):
        # An odd number of backslashes escapes the "]".
        if len(re.search(r'\\*$', view[:m.start()]).group()) % 2:
            continue
        group = 1 if m.group(1) is not None else 2
        yield m.start(group), 'markdown', text[m.start(group):m.end(group)]
    for tag in HTML_TAG.finditer(view):
        for m in HTML_ATTR.finditer(view, tag.start(), tag.end()):
            group = 1 if m.group(1) is not None else 2
            yield (m.start(group), 'html',
                   text[m.start(group):m.end(group)])


def file_info(path: pathlib.Path) -> dict:
    data = path.read_bytes()
    return {'exists': 1, 'bytes': len(data),
            'sha256': hashlib.sha256(data).hexdigest(),
            'mime': mimetypes.guess_type(path.name)[0] or ''}


def image_rows(root: pathlib.Path) -> list[dict]:
    """The CSV rows of a converted tree."""
    md = MarkdownIt('commonmark').enable(['table', 'strikethrough'])
    files = sorted(p.relative_to(root).as_posix() for p in root.rglob('*')
                   if p.is_file()
                   and p.relative_to(root).parts[0] != '_report')
    images = {f for f in files
              if posixpath.splitext(f)[1].lower() in IMAGE_EXT}
    info = {}
    rows, used = [], set()
    for page in (f for f in files if f.endswith('.md')):
        text = (root / page).read_text(encoding='utf-8')
        page_dir = posixpath.dirname(page)
        for offset, kind, ref in references(md, text):
            dest = html.unescape(ref) if kind == 'html' else ref
            image = resolve(dest, page_dir)
            if image is None or (
                    image not in images and
                    posixpath.splitext(image)[1].lower() not in IMAGE_EXT):
                continue
            if image not in info:
                info[image] = (file_info(root / image) if image in images
                               else {'exists': 0})
            line = text.count('\n', 0, offset) + 1
            column = offset - (text.rfind('\n', 0, offset) + 1) + 1
            rows.append(dict(info[image], image=image, page=page, line=line,
                             column=column, kind=kind, ref=ref))
            used.add(image)
    for image in sorted(images - used):
        rows.append(dict(file_info(root / image), image=image))
    for row in rows:
        row['file'] = str(root / row['image'])
    rows.sort(key=lambda r: (r['image'], r.get('page', ''),
                             r.get('line', 0), r.get('column', 0)))
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description='List the images of converted Markdown in a CSV file.')
    parser.add_argument('md_dir', type=pathlib.Path)
    parser.add_argument('csv_file', type=pathlib.Path)
    args = parser.parse_args(argv)
    root = args.md_dir.resolve()
    if not root.is_dir():
        parser.error('%s is not a folder' % args.md_dir)
    rows = image_rows(root)
    with open(args.csv_file, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, COLUMNS)
        w.writeheader()
        w.writerows(rows)
    refs = [r for r in rows if r.get('page')]
    print('images %d  references %d  pages with images %d  unused images %d'
          '  references to missing files %d' % (
              len({r['image'] for r in rows if r['exists']}), len(refs),
              len({r['page'] for r in refs}),
              sum(not r.get('page') for r in rows),
              sum(not r['exists'] for r in refs)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
