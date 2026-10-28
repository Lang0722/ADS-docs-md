"""Independent check of flare2md or sphinx2md output against the source HTML.

Usage:
    python verify.py SOURCE_DIR OUT_DIR [--jobs N]

It shares no code with flare2md.py or sphinx2md.py. For each .md file it:
  * checks the YAML header, and that local links and images exist,
  * renders the Markdown with markdown-it (CommonMark + GFM tables), and
    compares the rendered page with the source page: words (recall,
    precision, order), the punctuation that Markdown escaping touches, and
    the counts of tables, images, code blocks, links, list items and the
    heading sequence.

The exit status is 1 if a hard check fails, else 0. The hard checks: no
.md files at all, a topic page without a .md file, a .md file without a
source page, a file that cannot be checked, bad YAML, an unbalanced code
fence, a lost image (also a copied image file that is gone), an empty code
block or link, and a list item that is lost or shows a different number or
nesting depth. The other numbers are a report to read: some differences
come from the source.

A Sphinx page is checked against the documented Sphinx rules: an API
object shows as a heading with its full name and a code block with its
signature ("->" for the arrow), and the pages that Sphinx makes for every
build (index, search, module index) are no topics.
"""

from __future__ import annotations

import argparse
import collections
import difflib
import functools
import multiprocessing
import os
import pathlib
import posixpath
import re
import sys
import traceback
import unicodedata
import urllib.parse
import warnings

# The pure-Python packages in _vendor (see _vendor/vendor.txt) come first.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / '_vendor'))
# pylint: disable=wrong-import-position

from bs4 import BeautifulSoup
from bs4 import Comment
from bs4 import XMLParsedAsHTMLWarning
from markdown_it import MarkdownIt

warnings.filterwarnings('ignore', category=XMLParsedAsHTMLWarning)

TOKEN = re.compile(r'\w+')  # Unicode words, so that a lost "\u03a9" shows.
PUNCT = '\\*_`<>&#~'
# Text of the Flare skin. It must not get into the Markdown.
LEAKS = ['Was this page helpful', 'Skip To Main Content', 'On this page',
         'Navigate previous', 'Navigate next', 'Logout', 'buildtimestamp',
         'You are here', 'Download PDF', 'Change Theme Settings', 'Help Tour',
         'Keyboard Shortcuts']
FENCE = re.compile(r'^(?:[ \t>]|(?:[-*+]|\d+\.)[ \t])*(`{3,})(.*)$')
IMAGE_EXT = re.compile(r'\.(png|gif|jpe?g|svg|bmp|webp)$', re.I)
# maxNesting: the commonmark preset stops at 20 levels (a deep toctree
# entry in a link and code is more) and drops the rest.
MD = MarkdownIt('commonmark', {'html': True, 'maxNesting': 1000}).enable(
    ['table', 'strikethrough'])
GENERATED = re.compile(r'^(genindex(-[^/]+)?|search|[a-z]+-modindex)\.html?$')
SCHEME = re.compile(r'[A-Za-z][A-Za-z0-9+.-]+:')  # One letter is a drive.
NUMBER = re.compile(r'\d+(?:[.,]\d+)*')
# Elements that the browser shows on their own lines (or in a box: a tab
# button). Their edges separate words. The edges of inline elements do not:
# "<a>role</a>s" is "roles".
BLOCKS = frozenset(
    'address article aside blockquote br button caption dd details div dl dt '
    'figcaption figure footer h1 h2 h3 h4 h5 h6 header hr label li main nav ol '
    'p pre section summary table tbody td tfoot th thead tr ul'.split())
# Removed content that is no text for the reader in any page.
NOT_TEXT = ('script', 'style', 'title', '#buildmetadata')
_SRC = _OUT = None  # Set in each worker by _init().


def decode(raw: bytes) -> str:
    """Decodes a page. Line ends become LF, as the HTML standard says."""
    return _decode(raw).replace('\r\n', '\n').replace('\r', '\n')


# Charset labels that browsers read as windows-1252.
CP1252_LABELS = {'iso-8859-1', 'iso8859-1', 'latin1', 'latin-1', 'l1',
                 'us-ascii', 'ascii', 'windows-1252', 'x-cp1252'}


def _decode(raw: bytes) -> str:
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw.decode('utf-16')
    if raw[:3] == b'\xef\xbb\xbf':
        raw = raw[3:]
    if raw[1:2] == b'\x00':
        # UTF-8 bytes EF BF BD stored as UTF-16 units: a lost character.
        # The converter writes a pair as a space and one as U+FFFD.
        text = raw.decode('utf-16-le', 'replace')
        text = re.sub('(?:뿯\xbd){2,}', ' ', text)
        return text.replace('뿯\xbd', '�')
    if raw[:1] == b'\x00' and raw[2:3] == b'\x00':  # UTF-16BE, no BOM
        return raw.decode('utf-16-be', 'replace')
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        pass
    # Not UTF-8: the declared charset, else the one of old Windows pages.
    m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', raw[:4096], re.I)
    label = m.group(1).decode('ascii').lower() if m else 'cp1252'
    label = 'cp1252' if label in CP1252_LABELS else label
    try:
        return raw.decode(label, 'replace')
    except LookupError:
        return raw.decode('cp1252', 'replace')


def visible_text(root) -> str:
    """The text of root with a space at each block edge, as the browser
    lays it out."""
    out = []

    def walk(node):
        for kid in node.children:
            if isinstance(kid, Comment):
                continue
            if isinstance(kid, str):
                out.append(str(kid))
            elif kid.name in BLOCKS:
                out.append(' ')
                walk(kid)
                out.append(' ')
            else:
                walk(kid)

    walk(root)
    return ''.join(out)


def numbers(text: str, root) -> collections.Counter:
    """The numbers in text, with a minus sign that stands before a number
    ("-5", not the hyphen of "x-5"), and the numbers of the ordered list
    items in root (the reader sees them too)."""
    out = collections.Counter()
    for ol in root.find_all('ol'):
        if ol.get('type', '1') != '1':
            continue  # Letters or roman numbers.
        try:
            n = int(ol.get('start', 1) or 1)
        except ValueError:
            n = 1
        for li in ol.find_all('li', recursive=False):
            try:
                n = int(li.get('value', n))
            except ValueError:
                pass
            out[str(n)] += 1
            n += 1
    for m in NUMBER.finditer(text):
        i = m.start()
        sign = '-' if i and text[i - 1] in '-−' and (
            i < 2 or not text[i - 2].isalnum()) else ''
        out[sign + m.group()] += 1
    return out


def norm_path(path: str) -> str:
    """A path for comparison. The converter uses the names on the disk,
    which can differ in case or Unicode form from the link."""
    return unicodedata.normalize('NFC', posixpath.normpath(path)).casefold()


