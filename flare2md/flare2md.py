"""Convert MadCap Flare WebHelp2 help pages into Markdown files for RAG.

The tool reads the HTML topics of a Flare WebHelp2 build (for example the
Keysight ADS help in doc/ads) and writes one Markdown file for each topic.
Each file starts with a YAML header (title, source, breadcrumb, ...). The
images that the pages refer to are copied to the same relative paths.

Usage:
    python flare2md.py SOURCE_DIR OUT_DIR [options]

SOURCE_DIR is the help root (the folder with Data/HelpSystem.xml) or any
folder below it, for example Content/ads2025. Output paths are relative to
SOURCE_DIR. Run "python flare2md.py --help" for the options.
"""

from __future__ import annotations

import argparse
import collections
import csv
import dataclasses
import functools
import json
import multiprocessing
import os
import pathlib
import posixpath
import re
import shutil
import sys
import unicodedata
import urllib.parse
import warnings
import xml.etree.ElementTree as ElementTree

# The pure-Python packages in _vendor (see _vendor/vendor.txt) come first.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / '_vendor'))
# pylint: disable=wrong-import-position

from bs4 import BeautifulSoup
from bs4 import Comment
from bs4 import NavigableString
from bs4 import XMLParsedAsHTMLWarning
from markdownify import MarkdownConverter
from markdownify import abstract_inline_conversion

# Some pages start with an XML declaration. They are still HTML.
warnings.filterwarnings('ignore', category=XMLParsedAsHTMLWarning)