def norm_url(url: str) -> str:
    """A URL as the browser reads it: escapes decoded, and "\\" as "/" in
    the path of a web address."""
    url = url.strip()
    if re.match(r'(?i)(https?|ftp):', url):
        cut = min([i for i in (url.find('?'), url.find('#')) if i >= 0]
                  + [len(url)])
        url = url[:cut].replace('\\', '/') + url[cut:]
    return urllib.parse.unquote(url)


def src_target(href: str, page: pathlib.Path, image=False) -> str | None:
    """The output file (or URL) that a source link or image must point at,
    or None if the converter keeps only the text (an anchor)."""
    href = href.strip()
    if SCHEME.match(href) and not href.lower().startswith('qthelp:'):
        return 'url ' + norm_url(href)
    m = re.match(r'qthelp://[^/]+/doc/([^#?]+)', href)
    if m:
        path = urllib.parse.unquote(m.group(1))
        folder = next((f for f in page.parents if (f / path).is_file()),
                      None)
        target = folder / path if folder is not None else None
    else:
        path = urllib.parse.unquote(re.match(r'[^?#]*', href).group())
        path = path.replace('\\', '/')
        if not path or path.startswith('/'):
            return None  # This page, or a path from the help root.
        target = pathlib.Path(os.path.normpath(page.parent / path))
    if target is None:
        return None
    if not image:
        if (target / 'index.html').is_file():  # A folder: its index page.
            target = target / 'index.html'
        elif (target.suffix.lower() not in ('.html', '.htm') and
              not target.exists()):  # Sphinx html_link_suffix = ''.
            target = next((target.with_name(target.name + s)
                           for s in ('.html', '.htm')
                           if target.with_name(target.name + s).is_file()),
                          target)
    rel = os.path.relpath(target, _SRC).replace(os.sep, '/')
    low = rel.lower()
    if rel.startswith('../'):
        pass  # Outside the source: the converter keeps the path.
    elif not image and low.endswith(('.html', '.htm')):
        rel = rel[:rel.rfind('.')] + '.md'
    elif low.endswith('.md'):
        rel += '.txt'  # A copied MyST source.
    return 'path ' + norm_path(rel)


def md_target(href: str, md_path: pathlib.Path) -> str | None:
    """The file (or URL) that a link or image of the rendered page points
    at, relative to the output folder."""
    href = href.strip()
    if SCHEME.match(href):
        return 'url ' + norm_url(href)
    path = urllib.parse.unquote(re.match(r'[^?#]*', href).group())
    if not path:
        return None
    rel = os.path.relpath(os.path.normpath(md_path.parent / path), _OUT)
    return 'path ' + norm_path(rel.replace(os.sep, '/'))


def code_text(text: str, exact: bool) -> str:
    """A code block for comparison: no blank lines at its ends, no spaces
    on blank lines. Flare code also loses no-break and zero-width spaces
    and the spaces at line ends (help editors add them)."""
    if not exact:
        text = text.replace('\xa0', ' ').replace('​', '')
    lines = [l.rstrip(' \t') if (not exact or not l.strip()) else l
             for l in text.split('\n')]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return '\n'.join(lines)


def in_table(tag, skip_notes=False) -> bool:
    """True if tag is inside a table. Note tables can count as no table,
    because the converter turns them into quotes."""
    return any(not (skip_notes and 'note_table' in p.get('class', []))
               for p in tag.find_parents('table'))


def outer(tags, skip_notes=False):
    return [t for t in tags if not in_table(t, skip_notes)]


def filled(tags):
    """The tags with text or an image. Empty ones have nothing to show."""
    return [t for t in tags if t.get_text().strip() or t.find('img')]


def in_mixed_code(a) -> bool:
    """True if the link is in a code block, or in inline <code> that holds
    more than the link."""
    if a.find_parent('pre') is not None:
        return True
    code = a.find_parent('code')
    if code is None:
        return False
    return ' '.join(code.get_text().split()) != ' '.join(a.get_text().split())


def qthelp_target_exists(href: str, page: pathlib.Path) -> bool:
    """True if a "qthelp://x/doc/<path>" target is a file below a folder
    that holds the page."""
    m = re.match(r'qthelp://[^/]+/doc/([^#?]+)', href)
    if not m:
        return False
    path = urllib.parse.unquote(m.group(1))
    for folder in page.parents:
        if (folder / path).is_file():
            return True
        if folder == _SRC:
            break
    return False


def to_generated(href: str, page: pathlib.Path) -> bool:
    """True if the link goes to a page that Sphinx makes for every build.
    Such a page is no topic, so the link keeps only its text."""
    path = urllib.parse.unquote(re.match(r'[^?#]*', href).group())
    if not path or re.match(r'^[a-z]+:', path):
        return False
    target = page.parent / path
    if (target / 'index.html').is_file():  # The dirhtml builder.
        target = target / 'index.html'
    elif (target.suffix.lower() not in ('.html', '.htm') and
          not target.exists()):  # html_link_suffix '' ("pkg.module")
        target = target.with_name(target.name + '.html')
    return is_generated(target)


def is_generated(p: pathlib.Path) -> bool:
    """True for genindex, search and the module index of a build, also in
    the dirhtml form (genindex/index.html)."""
    if GENERATED.match(p.name):
        return is_build_root(p.parent)
    return (p.name in ('index.html', 'index.htm') and
            bool(GENERATED.match(p.parent.name + '.html')) and
            is_build_root(p.parent.parent))


def build_of(md_rel: str) -> str:
    """The Sphinx build (a folder relative to the source) that holds the
    source of a .md file, or '' (also for Flare)."""
    for folder in (_SRC / md_rel).parents:
        if is_build_root(folder):
            return folder.relative_to(_SRC).as_posix()
        if folder == _SRC:
            break
    return ''


def is_build_root(folder: pathlib.Path) -> bool:
    """True for the root folder of a Sphinx build. The singlehtml builder
    writes no searchindex.js."""
    return ((folder / 'searchindex.js').is_file() or
            (folder / '_static' / 'documentation_options.js').is_file())


def item_key(li) -> str:
    """The text that identifies a list item: its own text, without nested
    items and tables (note boxes count as no table). A full-width row is
    not table text: the converter moves it out of the table. A caption is
    not table text either: the converter writes it before a pipe table (a
    raw HTML table keeps it, so both sides count it). The converter
    writes the label of a note box (an icon in the source) as "Note:", and
    it removes zero-width spaces. Spaces are ignored, because element edges
    add spaces in the source text."""
    def own(t):
        tbl = t.find_parent('table')
        if t.find_parent('li') is not li:
            return False
        if (tbl is None or tbl.find_parent('li') is not li or
                'note_table' in tbl.get('class', []) or
                t.find_parent('caption') is not None):
            return True
        cell = t.find_parent(['td', 'th'])
        return (cell is not None and cell.get('colspan', '1') != '1' and
                len(cell.parent.find_all(['td', 'th'], recursive=False)) == 1)

    text = ''.join(t for t in li.find_all(string=True) if own(t))
    text = re.sub(r'\b(?:Note|Caution|Warning):', '', text)
    return ''.join(text.replace('\u200b', '').split())


def list_numbers(root, is_src) -> dict:
    """(item text, occurrence) -> the number that the item shows, for the
    items of ordered lists outside tables (note boxes count as no table)."""
    out = {}
    for ol in root.find_all('ol'):
        if any(not is_src or 'note_table' not in t.get('class', [])
               for t in ol.find_parents('table')):
            continue
        try:
            n = int(ol.get('start', 1) or 1)
        except ValueError:
            n = 1
        for li in ol.find_all('li', recursive=False):
            if is_src and li.get('value'):
                try:
                    n = int(li['value'])
                except ValueError:
                    pass
            text = item_key(li)
            if text:
                k = 0
                while (text, k) in out:
                    k += 1
                out[(text, k)] = n
            n += 1
    return out


def list_depths(root, is_src) -> dict:
    """(item text, occurrence) -> nesting depth, for list items outside
    tables (note boxes count as no table)."""
    out = {}
    for li in root.find_all('li'):
        if any(not is_src or 'note_table' not in t.get('class', [])
               for t in li.find_parents('table')):
            continue
        depth = len(li.find_parents('li'))
        if is_src:  # A list directly in a list, after an item, shows nested.
            depth += sum(1 for l in li.find_parents(['ul', 'ol'])
                         if l.parent is not None and
                         l.parent.name in ('ul', 'ol') and
                         l.find_previous_sibling('li') is not None)
        text = item_key(li)
        if text:
            k = 0
            while (text, k) in out:
                k += 1
            out[(text, k)] = depth
    return out


def structure(root, page=None) -> dict:
    """Counts that the source and the Markdown must agree on.

    Args:
        root: The source main content, or the rendered Markdown.
        page: The source file if root is the source, else None.
    """
    heads = [(int(h.name[1]),
              ' '.join(visible_text(h).replace('\u200b', '').split()))
             for h in root.find_all(re.compile(r'^h[1-6]$'))]
    heads = [h for h in heads if h[1]]
    if page is not None:
        # Note boxes become quotes. Equation layout tables become images.
        tables = [t for t in outer(root.find_all('table'), skip_notes=True)
                  if 'note_table' not in t.get('class', []) and
                  not t.find_parent('div', class_='latex_display')]
        imgs = [i for i in root.find_all('img') if i.get('src') and
                not i.find_parent('td', class_='note_td_one')]
        # A link in code keeps only its text. Markdown code cannot hold a
        # link (except a code span that is the whole link text). A link in
        # an SVG drawing (an image map) has no text, but it is a link.
        links = [a for a in root.find_all('a', href=True)
                 if (a.get_text().strip() or a.find('img') or
                     a.find_parent('svg') is not None) and
                 not in_mixed_code(a) and a['href'].strip() and
                 not a['href'].strip().startswith(('#', '?', 'file:')) and
                 'createpage.action' not in a['href'] and
                 not to_generated(a['href'], page) and
                 (not a['href'].startswith('qthelp:') or
                  qthelp_target_exists(a['href'], page))]
        lis = filled(outer(root.find_all('li'), skip_notes=True))
    else:
        tables = outer(root.find_all('table'))
        imgs = root.find_all('img')
        links = filled(root.find_all('a', href=True))
        lis = filled(outer(root.find_all('li')))
    return {'heads': heads, 'tables': len(tables), 'imgs': len(imgs),
            'pres': len([p for p in root.find_all('pre')
                         if p.get_text().strip()]),
            'links': len(links),
            'lis': len(lis), 'link_tags': links, 'img_tags': imgs}


def split_code(body):
    """Returns (prose lines, True if a code fence is left open)."""
    prose, fence = [], 0
    for line in body.split('\n'):
        m = FENCE.match(line)
        # An opening fence has no backtick in its info string (CommonMark).
        if m and ((not fence and '`' not in m.group(2)) or (
                fence and len(m.group(1)) >= fence and
                not m.group(2).strip())):
            fence = 0 if fence else len(m.group(1))
        elif not fence:
            prose.append(line)
    return prose, bool(fence)


# The escapes of a double-quoted YAML string.
YAML_UNESCAPE = {'0': '\0', 'a': '\x07', 'b': '\x08', 't': '\t', 'n': '\n',
                 'v': '\x0b', 'f': '\x0c', 'r': '\r', 'e': '\x1b', '"': '"',
                 '\\': '\\', '/': '/', ' ': ' ', 'N': '\x85', '_': '\xa0',
                 'L': ' ', 'P': ' '}
YAML_DOUBLE = re.compile(r'"((?:[^"\\]|\\(?:[0abtnvfre"\\/ N_LP]|'
                         r'x[0-9A-Fa-f]{2}|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}))*)"')
# A plain scalar that YAML does not read as one plain string.
YAML_BAD_PLAIN = re.compile(
    r"""^$|^['"\[\]{}#&*!|>%@`,]|^[-?:](?: |$)|:(?: |$)| #|^ | $""")


def yaml_scalar(text: str):
    """Reads one scalar of the YAML header: null, a plain string, a
    'single-quoted' or a "double-quoted" string. Other forms raise
    ValueError."""
    if text == 'null':
        return None
    m = re.fullmatch(r"'((?:[^']|'')*)'", text)
    if m:
        return m.group(1).replace("''", "'")
    m = YAML_DOUBLE.fullmatch(text)
    if m:
        return re.sub(r'\\(x..|u....|U........|.)', lambda e: (
            chr(int(e.group(1)[1:], 16)) if len(e.group(1)) > 1
            else YAML_UNESCAPE[e.group(1)]), m.group(1))
    if YAML_BAD_PLAIN.search(text):
        raise ValueError(f'bad YAML scalar {text!r}')
    return text


def read_header(text: str) -> dict:
    """Reads the YAML header that the converters write: "key: value" lines,
    "key: []", and "key:" with "- value" lines (a list). Other lines raise
    ValueError."""
    meta, bare, key = {}, [], None  # key: the key of the open list.
    for line in text.split('\n'):
        m = re.fullmatch(r'([A-Za-z_]\w*):(?: (.*))?', line)
        if m:
            name, value = m.groups()
            if name in meta:
                raise ValueError(f'two keys {name!r}')
            meta[name] = [] if value in (None, '[]') else yaml_scalar(value)
            key = name if value is None else None
            bare += [name] if value is None else []
        elif line.startswith('- ') and key:
            meta[key].append(yaml_scalar(line[2:]))
        else:
            raise ValueError(f'bad YAML header line {line!r}')
    for name in bare:  # "key:" with no items is null.
        meta[name] = meta[name] or None
    return meta