# Tags kept inside tables that stay as raw HTML.
HTML_KEEP = frozenset({
    'table', 'caption', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'p',
    'br', 'ul',
    'ol', 'li', 'a', 'img', 'code', 'pre', 'strong', 'em', 'b', 'i', 'sup',
    'sub', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'})
HTML_ATTRS = frozenset({'rowspan', 'colspan', 'href', 'src', 'alt'})
INLINE = frozenset({'a', 'strong', 'b', 'em', 'i', 'u', 'span', 'code', 'sup',
                    'sub'})
BLOCK_TAGS = frozenset({
    'p', 'div', 'li', 'ul', 'ol', 'td', 'th', 'tr', 'table', 'tbody', 'thead',
    'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'dd', 'dt', 'dl',
    'caption', 'section', 'body', 'pre', 'hr', 'tfoot'})
# Emphasis tags and the Markdown emphasis that each one gives.
EMPHASIS = {'em': 'em', 'i': 'em', 'strong': 'strong', 'b': 'strong'}
STRIKE = ('del', 's', 'strike')  # Tags that give "~~text~~".
# Inline tags that write Markdown or HTML markup around their text. Other
# inline tags (span, font, var, small, ...) write only their text.
MARKUP = frozenset({'a', 'code', 'sup', 'sub', 'img', 'del', 's', 'strike',
                    'q', 'hstrong', 'hem', *EMPHASIS})
NOTE_KIND = {'note.svg': 'Note', 'caution.svg': 'Caution',
             'warning.svg': 'Warning'}

# Marker characters. The source text cannot hold them (read_html removes
# them). They stand for Markdown that must go in after markdownify.
BR = '\x01'  # A <br> in a pipe-table cell. tidy() writes " <br> ".
ESC = '\x02'  # A Markdown escape "\". Raw HTML tables drop it.
# The escape of a block marker at a line start ("\- x", "1\. x"). A
# pipe-table cell drops it too: a cell holds inline content only.
LINE_ESC = '\x04'
CODE_BR = '\x03'  # A <br> inside inline code, while the code is split.
RAW = '\x00RAW{}\x00'  # The place of a raw HTML table.

WORD = re.compile(r'[A-Za-z0-9]+')
# Source text that would start a Markdown block at the start of a line.
# Groups: leading space, digits of "2." (may be empty), the marker itself.
# "[x]: y" would be a link definition and vanish.
LINE_START = re.compile(r'^(\s*)(\d{0,9})((?<=\d)[.)](?=\s|$)|(?<!\d)'
                        r'(?:[#>]|[-+](?=\s|$)|[-=](?=[-=]*\s*$)|=(?==)|'
                        r'\[(?=[^\]]*\]:)|~(?=~~)|-(?=(?:[ \t]*-){2,}\s*$)|'
                        # "-|-" under a line with "|": a table delimiter row.
                        r'[-:](?=[-: \t]*\|[-:| \t]*$)|'
                        r'\|(?=[-:| \t]*-[-:| \t]*$)))')
# A code fence, also after quote and list markers ("> 4. ```"). Groups: the
# backticks, the rest of the line.
FENCE = re.compile(r'^(?:[ \t>]|(?:[-*+]|\d+\.)[ \t])*(`{3,})(.*)$')
# A line that starts a new block. A hard break before it would show as "\".
# Inline HTML such as "<sup>" continues the paragraph, so it is not here.
BLOCK_START = re.compile(
    r'^[ \t>]*(?:[-*+][ \t]|\d+[.)][ \t]|#{1,6}[ \t]|```|<!--|'
    r'</?(?:blockquote|dd|div|dl|dt|h[1-6]|hr|li|ol|p|pre|table|tbody|td|'
    r'tfoot|th|thead|tr|ul)\b)')
# A URL scheme ("https:", "mailto:", "tel:"). One letter is a drive ("C:").
EXTERNAL = re.compile(r'[A-Za-z][A-Za-z0-9+.-]+:')
# "&" that starts an entity ("&amp;", "&#169;"). A Markdown link target
# would decode it.
ENTITY_AMP = re.compile(r'&(?=#?\w+;)')
# Confluence's default code brush is java. Help code is mostly scripts and
# netlists, so a java tag would mislead.
CODE_LANG = {'java': '', 'jfx': '', 'text': '', 'py': 'python'}
REPORT_KEYS = ['src_words', 'md_bytes', 'recall_x1000', 'images', 'tables_md',
               'tables_html', 'code_blocks', 'notes', 'equations', 'h1_added',
               'no_main_div', 'code_lines_missing']


@dataclasses.dataclass(frozen=True)
class Config:
    """Settings that every worker process needs.

    Attributes:
        src: The folder whose pages are converted.
        out: The output folder.
        help_root: The folder with Data/HelpSystem.xml, or None.
        toc: Page path relative to help_root -> titles of its TOC ancestors.
        skip: Page paths relative to help_root that are not topics.
        product: The product name for the YAML header, or None to read it
            from the page's build metadata.
        version: The product version, or None to read it from the page.
        source_prefix: A path put before the source path in the YAML header.
        content_base: The folder (relative to src) that holds the topic
            folders. The first folder below it is the page's section.
            "qthelp://x/doc/<path>" links are relative to it.
        page_class: The Page class that converts a page. None means Page.
    """
    src: pathlib.Path
    out: pathlib.Path
    help_root: pathlib.Path | None = None
    toc: dict = dataclasses.field(default_factory=dict)
    skip: frozenset = frozenset()
    product: str | None = None
    version: str | None = None
    source_prefix: str = ''
    content_base: str = ''
    page_class: type | None = None


_CONFIG: Config | None = None  # Set in each worker by _init_worker().


class TaskMarker(NavigableString):
    """The state of a task-list checkbox ("[x] "). It is the only text at
    the start of a list item that may read as a GFM task box."""


def copy_name(rel: str) -> str:
    """The output path of a copied local file. A copied ".md" file (a
    download of a MyST source) gets ".txt", so that it is no page in the
    output and does not replace one."""
    return rel + '.txt' if rel.lower().endswith('.md') else rel


def quote_path(path: str) -> str:
    """Percent-encodes a local path so that every Markdown parser reads it."""
    return ENTITY_AMP.sub('%26', urllib.parse.quote(
        path.replace('\\', '/'), safe="/._-~+,=&'!$*;:@"))


@functools.lru_cache(maxsize=4096)
def _listdir(folder: str) -> tuple[frozenset, tuple]:
    """The names in folder, as a set and as a sorted tuple."""
    try:
        names = os.listdir(folder)
    except OSError:
        names = []
    return frozenset(names), tuple(sorted(names))


def disk_name(src: pathlib.Path, rel: pathlib.PurePosixPath):
    """Returns rel with the names that the files have on the disk. macOS
    and Windows find "GUIDE.html" for "guide.html", and macOS finds an NFD
    name for an NFC one. Other systems do not, so the output uses the real
    names. A name with no file stays as it is."""
    parts, folder = [], src
    for name in rel.parts:
        found, names = _listdir(str(folder))
        if name not in found:
            key = unicodedata.normalize('NFC', name)
            name = next(
                (n for n in names if unicodedata.normalize('NFC', n) == key),
                next((n for n in names if unicodedata.normalize(
                    'NFC', n).casefold() == key.casefold()), name))
        parts.append(name)
        folder = folder / name
    return pathlib.PurePosixPath(*parts) if parts else rel


# Strings that a YAML 1.2 reader reads as numbers. PyYAML (YAML 1.1) quotes
# most of them, but not "1e3" or "0o17".
YAML12_NUMBER = re.compile(r'[-+]?(\.[0-9]+|[0-9]+(\.[0-9]*)?)([eE][-+]?[0-9]+)?'
                           r'|0o[0-7]+|0x[0-9a-fA-F]+|[-+]?\.(inf|Inf|INF)'
                           r'|\.(nan|NaN|NAN)')


# Strings that PyYAML (YAML 1.1) reads as another type: the implicit types
# of yaml.SafeLoader.
YAML11_NOT_STR = re.compile(r'''(?x)
    yes|Yes|YES|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF
  | [-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?
  | \.[0-9][0-9_]*(?:[eE][-+][0-9]+)?
  | [-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*
  | [-+]?\.(?:inf|Inf|INF) | \.(?:nan|NaN|NAN)
  | [-+]?0b[0-1_]+ | [-+]?0[0-7_]+ | [-+]?(?:0|[1-9][0-9_]*)
  | [-+]?0x[0-9a-fA-F_]+ | [-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+
  | <<|~|null|Null|NULL|=|!|&|\*
  | [0-9]{4}-[0-9]{2}-[0-9]{2}
  | [0-9]{4}-[0-9][0-9]?-[0-9][0-9]?(?:[Tt]|[ \t]+)[0-9][0-9]?:[0-9]{2}:[0-9]{2}
    (?:\.[0-9]*)?(?:[ \t]*(?:Z|[-+][0-9][0-9]?(?::[0-9][0-9])?))?''')
# A plain (unquoted) YAML string must not start with an indicator, hold
# ": " or " #", or start or end with a space.
YAML_NOT_PLAIN = re.compile(r'''(?x)
    ^(?:---|\.\.\.) | ^[\#,\[\]{}&*!|>'"%@`] | ^[?:-](?:\ |$)
  | :(?:\ |$) | \ \# | ^\ | \ $''')
# Characters that only a double-quoted YAML string can hold, with the escapes
# of PyYAML.
YAML_SPECIAL = re.compile('[^\x20-\x7e\xa0-‧‪-퟿-﻾'
                          '＀-�\U00010000-\U0010fffe]')
YAML_ESCAPES = {'\0': '0', '\x07': 'a', '\x08': 'b', '\t': 't', '\n': 'n',
                '\x0b': 'v', '\x0c': 'f', '\r': 'r', '\x1b': 'e', '"': '"',
                '\\': '\\', '\x85': 'N', ' ': 'L', ' ': 'P'}


def _yaml_escape(ch: str) -> str:
    """A character in a double-quoted YAML string, as PyYAML writes it."""
    if ch in YAML_ESCAPES:
        return '\\' + YAML_ESCAPES[ch]
    if '\x20' <= ch <= '\x7e' or ('\xa0' <= ch <= '�' and ch != '﻿'
                                  and not '퟿' < ch < ''):
        return ch
    code = ord(ch)
    return ('\\x%02X' if code <= 0xff else
            '\\u%04X' if code <= 0xffff else '\\U%08X') % code


def yaml_value(value) -> str:
    """A YAML scalar as yaml.safe_dump(allow_unicode=True) writes it, but
    with no line folding. YAML 1.2 numbers ("1e3", "0o17") get quotes too.
    A string with a line break is double-quoted (PyYAML can single-quote
    it)."""
    if value is None:
        return 'null'
    if YAML_SPECIAL.search(value):
        return '"%s"' % ''.join(map(_yaml_escape, value))
    if (not value or YAML11_NOT_STR.fullmatch(value) or
            YAML12_NUMBER.fullmatch(value) or YAML_NOT_PLAIN.search(value)):
        return "'%s'" % value.replace("'", "''")
    return value


def yaml_header(meta: dict) -> str:
    """The lines of the YAML header. A value is a string, None or a list of
    strings (a block sequence)."""
    lines = []
    for key, value in meta.items():
        if isinstance(value, list):
            lines.append(f'{key}:' if value else f'{key}: []')
            lines += ['- ' + yaml_value(v) for v in value]
        else:
            lines.append(f'{key}: {yaml_value(value)}')
    return ''.join(line + '\n' for line in lines)


def read_html(path: pathlib.Path) -> str:
    """Reads an HTML file. Handles UTF-8, UTF-16 and a broken mix of both."""
    raw = path.read_bytes()
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        text = raw.decode('utf-16')
    else:
        if raw[:3] == b'\xef\xbb\xbf':
            raw = raw[3:]
        if raw[1:2] == b'\x00' and raw[3:4] == b'\x00':
            # UTF-16LE text behind a UTF-8 BOM. The UTF-8 bytes EF BF BD
            # (U+FFFD) were stored as UTF-16 units and read as "\ubfef\xbd".
            # The real character is lost. Pairs stood for the two spaces
            # after a sentence. A single one becomes U+FFFD.
            text = raw.decode('utf-16-le', errors='replace').lstrip('\ufeff')
            text = re.sub('(?:\ubfef\xbd){2,}', ' ', text)
            text = text.replace('\ubfef\xbd', '\ufffd')
        elif raw[0:1] == b'\x00' and raw[2:3] == b'\x00':
            text = raw.decode('utf-16-be', errors='replace').lstrip('\ufeff')
        else:
            text = _decode_8bit(raw)
    # The HTML standard makes CR LF and CR into LF before parsing. Do it here
    # too, so that the result does not depend on the parser.
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    # These are marker characters (\x05: sphinx2md.INDENT).
    return re.sub('[\x00-\x05]', '', text)


def _decode_8bit(raw: bytes) -> str:
    """Decodes UTF-8, or else the charset that the page declares, or else
    cp1252 (the usual charset of old Windows help pages)."""
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        pass
    m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', raw[:4096], re.I)
    label = m.group(1).decode('ascii').lower() if m else 'cp1252'
    # Browsers read these labels as windows-1252.
    if label in ('iso-8859-1', 'iso8859-1', 'latin1', 'latin-1', 'l1',
                 'us-ascii', 'ascii', 'windows-1252', 'x-cp1252'):
        label = 'cp1252'
    for enc in (label, 'cp1252'):
        try:
            return raw.decode(enc, errors='replace')
        except LookupError:
            continue
    return raw.decode('utf-8', errors='replace')  # Not reached.


def tidy(md: str, exact_code: bool = False) -> str:
    """Cleans markdownify output. Lines inside fenced code stay as they are.
    No-break and zero-width spaces in code stay only if exact_code."""
    lines, code, in_code, fence = [], [], False, 0
    for line in md.split('\n'):
        if not (in_code and exact_code):
            line = line.replace('\u200b', '').replace('\xa0', ' ')
        # Exact code keeps the spaces at the end of a code line. A blank
        # line gets no indent (from a list item).
        if not (in_code and exact_code and line.strip()):
            line = line.rstrip(' \t')
        m = FENCE.match(line)
        # A closing fence is at least as long as the opening one. An
        # opening fence has no backtick after it ("``` a`b ```" is a code
        # span at the line start).
        if m and ((not in_code and '`' not in m.group(2)) or (
                in_code and len(m.group(1)) >= fence and
                not m.group(2).strip())):
            in_code = not in_code
            fence = len(m.group(1))
            lines.append(line)
            code.append(True)
            continue
        if not in_code:
            # Collapse inner spaces, but keep the quote and indent prefix.
            prefix = re.match(r'[ \t>]*', line).group()
            line = prefix + re.sub(r'(?<=\S)[ \t]{2,}(?=\S)', ' ',
                                   line[len(prefix):])
            # Lone hard breaks (from empty <br>) and empty headings.
            line = re.sub(r'^([ \t>]*)(?:\\|#{1,6})$', r'\1', line).rstrip()
            if not line and lines and not lines[-1]:
                continue  # Collapse blank lines, but not inside code.
        lines.append(line)
        code.append(in_code)
    for i, line in enumerate(lines):
        # A hard break before a blank line or a new block is not needed. An
        # even run of "\" at the end is escaped backslashes, not a break.
        nxt = lines[i + 1] if i + 1 < len(lines) else ''
        rest = line.rstrip('\\')
        trail = len(line) - len(rest) + rest.endswith(ESC)  # ESC is a "\".
        if not code[i] and trail % 2 and (
                not nxt.strip(' \t>') or BLOCK_START.match(nxt)):
            lines[i] = line[:-1].rstrip()
    md = '\n'.join(lines)
    # Cell breaks: merge runs, drop them at cell edges, then write <br>.
    # A pipe after ESC is cell text, not a cell edge.
    md = re.sub(rf'[ \t]*{BR}(?:[ \t]*{BR})*', BR, md)
    md = re.sub(rf'(?<!{ESC})\|[ \t]*{BR}[ \t]*', '| ', md)
    md = re.sub(rf'[ \t]*{BR}[ \t]*\|', ' |', md)
    md = md.replace(BR, ' <br> ').replace(ESC, '\\').replace(LINE_ESC, '\\')
    return md.strip() + '\n'


def merge_touching_code(main):
    """Two code elements that touch would give "`a``b`", which Markdown
    reads as one span with a "``" fence. Merges them."""
    for code in main.find_all('code'):
        nxt = code.next_sibling
        while (getattr(nxt, 'name', None) == 'code' and
               code.find_parent('pre') is None):
            code.append(nxt.get_text())
            nxt.decompose()
            nxt = code.next_sibling


def _new_tag(soup, name, text=None, **attrs):
    tag = soup.new_tag(name, attrs=attrs)
    if text is not None:
        tag.string = text
    return tag


def _bold_paragraph(soup, text):
    """Returns <p><strong>text</strong></p>."""
    p = soup.new_tag('p')
    p.append(_new_tag(soup, 'strong', text))
    return p


def is_wrapper(node) -> bool:
    """True for an inline element that writes only its content (span, font,
    small, ...): not a block, a break or Markdown markup."""
    name = getattr(node, 'name', None)
    return name is not None and name not in MARKUP and \
        name not in BLOCK_TAGS and name not in ('br', 'listbreak', 'rawblock')


_NOTHING = object()  # last_text(): an element or text that writes nothing.


def last_text(node):
    """The text node that writes the last character before node on its
    Markdown line, or None if markup, a block or a break comes first."""
    def last_in(e):
        if isinstance(e, Comment):
            return _NOTHING
        if isinstance(e, NavigableString):
            return e if str(e) else _NOTHING
        if not is_wrapper(e):
            return None
        for kid in reversed(e.contents):
            found = last_in(kid)
            if found is not _NOTHING:
                return found
        return _NOTHING

    while True:
        for prev in node.previous_siblings:
            found = last_in(prev)
            if found is not _NOTHING:
                return found
        node = node.parent
        if node is None or not is_wrapper(node):
            return None


def _only_space(nodes):
    """True if the nodes hold no text other than spaces and <br>."""
    return not any(getattr(n, 'name', None) != 'br' and
                   str(n).replace('\u200b', '').strip() for n in nodes)


class Converter(MarkdownConverter):
    """markdownify with fixes for help pages and raw-HTML table placeholders.
    """

    def convert_listbreak(self, el, text, parent_tags):
        # Two lists in a row would merge into one list. A comment ends the
        # first list and shows nothing.
        return '\n\n<!-- -->\n\n'

    def convert_pre(self, el, text, parent_tags):
        md = super().convert_pre(el, text, parent_tags)
        # markdownify also removes the spaces at the end of the last line.
        # Put them back: tidy() removes them where code is not exact.
        last = re.sub(r'\n[ \n]*$', '', text)
        spaces = re.search(r'[ \t]*$', last).group()
        if md and spaces and last.strip():
            md = md[:-6] + spaces + md[-6:]
        # Code with "```" in it needs a longer fence.
        run = max((len(r) for r in re.findall(r'`{3,}', text)), default=0)
        if not run or not md:
            return md
        fence = '`' * (run + 1)
        return '\n\n' + fence + md[5:-5] + fence + '\n\n'

    def convert_li(self, el, text, parent_tags):
        md = super().convert_li(el, text, parent_tags)
        # A fence on the bullet line ("4. ```") is valid, but a heading
        # splitter that finds fences at line starts only misreads it.
        # Start the fence on the next line, at the content indent. A nested
        # list comes directly after the text of its parent item, and an
        # empty first bullet there would make that text a heading ("Text"
        # and "-"). A comment fills that bullet.
        first = 'li' in parent_tags and el.find_previous_sibling('li') is None
        return re.sub(r'\A((?:[-*+]|\d+\.) )(?=`{3,}[^`\n]*\n)', lambda m: (
            m.group(1) + '<!-- -->' if first else m.group(1)[:-1]) +
            '\n' + ' ' * len(m.group(1)), md)

    def convert_rawblock(self, el, text, parent_tags):
        # Placeholder. The HTML goes in after tidy(), which would change it.
        return '\n\n' + RAW.format(el['data-k']) + '\n\n'

    # Emphasis that Markdown delimiters cannot close or open ("**Note:**Text")
    # is written as HTML. Page.fix_flanking() renames these elements.
    convert_hstrong = abstract_inline_conversion(lambda self: '<strong>')
    convert_hem = abstract_inline_conversion(lambda self: '<em>')

    def convert_img(self, el, text, parent_tags):
        # "[", "]" and "\" in alt text would end or break the image syntax.
        # In a pipe table, "|" would end the cell.
        alt = re.sub(r'([][\\])', r'\\\1', el.get('alt', ''))
        if el.find_parent(['td', 'th']) is not None:
            alt = alt.replace('|', '\\|')
        el['alt'] = alt
        # markdownify writes only the alt text of an image in a heading or a
        # table cell. Keep the image there too.
        return super().convert_img(el, text, parent_tags - {'_inline'})

    def escape(self, text, parent_tags):
        text = super().escape(text, parent_tags)
        if '_noformat' in parent_tags:
            return text
        # "*", "`" and an "_" at a word edge would open emphasis or code.
        text = re.sub(r'([*`])', r'\\\1', text)
        text = re.sub(r'(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])', r'\\_', text)
        # "x[1](y)" would become a link. In link text, any bracket can end
        # or split the link.
        if 'a' in parent_tags:
            text = re.sub(r'([][])', r'\\\1', text)
        else:
            text = re.sub(r'\](?=\()', r'\\]', text)
        # "&ohm;" typed as text would become an entity.
        text = re.sub(r'&(?=#?\w+;)', r'\\&', text)
        # GFM reads "~~10~~" and "~5~" as strikethrough. One run of tildes
        # ("~/bin") has nothing to pair with.
        # In strikethrough, a "~" joins the "~~" delimiters.
        runs = r'(?<!~)~{1,2}(?!~)'
        if parent_tags & set(STRIKE):
            text = text.replace('~', '\\~')
        elif len(re.findall(runs, text)) > 1:
            text = re.sub(runs, lambda m: '\\~' * len(m.group()), text)
        # Text such as "<library name>" would read as an HTML tag and vanish.
        # A "<" at the end can be followed by a tag name in the next text
        # ("<span>&lt;</span><span>int</span>").
        return re.sub(r'<(?=[A-Za-z/!?]|$)', r'\\<', text)


class Page:
    """Converts one source page.

    Attributes:
        rel: The page path relative to the source folder.
        images: Images and other local files that the page refers to (they
            are copied), relative to the source folder.
        missing_images: Image references whose file does not exist.
        stats: Counts for the report.
    """
    report_keys = REPORT_KEYS  # The columns of _report/pages.csv.
    # The BeautifulSoup parser for the pages: the HTML5 parsing rules of a
    # browser. The lxml parser (HTML 4 rules) would end a <dt> at a <dl>
    # inside it (a Sphinx signature with one parameter per line).
    # html.parser does not know implied end tags (raw HTML such as
    # "<li>one<li>two"). html5lib is pure Python, so it can be vendored.
    parser = 'html5lib'
    # Elements in the main content that are no text for the reader: the
    # search form of the help, the build data, scripts and styles.
    hidden = 'form, #buildmetadata, script, style, title'
    # Keep no-break and zero-width spaces in code blocks. In the help they
    # come from the editor, and a copied example would not run.
    exact_code = False

    def __init__(self, rel: str, config: Config):
        self.cfg = config
        self.rel = pathlib.PurePosixPath(rel)
        self.src_path = config.src / rel
        self.images = set()
        self.missing_images = []
        self.raw_blocks = []
        self.stats = collections.Counter()
        if config.help_root is not None:
            self.help_rel = (config.src / rel).relative_to(
                config.help_root).as_posix()
        else:
            self.help_rel = rel

    # ---- Link and image paths ----------------------------------------------

    def resolve(self, href: str) -> pathlib.PurePosixPath | None:
        """Returns the target path relative to the source folder, or None if
        the target is outside it."""
        # Browsers read "\" as "/". A path that starts with "/" starts at the
        # help root.
        path = urllib.parse.unquote(href).replace('\\', '/')
        if path.startswith('/'):
            if self.cfg.help_root is None:
                return None
            path = posixpath.relpath(
                posixpath.join(self.cfg.help_root.as_posix(), path[1:]),
                self.cfg.src.as_posix())
        else:
            path = posixpath.join(str(self.rel.parent), path)
        # A path can leave src and come back ("../a/x.png" in src "a").
        target = posixpath.relpath(posixpath.normpath(posixpath.join(
            self.cfg.src.as_posix(), path)), self.cfg.src.as_posix())
        if target == '..' or target.startswith('../'):
            return None
        return disk_name(self.cfg.src, pathlib.PurePosixPath(target))

    def relative_md(self, target: pathlib.PurePosixPath) -> str:
        """Returns the encoded link from this page to the .md of target."""
        return quote_path(posixpath.relpath(str(target.with_suffix('.md')),
                                            str(self.rel.parent)))

    def rewrite_href(self, href: str) -> str | None:
        """Returns the Markdown link target, or None to keep only the text."""
        href = href.strip()
        low = href.lower()
        if href and (low.startswith(('#', 'file:', 'javascript:', 'vbscript:',
                                     'data:')) or
                     re.match(r'[A-Za-z]:[\\/]', href) or
                     'createpage.action' in href):
            return None  # Dead anchors, local files, scripts, unwritten pages.
        if not href:
            return href
        if EXTERNAL.match(href) and not low.startswith('qthelp:'):
            # "|" would end a pipe-table cell. "\" would be an escape: a
            # browser reads it as "/" in the path of a web address (not in
            # the query or the fragment).
            path, rest = re.match(r'([^?#]*)(.*)', href, re.S).groups()
            if low.startswith(('http:', 'https:', 'ftp:')):
                path = path.replace('\\', '/')
            href = (path + rest).replace('\\', '%5C')
            return ENTITY_AMP.sub('%26', href.replace(' ', '%20')
                                  .replace('(', '%28').replace(')', '%29')
                                  .replace('|', '%7C'))
        m = re.match(r'qthelp://[^/]+/doc/([^#?]+)', href)
        if m:  # Old Qt-help link. Point it at the page in this doc set if any.
            target = pathlib.PurePosixPath(
                self.cfg.content_base, urllib.parse.unquote(m.group(1)))
            if not (self.cfg.src / target).is_file():
                return None
            return self.relative_md(target)
        path, rest = re.match(r'([^?#]*)(.*)', href, re.S).groups()
        if not path:
            return None  # "?tab=1#x" is this page, as "#x" is.
        target = self.resolve(path)
        if target is None:
            return quote_path(urllib.parse.unquote(path)) + rest
        if (self.cfg.src / target / 'index.html').is_file():
            target = target / 'index.html'  # A folder shows its index page.
        elif (target.suffix.lower() not in ('.html', '.htm') and
              not (self.cfg.src / target).exists()):
            # A link without the page suffix (Sphinx html_link_suffix = '').
            # The name can hold a dot ("pkg.module").
            target = next((target.with_name(target.name + s)
                           for s in ('.html', '.htm')
                           if (self.cfg.src / target.with_name(
                               target.name + s)).is_file()), target)
        if target.suffix.lower() in ('.html', '.htm'):
            key = (self.cfg.src / target).relative_to(
                self.cfg.help_root or self.cfg.src).as_posix()
            if key in self.cfg.skip:
                return None  # A page that is not converted (search page).
            return self.relative_md(target)
        # Another local file, such as a PDF or an image. Copy it too.
        if (self.cfg.src / target).is_file():
            self.images.add(str(target))
        return quote_path(posixpath.relpath(copy_name(str(target)),
                                            str(self.rel.parent))) + rest

    def note_image(self, src: str):
        target = self.resolve(src)
        if target is None or not (self.cfg.src / target).is_file():
            self.missing_images.append(src)
        else:
            self.images.add(str(target))

    # ---- DOM clean-up -------------------------------------------------------

    def clean(self, soup, main):
        """Removes page decoration and fixes markup that Markdown cannot show.
        """
        for e in main.find_all(['kbd', 'samp', 'tt']):
            e.name = 'code'  # The same as inline code for Markdown.
        for sel in [self.hidden, 'input', 'span.aui-icon',
                    '#onthispage_wrapper']:
            for e in main.select(sel):
                e.decompose()
        self.convert_definitions(soup, main)
        # Confluence "expand" sections keep their title in a button.
        for ctl in main.select('div.expand-control'):
            ctl.replace_with(_bold_paragraph(
                soup, ctl.get_text(' ', strip=True)))
        # Anchor spans are usually empty, but some wrap real text.
        for e in main.select('button, span.confluence-anchor-link'):
            e.unwrap()
        self.fix_emphasis(main)
        self.fix_image_maps(soup, main)
        # Malformed source: a list directly inside a list. Move it into the
        # previous item, or markdownify joins it to the line above.
        for lst in main.select('ol > ol, ol > ul, ul > ol, ul > ul'):
            prev = lst.find_previous_sibling('li')
            if prev is not None:
                prev.append(lst.extract())
        # Text after a nested list in the same item would join the last
        # nested item. Put it in a <p>, so it stays in the outer item.
        for lst in main.select('li > ol, li > ul'):
            run = []
            for kid in lst.next_siblings:
                if getattr(kid, 'name', None) in BLOCK_TAGS:
                    break
                run.append(kid)
            if not _only_space(run):
                p = soup.new_tag('p')
                run[0].insert_before(p)
                for kid in run:
                    p.append(kid.extract())
        for br in main.select('pre br'):
            br.replace_with(NavigableString('\n'))
        for pre in main.find_all('pre'):
            if not pre.get_text().strip():
                pre.decompose()  # It would give an empty code block.
        self.fix_inline_code(soup, main)
        for c in main.find_all(string=lambda s: isinstance(s, Comment)):
            c.extract()
        for a in main.find_all('a'):
            if not a.get('href'):
                a.unwrap()
        # Two <br> make a blank line, which ends the paragraph. In a link or
        # emphasis the delimiters would go into two paragraphs. Split the
        # element there: <b>a<br><br>b</b> becomes <b>a</b><br><br><b>b</b>.
        for br in main.find_all('br'):
            run = [br]
            for nxt in br.next_siblings:
                if getattr(nxt, 'name', None) == 'br':
                    run.append(nxt)
                elif not (isinstance(nxt, NavigableString) and
                          not nxt.strip()):
                    break
            if len([n for n in run if getattr(n, 'name', None)]) < 2:
                continue
            # The spaces after the last break do not belong to the run.
            while isinstance(run[-1], NavigableString):
                run.pop()
            while (br.parent is not None and br.parent is not main and
                   br.parent.name not in BLOCK_TAGS):
                parent = br.parent
                tail = soup.new_tag(parent.name, attrs=dict(parent.attrs))
                for kid in list(run[-1].next_siblings):
                    tail.append(kid.extract())
                anchor = parent
                for n in run:
                    anchor.insert_after(n.extract())
                    anchor = n
                if not _only_space(tail.contents):
                    anchor.insert_after(tail)
                if _only_space(parent.contents) and parent.find('img') is None:
                    parent.decompose()  # The breaks were at its start.
        # A <br> at the edge of link text or emphasis breaks the Markdown
        # syntax ("\" before "]" or "**"). Move edge breaks out of the element.
        for br in main.find_all('br'):
            while br.parent is not None and br.parent.name in INLINE:
                if _only_space(br.next_siblings):
                    br.parent.insert_after(br.extract())
                elif _only_space(br.previous_siblings):
                    br.parent.insert_before(br.extract())
                else:
                    break
        # A <br> at the start of a block has no Markdown form.
        for br in main.find_all('br'):
            if (br.parent is not None and br.parent.name in BLOCK_TAGS and
                    br.parent.name != 'pre' and
                    _only_space(br.previous_siblings)):
                br.decompose()

    @staticmethod
    def _flatten_dd(dd):
        # Inline content becomes a paragraph. Unwrapped, it would join the
        # text around it.
        if dd.find(list(BLOCK_TAGS)) is None:
            dd.name = 'p'
        else:
            dd.unwrap()

    def convert_definitions(self, soup, main, objects=()):
        """Definition lists (and Sphinx field lists such as "Parameters:")
        that are no API objects: each term becomes a bold paragraph. The
        Markdown of markdownify (": text") is no Markdown syntax, and it
        loses a list or code in a definition."""
        skip = {id(dl) for dl in objects}
        for dl in main.find_all('dl'):
            if id(dl) in skip or dl.find_parent('dt') is not None:
                continue  # An object, or the parameters of a signature.
            for dt in dl.find_all('dt', recursive=False):
                # The term keeps its links and code.
                dt.name = 'strong'
                dt.attrs = {}
                dt.wrap(soup.new_tag('p'))
            for dd in dl.find_all('dd', recursive=False):
                self._flatten_dd(dd)
            dl.unwrap()

    @staticmethod
    def fix_emphasis(main):
        """Unwraps emphasis that Markdown would show wrongly."""
        # <em><em>x</em></em> would become "**x**", which reads as bold.
        for e in main.find_all(list(EMPHASIS)):
            if any(EMPHASIS.get(p.name) == EMPHASIS[e.name]
                   for p in e.parents):
                e.unwrap()
        for e in main.find_all([*EMPHASIS, 's', 'del', 'strike']):
            # Emphasis with no text (only <br> or spaces) would print "** **"
            # (or "~~\\~~" for strikethrough).
            if (not e.get_text().replace('\u200b', '').strip() and
                    e.find('img') is None):
                e.unwrap()
            # Markdown emphasis cannot span blocks.
            elif e.find(list(BLOCK_TAGS - {'body'})) is not None:
                e.unwrap()

    @staticmethod
    def merge_emphasis(main):
        """Merges touching emphasis of the same kind. <em>a</em><em>b</em>
        would become "*a**b*". Runs after the clean-up that empties text
        (comments, zero-width spaces), so that such gaps do not count."""
        def after(e):
            nxt = e.next_sibling
            while isinstance(nxt, NavigableString) and not str(nxt):
                nxt.extract()
                nxt = e.next_sibling
            return nxt

        # Strikethrough too: <s>a</s><s>b</s> would become "~~a~~~~b~~".
        def kind(e):
            name = getattr(e, 'name', None)
            return EMPHASIS.get(name) or ('del' if name in STRIKE else None)

        for e in main.find_all([*EMPHASIS, *STRIKE]):
            if e.parent is None:
                continue  # Merged into the one before.
            nxt = after(e)
            while kind(nxt) == kind(e):
                for child in list(nxt.contents):
                    e.append(child.extract())
                nxt.decompose()
                nxt = after(e)

    @staticmethod
    def fix_image_maps(soup, main):
        """Replaces SVG drawings by their links (image maps) or their text."""
        for svg in main.find_all('svg'):
            links = svg.find_all('a', href=True)
            if links:
                p = soup.new_tag('p')
                for a in links:
                    p.append(_new_tag(soup, 'a', a['href'], href=a['href']))
                    p.append(' ')
                svg.replace_with(p)
            else:
                svg.replace_with(NavigableString(
                    ' '.join(svg.get_text(' ').split())))

    @staticmethod
    def fix_inline_code(soup, main):
        """Fixes inline <code> that a Markdown code span cannot hold."""
        # A code span cannot hold a line break. If the code is the whole
        # paragraph, make it a code block. Otherwise split it at each <br>.
        for code in main.find_all('code'):
            if code.find('br') is None or code.find_parent('pre') is not None:
                continue
            for br in code.find_all('br'):
                br.replace_with(NavigableString(CODE_BR))
            # A browser collapses spaces in inline code. Keep the <br> lines.
            lines = [' '.join(l.split())
                     for l in code.get_text().split(CODE_BR)]
            p = code.parent
            if p is not None and p.name in ('p', 'div') and not any(
                    c is not code and str(c).strip() and
                    getattr(c, 'name', None) != 'br' for c in p.contents):
                p.replace_with(_new_tag(soup, 'pre',
                                        '\n'.join(lines).strip('\n')))
                continue
            parts = []
            for i, line in enumerate(lines):
                if i:
                    parts.append(soup.new_tag('br'))
                if line:
                    parts.append(_new_tag(soup, 'code', line))
            for part in parts:
                code.insert_before(part)
            code.decompose()
        merge_touching_code(main)

    # ---- Confluence and Flare constructs ------------------------------------

    def convert_notes(self, soup, main):
        """Note, Caution and Warning boxes become "> **Note:**" quotes."""
        for tbl in main.select('table.note_table'):
            icon = tbl.select_one('td.note_td_one img')
            kind = 'Note'
            if icon is not None:
                kind = NOTE_KIND.get(icon.get('src', '').rsplit('/', 1)[-1],
                                     'Note')
            body = tbl.select_one('td.note_td_two')
            quote = soup.new_tag('blockquote')
            quote.append(_bold_paragraph(soup, kind + ':'))
            if body is not None:
                if not body.find(['p', 'ul', 'ol', 'pre', 'table', 'div']):
                    wrap = soup.new_tag('p')
                    for child in list(body.contents):
                        wrap.append(child.extract())
                    quote.append(wrap)
                else:
                    for child in list(body.contents):
                        quote.append(child.extract())
            tbl.replace_with(quote)
            self.stats['notes'] += 1

    def convert_code(self, soup, main):
        """Code panels become a bold title and a fenced code block."""
        for panel in main.select('div.code.panel, div.preformatted.panel'):
            pre = panel.find('pre')
            header = panel.select_one('div.codeHeader, div.panelHeader')
            new = []
            if header is not None and header.get_text(strip=True):
                new.append(_bold_paragraph(soup,
                                           header.get_text(' ', strip=True)))
            if pre is not None:
                params = pre.get('data-syntaxhighlighter-params', '')
                m = re.search(r'brush:\s*(\w+)', params)
                lang = m.group(1) if m else ''
                new.append(_new_tag(soup, 'pre', pre.get_text(),
                                    **{'data-lang': CODE_LANG.get(lang, lang)}))
                self.stats['code_blocks'] += 1
            for n in reversed(new):
                panel.insert_after(n)
            panel.decompose()

    def convert_math(self, soup, main):
        """Equations are images in the source. Keep them as images."""
        for e in main.select('div.latex_display, span.latexmath-mathinline'):
            for img in e.find_all('img'):
                img['alt'] = 'equation'
            if e.name == 'div':  # The source puts the image in a layout table.
                p = soup.new_tag('p')
                for img in e.find_all('img'):
                    p.append(img.extract())
                e.replace_with(p)
            self.stats['equations'] += 1
        for e in main.select('span.katex'):
            imgs = e.find_all('img')
            if imgs:  # Some equation images are inside a KaTeX span.
                for img in imgs:
                    e.insert_before(img.extract())
                e.decompose()
            else:
                e.replace_with(NavigableString(
                    ' '.join(e.get_text(' ').split())))

    @staticmethod
    def convert_seealso(soup, main):
        """A fieldset legend ("See also") becomes a bold paragraph."""
        for fs in main.select('fieldset'):
            legend = fs.find('legend')
            if legend is not None:
                legend.replace_with(_bold_paragraph(
                    soup, legend.get_text(' ', strip=True)))
            fs.unwrap()

    def fix_links_and_images(self, main):
        """Points links at .md files and images at encoded local paths."""
        # A video or a sound becomes a link with its file name. Its content
        # is the text for browsers that cannot play it. An embedded page
        # (<iframe>, a YouTube video) becomes a link with its title.
        for media in main.find_all(['video', 'audio', 'iframe']):
            source = media.find('source', src=True)
            src = media.get('src') or (source['src'] if source else '')
            if not src:
                media.decompose()
                continue
            if media.name == 'iframe':
                text = ' '.join(media.get('title', '').split()) or src
            else:
                text = posixpath.basename(
                    re.match(r'[^?#]*', src).group()) or src
            media.name, media.attrs = 'a', {'href': src}
            media.string = text
        # A Markdown link holds inline text only. Blocks in a link (a card
        # of divs) become inline text.
        for a in main.find_all('a'):
            for block in a.find_all(list(BLOCK_TAGS)):
                block.insert_before(' ')
                block.insert_after(' ')
                block.unwrap()
        for a in main.find_all('a', href=True):
            new = self.rewrite_href(a['href'])
            if new is None:
                a.unwrap()
            else:
                a['href'] = new
                a.attrs.pop('title', None)  # Titles such as "$viewTitle".
        merge_touching_code(main)  # Code that was in a removed link.
        # A Markdown code span holds only text. A link or emphasis that is
        # the whole code moves outside: <code><a>x</a></code> becomes
        # <a><code>x</code></a>.
        for code in main.find_all('code'):
            if code.find_parent('pre') is not None:
                continue
            chain, node = [], code  # The wrappers, from outer to inner.
            while True:
                kids = [k for k in node.contents if str(k).strip()]
                if len(kids) != 1 or getattr(kids[0], 'name', None) not in (
                        'a', *EMPHASIS):
                    break
                node = kids[0]
                chain.append(node)
            if not chain:
                continue
            text = code.get_text()
            code.clear()
            code.string = text
            target = code
            for wrapper in reversed(chain):
                wrapper.clear()
                target.wrap(wrapper)
                target = wrapper
        for img in main.find_all('img'):
            src = img.get('src', '')
            if not src:
                img.decompose()
                continue
            if src.lower().startswith('data:'):
                # The image is in the page: kilobytes of base64 text. Its
                # alt text stays.
                img.replace_with(NavigableString(img.get('alt', '').strip()))
                continue
            if src.lower().startswith(('http:', 'https:')):
                continue
            # A query or fragment ("a.png?version=1") is not part of the file.
            src = re.match(r'[^?#]*', src).group()
            self.note_image(src)
            target = self.resolve(src)
            img['src'] = quote_path(
                urllib.parse.unquote(src) if target is None else
                posixpath.relpath(str(target), str(self.rel.parent)))
            if not img.get('alt', '').strip():
                img['alt'] = posixpath.splitext(posixpath.basename(
                    urllib.parse.unquote(src).replace('\\', '/')))[0]
            self.stats['images'] += 1

    @staticmethod
    def at_line_start(node, main) -> bool:
        """True if the text node will start a Markdown line."""
        while True:
            prev = node.previous_sibling
            # Skip space and elements that write nothing ("<span id=x>",
            # "<font></font>", "<wbr>").
            while prev is not None and (
                    (isinstance(prev, NavigableString) and not prev.strip()) or
                    ((getattr(prev, 'name', None) in INLINE or
                      is_wrapper(prev)) and
                     not prev.get_text().strip() and
                     prev.find(['img', 'br']) is None)):
                prev = prev.previous_sibling
            if prev is not None:
                name = getattr(prev, 'name', None)
                return name == 'br' or name in BLOCK_TAGS
            if (node.parent is None or node.parent is main or
                    node.parent.name in BLOCK_TAGS):
                return True
            node = node.parent

    @staticmethod
    def text_before(node, main) -> str | None:
        """The text before node on its Markdown line, or None if markup
        (emphasis, a link, code) comes before it on that line."""
        block = node.find_parent(list(BLOCK_TAGS))
        text = ''
        for e in node.previous_elements:
            if e is block or e is main:
                break
            if isinstance(e, Comment):
                continue
            if isinstance(e, NavigableString):
                if e.find_parent(list(BLOCK_TAGS)) is not block:
                    break  # The end of a block before node.
                text = e + text
            elif e.name == 'br' or e.name in BLOCK_TAGS:
                break
            elif e.name in MARKUP:
                return None
        return text

    def escape_line_starts(self, main):
        """Escapes text such as "2. Click" or "=====" at the start of a line,
        which Markdown would read as a list or a heading."""
        for s in main.find_all(string=LINE_START):
            if (s.find_parent(['pre', 'code']) is None and
                    self.at_line_start(s, main)):
                # "~~~" and "|" are inline syntax too. They keep ESC.
                s.replace_with(NavigableString(LINE_START.sub(
                    lambda m: m.group(1) + m.group(2) + (
                        ESC if m.group(3)[:1] in '~|' else LINE_ESC) +
                    m.group(3), s, 1)))
                self.stats['line_starts_escaped'] += 1
        # Markers split over elements: "<span>1</span>. Click" is
        # "1. Click" in Markdown too.
        for s in main.find_all(string=re.compile(r'^[.)](?=\s|$)')):
            if s.find_parent(['pre', 'code']) is not None:
                continue  # Code is not escaped. (A big <pre> has many.)
            before = self.text_before(s, main)
            if before is not None and re.fullmatch(r'\s*\d{1,9}', before):
                s.replace_with(NavigableString(LINE_ESC + s))
        # A "!" just before a link makes it an image: "Go![page](x.md)".
        for a in main.find_all('a', href=True):
            s = last_text(a)
            if (s is not None and s.endswith('!') and
                    s.find_parent(['pre', 'code']) is None):
                s.replace_with(NavigableString(s[:-1] + ESC + '!'))
        # "[x] Text" at the start of a list item is a task box in GFM. A
        # real checkbox (TaskMarker) stays a task box.
        for li in main.find_all('li'):
            s = next((t for t in li.find_all(string=True)
                      if t.strip() and not isinstance(t, Comment)), None)
            if (s is not None and not isinstance(s, TaskMarker) and
                    s.find_parent(['pre', 'code']) is None and
                    re.match(r'\s*\[[ xX]\](?=\s|$)', s)):
                s.replace_with(NavigableString(s.replace('[', ESC + '[', 1)))
        # "[<b>x</b>]: y" and "<span>[x]</span>: y" are link definitions.
        for s in main.find_all(string=re.compile(r'^\s*\[')):
            if s.find_parent(['pre', 'code', 'a']) is not None:
                continue
            block = s.find_parent(list(BLOCK_TAGS)) or main
            first = next((t for t in block.find_all(string=True)
                          if t.strip() and not isinstance(t, Comment)), None)
            if first is s and re.match(r'\s*\[[^\]]*\]:', block.get_text()):
                s.replace_with(NavigableString(s.replace('[', ESC + '[', 1)))
        # A "#" run at the end of a heading would be read as a closing
        # sequence and dropped.
        for h in main.find_all(re.compile(r'^h[1-6]$')):
            texts = [t for t in h.find_all(string=True) if t.strip()]
            if (texts and texts[-1].find_parent('code') is None and
                    re.search(r'(?:^|\s)#+\s*$', texts[-1])):
                texts[-1].replace_with(NavigableString(
                    re.sub(r'(#+\s*)$', ESC + r'\1', texts[-1])))

    # ---- Tables -------------------------------------------------------------

    @staticmethod
    def span(cell, attr) -> int:
        try:
            return int(cell.get(attr, 1) or 1)
        except ValueError:
            return 1

    @staticmethod
    def rows(tbl):
        """The rows of tbl, without the rows of nested tables. Footer rows
        go last, where a browser shows them."""
        rows = [r for r in tbl.find_all('tr') if r.find_parent('table') is tbl]
        return sorted(rows, key=lambda r: r.parent.name == 'tfoot')

    @staticmethod
    def cells(row):
        return row.find_all(['td', 'th'], recursive=False)

    def width(self, rows) -> int:
        return max((sum(self.span(c, 'colspan') for c in self.cells(r))
                    for r in rows), default=0)

    def full_row(self, row, ncols):
        """Returns the only cell of a row that spans the whole table, or None.
        """
        cells = self.cells(row)
        if (len(cells) == 1 and ncols > 1 and
                self.span(cells[0], 'colspan') == ncols):
            return cells[0]
        return None

    def is_simple(self, tbl) -> bool:
        """True if the table fits a pipe table after full rows move out."""
        if tbl.find('table'):
            return False
        rows = self.rows(tbl)
        ncols = self.width(rows)
        # A pipe table has one header row. (Some pages put all rows in
        # <thead>. The data rows have <td> cells.)
        head = [r for r in tbl.select('thead > tr')
                if self.full_row(r, ncols) is None and
                all(c.name == 'th' for c in self.cells(r))]
        if len(head) > 1:
            return False
        for row in rows:
            full = self.full_row(row, ncols)
            for cell in self.cells(row):
                if self.span(cell, 'rowspan') > 1:
                    return False
                if self.span(cell, 'colspan') > 1 and full is None:
                    return False
                if cell.find(['pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                    return False
                # A cell line cannot show a nested list.
                if cell.select_one('li ul, li ol'):
                    return False
        return True

    def move_full_rows(self, soup, tbl):
        """Moves full-width title rows before and footnote rows after the
        table. Full-width rows in the middle keep their text in column 1."""
        rows = self.rows(tbl)
        ncols = self.width(rows)
        lead = 0
        while lead < len(rows) and self.full_row(rows[lead], ncols) is not None:
            lead += 1
        trail = len(rows)
        while (trail > lead and
               self.full_row(rows[trail - 1], ncols) is not None):
            trail -= 1
        anchor = tbl
        for i, row in enumerate(rows):
            cell = self.full_row(row, ncols)
            if cell is None:
                continue
            if lead <= i < trail:
                del cell['colspan']
                for _ in range(ncols - 1):
                    row.append(soup.new_tag(cell.name))
                continue
            div = soup.new_tag('div')
            for child in list(cell.contents):
                div.append(child.extract())
            if i < lead:
                tbl.insert_before(div)
            else:
                anchor.insert_after(div)
                anchor = div
            row.decompose()

    @staticmethod
    def flatten_cells(tbl):
        """Puts cell content on one line: lists, notes and paragraphs join on
        BR markers."""
        for cell in tbl.find_all(['td', 'th']):
            for s in cell.find_all(string=re.compile(LINE_ESC)):
                s.replace_with(NavigableString(s.replace(LINE_ESC, '')))
            # A "|" that escape_line_starts() escaped has its ESC already.
            for s in cell.find_all(string=re.compile(r'\|')):
                s.replace_with(NavigableString(
                    re.sub(f'(?<!{ESC})\\|', ESC + '|', s)))
            for lst in reversed(cell.find_all(['ul', 'ol'])):
                for n, li in enumerate(lst.find_all('li', recursive=False), 1):
                    li.insert(0, NavigableString(
                        f'{n}. ' if lst.name == 'ol' else '- '))
                    li.append(NavigableString(BR))
                    li.unwrap()
                lst.unwrap()
            for q in cell.find_all('blockquote'):
                q.unwrap()
            for br in cell.find_all('br'):
                br.replace_with(NavigableString(BR))
            for p in cell.find_all(['p', 'div']):
                p.append(NavigableString(BR))
                p.unwrap()

    def mark_header_row(self, body):
        """Uses the first row as the header only if the source marks it so
        (th cells, or text that is all bold in a row above a row that is
        not). Otherwise markdownify writes an empty header row, and no data
        row shows as a header."""
        first = body.find('tr')
        if first is None:
            return
        cells = self.cells(first)

        def words(text):
            # The cell is flat now: a paragraph ends with a BR marker.
            return text.replace(BR, ' ').split()

        def is_head(c):
            bold = words(''.join(b.get_text()
                                 for b in c.find_all(['strong', 'b'])))
            return c.name == 'th' or bold == words(c.get_text())

        def head_row(row):
            filled = [c for c in self.cells(row) if words(c.get_text())]
            return filled and all(is_head(c) for c in filled)

        # In a list of bold names, the next row is all bold too. Such a
        # first row is data.
        second = first.find_next_sibling('tr')
        if head_row(first) and (
                any(c.name == 'th' for c in cells) or second is None or
                not head_row(second)):
            for c in cells:
                c.name = 'th'

    def to_pipe_table(self, soup, tbl):
        """Prepares a simple table for markdownify's pipe-table output."""
        rows = self.rows(tbl)
        caption = tbl.find('caption')
        if caption is not None and all(
                self.full_row(r, self.width(rows)) is not None for r in rows):
            # No table is left. The caption goes first, as a paragraph.
            caption.name = 'p'
            tbl.insert_before(caption.extract())
        self.move_full_rows(soup, tbl)
        self.flatten_cells(tbl)
        # One tbody. markdownify takes the first row of each as a header.
        body = soup.new_tag('tbody')
        caption = tbl.find('caption')
        for row in self.rows(tbl):
            body.append(row.extract())
        tbl.clear()
        if caption is not None:
            tbl.append(caption)
        tbl.append(body)
        # GFM drops cells beyond the header width. Pad every row.
        rows = body.find_all('tr', recursive=False)
        width = max((len(self.cells(r)) for r in rows), default=0)
        # All rows were full-width rows and moved out, or the rows have no
        # cells (a browser shows nothing).
        if not width:
            tbl.decompose()
            return
        for r in rows:
            cells = self.cells(r)
            for _ in range(width - len(cells)):
                r.append(soup.new_tag(cells[-1].name if cells else 'td'))
        self.mark_header_row(body)

    @staticmethod
    def html_fragment(tbl) -> str:
        """Returns the table as clean HTML with layout whitespace removed."""
        for e in tbl.find_all(True):
            if e.name == 'div' and e.find(list(BLOCK_TAGS)) is None and any(
                    str(n).strip() for n in (*e.previous_siblings,
                                             *e.next_siblings)):
                e.name = 'p'  # Unwrapped, it would join the text around it.
            if e.name not in HTML_KEEP:
                e.unwrap()
                continue
            e.attrs = {k: v for k, v in e.attrs.items() if k in HTML_ATTRS and
                       not (k in ('rowspan', 'colspan') and str(v) == '1')}
        tbl.attrs = {}
        # Empty links, and empty and doubled emphasis from Confluence.
        for e in tbl.find_all(['a', 'strong', 'em', 'b', 'i', 'code', 'sup',
                               'sub']):
            if not e.get_text(strip=True) and not e.find('img'):
                e.unwrap()
            elif (e.parent is not None and e.parent.name == e.name and
                  len([c for c in e.parent.contents if str(c).strip()]) == 1):
                e.unwrap()
        for e in tbl.find_all('p'):
            if not e.get_text(strip=True) and not e.find('img'):
                e.decompose()
        html = str(tbl).replace(ESC, '').replace(LINE_ESC, '')
        parts = re.split(r'(<pre>.*?</pre>)', html, flags=re.S)
        for i in range(1, len(parts), 2):
            # A blank line would end the Markdown HTML block. Write the line
            # end that closes a blank line as "&#10;", which shows the same.
            parts[i] = re.sub(r'(?<=\n)([ \t]*)\n', r'\1&#10;', parts[i])
            # A heading splitter would take "# x" for a heading.
            parts[i] = re.sub(r'(?<=\n)#', '&#35;', parts[i])
        for i in range(0, len(parts), 2):  # Not inside <pre>.
            parts[i] = re.sub(r'\s+', ' ', parts[i])
            parts[i] = re.sub(r'\s*(</?(?:table|caption|thead|tbody|tfoot|'
                              r'tr|th|td|'
                              r'p|ul|ol|li|blockquote|h[1-6])\b[^>]*>)\s*',
                              r'\1', parts[i])
            parts[i] = re.sub(r'(</tr>|</caption>|<tbody>|</tbody>|<thead>|'
                              r'</thead>|<tfoot>|</tfoot>)', r'\1\n', parts[i])
        return ''.join(parts).replace('<table>', '<table>\n')

    def convert_tables(self, soup, main):
        """Simple tables become pipe tables. Others stay as raw HTML."""
        # Outermost tables only. Nested tables go with their parent's HTML.
        for tbl in [t for t in main.find_all('table')
                    if t.find_parent('table') is None]:
            if self.is_simple(tbl):
                if self.rows(tbl):
                    self.to_pipe_table(soup, tbl)
                self.stats['tables_md'] += 1
                continue
            self.raw_blocks.append(self.html_fragment(tbl))
            tbl.replace_with(_new_tag(
                soup, 'rawblock', **{'data-k': str(len(self.raw_blocks) - 1)}))
            self.stats['tables_html'] += 1

    @staticmethod
    def separate_lists(soup, main):
        """Marks a list that directly follows a list of the same kind.
        Markdown would join them (and number the second list on from the
        first)."""
        def before(node):
            # The block before node, looking through wrapper <div> elements.
            while True:
                prev = node.previous_sibling
                while isinstance(prev, NavigableString) and not prev.strip():
                    prev = prev.previous_sibling
                if prev is not None:
                    break
                if node.parent is None or node.parent is main or \
                        node.parent.name != 'div':
                    return None
                node = node.parent
            while getattr(prev, 'name', None) == 'div':
                kids = [k for k in prev.contents if str(k).strip()]
                if not kids:
                    return before(prev)
                prev = kids[-1]
            return prev

        for lst in main.find_all(['ol', 'ul']):
            if getattr(before(lst), 'name', None) == lst.name:
                lst.insert_before(soup.new_tag('listbreak'))

    @staticmethod
    def fix_flanking(main):
        """Renames emphasis whose "*" delimiters Markdown would not read as
        emphasis, for example "**Note:**Text" (punctuation inside, a letter
        outside). The Converter writes these as HTML."""
        def kind(c):
            # ' ' space or edge, '.' punctuation, 'a' other (CommonMark).
            # The BR marker writes " <br> ". The ESC marker writes "\\".
            if c.isspace() or c == BR:
                return ' '
            if c in (ESC, LINE_ESC):
                return '.'
            return '.' if unicodedata.category(c)[0] in 'PS' else 'a'

        def writes(node, last):
            # The kind of the first (or last) character that node writes, or
            # None if it writes nothing. 'e' is "*" of other Markdown
            # emphasis: the two delimiter runs would join.
            if isinstance(node, Comment):
                return None
            if isinstance(node, NavigableString):
                return kind(node[-1] if last else node[0]) if node else None
            if node.name in BLOCK_TAGS or node.name in ('br', 'listbreak',
                                                        'rawblock'):
                return ' '
            if node.name not in MARKUP:  # It writes only its content.
                kids = reversed(node.contents) if last else node.contents
                return next((k for k in map(lambda n: writes(n, last), kids)
                             if k is not None), None)
            if not node.get_text() and node.find('img') is None and \
                    node.name != 'img':
                return None
            if node.name not in EMPHASIS:
                return '.'
            # markdownify moves edge spaces of emphasis outside it.
            text = node.get_text()
            return ' ' if text and text[-1 if last else 0].isspace() else 'e'

        def outside(node, last):
            while True:
                sib = node.next_sibling if last else node.previous_sibling
                while sib is not None:
                    k = writes(sib, not last)
                    if k is not None:
                        return k
                    sib = sib.next_sibling if last else sib.previous_sibling
                parent = node.parent
                if parent is None or parent is main or \
                        parent.name in BLOCK_TAGS:
                    return ' '
                if parent.name in MARKUP:
                    return '.'  # The delimiter of the link or emphasis.
                node = parent

        def inside(node, last):
            # The kind of the first (or last) character that is not a space,
            # and True if spaces come before it (they move outside).
            spaced = False
            for k in (reversed(node.contents) if last else node.contents):
                if isinstance(k, NavigableString) and not isinstance(
                        k, Comment):
                    t = k.rstrip() if last else k.lstrip()
                    spaced = spaced or len(t) < len(k)
                    if t:
                        return kind(t[-1] if last else t[0]), spaced
                    continue
                w = writes(k, last)
                if w is not None:
                    return w, spaced
            return None, spaced

        for e in main.find_all(list(EMPHASIS)):
            if e.find_parent(['pre', 'code']) is not None:
                continue
            first, sp1 = inside(e, False)
            last, sp2 = inside(e, True)
            if first is None:
                continue
            before, after = outside(e, False), outside(e, True)
            if ((before == 'e' and not sp1) or (after == 'e' and not sp2) or
                    (not sp1 and first == '.' and before == 'a') or
                    (not sp2 and last == '.' and after == 'a')):
                e.name = 'h' + EMPHASIS[e.name]

    # ---- Driver -------------------------------------------------------------

    @staticmethod
    def find_main(soup):
        """The main content of the page, or None if it has none."""
        return soup.find(id='mc-main-content')

    @staticmethod
    def page_title(soup) -> str:
        title = soup.find('title')
        return ' '.join(title.get_text().split()) if title else ''

    def metadata(self, soup, title) -> dict:
        """Returns the YAML header fields."""
        html_tag = soup.find('html')
        toc = html_tag.get('data-mc-toc-path', '') if html_tag else ''
        page_id = soup.find('input', id='pageid')

        def build_info(key):
            li = soup.select_one(f'#buildmetadata #{key}')
            return ' '.join(li.get_text().split()) or None if li else None

        product = self.cfg.product
        version = self.cfg.version
        source = posixpath.join(self.cfg.source_prefix, self.help_rel)
        return {
            'title': title or self.rel.stem,
            'source': source,
            'breadcrumb': ([s for s in toc.split('|') if s] if toc
                           else self.cfg.toc.get(self.help_rel, [])),
            'section': self.section(),
            'page_id': page_id.get('value') if page_id else None,
            'product': build_info('productname') if product is None
                       else product,
            'version': build_info('productrelease') if version is None
                       else version,
        }

    def section(self) -> str:
        """The first folder of the page below content_base, or ''."""
        base = self.cfg.content_base
        parts = self.rel.parts
        if base and self.rel.is_relative_to(base):
            parts = self.rel.relative_to(base).parts
        return parts[0] if len(parts) > 1 else ''

    def prepare_text(self, main):
        """Normalizes text nodes before markdownify sees them."""
        # No-break and zero-width spaces go first, so that markdownify can
        # remove them at the edges of emphasis. Code blocks keep them if
        # exact_code.
        for s in main.find_all(string=re.compile('[\xa0\u200b]')):
            if self.exact_code and s.find_parent('pre') is not None:
                continue
            s.replace_with(NavigableString(
                s.replace('\xa0', ' ').replace('\u200b', '')))
        # Collapse source layout whitespace. markdownify keeps newlines.
        # It drops the whitespace between blocks, so that stays as it is.
        # (A replace looks up the place of the text among its siblings, and
        # a page with 20,000 paragraphs in one section has many.)
        def between_blocks(s):
            return (not s.strip() and s.parent.name in BLOCK_TAGS and all(
                x is None or getattr(x, 'name', None) in BLOCK_TAGS
                for x in (s.previous_sibling, s.next_sibling)))

        for s in main.find_all(string=True):
            if (s.find_parent('pre') is None and re.search(r'\s\s|\n', s) and
                    not between_blocks(s)):
                s.replace_with(NavigableString(re.sub(r'\s+', ' ', s)))
        # A source "\" before punctuation or at the end of a text would act
        # as an escape or a hard break. Double it.
        for s in main.find_all(string=re.compile(r'\\')):
            if s.find_parent(['pre', 'code']) is None:
                s.replace_with(NavigableString(
                    re.sub(r'\\(?=[!-/:-@\[-`{-~]|\s*$)', ESC + r'\\', s)))

    def run(self) -> dict:
        """Converts the page and writes the .md file.

        Returns:
            A dict with 'rel' and either 'skipped', or 'stats', 'images' and
            'missing'.
        """
        soup = BeautifulSoup(read_html(self.src_path), self.parser)
        main = self.find_main(soup)
        self.stats['no_main_div'] = int(main is None)
        if main is None and self.help_rel not in self.cfg.toc:
            return {'rel': str(self.rel), 'skipped': True}  # Not a topic.
        main = main or soup.body  # html5lib always makes a <body>.
        title = self.page_title(soup)
        meta = self.metadata(soup, title)

        # Count source words before any clean-up that could lose text.
        for e in main.select(self.hidden):
            e.decompose()
        src_words = collections.Counter(
            w.lower() for w in WORD.findall(main.get_text(' ')))
        self.clean(soup, main)
        self.prepare_text(main)
        self.merge_emphasis(main)
        code_lines = [l.rstrip() for pre in main.find_all('pre')
                      for l in pre.get_text().replace('\xa0', ' ').split('\n')
                      if l.strip()]
        self.convert_notes(soup, main)
        self.convert_code(soup, main)
        self.convert_math(soup, main)
        self.convert_seealso(soup, main)
        self.fix_links_and_images(main)
        self.escape_line_starts(main)
        self.convert_tables(soup, main)
        self.separate_lists(soup, main)
        self.fix_flanking(main)

        first_h = main.find(re.compile(r'^h[1-6]$'))
        if title and (first_h is None or first_h.name != 'h1'):
            main.insert(0, _new_tag(soup, 'h1', title))
            self.stats['h1_added'] = 1

        md = Converter(
            heading_style='ATX', bullets='-', newline_style='BACKSLASH',
            escape_underscores=False, escape_asterisks=False,
            escape_misc=False,
            sup_symbol='<sup>', sub_symbol='<sub>',  # "cm<sup>-3</sup>"
            table_infer_header=False, strip_document='STRIP',
            autolinks=False,  # "<x.pdf>" is not a link in Markdown.
            code_language_callback=lambda el: el.get('data-lang') or '',
        ).convert_soup(main)
        md = tidy(md, self.exact_code)
        # Give every HTML line the indent or quote prefix of its placeholder.
        def insert_raw(m):
            # The first line keeps a list marker ("1. "). Later lines get
            # spaces of the same width, so they continue the same item.
            first = m.group(1)
            rest = re.sub(r'[-*+](?=[ \t])|\d+[.)](?=[ \t])',
                          lambda n: ' ' * len(n.group()), first)
            lines = self.raw_blocks[int(m.group(2))].split('\n')
            return '\n'.join([first + lines[0]] + [rest + l for l in lines[1:]])

        md = re.sub(r'^(.*?)\x00RAW(\d+)\x00', insert_raw, md, flags=re.M)

        front = yaml_header(meta)
        out = self.cfg.out / self.rel.with_suffix('.md')
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, 'w', encoding='utf-8', newline='\n') as f:
            f.write(f'---\n{front}---\n\n{md}')

        md_words = collections.Counter(w.lower() for w in WORD.findall(md))
        total = sum(src_words.values())
        kept = sum((src_words & md_words).values())
        # Most code lines are a whole Markdown line (after an indent or a
        # quote prefix). Only the others need a search in the text, which
        # takes long for a big code block.
        md_lines = {l.lstrip(' \t>') for l in md.split('\n')}
        self.stats['code_lines_missing'] = sum(
            1 for l in (l.replace('\u200b', '') for l in code_lines)
            if l.lstrip(' \t') not in md_lines and l not in md)
        self.stats.update(
            src_words=total, md_bytes=len(md.encode()),
            recall_x1000=round(1000 * kept / total) if total else 1000)
        return {'rel': str(self.rel), 'stats': dict(self.stats),
                'images': sorted(self.images), 'missing': self.missing_images}


# ---- Help system ------------------------------------------------------------


def find_help_root(path: pathlib.Path) -> pathlib.Path | None:
    """Returns the nearest folder at or above path with Data/HelpSystem.xml."""
    for folder in [path, *path.parents]:
        if (folder / 'Data' / 'HelpSystem.xml').is_file():
            return folder
    return None


def _warn(text):
    print(f'warning: {text}', file=sys.stderr)


def read_help_system(help_root: pathlib.Path) -> dict:
    """Returns the attributes of the WebHelpSystem element."""
    try:
        return dict(ElementTree.parse(
            help_root / 'Data' / 'HelpSystem.xml').getroot().attrib)
    except ElementTree.ParseError as e:
        _warn(f'Data/HelpSystem.xml not read ({e}). No TOC is used.')
        return {}


def load_toc(toc_file: pathlib.Path) -> dict:
    """Reads a Flare WebHelp2 TOC.

    Args:
        toc_file: The TOC script, for example Data/Tocs/ads2025.js. Its
            chunk files must be in the same folder.

    Returns:
        Page path relative to the help root -> titles of its TOC ancestors.
    """
    if not toc_file.is_file():
        _warn(f'TOC file {toc_file} not found. Breadcrumbs come only from '
              'the pages.')
        return {}
    try:
        return _parse_toc(toc_file)
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        _warn(f'TOC {toc_file} not read ({type(e).__name__}: {e}). '
              'Breadcrumbs come only from the pages.')
        return {}


def _parse_toc(toc_file: pathlib.Path) -> dict:
    tree = toc_file.read_text(encoding='utf-8-sig')
    m = re.search(r"numchunks\s*:\s*(\d+)\s*,\s*prefix\s*:\s*'([^']*)'",
                  tree)
    if m is None:
        raise ValueError('no numchunks and prefix')
    nodes = {}  # TOC node id -> (page path, title)
    item = re.compile(r"'/((?:[^'\\]|\\.)+)'\s*:\s*\{\s*i\s*:\s*\[([\d,\s]+)\]"
                      r"\s*,\s*t\s*:\s*\[((?:\s*'(?:[^'\\]|\\.)*'\s*,?)*)\]")
    for n in range(int(m.group(1))):
        chunk = toc_file.parent / f'{m.group(2)}{n}.js'
        if not chunk.is_file():
            _warn(f'TOC chunk {chunk} not found.')
            continue
        for it in item.finditer(chunk.read_text(encoding='utf-8-sig')):
            titles = re.findall(r"'((?:[^'\\]|\\.)*)'", it.group(3))
            for i, t in zip(it.group(2).replace(' ', '').split(','), titles):
                nodes[int(i)] = (it.group(1).replace("\\'", "'"),
                                 t.replace("\\'", "'"))
    tree = tree[re.search(r'tree\s*:', tree).end():].rstrip()
    tree = re.sub(r'\}\s*\)\s*;?$', '', tree)  # End of define({..});
    tree = json.loads(re.sub(r'([{,]\s*)(\w+)\s*:', r'\1"\2":', tree))
    crumbs = {}

    def walk(node, path):
        if 'i' in node and node['i'] in nodes:
            rel, title = nodes[node['i']]
            crumbs.setdefault(urllib.parse.unquote(rel), path)
            path = path + [title]
        for child in node.get('n', []):
            walk(child, path)

    walk(tree, [])
    return crumbs


# ---- Driver -----------------------------------------------------------------


def _init_worker(config: Config):
    global _CONFIG
    _CONFIG = config


def _work(rel: str) -> dict:
    try:
        return (_CONFIG.page_class or Page)(rel, _CONFIG).run()
    except Exception as e:  # pylint: disable=broad-except
        return {'rel': rel, 'error': f'{type(e).__name__}: {e}'}


def make_config(src, out, product=None, version=None,
                source_prefix='') -> Config:
    """Builds the Config. Reads the help system files if there are any."""
    src, out = pathlib.Path(src).resolve(), pathlib.Path(out).resolve()
    help_root = find_help_root(src)
    toc, skip = {}, frozenset()
    if help_root is not None:
        attrs = read_help_system(help_root)
        if attrs.get('Toc'):
            toc = load_toc(help_root / attrs['Toc'])
        if attrs.get('SearchUrl'):
            skip = frozenset({attrs['SearchUrl']})
    config = Config(src=src, out=out, help_root=help_root, toc=toc,
                    skip=skip, product=product, version=version,
                    source_prefix=source_prefix.strip('/'))
    return dataclasses.replace(config, content_base=content_base(config))


def content_base(config: Config) -> str:
    """Returns the deepest folder (relative to src) that holds all pages in
    the TOC, or all pages if there is no TOC."""
    pages = []
    if config.help_root is not None:
        for key in config.toc:
            path = config.help_root / key
            if path.is_relative_to(config.src):
                pages.append(path.relative_to(config.src).as_posix())
    folders = {posixpath.dirname(p) for p in pages or list_pages(config)}
    if not folders:
        return ''
    base = posixpath.commonpath([f or '.' for f in folders])
    return '' if base == '.' else base


def list_pages(config: Config) -> list[str]:
    """Returns the .htm and .html files below src, minus the search page."""
    rels = []
    for p in config.src.rglob('*'):
        if p.suffix.lower() not in ('.htm', '.html') or not p.is_file():
            continue
        if config.out in p.parents:
            continue
        if (p.relative_to(config.help_root or config.src).as_posix() in
                config.skip):
            continue
        rels.append(p.relative_to(config.src).as_posix())
    return sorted(rels)


def copy_images(config: Config, images) -> list[dict]:
    """Copies each image unless the copy is already the same. Returns the
    images that could not be copied, as error results."""
    errors = []
    for rel in sorted(images):
        src, dst = config.src / rel, config.out / copy_name(rel)
        try:
            st = src.stat()
            if dst.is_file():
                dt = dst.stat()
                if (dt.st_size == st.st_size and
                        int(dt.st_mtime) == int(st.st_mtime)):
                    continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        except OSError as e:
            errors.append({'rel': rel, 'error': f'{type(e).__name__}: {e}'})
    return errors


def write_reports(config: Config, results) -> tuple[set, list, list]:
    """Writes _report/ and returns (images, errors, skipped)."""
    images, errors, skipped = set(), [], []
    rep = config.out / '_report'
    rep.mkdir(parents=True, exist_ok=True)
    with open(rep / 'pages.csv', 'w', encoding='utf-8', newline='') as f, \
            open(rep / 'missing_images.txt', 'w', encoding='utf-8',
                 newline='\n') as fm:
        w = csv.writer(f, lineterminator='\n')
        keys = (config.page_class or Page).report_keys
        w.writerow(['page'] + keys)
        for r in results:
            if 'error' in r:
                errors.append(r)
                continue
            if r.get('skipped'):
                skipped.append(r['rel'])
                continue
            images.update(r['images'])
            w.writerow([r['rel']] + [r['stats'].get(k, 0) for k in keys])
            for m in r['missing']:
                fm.write(f"{r['rel']}\t{m}\n")
    with open(rep / 'errors.txt', 'w', encoding='utf-8', newline='\n') as f:
        for r in errors:
            f.write(f"{r['rel']}\t{r['error']}\n")
    with open(rep / 'skipped.txt', 'w', encoding='utf-8', newline='\n') as f:
        for rel in skipped:
            f.write(rel + '\n')
    return images, errors, skipped


def _write_list(path: pathlib.Path, lines):
    """Writes one line for each item, or removes the file of an earlier
    run if there is nothing to report."""
    if not lines:
        if path.exists():
            path.unlink()
        return
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(''.join(l + '\n' for l in lines))


def find_collisions(rels) -> list[list[str]]:
    """Returns groups of pages whose .md paths differ only in case or in
    the Unicode form of a letter. On macOS and Windows they would write the
    same file ("Guide.htm" and "guide.html", "café" in NFC and NFD, "ß" and
    "ss" on macOS)."""
    groups = collections.defaultdict(list)
    for rel in rels:
        stem = posixpath.splitext(rel)[0]
        groups[unicodedata.normalize('NFC', stem).casefold()].append(rel)
    return [g for g in groups.values() if len(g) > 1]


def find_stale(config: Config, results) -> list[str]:
    """Returns .md files in out that this run did not write. A page that
    failed keeps its old .md. errors.txt lists it, so it is not stale."""
    written = {posixpath.splitext(r['rel'])[0] + '.md' for r in results
               if 'stats' in r or 'error' in r}
    stale = []
    for p in config.out.rglob('*.md'):
        rel = p.relative_to(config.out).as_posix()
        if rel not in written and not rel.startswith('_report/'):
            stale.append(rel)
    return sorted(stale)


def convert(config: Config, only=None, jobs=None) -> dict:
    """Converts the pages and writes the output and the reports.

    Args:
        config: The settings.
        only: Page paths relative to src to convert, or None for all. With
            pages given, the reports of the last full run stay as they are,
            and errors go to stderr only.
        jobs: The number of worker processes. None means one per CPU.

    Returns:
        A dict of counts: pages, converted, skipped, errors, images, stale.
    """
    rels = list(only) if only else list_pages(config)
    collisions = find_collisions(rels)
    # More processes than CPUs or pages do not make the work faster.
    jobs = max(1, min(jobs or os.cpu_count() or 1, os.cpu_count() or 1,
                      len(rels)))
    if os.name == 'nt':
        jobs = min(jobs, 61)  # The Windows process pool limit.
    if jobs == 1:
        _init_worker(config)
        results = [_work(r) for r in rels]
    else:
        with multiprocessing.Pool(jobs, initializer=_init_worker,
                                  initargs=(config,)) as pool:
            results = pool.map(_work, rels, chunksize=16)
    if only:
        images = set().union(*(r.get('images', []) for r in results))
        errors = [r for r in results if 'error' in r]
        skipped = [r['rel'] for r in results if r.get('skipped')]
        for r in errors:
            print(f"error: {r['rel']}: {r['error']}", file=sys.stderr)
        stale = []
    else:
        images, errors, skipped = write_reports(config, results)
        stale = find_stale(config, results)
        rep = config.out / '_report'
        _write_list(rep / 'collisions.txt', ['\t'.join(c) for c in collisions])
        _write_list(rep / 'stale.txt', stale)
    copy_errors = copy_images(config, images)
    for r in copy_errors:
        print(f"error: {r['rel']}: {r['error']}", file=sys.stderr)
    if copy_errors and not only:
        with open(config.out / '_report' / 'errors.txt', 'a',
                  encoding='utf-8', newline='\n') as f:
            for r in copy_errors:
                f.write(f"{r['rel']}\t{r['error']}\n")
    return {'pages': len(rels),
            'converted': len(rels) - len(errors) - len(skipped),
            'skipped': len(skipped), 'errors': len(errors) + len(copy_errors),
            'images': len(images), 'stale': len(stale),
            'collisions': len(collisions)}


def page_rel(config: Config, page: str) -> str | None:
    """Returns a page given relative to src or as an absolute path as a
    path relative to src, or None if it is not a file below src."""
    path = config.src / page
    # The folders are resolved, the file is not: a symlink keeps its name,
    # as in a full run.
    path = path.parent.resolve() / path.name
    try:
        rel = path.relative_to(config.src.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    return disk_name(config.src, pathlib.PurePosixPath(rel)).as_posix()


def _positive_int(text: str) -> int:
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError('must be 1 or more')
    return n


def make_parser(description, source_help) -> argparse.ArgumentParser:
    """The command line options that every reader shares."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('source_dir', help=source_help)
    parser.add_argument('out_dir', help='the output folder')
    parser.add_argument('--product', help='product name for the YAML header '
                        '(default: from the pages)')
    parser.add_argument('--version', help='product version for the YAML '
                        'header (default: from the pages)')
    parser.add_argument('--source-prefix', default='',
                        help='path put before "source" in the YAML header, '
                        'for example doc/ads')
    parser.add_argument('--jobs', type=_positive_int, default=None,
                        help='worker processes (default: one per CPU)')
    parser.add_argument('--only', nargs='+', metavar='PAGE',
                        help='convert only these pages (relative to '
                        'source_dir, or absolute). The reports in '
                        '_report/ stay as they are.')
    return parser


def run_cli(parser, args, config: Config) -> int:
    """Checks the options, converts and prints the counts."""
    if not config.src.is_dir():
        parser.error(f'not a folder: {config.src}')
    if (config.src / '_report').is_dir():
        _warn('source_dir has a _report folder. Its pages go into the same '
              'folder as the reports of this tool.')
    # samefile also knows "HTML" and "html" as one folder on macOS.
    for folder in [config.src, *config.src.parents]:
        if config.out == folder or (config.out.exists() and
                                    os.path.samefile(config.out, folder)):
            parser.error('out_dir must not be source_dir or a folder above '
                         'it')
    if config.out.exists() and not config.out.is_dir():
        parser.error(f'out_dir is not a folder: {config.out}')
    try:
        config.out.mkdir(parents=True, exist_ok=True)
        if not os.access(config.out, os.W_OK):
            raise PermissionError('no write permission')
    except OSError as e:
        parser.error(f'cannot write to out_dir {config.out}: {e}')
    only = [page_rel(config, r) for r in args.only or []]
    missing = [r for r, rel in zip(args.only or [], only) if rel is None]
    if missing:
        parser.error(f'no such page in {config.src}: {", ".join(missing)}')
    only = list(dict.fromkeys(only))  # The same page given twice.
    skipped = [r for r in only if (config.src / r).relative_to(
        config.help_root or config.src).as_posix() in config.skip]
    for r in skipped:
        _warn(f'{r} is not a topic (for example the search page). It is not '
              'converted.')
    only = [r for r in only if r not in skipped]
    if args.only and not only:
        return 0
    counts = convert(config, only, args.jobs)
    print('  '.join(f'{k} {v}' for k, v in counts.items()))
    if counts['collisions']:
        print(f"warning: {counts['collisions']} groups of pages differ only "
              'in case and can write the same .md file. See '
              '_report/collisions.txt.', file=sys.stderr)
    if counts['stale']:
        print(f"warning: {counts['stale']} .md files in the output have no "
              'source page. See _report/stale.txt.', file=sys.stderr)
    return 1 if counts['errors'] else 0


def main(argv=None) -> int:
    parser = make_parser(
        'Convert MadCap Flare WebHelp2 pages to Markdown for RAG.',
        'the help root (with Data/HelpSystem.xml) or a folder below it')
    args = parser.parse_args(argv)
    config = make_config(args.source_dir, args.out_dir, args.product,
                         args.version, args.source_prefix)
    if config.src.is_dir() and config.help_root is None:
        print('warning: no Data/HelpSystem.xml found. Breadcrumbs come only '
              'from the pages.', file=sys.stderr)
    return run_cli(parser, args, config)


if __name__ == '__main__':
    sys.exit(main())