def check_header_and_links(md_path: pathlib.Path, text: str, res: dict):
    """Checks the YAML header and that local link targets exist. Returns
    (the Markdown body, the rendered body)."""
    m = re.match(r'---\n(.*?)\n---\n', text, re.S)
    try:
        meta = read_header(m.group(1))
        # A Sphinx page always has a project (Flare pages have no key).
        res['yaml_ok'] = bool(meta['title'] and meta['source'] and (
            'project' not in meta or
            (isinstance(meta['project'], str) and meta['project'].strip())))
        res['breadcrumb'] = bool(meta.get('breadcrumb'))
        res['project'] = meta.get('project')
        res['meta'] = (meta.get('title'), meta.get('breadcrumb'))
    except Exception:  # pylint: disable=broad-except
        res['yaml_ok'] = False
        res['breadcrumb'] = False
    body = text[m.end():] if m else text
    # The rendered page has the real targets, also of an image in a link.
    # Text in code is no link there.
    rendered = BeautifulSoup(MD.render(body), 'html5lib')
    targets = [(False, a['href']) for a in rendered.find_all('a', href=True)]
    targets += [(True, i['src']) for i in rendered.find_all('img', src=True)]
    links = collections.Counter()
    broken = []
    for is_img, url in targets:
        if SCHEME.match(url):
            links['external'] += 1
            continue
        path = urllib.parse.unquote(url.split('#')[0])
        if not path:
            continue
        if is_img or IMAGE_EXT.search(path):
            kind = 'img'
        elif path.endswith('.md'):
            kind = 'md'
        else:
            kind = 'other'
        ok = exact_file(md_path.parent / path)
        links[f'{kind}_{"ok" if ok else "broken"}'] += 1
        if not ok:
            broken.append((kind, path))
            # An image that is in the source but not in the output was not
            # copied, or it was removed later.
            src_img = (_SRC / md_path.parent.relative_to(_OUT) / path)
            if kind == 'img' and src_img.is_file():
                res['img_not_copied'] = res.get('img_not_copied', 0) + 1
    res['link_counts'] = dict(links)
    res['broken'] = broken
    return body, rendered


@functools.lru_cache(maxsize=4096)
def _names(folder: str) -> frozenset:
    try:
        return frozenset(os.listdir(folder))
    except OSError:
        return frozenset()


def exact_file(p: pathlib.Path) -> bool:
    """True if p is a file and each name on its path below the output
    folder has exactly this spelling. (macOS and Windows also find a name in
    another case or Unicode form. Other systems do not.)"""
    p = pathlib.Path(os.path.normpath(p))
    if not p.is_file():
        return False
    for part in [p, *p.parents]:
        if part == _OUT or part.parent == part:
            break
        if part.name not in _names(str(part.parent)):
            return False
    return True


def find_main(s):
    """Returns (main content, True for a Sphinx page), or (None, False)."""
    main = s.find(id='mc-main-content')
    if main is not None:
        return main, False
    main = (s.find(attrs={'itemprop': 'articleBody'}) or
            s.select_one('div.body') or s.select_one('article.bd-article') or
            s.select_one('article[role=main]') or s.find(attrs={'role': 'main'}))
    return main, main is not None


def letter_label(kind: str, n: int) -> str:
    """The CSS list label of item n: "loweralpha" 28 -> "ab", "lowerroman"
    4 -> "iv"."""
    if kind.endswith('alpha'):
        s = ''
        while n > 0:
            n, r = divmod(n - 1, 26)
            s = 'abcdefghijklmnopqrstuvwxyz'[r] + s
    else:
        s = ''
        for value, digits in zip(
                (1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1),
                'm cm d cd c xc l xl x ix v iv i'.split()):
            s += digits * (n // value)
            n %= value
    return s.upper() if kind.startswith('upper') else s


def expect_sphinx(s, main):
    """Writes the source the way the Sphinx rules show it: API objects of
    any domain as a heading and a signature, math without its delimiters
    as code, and code without line numbers."""
    for e in main.select('a.headerlink, span.linenos, td.linenos, '
                         'pre a.viewcode-back'):
        e.decompose()
    # Text that the browser shows: the classifier colon (CSS), the
    # expansion of an abbreviation (the converter writes it), the state of
    # a task-list checkbox and the labels of letter lists.
    for e in main.select('span.classifier'):
        before = e.previous_sibling
        if not (str(before or '').rstrip().endswith(':') or (
                getattr(before, 'get', None) and 'classifier-delimiter' in
                before.get('class', []))):
            e.insert(0, ' : ')
    for e in main.find_all('abbr', title=True):
        if not e.find_parents(['pre', 'code']) and not any(
                'sig' in d.get('class', []) for d in e.find_parents('dt')):
            e.append(f' ({e["title"]})')
    # The text of a sphinx-design card link is hidden. The link goes on
    # the card title.
    for a in main.select('a.sd-stretched-link[href]'):
        card = a.find_parent(class_='sd-card')
        title = card.select_one('.sd-card-title') if card else None
        if title is not None:
            new = s.new_tag('a', href=a['href'])
            new.string = title.get_text()
            title.clear()
            title.append(new)
            a.decompose()
    # The links back from a footnote to its references are no text.
    for e in main.select('span.backrefs, span.fn-backref, em > a.fn-backref'):
        if e.find_parent(class_=['footnote', 'citation']) is not None:
            e.decompose()
    for e in main.select('input.task-list-item-checkbox'):
        e.replace_with('[x] ' if e.has_attr('checked') else '[ ] ')
    for ol in main.find_all('ol'):
        kind = next((c for c in ol.get('class', []) if c in (
            'loweralpha', 'upperalpha', 'lowerroman', 'upperroman')), None)
        n = int(ol.get('start', 1) or 1)
        items = ol.find_all('li', recursive=False)
        if not kind and n + len(items) - 1 >= 10 ** 9:
            kind = 'decimal'  # More digits than CommonMark allows.
        if kind:
            for li in items:
                li.insert(0, (str(n) if kind == 'decimal' else
                              letter_label(kind, n)) + '. ')
                n += 1
            ol.name = 'ul'
    # A guilabel accelerator ("<span class="accelerator">C</span>ancel") is
    # part of the word.
    for e in main.select('span.accelerator'):
        parent = e.parent
        e.unwrap()
        parent.smooth()
    # Code shows its text as one run ("python<em>version</em>" is one word).
    for code in main.find_all('code'):
        if code.find_parent('pre') is None and code.find('a') is None:
            code.string = code.get_text()
    for h in main.find_all(re.compile(r'^h([7-9]|\d\d)$')):
        h.name = 'h6'
    for tbl in main.select('table.highlighttable'):
        pre = tbl.select_one('td.code pre')
        if pre is not None:
            tbl.replace_with(pre)
    # Old footnotes are tables. The converter writes paragraphs.
    for tbl in main.select('table.footnote, table.citation'):
        div = s.new_tag('div')
        for td in tbl.find_all('td'):
            p = s.new_tag('p')
            p.extend(list(td.contents))
            div.append(p)
        tbl.replace_with(div)
    # The HTML4 writer writes field lists, option lists and the docinfo as
    # tables. The converter writes the <dl> of the HTML5 writer. A term is a
    # <th> or an option group. A cell with no content is a spacer.
    for tbl in main.select('table.field-list, table.option-list, '
                           'table.docinfo'):
        dl = s.new_tag('dl')
        if 'option-list' not in tbl.get('class', []):
            dl['class'] = ['field-list']
        for cell in [c for c in tbl.find_all(['th', 'td'])
                     if c.find_parent('table') is tbl]:
            term = cell.name == 'th' or 'option-group' in cell.get('class', [])
            if not term and not cell.get_text().strip() and not cell.find(
                    True):
                continue
            item = s.new_tag('dt' if term else 'dd')
            item.extend(list(cell.contents))
            dl.append(item)
        tbl.replace_with(dl)
    # The browser shows an SVG <object>, not its fallback content.
    for obj in main.find_all('object'):
        if obj.get('type', '').startswith('image/') or re.search(
                r'\.(svg|png|gif|jpe?g)$', obj.get('data', ''), re.I):
            obj.replace_with(s.new_tag('img', src=obj.get('data', '')))
    # Sphinx 4 and older: text that CSS adds.
    for e in main.select('.brackets'):
        if not e.find(class_='fn-bracket') and '[' not in e.get_text():
            e.insert(0, '[')
            e.append(']')
    for dt in main.select('dl.field-list > dt'):
        if not dt.find(class_='colon') and ':' not in dt.get_text():
            dt.append(':')
    # Math: the LaTeX is text (MathJax) or the alt text of images (imgmath).
    # A display equation keeps its number after it.
    for e in main.select('div.math') + main.select('span.math, img.math'):
        if e.parent is None:
            continue  # In a display equation that is done.
        numbers = [n.extract().get_text().strip()
                   for n in e.select('span.eqno')]
        imgs = [e] if e.name == 'img' else e.find_all('img')
        tex = ' '.join(i.get('alt', '') for i in imgs) or e.get_text()
        new = s.new_tag('pre' if e.name == 'div' else 'code')
        new.string = re.sub(r'^\\[(\[]|\\[)\]]$', '', tex.strip()).strip()
        e.replace_with(new)
        if numbers:
            p = s.new_tag('p')
            p.string = numbers[0]
            new.insert_after(p)
    objects = []
    for dl in main.find_all('dl'):
        dt = dl.find('dt', recursive=False)
        if dt is not None and ('sig-object' in dt.get('class', []) or
                               dt.find(class_='descname') is not None):
            objects.append(dl)
    plan = []
    ids = {id(dl) for dl in objects}  # Tags compare by content, not by id.
    for dl in objects:
        sec = dl.find_parent(lambda x: x.name == 'section' or (
            x.name == 'div' and 'section' in x.get('class', [])))
        # The objects between the section and this object.
        between = [p for p in dl.find_parents('dl') if id(p) in ids and (
            sec is None or any(q is sec for q in p.parents))]
        head = sec.find(re.compile(r'^h[1-6]$')) if sec is not None else None
        level = min(6, (int(head.name[1]) if head is not None else 1) + 1 +
                    len(between))
        # Headings in the description go below the object heading.
        for dd in dl.find_all('dd', recursive=False):
            for h in dd.find_all(re.compile(r'^h[1-6]$')):
                h.name = 'h%d' % min(6, max(int(h.name[1]), level + 1))
        # Sphinx 2 and older have no domain class. A C id starts with "c.",
        # a C++ id with "_CPPv". An option or envvar is a std object.
        # An object in an alias directive: the domain is on its terms.
        first_id = dl.find('dt', recursive=False).get('id') or ''
        classes = set(dl.get('class', [])) | set(
            dl.find('dt', recursive=False).get('class', []))
        python = not classes & {
            'c', 'cpp', 'js', 'rst', 'std', 'http', 'mat', 'describe',
            'object', 'option', 'cmdoption', 'envvar'} and not (
                first_id.startswith('c.') or first_id.startswith('_CPPv'))
        plan += [(dt, level, i == 0, python)
                 for i, dt in enumerate(dl.find_all('dt', recursive=False))]
    for dt, level, first, python in plan:
        for e in dt.select('.viewcode-link'):
            e.decompose()
        # Only the arrow before the return annotation becomes "->".
        icons = dt.select('.sig-return-icon')
        for e in icons:
            e.string = '->'
        if not icons:
            for x in dt.find_all(string=re.compile('\u2192')):
                if x.find_parent(class_=['sig-param', 'default_value']) is None:
                    x.replace_with(x.replace('\u2192', '->'))
        sig = ' '.join(dt.get_text(' ').split())
        if first:
            ident = dt.get('id') or ''
            # The name parts of the last signature line that has some. A
            # Python id is the full name if it ends with them.
            names = ['sig-prename', 'descclassname', 'sig-name', 'descname']
            parts = [[]]
            for e in dt.descendants:
                if getattr(e, 'name', None) == 'br':
                    parts.append([])
                elif (getattr(e, 'name', None) and
                      set(e.get('class', [])) & set(names) and
                      e.find_parent(class_=names) is None):
                    parts[-1].append(e.get_text())
            shown = ' '.join(''.join(
                next((p for p in reversed(parts) if p), [])).split())
            if not (python and shown and (ident == shown or
                                          ident.endswith('.' + shown))):
                ident = shown
            h = s.new_tag(f'h{level}')
            h.string = (' '.join(ident.split()) or
                        ' '.join(visible_text(dt).split()))
            dt.insert_before(h)
        if sig:
            pre = s.new_tag('pre', attrs={'data-sig': ''})
            pre.string = sig
            dt.insert_before(pre)
        dt.decompose()


def source_of(md_rel: str) -> pathlib.Path | None:
    """The source page of a .md file. The extension case can differ."""
    stem = _SRC / md_rel[:-3]
    for ext in ('.html', '.htm'):
        if (stem.parent / (stem.name + ext)).is_file():
            return stem.parent / (stem.name + ext)
    if stem.parent.is_dir():
        for p in stem.parent.iterdir():
            if (p.stem == stem.name and p.suffix.lower() in ('.html', '.htm')
                    and p.is_file()):
                return p
    return None


def check_safe(md_rel: str) -> dict:
    """check(), but a file that cannot be checked is a result too."""
    try:
        return check(md_rel)
    except Exception as e:  # pylint: disable=broad-except
        return {'rel': md_rel, 'crash': f'{type(e).__name__}: {e}',
                'trace': traceback.format_exc(limit=2), 'yaml_ok': True,
                'breadcrumb': False, 'link_counts': {}, 'broken': []}


def check(md_rel: str) -> dict:
    md_path = _OUT / md_rel
    src = source_of(md_rel)
    text = md_path.read_text(encoding='utf-8')
    res = {'rel': md_rel}
    body, r = check_header_and_links(md_path, text, res)
    if src is None:
        res['no_source'] = True
        return res

    html = decode(src.read_bytes())
    s = BeautifulSoup(html, 'html5lib')
    main, sphinx = find_main(s)
    if sphinx:  # The HTML5 rules of a browser, as the converter.
        s = BeautifulSoup(html, 'html5lib')
        main = find_main(s)[0]
    main = main or s.body or s
    if sphinx:
        # A viewcode page (_modules): Sphinx 9 writes '<div …>\n<a …>[docs]
        # </a>\n' before an object and '</div>\n' after it, in the <pre>.
        # The added line breaks are no part of the source file.
        for pre in main.find_all('pre'):
            if pre.find('div', class_='viewcode-block') is None:
                continue
            markup = re.sub(r'(<div class="viewcode-block"[^>]*>)\n'
                            r'(<a class="viewcode-back"[^>]*>.*?</a>)\n',
                            r'\1\2', pre.decode_contents(), flags=re.S)
            if markup != pre.decode_contents():
                markup = re.sub(r'</div>\n', '</div>', markup)
            new = BeautifulSoup('<pre>%s</pre>' % markup, 'html.parser').pre
            new.attrs = pre.attrs
            pre.replace_with(new)
    # A link in an SVG drawing has no text. The converter shows its URL.
    for a in main.select('svg a[href]'):
        if not a.get_text().strip():
            a.string = a['href']
    # The browser does not show these. A form in a Sphinx article is
    # content: only its controls are no text. A Sphinx contents list is
    # navigation (anchor links to the headings of the page), and so is a
    # <nav> in the article (the breadcrumb bar of sphinx-nefertiti).
    hidden = (['script', 'style', 'title', 'select', 'textarea',
               'nav', 'div.topic.contents', 'aside.topic.contents',
               'div.nbinput > div.prompt', 'div.nboutput > div.prompt',
               '[role=navigation]', 'noscript', 'template',
               # myst-nb shows one label of a toggle. The book theme hides
               # the sidenote copy in an admonition.
               'summary > .expanded', 'span.sidenote.d-n',
               'span.marginnote.d-n']
              if sphinx
              else ['form', '#buildmetadata', 'script', 'style', 'title'])
    # The words that these rules remove (a rule that both tools share
    # would hide a loss from this check).
    removed = collections.Counter()
    for sel in hidden:
        for e in main.select(sel):
            if sel not in NOT_TEXT:
                removed[sel] += len(tokens(visible_text(e)))
            e.decompose()
    res['removed'] = {k: v for k, v in removed.items() if v}
    for c in main.find_all(string=lambda x: isinstance(x, Comment)):
        c.extract()
    # An image in a data: URI is in the page. The converter keeps its alt.
    for img in main.find_all('img'):
        if img.get('src', '').lower().startswith('data:'):
            img.replace_with(img.get('alt', '').strip())
    # A video or a sound is a link to its file (the content is fallback
    # text for old browsers).
    # An <iframe> is a link with its title, or else its URL.
    for media in main.find_all(['video', 'audio', 'iframe']):
        urls = [media.get('src')] + [e.get('src') for e in media('source')]
        url = next((x for x in urls if x), None)
        if url is None:
            media.decompose()
        else:
            link = s.new_tag('a', href=url)
            if media.name == 'iframe':
                link.string = media.get('title', '').strip() or url
            else:
                link.string = (url.split('?')[0].split('#')[0]
                               .rsplit('/', 1)[-1])
            media.replace_with(link)
    if sphinx:
        expect_sphinx(s, main)
        # Inline math is "$`E = mc^2`$". The dollar signs are delimiters.
        for code in r.find_all('code'):
            before, after = code.previous_sibling, code.next_sibling
            if (isinstance(before, str) and isinstance(after, str) and
                    before.endswith('$') and after.startswith('$')):
                before.replace_with(before[:-1])
                after.replace_with(after[1:])
    src_struct = structure(main, src)
    # A code block shows its text as it is. Highlight spans can end inside
    # a word ("{% f" + "or").
    for pre in main.find_all('pre'):
        for br in pre.find_all('br'):
            br.replace_with('\n')  # The browser shows a line break.
        pre.string = pre.get_text()
    src_vis = visible_text(main)
    src_tok = tokens(src_vis)

    # The YAML title is the <title> of a Flare page, or the <h1> of a Sphinx
    # article. The breadcrumb of a Flare page is its TOC path.
    title, crumbs = res.get('meta', (None, None))
    if sphinx:
        h1 = main.find('h1')
        want = ' '.join(visible_text(h1).split()) if h1 is not None else ''
    else:
        want = ' '.join(s.title.get_text().split()) if s.title else ''
    res['title'] = (want, title) if want and title != want else None
    toc = s.html.get('data-mc-toc-path') if s.html and not sphinx else None
    if toc and [c for c in toc.split('|') if c] != crumbs:
        res['crumbs'] = ([c for c in toc.split('|') if c], crumbs)

    # The converter adds the title as H1 when the page has none. It must
    # be the title. Then drop it.
    src_first = main.find(re.compile(r'^h[1-6]$'))
    md_first = r.find(re.compile(r'^h[1-6]$'))
    if md_first is not None and md_first.name == 'h1' and (
            src_first is None or src_first.name != 'h1'):
        added = ' '.join(visible_text(md_first).split())
        if title and added != ' '.join(str(title).split()):
            res['title'] = (title, added)
        md_first.decompose()
    md_struct = structure(r)
    md_vis = visible_text(r)
    md_tok = tokens(md_vis)

    # Numbers must be exact. One lost value is below the recall limit of a
    # long page.
    sn, mn = numbers(src_vis, main), numbers(md_vis, r)
    res['numbers'] = (((sn - mn).most_common(4), (mn - sn).most_common(4))
                      if sn != mn else None)
    # Code blocks, in order. A signature is compared without spaces: the
    # converter can write one parameter on each line.
    src_code = [(p.has_attr('data-sig'), p.get_text())
                for p in main.find_all('pre') if p.get_text().strip()]
    md_code = [p.get_text() for p in r.find_all('pre')
               if p.get_text().strip()]
    res['code'] = None
    # A different number of blocks is in the 'pres' counts.
    for i, ((sig, a), b) in enumerate(zip(src_code, md_code)
                                      if len(src_code) == len(md_code)
                                      else []):
        if sig:
            a, b = ''.join(a.split()), ''.join(b.split())
        else:
            a, b = code_text(a, sphinx), code_text(b, sphinx)
        if a != b:
            la, lb = a.split('\n'), b.split('\n')
            j = next((j for j, (x, y) in enumerate(zip(la, lb)) if x != y),
                     min(len(la), len(lb)))
            res['code'] = (i, (la + [''])[j][:60], (lb + [''])[j][:60])
            break
    # Link and image targets: the converter must point each one at the
    # page or file of the source link.
    st = collections.Counter(filter(None, [
        src_target(a['href'], src) for a in src_struct['link_tags']] + [
        src_target(i['src'], src, True) for i in src_struct['img_tags']]))
    mt = collections.Counter(filter(None, [
        md_target(a['href'], md_path) for a in md_struct['link_tags']] + [
        md_target(i['src'], md_path) for i in md_struct['img_tags']
        if i.get('src')]))
    res['targets'] = (((st - mt).most_common(3), (mt - st).most_common(3))
                      if st != mt else None)
    src_text, md_text = main.get_text(), r.get_text()

    src_nums, md_nums = list_numbers(main, True), list_numbers(r, False)
    src_depth, md_depth = list_depths(main, True), list_depths(r, False)
    cs, cm = collections.Counter(src_tok), collections.Counter(md_tok)
    common = sum((cs & cm).values())
    sm = difflib.SequenceMatcher(None, src_tok, md_tok,
                                 autojunk=len(src_tok) > 4000)
    res.update({
        'src_tok': len(src_tok), 'md_tok': len(md_tok),
        'recall': common / len(src_tok) if src_tok else 1.0,
        'precision': common / len(md_tok) if md_tok else 1.0,
        'order': sm.ratio(),
        # Punctuation that Markdown escaping touches must survive rendering.
        'punct': {c: (src_text.count(c), md_text.count(c)) for c in PUNCT
                  if src_text.count(c) != md_text.count(c)},
        'lost': (cs - cm).most_common(6), 'extra': (cm - cs).most_common(6),
        # Skin text, unless the page itself has it.
        'leaks': [l for l in LEAKS if l in body and l not in src_text],
        'pipe_text': len([p for p in r.find_all('p')
                          if p.get_text().lstrip().startswith('| ')]),
        'unbalanced_fence': split_code(body)[1],
        # An item must show the same number as in the source.
        # A lost item (for example one joined into the item above) fails.
        'ol_numbers': any(md_nums.get(k) != v for k, v in src_nums.items()),
        'li_depth': any(md_depth.get(k) != v for k, v in src_depth.items()),
        # Empty output that shows nothing. The counts above skip it.
        'empty': (len([p for p in r.find_all('pre')
                       if not p.get_text().strip()]) +
                  len([a for a in r.find_all('a', href=True)
                       if not a.get_text().strip() and not a.find('img')])),
    })
    for k in ('tables', 'imgs', 'pres', 'links', 'lis'):
        res[k] = (src_struct[k], md_struct[k])
    res['heads'] = (src_struct['heads'], md_struct['heads'])
    return res


def tokens(text: str) -> list[str]:
    # A zero-width space does not split a word on the screen.
    return [t.lower() for t in TOKEN.findall(
        unicodedata.normalize('NFC', text.replace('\u200b', '')))]


def in_build(p: pathlib.Path) -> bool:
    """True if a folder above p (up to the source folder) is the root of a
    Sphinx build."""
    for folder in p.parents:
        if is_build_root(folder):
            return True
        if folder == _SRC:
            break
    return False


def topic_pages() -> tuple[list[str], list[dict], list[str]]:
    """Source pages with a Flare main content div or a Sphinx article body,
    as .md paths. Each one must have a .md file. Pages that Sphinx makes
    for every build, and files in _static and _sources, are no topics.
    Also returns the pages that cannot be read, as crashed results, and
    the other pages with text (no main content found) as .md paths."""
    out, unreadable, others = [], [], []
    for p in _SRC.rglob('*'):
        if (p.suffix.lower() not in ('.html', '.htm') or not p.is_file() or
                _OUT in p.parents):
            continue
        try:
            text = decode(p.read_bytes())
        except (OSError, UnicodeError) as e:
            unreadable.append({
                'rel': p.relative_to(_SRC).as_posix(),
                'crash': f'source page: {type(e).__name__}: {e}',
                'yaml_ok': True, 'breadcrumb': False, 'link_counts': {},
                'broken': []})
            continue
        md_rel = p.relative_to(_SRC).with_suffix('.md').as_posix()
        if 'id="mc-main-content"' not in text:
            if is_generated(p) or any(d in ('_static', '_sources')
                                      for d in p.relative_to(_SRC).parts):
                continue
            soup = BeautifulSoup(text, 'html5lib')
            if not in_build(p) or find_main(soup)[0] is None:
                # A redirect, or an app page (JupyterLite), has few words.
                for e in soup(['script', 'style', 'title']):
                    e.decompose()
                if (soup.find('meta', attrs={'http-equiv': re.compile(
                        '^refresh$', re.I)}) is None and
                        len(soup.get_text(' ').split()) >= 20):
                    others.append(md_rel)
                continue
        out.append(md_rel)
    return out, unreadable, others


def _init(src, out):
    global _SRC, _OUT
    _SRC, _OUT = pathlib.Path(src).resolve(), pathlib.Path(out).resolve()


def report(res, missing_md, not_checked=()) -> int:
    """Prints the report. Returns the number of hard failures."""
    crashed = [r for r in res if 'crash' in r]
    print(f'files that crashed the check {len(crashed)}')
    for r in crashed[:5]:
        print(f"    {r['rel']}: {r['crash']}")
    hard_extra = len(crashed) + len(missing_md)
    print(f'topic pages without a .md file {len(missing_md)} '
          f'{missing_md[:3]}')
    pages = [r for r in res if 'recall' in r]
    no_src = [r['rel'] for r in res if r.get('no_source')]
    yaml_bad = [r['rel'] for r in res if not r['yaml_ok']]
    links = collections.Counter()
    broken = collections.Counter()
    for r in res:
        links.update(r['link_counts'])
        broken.update((k, p.rsplit('/', 1)[-1]) for k, p in r['broken'])
    print(f'md files {len(res)}  with source page {len(pages)}  '
          f'without {len(no_src)} {no_src[:3]}')
    print(f'yaml ok {len(res) - len(yaml_bad)}  bad {len(yaml_bad)} '
          f'{yaml_bad[:3]}  with breadcrumb '
          f'{sum(r["breadcrumb"] for r in res)}')
    # All pages of one build have one project. (A page title in it would
    # make one value for each page.)
    projects = collections.defaultdict(set)
    for r in res:
        if r.get('project') is not None:
            projects[build_of(r['rel'])].add(str(r['project']))
    mixed = {b: sorted(v)[:3] for b, v in projects.items() if len(v) > 1}
    print(f'builds with more than one project {len(mixed)} '
          f'{list(mixed.items())[:2]}')
    print('links', dict(sorted(links.items())))
    for (kind, name), n in broken.most_common(8):
        print(f'    broken {kind} x{n}: {name[:70]}')
    not_copied = [r['rel'] for r in res if r.get('img_not_copied')]
    print('pages with source images missing in the output', len(not_copied),
          not_copied[:3])
    hard_extra += len(not_copied)
    if not pages:
        return len(no_src) + len(yaml_bad) + hard_extra
    for k, lim in (('recall', 0.995), ('precision', 0.99), ('order', 0.98)):
        bad = sorted((r for r in pages if r[k] < lim), key=lambda r: r[k])
        print(f'{k:10} min {min(r[k] for r in pages):.4f}  '
              f'pages below {lim}: {len(bad)}')
        for r in bad[:6]:
            print(f'    {r[k]:.4f} {r["rel"]}  src={r["src_tok"]} '
                  f'md={r["md_tok"]}  lost={r["lost"][:4]} '
                  f'extra={r["extra"][:4]}')
    print('leaks', sum(bool(r['leaks']) for r in pages),
          collections.Counter(l for r in pages
                              for l in r['leaks']).most_common())
    print('pipe rows shown as text', sum(r['pipe_text'] for r in pages),
          [r['rel'] for r in pages if r['pipe_text']][:5])
    fences = [r['rel'] for r in pages if r['unbalanced_fence']]
    print('unbalanced fences', len(fences), fences[:5])
    pd = [r for r in pages if r['punct']]
    print('punctuation differs:', len(pd), collections.Counter(
        c for r in pd for c in r['punct']).most_common())
    for r in pd[:8]:
        print('   ', r['rel'], r['punct'])
    for k in ('tables', 'imgs', 'pres', 'links', 'lis'):
        diff = [r for r in pages if r[k][0] != r[k][1]]
        print(f'{k:7} src={sum(r[k][0] for r in pages):7} '
              f'md={sum(r[k][1] for r in pages):7}  pages differ: {len(diff)}',
              [(r['rel'], r[k]) for r in diff[:4]])
    hd = [r for r in pages if r['heads'][0] != r['heads'][1]]
    print('heading sequence differs:', len(hd))
    for r in hd[:5]:
        sh, mh = r['heads']
        i = next((i for i, (a, b) in enumerate(zip(sh, mh)) if a != b),
                 min(len(sh), len(mh)))
        print('   ', r['rel'], len(sh), len(mh), 'first diff', sh[i:i + 1],
              mh[i:i + 1])
    for key, label in (('numbers', 'numbers differ'),
                       ('code', 'code blocks differ'),
                       ('targets', 'link and image targets differ'),
                       ('title', 'titles differ'),
                       ('crumbs', 'breadcrumbs differ')):
        bad = [r for r in pages if r.get(key)]
        print(f'{label}: {len(bad)}')
        for r in bad[:5]:
            print('   ', r['rel'], str(r[key])[:200])
    removed = collections.Counter()
    for r in pages:
        removed.update(r.get('removed', {}))
    # A search form removes a few words on each Flare page.
    many = [(r['rel'], sum(r['removed'].values())) for r in pages
            if sum(r.get('removed', {}).values()) > 20]
    print(f'content removed by hidden rules: {sum(removed.values())} words '
          f'{removed.most_common(5)}; pages with more than 20 words: '
          f'{len(many)} {many[:3]}')
    print(f'html pages not checked (no main content): {len(not_checked)} '
          f'{not_checked[:3]}')
    lost_imgs = sum(1 for r in pages if r['imgs'][1] < r['imgs'][0])
    empty = [r['rel'] for r in pages if r['empty']]
    print('pages with empty code blocks or links', len(empty), empty[:5])
    numbers = [r['rel'] for r in pages if r['ol_numbers']]
    print('pages with wrong list numbers', len(numbers), numbers[:5])
    depth = [r['rel'] for r in pages if r['li_depth']]
    print('pages with wrong list nesting', len(depth), depth[:5])
    hard = (len(no_src) + len(yaml_bad) + len(fences) + lost_imgs +
            len(empty) + len(numbers) + len(depth) + hard_extra)
    print(f'hard failures {hard} (no source {len(no_src)}, bad yaml '
          f'{len(yaml_bad)}, unbalanced fences {len(fences)}, pages with '
          f'lost images {lost_imgs}, images not copied {len(not_copied)}, '
          f'empty output {len(empty)}, lost or wrong list numbers '
          f'{len(numbers)}, lost or wrong list nesting {len(depth)}, '
          f'crashed {len(crashed)}, no .md {len(missing_md)})')
    return hard


def _positive_int(text: str) -> int:
    try:
        n = int(text)
    except ValueError:
        n = 0
    if n < 1:
        raise argparse.ArgumentTypeError(f'must be a number of 1 or more: '
                                         f'{text}')
    return n


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description='Check flare2md output against the source HTML.')
    parser.add_argument('source_dir', help='the SOURCE_DIR given to flare2md')
    parser.add_argument('out_dir', help='the OUT_DIR given to flare2md')
    parser.add_argument('--jobs', type=_positive_int, default=None,
                        help='worker processes (default: one per CPU)')
    args = parser.parse_args(argv)
    _init(args.source_dir, args.out_dir)
    if not _SRC.is_dir() or not _OUT.is_dir():
        print(f'error: not a folder: {_SRC if not _SRC.is_dir() else _OUT}',
              file=sys.stderr)
        return 1
    rels = sorted(p.relative_to(_OUT).as_posix() for p in _OUT.rglob('*.md')
                  if p.relative_to(_OUT).parts[0] != '_report')
    if not rels:
        print(f'error: no .md files in {_OUT}', file=sys.stderr)
        return 1
    have = set(rels)
    topics, unreadable, others = topic_pages()
    missing_md = sorted(r for r in topics if r not in have)
    not_checked = sorted(r for r in others if r not in have)
    jobs = min(args.jobs or os.cpu_count() or 1, os.cpu_count() or 1)
    if jobs == 1:
        res = [check_safe(r) for r in rels]
    else:
        with multiprocessing.Pool(jobs, _init,
                                  (args.source_dir, args.out_dir)) as pool:
            res = pool.map(check_safe, rels, chunksize=8)
    return 1 if report(res + unreadable, missing_md, not_checked) else 0


if __name__ == '__main__':
    sys.exit(main())
