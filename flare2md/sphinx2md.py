"""Converts Sphinx HTML builds to Markdown for RAG.

Usage:
    python sphinx2md.py SOURCE_DIR OUT_DIR [--product NAME] [--version V]
                        [--source-prefix PATH] [--jobs N] [--only PAGE ...]

SOURCE_DIR is a Sphinx HTML build (the folder with searchindex.js or
_static/documentation_options.js) or a folder that holds several builds.
The Markdown rules (escapes, emphasis, tables, lists, links, images and the
reports) are the rules of flare2md.py.
This file adds what is special to Sphinx: the content area, API
signatures, field lists, admonitions, code blocks, math, footnotes, the
breadcrumb and the YAML header.
"""

from __future__ import annotations

import collections
import dataclasses
import pathlib
import posixpath
import re
import sys
import urllib.parse

# The pure-Python packages in _vendor (see _vendor/vendor.txt) come first.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / '_vendor'))
# pylint: disable=wrong-import-position

from bs4 import BeautifulSoup
from bs4 import NavigableString

import flare2md
from flare2md import _new_tag
from flare2md import BLOCK_TAGS

# Pages that Sphinx makes for every build. They are no topics.
# A build can use the suffix .htm (html_file_suffix).
GENERATED = re.compile(r'^(genindex(-[^/]+)?|search|[a-z]+-modindex)\.html?$')
PAGE_SUFFIXES = ('.html', '.htm')
# Pygments lexer name -> fence language. "default" is Python 3, but Sphinx
# shows the code without highlighting if it is no Python (see code_lang()).
SPHINX_LANG = {'default': 'python', 'python3': 'python', 'text': '',
               'none': ''}
# Sphinx domain -> fence language of its signatures.
DOMAIN_LANG = {'py': 'python', 'c': 'c', 'cpp': 'cpp', 'js': 'javascript'}
DOMAINS = frozenset({'py', 'c', 'cpp', 'js', 'rst', 'std', 'http', 'mat'})
INDENT = '\x05'  # The indent of a parameter line in a signature.
HEADING = re.compile(r'^h[1-6]$')
# The classes of the parts of an object name in a signature.
NAME_CLASSES = frozenset({'sig-prename', 'descclassname', 'sig-name',
                          'descname'})


@dataclasses.dataclass(frozen=True)
class SphinxConfig(flare2md.Config):
    """Config with the builds below src.

    Attributes:
        builds: Build root relative to src ('' for src itself) -> the
            version that the build declares, or None.
        toctree: Page relative to src -> titles of its toctree ancestors.
        docstitles: Build root -> the project part of <title> that all
            pages of the build share ("Kit 1.0 documentation"), or ''.
        dirhtml: Build roots made by the dirhtml builder (each page is
            <docname>/index.html).
        languages: Build root -> the language of the build ('en', 'ja').
        roots: Build root -> its root page (root_doc), or None.
        projects: Build root -> the project in objects.inv, or None.
    """
    builds: dict = dataclasses.field(default_factory=dict)
    toctree: dict = dataclasses.field(default_factory=dict)
    docstitles: dict = dataclasses.field(default_factory=dict)
    dirhtml: frozenset = frozenset()
    languages: dict = dataclasses.field(default_factory=dict)
    roots: dict = dataclasses.field(default_factory=dict)
    projects: dict = dataclasses.field(default_factory=dict)


def find_builds(src: pathlib.Path) -> tuple[dict, dict, dict]:
    """Returns build root (relative to src) -> version, build root ->
    language and build root -> project, for each folder at or below src
    that has searchindex.js or _static/documentation_options.js (the
    singlehtml builder writes no search index). The project is the
    "# Project:" line in the text head of objects.inv."""
    builds, languages, projects = {}, {}, {}
    roots = {p.parent for p in src.rglob('searchindex.js')}
    roots |= {p.parent.parent for p in src.rglob('documentation_options.js')
              if p.parent.name == '_static'}
    for root in sorted(roots):
        options = root / '_static' / 'documentation_options.js'
        text = (options.read_text(encoding='utf-8', errors='replace')
                if options.is_file() else '')
        rel = root.relative_to(src).as_posix()
        rel = '' if rel == '.' else rel
        for key, table in (('VERSION', builds), ('LANGUAGE', languages)):
            m = re.search(key + r":\s*'([^']*)'", text)
            table[rel] = m.group(1) if m else None
        try:
            with open(root / 'objects.inv', 'rb') as f:
                head = f.read(1024)
        except OSError:
            head = b''
        m = re.search(rb'^# Project: ?(.*)$', head, re.M)
        name = m.group(1).decode('utf-8', 'replace').strip() if m else ''
        projects[rel] = name or None
    return builds, languages, projects


# html_title is "%s %s documentation" % (project, release), translated.
# From the catalogs of Sphinx 9.1 (sphinx/locale/*/LC_MESSAGES/sphinx.po).
DOC_TITLES = {
    'ca': '%s %s documentaci\u00f3', 'cs': 'Dokumentace pro %s %s',
    'cy': 'Dogfennaeth %s %s', 'da': '%s %s dokumentation',
    'de': '%s %s Dokumentation',
    'el': '\u03a4\u03b5\u03ba\u03bc\u03b7\u03c1\u03af\u03c9\u03c3\u03b7 '
          '\u03c4\u03bf\u03c5 %s - %s',
    'en': '%s %s documentation', 'eo': '%s %s dokumentaro',
    'es': 'documentaci\u00f3n de %s - %s', 'et': '%s %s dokumentatsioon',
    'eu': '%s %s dokumentazioa',
    'fa': '\u0645\u0633\u062a\u0646\u062f\u0627\u062a %s%s',
    'fr': 'Documentation %s %s', 'gl': 'Documentaci\u00f3n %s %s',
    'he': '\u05ea\u05d9\u05e2\u05d5\u05d3 %s %s',
    'hi': '%s %s \u0926\u093f\u0917\u094d\u0926\u0930\u094d\u0936\u093f'
          '\u0915\u093e',
    'hr': '%s %s dokumentacija', 'hu': '%s %s dokument\u00e1ci\u00f3',
    'id': 'Dokumentasi %s %s', 'it': 'Documentazione %s %s',
    'ja': '%s %s \u30c9\u30ad\u30e5\u30e1\u30f3\u30c8',
    'ka': '%s %s \u10d3\u10dd\u10d9\u10e3\u10db\u10d4\u10dc\u10e2\u10d0'
          '\u10ea\u10d8\u10d0',
    'ko': '%s %s \ubb38\uc11c',
    'mk': '%s %s \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430'
          '\u0446\u0438\u0458\u0430',
    'nl': '%s %s documentatie', 'pl': '%s %s - dokumentacja',
    'pt_BR': 'Documenta\u00e7\u00e3o %s %s',
    'pt_PT': 'Documenta\u00e7\u00e3o %s %s',
    'ro': '%s %s documenta\u021bie',
    'ru': '\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430\u0446'
          '\u0438\u044f %s %s',
    'si': '%s %s \u0dbd\u0dda\u0d9b\u0dab\u0dba',
    'sk': 'Dokument\u00e1cia %s %s', 'sq': 'Dokumentim i %s %s',
    'sr': '\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430\u0446'
          '\u0438\u0458\u0430 %s %s',
    'sv': '%s %s dokumentation', 'tr': '%s %s belgelendirmesi',
    'vi': 'T\u00e0i li\u1ec7u %s %s', 'zh_CN': '%s %s \u6587\u6863',
    'zh_TW': '%s %s \u8aaa\u660e\u6587\u4ef6',
}


def project_from_doc_title(name: str, language, version):
    """The project of a default html_title in the language of the build,
    or None if name does not have that form."""
    lang = (language or 'en').replace('-', '_')
    fmt = DOC_TITLES.get(lang) or DOC_TITLES.get(lang.split('_')[0])
    if fmt is None:
        return None
    head, mid, tail = (' '.join(p.split()) for p in fmt.split('%s'))
    name = ' '.join(name.split())
    if not (name.casefold().startswith(head.casefold()) and
            name.casefold().endswith(tail.casefold())):
        return None
    inner = name[len(head):len(name) - len(tail)].strip()
    if version and inner.endswith(version):
        inner = inner[:-len(version)].strip()
    elif version:
        return None
    if mid and inner.endswith(mid):
        inner = inner[:-len(mid)].strip()
    return inner or None


def drop_version(name: str, version) -> str:
    """Removes "documentation" and the version from the end of a project
    name: "Kit 1.2 documentation" and "Kit Documentation (1.2.x)" give
    "Kit". The version must agree with the build version. A part of it must
    have two numbers or more ("Web 2" stays for version 2.1)."""
    while True:
        old = name
        name = re.sub(r'\s+documentation$', '', name, flags=re.I).strip()
        m = re.search(r'\s(\(\s*)?v?(\d[\w.]*?)(\.[x*])?(\s*\))?$', name)
        if m and version and bool(m.group(1)) == bool(m.group(4)):
            num = m.group(2)
            if num == version or ('.' in num and
                                  version.startswith(num + '.')):
                name = name[:m.start()].strip()
        if name == old:
            return name


# The separators between the page title and the project part of <title>:
# Sphinx " — ", furo " - ", sphinxawesome " | ".
TITLE_SEPS = (' \u2014 ', ' | ', ' - ')


def split_title(title: str) -> tuple[str, str]:
    """Splits <title> into the page title and the project part. The page
    title can also hold the separator, so the last one counts."""
    title = ' '.join(title.split())
    for sep in TITLE_SEPS:
        if sep in title:
            page, project = title.rsplit(sep, 1)
            return page.strip(), project.strip()
    return title, ''


# The article, in the order of SphinxPage.find_main(). Some themes (haiku)
# have an <h1> in the page header too.
MAIN_SELECTORS = (
    '[itemprop="articleBody"]', 'div.body', 'article.bd-article',
    'article[role="main"]', '[role="main"]')


def _title(doc) -> str:
    """The page title of a page (see SphinxPage.page_title)."""
    main = next((m for m in map(doc.select_one, MAIN_SELECTORS) if m), None)
    h1 = (main if main is not None else doc).find('h1')
    if h1:
        for a in h1.select('a[class*="headerlink"]'):
            a.decompose()
        text = ' '.join(h1.get_text().split())
        if text:
            return text
    title = doc.find('title')
    return split_title(title.get_text() if title else '')[0]


def docstitle(titles) -> str:
    """The project part that the <title> of the pages share: "Page — Kit
    1.0 documentation" gives "Kit 1.0 documentation". Each part after a
    separator is a candidate, and the root page can be the project part
    only. The part in most titles wins, and of those the longest, because
    the project name can hold a separator too ("Foo - Bar")."""
    titles = [' '.join(title.split()) for title in titles]
    counts = collections.Counter()
    for title in titles:
        parts = set()
        for sep in TITLE_SEPS:
            pieces = title.split(sep)
            parts.update(sep.join(pieces[i:]).strip()
                         for i in range(1, len(pieces)))
        counts.update(parts)
    for title in titles:
        if title in counts:
            counts[title] += 1  # A root page with the project part only.
    best = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    return best[0][0] if best and (best[0][1] > 1 or len(titles) == 1) else ''


def scan_build(src: pathlib.Path, build: str, skip, nested=()):
    """Reads all pages of one build. Returns (page -> titles of its toctree
    ancestors (root page first), the docstitle, True for a dirhtml build,
    the root page). Paths are relative to src. A page that two toctrees
    list belongs to the first one that a walk from the root page finds.
    The pages of the builds in nested (build roots below this one) belong
    to those builds."""
    folder = src / build
    titles, children, raw_titles, starts = {}, {}, [], []
    for p in sorted(folder.rglob('*.htm*')):
        rel = p.relative_to(src).as_posix()
        if (rel in skip or p.suffix.lower() not in PAGE_SUFFIXES or
                any(rel.startswith(n + '/') for n in nested)):
            continue
        try:
            text = flare2md.read_html(p) if p.is_file() else None
        except Exception:  # pylint: disable=broad-except
            text = None  # The page itself reports the error.
        if text is None:
            continue
        try:
            doc = BeautifulSoup(text, 'html5lib')
        except Exception:  # pylint: disable=broad-except
            continue  # The page itself reports the error.
        titles[rel] = _title(doc)
        # The root page (root_doc) starts the next/prev chain of the pages.
        if (doc.select_one('head > link[rel="next"]') and
                not doc.select_one('head > link[rel="prev"]')):
            starts.append(rel)
        title = doc.find('title')
        raw_titles.append(' '.join((title.get_text() if title else '').split()))
        kids = []
        # The entries of each toctree, not the entries below them: those
        # belong to the pages that the entries link to.
        for a in doc.select('div.toctree-wrapper ul:not(li ul) > li > a[href]'):
            href = a['href']
            path = urllib.parse.unquote(re.match(r'[^?#]*', href).group())
            if path and not re.match(r'[A-Za-z][\w+.-]*:', path):
                kids.append(posixpath.normpath(
                    posixpath.join(posixpath.dirname(rel), path)))
        children[rel] = list(dict.fromkeys(kids))
    root = starts[0] if len(starts) == 1 else next(
        (posixpath.join(build, 'index' + s) for s in PAGE_SUFFIXES
         if posixpath.join(build, 'index' + s) in titles), None)
    ancestors = {root: []} if root else {}
    queue = collections.deque(ancestors)
    while queue:
        page = queue.popleft()
        for kid in children.get(page, []):
            # html_link_suffix = '' drops the suffix. The dirhtml builder
            # links to folders.
            kid = next((k for k in [kid] + [kid + s for s in PAGE_SUFFIXES] +
                        [posixpath.join(kid, 'index.html')] if k in titles),
                       kid)
            if kid in titles and kid not in ancestors:
                ancestors[kid] = ancestors[page] + [titles[page]]
                queue.append(kid)
    dirhtml = len(titles) > 1 and all(
        posixpath.basename(r) == 'index.html' for r in titles)
    return ancestors, docstitle(raw_titles), dirhtml, root


def make_config(src, out, product=None, version=None,
                source_prefix='') -> SphinxConfig:
    """Builds the config. Generated pages and the files in _static and
    _sources are skipped."""
    src, out = pathlib.Path(src).resolve(), pathlib.Path(out).resolve()
    builds, languages, projects = (find_builds(src) if src.is_dir() else
                                   ({}, {}, {}))
    skip = set()
    for root in builds:
        folder = src / root
        for p in folder.iterdir():
            if GENERATED.match(p.name):
                skip.add(posixpath.join(root, p.name))
            elif GENERATED.match(p.name + '.html') and (
                    p / 'index.html').is_file():  # The dirhtml builder.
                skip.add(posixpath.join(root, p.name, 'index.html'))
        for sub in ('_static', '_sources'):
            for p in (folder / sub).rglob('*.htm*'):
                skip.add(p.relative_to(src).as_posix())
    toc, titles, dirhtml, roots = {}, {}, set(), {}
    for root in builds:
        nested = [r for r in builds if r != root and (
            root == '' or r.startswith(root + '/'))]
        ancestors, titles[root], is_dirhtml, roots[root] = scan_build(
            src, root, skip, nested)
        toc.update(ancestors)
        if is_dirhtml:
            dirhtml.add(root)
    return SphinxConfig(src=src, out=out, toctree=toc, skip=frozenset(skip),
                        product=product, version=version,
                        source_prefix=source_prefix.strip('/'),
                        page_class=SphinxPage, builds=builds,
                        docstitles=titles, dirhtml=frozenset(dirhtml),
                        languages=languages, roots=roots, projects=projects)


def bold_paragraph(soup, tag):
    """Returns <p><strong>…</strong></p> with the inline content of tag
    (its links, code and math stay). Spaces at the edges are removed."""
    p, strong = soup.new_tag('p'), soup.new_tag('strong')
    for kid in list(tag.contents):
        strong.append(kid.extract())
    for end in (0, -1):
        while strong.contents and isinstance(strong.contents[end],
                                             NavigableString):
            text = (strong.contents[end].lstrip() if end == 0 else
                    strong.contents[end].rstrip())
            if text:
                strong.contents[end].replace_with(text)
                break
            strong.contents[end].extract()
    p.append(strong)
    return p


def _alpha(n: int) -> str:
    """1 -> a, 26 -> z, 27 -> aa (the CSS lower-alpha style)."""
    s = ''
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(ord('a') + r) + s
    return s


def _roman(n: int) -> str:
    """1 -> i, 4 -> iv (the CSS lower-roman style)."""
    out = ''
    for value, digits in ((1000, 'm'), (900, 'cm'), (500, 'd'), (400, 'cd'),
                          (100, 'c'), (90, 'xc'), (50, 'l'), (40, 'xl'),
                          (10, 'x'), (9, 'ix'), (5, 'v'), (4, 'iv'),
                          (1, 'i')):
        while n >= value:
            out += digits
            n -= value
    return out


def _is_section(tag) -> bool:
    # Sphinx 4 and older write sections as <div class="section">.
    return tag.name == 'section' or (
        tag.name == 'div' and 'section' in tag.get('class', []))


def _first_term(dl):
    return dl.find('dt', recursive=False)


def is_api_object(dl) -> bool:
    """True for an object description of any Sphinx domain. Sphinx 4 and
    later mark the term "sig-object". Older versions have only "descname".
    """
    dt = _first_term(dl)
    return dt is not None and (
        'sig-object' in dt.get('class', []) or
        dt.find(class_='descname') is not None)


def _strip_newline(node) -> bool:
    """Removes one newline at the start of a text node. Returns True if
    there was one."""
    if isinstance(node, NavigableString) and node.startswith('\n'):
        node.replace_with(node[1:])
        return True
    return False


def is_code_div(div) -> bool:
    """True for a highlighted block: <div class="highlight-<lexer>"> that
    holds only the highlighted code (a container can have a class such as
    "highlight-box")."""
    if not any(c.startswith('highlight-') for c in div.get('class', [])):
        return False
    kids = [k for k in div.contents if str(k).strip()]
    return bool(kids) and all(
        getattr(k, 'name', None) in ('div', 'table') and
        {'highlight', 'highlighttable'} & set(k.get('class', []))
        for k in kids)


def code_lang(div, pre) -> str:
    """The fence language of a highlighted block."""
    classes = div.get('class', [])
    lang = next((c[len('highlight-'):] for c in classes
                 if c.startswith('highlight-')), '')
    if 'doctest' in classes or pre.get_text().startswith('>>> '):
        return 'pycon'
    # Code that Pygments could not read as Python has no token spans.
    if lang == 'default' and not any(s.get('class') for s in
                                     pre.find_all('span')):
        return ''
    return SPHINX_LANG.get(lang, lang)


def signature(dt) -> str:
    """The text of a signature. A <br> and each parameter of a signature
    with one parameter per line (a <dl> in the term) start a line."""
    for e in dt.find_all(string=re.compile(INDENT)):
        e.replace_with(e.replace(INDENT, ''))
    for e in dt.select('a:has(.viewcode-link), .viewcode-link'):
        e.decompose()  # "[source]", a link to the highlighted source.
    for br in dt.find_all('br'):
        br.replace_with('\n')
    for dd in dt.select('dl > dd'):
        dd.insert(0, NavigableString('\n' + INDENT))
    for dl in dt.find_all('dl'):
        dl.insert_after(NavigableString('\n'))
    # The arrow before the return annotation becomes "->". An arrow in a
    # default value stays.
    icons = dt.select('.sig-return-icon')
    for e in icons:
        e.string = '->'
    if not icons:  # Before Sphinx 4.3: " → " is plain text in the term.
        for s in dt.find_all(string=re.compile('\u2192')):
            if s.find_parent(class_=['sig-param', 'default_value']) is None:
                s.replace_with(s.replace('\u2192', '->'))
    lines = [' '.join(l.split()) for l in dt.get_text().split('\n')]
    lines = [l.replace(INDENT, '    ', 1) if l.startswith(INDENT) else l
             for l in (l.strip() for l in lines) if l]
    return '\n'.join(lines).replace(' ' + INDENT, '    ')


class SphinxPage(flare2md.Page):
    """Converts one page of a Sphinx build."""
    report_keys = flare2md.REPORT_KEYS + ['api_objects', 'math']
    # A form in an article is content (raw HTML). Its text stays. The engine
    # removes the input controls.
    # A contents directive lists the headings of the page with anchor
    # links. Markdown has no anchors, so it is navigation only.
    # nbsphinx shows the cell prompts ("[1]:") in their own code blocks.
    # Some themes put navigation (previous/next links) in the article.
    # A <nav> in the article is navigation too (the breadcrumb bar of
    # sphinx-nefertiti, a contents list).
    hidden = ('script, style, title, select, textarea, nav, '
              'div.topic.contents, aside.topic.contents, '
              'div.nbinput > div.prompt, div.nboutput > div.prompt, '
              '[role=navigation], noscript, template, '
              # The label of an open myst-nb toggle. The sidenote copy that
              # the book theme hides (it shows one outside the admonition).
              'summary > .expanded, span.sidenote.d-n, span.marginnote.d-n')
    # Code comes from source files: each character counts.
    exact_code = True

    @staticmethod
    def find_main(soup):
        # The article first. In some themes (pydata) role="main" also holds
        # the navigation and the footer.
        return (soup.find(attrs={'itemprop': 'articleBody'}) or
                soup.select_one('div.body') or
                soup.select_one('article.bd-article') or
                soup.select_one('article[role=main]') or
                soup.find(attrs={'role': 'main'}))

    def page_title(self, soup) -> str:
        """The text of the first <h1> of the article. Sphinx makes <title>
        from it. Without an <h1>, the page part of <title>, or the document
        name if that is the "<no title>" placeholder (it is translated)."""
        main = self.find_main(soup)
        h1 = main.find('h1') if main is not None else None
        if h1 is not None:
            text = ' '.join(''.join(
                s for s in h1.find_all(string=True)
                if s.find_parent('a', class_='headerlink') is None).split())
            if text:
                return text
        title = split_title(flare2md.Page.page_title(soup))[0]
        if re.fullmatch(r'<[^<>]+>', title):
            return self.docname().name
        return title

    def docname(self) -> pathlib.PurePosixPath:
        """The Sphinx document name of the page ("guide/install")."""
        root = self.build_root()
        name = self.rel.relative_to(root) if root else self.rel
        if root in self.cfg.dirhtml:  # <docname>/index.html
            name = (name.parent if name.parent.name else
                    pathlib.PurePosixPath('index.html'))
        return name.with_suffix('')

    def build_root(self) -> str:
        """The build that holds the page, relative to src."""
        roots = [r for r in self.cfg.builds
                 if r == '' or self.rel.is_relative_to(r)]
        return max(roots, key=len, default='')

    def section(self) -> str:
        """The first folder of the page, as for the html builder: a dirhtml
        page <docname>/index.html counts as <docname>.html."""
        rel = self.rel
        if (self.build_root() in self.cfg.dirhtml and
                rel.name == 'index.html' and rel.parent.name and
                rel.parent.as_posix() != self.build_root()):
            rel = rel.parent.with_suffix('.html')
        return rel.parts[0] if len(rel.parts) > 1 else ''

    def project_name(self, soup, version) -> str:
        """The project name from the home link, or else from the project
        part of <title> that the pages of the build share, or else from
        objects.inv (html_title = "", or a build of one page)."""
        for home in soup.select('a.icon-home'):
            if home.get_text(strip=True):
                return ' '.join(home.get_text().split())
        root = self.build_root()
        name = self.cfg.docstitles.get(root, '')
        if not name and self.cfg.projects.get(root):
            return self.cfg.projects[root]
        if not name:  # A build of one page, or titles with no project part.
            title = flare2md.Page.page_title(soup)
            name = split_title(title)[1]
            if not name and project_from_doc_title(
                    title, self.cfg.languages.get(root), version):
                name = title
        return (project_from_doc_title(name, self.cfg.languages.get(root),
                                       version) or
                drop_version(name, version))

    def metadata(self, soup, title) -> dict:
        root = self.build_root()
        version = self.cfg.builds.get(root)
        project = self.project_name(soup, version)
        # The links of the breadcrumb bar (Read the Docs and pydata themes),
        # without the "View page source" part. The home link can be an
        # icon only.
        crumbs = [(li, a) for li in soup.select(
                      'ul.wy-breadcrumbs > li, ul.bd-breadcrumbs > li')
                  if 'wy-breadcrumbs-aside' not in li.get('class', [])
                  for a in li.find_all('a')]
        crumbs = [' '.join(a.get_text().split()) or
                  (project if 'icon-home' in a.get('class', []) or
                   'breadcrumb-home' in li.get('class', []) else '')
                  for li, a in crumbs]
        # The toctree items that the sidebar marks "current". The class
        # toctree-lN keeps out the navigation bar of some themes.
        current = [' '.join(li.a.get_text().split())
                   for li in soup.select('li.current')
                   if li.a is not None and
                   'current' not in li.a.get('class', []) and
                   any(re.fullmatch(r'toctree-l\d+', c)
                       for c in li.get('class', []))]
        # The parents in the "related" bar of the basic layout (classic,
        # nature, the Python docs): nav-item-1, nav-item-2 ... The root
        # page link (nav-item-0) and the page itself (nav-item-this) are
        # no parents.
        related = soup.select_one('div.related')
        parents = [' '.join(li.get_text().split()).rstrip('\xbb ').strip()
                   for li in (related.select('li.nav-item') if related
                              else [])
                   if re.search(r'\bnav-item-[1-9]\d*\b',
                                ' '.join(li.get('class', [])))]
        has_related = related is not None and bool(
            related.select('li.nav-item'))
        root_page = self.rel.as_posix() == self.cfg.roots.get(root)
        if any(crumbs):  # A theme with a breadcrumb bar.
            breadcrumb = [c for c in crumbs if c]
        elif has_related:
            breadcrumb = parents
            if project and not (root_page and not parents):
                breadcrumb.insert(0, project)
        elif current:  # The sidebar marks the path with "current".
            breadcrumb = current
            if project and not root_page:
                breadcrumb.insert(0, project)
        else:  # The toctrees of the build.
            breadcrumb = list(self.cfg.toctree.get(str(self.rel), []))
            if breadcrumb and project:  # The root page stands for it.
                breadcrumb[0] = project
            elif project and not root_page:  # Not in a visible toctree.
                breadcrumb = [project]
        return {
            'title': title or self.rel.stem,
            'source': posixpath.join(self.cfg.source_prefix, str(self.rel)),
            'breadcrumb': breadcrumb,
            'section': self.section(),
            'page_id': self.docname().as_posix(),
            'product': self.cfg.product or project or None,
            'version': self.cfg.version or version,
            'project': project or None,
        }

    def clean(self, soup, main):
        self.convert_sphinx(soup, main)
        super().clean(soup, main)

    def convert_sphinx(self, soup, main):
        """Replaces Sphinx markup by plain HTML that the shared rules know.
        """
        for e in main.select('a.headerlink'):
            e.decompose()  # An icon character that links to the anchor.
        for h in main.find_all(re.compile(r'^h([7-9]|\d\d)$')):
            h.name = 'h6'  # Docutils numbers deep sections past 6.
        self.convert_html4_lists(soup, main)
        self.add_css_text(main)
        # A type annotation is emphasis in parts: <em>list</em><em>[</em>
        # <a><em>int</em></a><em>]</em>. Emphasis that is all of a link goes
        # around the link, so that the parts merge into one run.
        for a in main.find_all('a'):
            kids = [k for k in a.contents if str(k).strip()]
            if (len(kids) == 1 and getattr(kids[0], 'name', None) == 'em' and
                    len(a.contents) == 1):
                kids[0].unwrap()
                a.wrap(soup.new_tag('em'))
        for e in main.find_all('svg'):
            e.decompose()  # An icon (octicon, Font Awesome). It has no text.
        for e in main.find_all('abbr', title=True):
            # The browser shows the expansion only on hover. The text gets
            # it, but code does not: "*" in a signature has the title
            # "Keyword-only parameters separator (PEP 3102)".
            if (e.find_parent(['pre', 'code']) is None and
                    e.find_parent('dt', class_='sig') is None):
                e.append(NavigableString(f' ({e["title"]})'))
            e.unwrap()
        # MyST task lists: a checkbox shows the state of the item.
        for e in main.select('input.task-list-item-checkbox'):
            e.replace_with(flare2md.TaskMarker(
                '[x] ' if e.has_attr('checked') else '[ ] '))
        # A sphinx-design card link covers the card, and its text is hidden.
        # The card title gets the link.
        for a in main.select('a.sd-stretched-link'):
            card = a.find_parent(class_='sd-card')
            title = card.select_one('.sd-card-title') if card else None
            if title is not None and a.get('href'):
                link = soup.new_tag('a', href=a['href'])
                link.extend(list(title.contents))
                title.append(link)
                a.decompose()
        self.convert_tabs(soup, main)
        self.convert_letter_lists(soup, main)
        # An SVG diagram (graphviz, inheritance) is an <object>. Its content
        # is a fallback that the browser does not show.
        for obj in main.find_all('object'):
            data = obj.get('data', '')
            if obj.get('type', '').startswith('image/') or re.search(
                    r'\.(svg|png|gif|jpe?g)$', data, re.I):
                img = soup.new_tag('img', src=data, alt=obj.get('alt', ''))
                obj.replace_with(img)
        # A docutils block quote is an indent, not a quote. An epigraph, a
        # pull quote and highlights are quotes.
        for quote in main.select('blockquote'):
            kids = [k for k in quote.contents if str(k).strip()]
            if len(kids) == 1 and getattr(kids[0], 'name', None) == 'div':
                kids[0].unwrap()
            if not {'epigraph', 'pull-quote', 'highlights'} & set(
                    quote.get('class', [])):
                quote.unwrap()
        self.convert_code_blocks(soup, main)
        self.convert_tex(soup, main)
        self.convert_footnotes(soup, main)
        objects = [dl for dl in main.find_all('dl') if is_api_object(dl)]
        self.convert_definitions(soup, main, objects)
        self.convert_api_objects(soup, objects)
        for div in main.select('div.admonition'):
            title = div.find('p', class_='admonition-title')
            quote = soup.new_tag('blockquote')
            if title is not None:
                quote.append(bold_paragraph(soup, title))
            for child in list(div.contents):
                quote.append(child.extract())
            div.replace_with(quote)
            self.stats['notes'] += 1
        for p in main.select('p.rubric, p.caption, p.topic-title, '
                             'p.sidebar-title, summary'):
            if 'caption' in p.get('class', []) and p.find_parent(
                    'div', class_='figure') is not None:
                continue  # A figure caption (Sphinx 3 and older) is text.
            p.replace_with(bold_paragraph(soup, p))
        for cap in main.select('table > caption'):
            cap.find_parent('table').insert_before(bold_paragraph(soup, cap))
            cap.decompose()
        # A figure is a paragraph with its image. The caption follows it.
        for fig in main.select('figure'):
            cap = fig.find('figcaption')
            if cap is not None:
                fig.insert_after(cap.extract())
                cap.name = 'div'
            fig.name = 'p' if fig.find(list(BLOCK_TAGS)) is None else 'div'
        for e in main.select('details'):
            e.unwrap()
        for e in main.find_all('cite'):
            e.name = 'em'
        for img in main.find_all('img'):
            if img.get('alt') == img.get('src'):
                img['alt'] = ''  # A file path as alt text. Use the name.

    @staticmethod
    def convert_tabs(soup, main):
        """The label of each tab becomes a bold paragraph before its panel.
        A browser shows one panel at a time. The Markdown has all of them.
        """
        # sphinx-tabs: buttons in a tab list, panels after it.
        for tablist in main.select('[role=tablist]'):
            if not tablist.find('button'):
                continue  # Raw HTML tabs: the labels are links in a list.
            for btn in tablist.find_all('button'):
                panel = (main.find(id=btn.get('aria-controls')) if
                         btn.get('aria-controls') else None)
                if panel is not None:
                    panel.insert(0, bold_paragraph(soup, btn))
            for btn in tablist.find_all('button'):  # Without a panel.
                btn.replace_with(bold_paragraph(soup, btn))
            tablist.unwrap()
        # sphinx-design and sphinx-inline-tabs: a label after its radio
        # input, then the panel. A sphinx-design card title is a label too.
        for label in main.select('label.sd-tab-label, div.sd-card-title, '
                                 'div.tab-set > label.tab-label'):
            label.replace_with(bold_paragraph(soup, label))

    @staticmethod
    def convert_letter_lists(soup, main):
        """Markdown numbers an ordered list with digits only, and with 9
        digits at most. A list with letters or roman numbers (CSS classes of
        docutils) or with larger numbers becomes a bullet list, and each
        item starts with its label."""
        styles = {'loweralpha': _alpha, 'upperalpha':
                  lambda n: _alpha(n).upper(), 'lowerroman': _roman,
                  'upperroman': lambda n: _roman(n).upper()}
        for ol in main.find_all('ol'):
            style = next((styles[c] for c in ol.get('class', [])
                          if c in styles), None)
            try:
                n = int(ol.get('start', 1) or 1)
            except ValueError:
                n = 1
            items = ol.find_all('li', recursive=False)
            if style is None and n + len(items) > 10 ** 9:
                style = str  # CommonMark allows 9 digits at most.
            if style is None:
                continue
            for li in items:
                label = NavigableString(f'{style(n)}. ')
                first = next((k for k in li.contents if str(k).strip()), None)
                if getattr(first, 'name', None) == 'p':
                    first.insert(0, label)
                else:
                    li.insert(0, label)
                n += 1
            ol.name = 'ul'
            ol.attrs.pop('start', None)

    @staticmethod
    def add_css_text(main):
        """Sphinx lets CSS add some text. The tool writes it: the " : "
        before a classifier ("width : int"). Sphinx 4 and older also add
        the brackets of a footnote reference ("[1]") and the colon after a
        field name. Newer versions write these in the HTML."""
        for e in main.select('span.classifier'):
            before = e.previous_sibling
            if not (str(before or '').rstrip().endswith(':') or (
                    getattr(before, 'get', None) and 'classifier-delimiter'
                    in before.get('class', []))):
                e.insert(0, NavigableString(' : '))
        for e in main.select('.brackets'):
            if (e.find(class_='fn-bracket') is None and
                    not e.get_text().strip().startswith('[')):
                e.insert(0, NavigableString('['))
                e.append(NavigableString(']'))
        for dt in main.select('dl.field-list > dt'):
            if (dt.find(class_='colon') is None and
                    not dt.get_text().rstrip().endswith(':')):
                dt.append(NavigableString(':'))

    def convert_code_blocks(self, soup, main):
        """Highlighted code becomes a plain <pre> with its language."""
        for pre in main.select('pre.mermaid'):  # sphinxcontrib-mermaid
            pre['data-lang'] = 'mermaid'
        for cap in main.select('div.code-block-caption'):
            cap.replace_with(bold_paragraph(soup, cap))
        # The source of a viewcode page (_modules) has a <div> and a "[docs]"
        # link for each object in its <pre>. Sphinx 9 adds
        # '<div …>\n<a …>[docs]</a>\n' before a line and '</div>\n' after
        # a line. Sphinx 4 adds no newlines. Remove the added text, and the
        # code is the source file.
        for pre in main.find_all('pre'):
            for div in pre.select('div.viewcode-block'):
                added = _strip_newline(div.contents[0] if div.contents
                                       else None)
                for a in div.find_all('a', class_='viewcode-back',
                                      recursive=False):
                    if added:
                        _strip_newline(a.next_sibling)
                    a.decompose()
                if added:
                    _strip_newline(div.next_sibling)
            for e in pre.select('a.viewcode-back'):
                e.decompose()
            for e in pre.find_all('div'):
                e.unwrap()
        for div in [d for d in main.find_all('div') if is_code_div(d)]:
            # Line numbers are inline spans, or a table column (older
            # Sphinx, or the "table" style).
            pre = div.select_one('td.code pre') or div.find('pre')
            if pre is None:
                div.decompose()
                continue
            for e in pre.select('span.linenos'):
                e.decompose()
            lang = code_lang(div, pre)
            div.replace_with(_new_tag(soup, 'pre', pre.get_text(),
                                      **{'data-lang': lang}))
            self.stats['code_blocks'] += 1

    def convert_tex(self, soup, main):
        """Math keeps its LaTeX: inline as $`x`$, display as a "math" code
        block. GitHub shows both as math. MathJax pages hold the LaTeX as
        text, imgmath pages as the alt text of an image."""
        def tex(e):
            imgs = [e] if e.name == 'img' else e.find_all('img')
            text = (' '.join(i.get('alt', '') for i in imgs) if imgs else
                    e.get_text())
            return re.sub(r'^\\[(\[]|\\[)\]]$', '', text.strip()).strip()

        # Display math first: the imgmath fallback puts a span.math in it.
        for e in main.select('div.math'):
            eqno = [n.get_text().strip() for n in e.select('span.eqno')]
            for n in e.select('span.eqno'):
                n.decompose()
            pre = _new_tag(soup, 'pre', tex(e), **{'data-lang': 'math'})
            e.replace_with(pre)
            if eqno:  # "(1)": the number that :eq: references show.
                pre.insert_after(_new_tag(soup, 'p', eqno[0]))
            self.stats['math'] += 1
        for e in main.select('span.math, img.math'):
            code = _new_tag(soup, 'code', tex(e))
            e.replace_with(code)
            code.insert_before(NavigableString('$'))
            code.insert_after(NavigableString('$'))
            self.stats['math'] += 1

    @staticmethod
    def convert_html4_lists(soup, main):
        """The HTML4 writer (Sphinx 1.8 and older, or html4_writer) writes a
        field list, an option list and the docinfo as a table. The HTML5
        writer writes a <dl>. Both give the same Markdown. A long name has
        a row of its own, and its body is in the next row."""
        for tbl in main.select('table.field-list, table.option-list, '
                               'table.docinfo'):
            kind = ('option-list' if 'option-list' in tbl.get('class', [])
                    else 'field-list')
            dl = soup.new_tag('dl', attrs={'class': kind})
            for tr in tbl.select(':scope > tbody > tr, :scope > tr'):
                for cell in tr.find_all(['th', 'td'], recursive=False):
                    if cell.name == 'th' or 'option-group' in cell.get(
                            'class', []):
                        new = soup.new_tag('dt')
                    elif cell.get_text().strip() or cell.find(True):
                        new = soup.new_tag('dd')
                    else:
                        continue  # The empty cell below a long name.
                    new.extend(list(cell.contents))
                    dl.append(new)
            tbl.replace_with(dl)

    @staticmethod
    def convert_footnotes(soup, main):
        """A footnote or a citation becomes a paragraph "[label] text".
        Sphinx writes them as <aside> or <div> (docutils 0.18 and later),
        <dl> (0.17) or a <table> (older)."""
        entries = []  # (element to replace, label, body nodes)
        for e in main.select('aside.footnote, aside.citation, '
                             'div.footnote, div.citation'):
            label = e.find(class_='label')
            text = label.get_text() if label is not None else ''
            if label is not None:
                label.decompose()
            entries.append((e, text, list(e.contents)))
        for dl in main.select('dl.footnote, dl.citation'):
            items = []
            for dt in dl.find_all('dt', recursive=False):
                # The label, without the links back to the references.
                for back in dt.select('span.fn-backref, span.backrefs'):
                    back.decompose()
                dd = dt.find_next_sibling('dd')
                items.append((dt.get_text(), list(dd.contents) if dd else []))
            entries.append((dl, None, items))
        for tbl in main.select('table.footnote, table.citation'):
            items = []
            for tr in tbl.find_all('tr'):
                cells = tr.find_all('td', recursive=False)
                if len(cells) >= 2:
                    items.append((cells[0].get_text(),
                                  list(cells[-1].contents)))
            entries.append((tbl, None, items))
        for e, label, body in entries:
            items = [(label, body)] if label is not None else body
            div = soup.new_tag('div')
            for text, nodes in items:
                text = ' '.join(text.split()).strip('[]')
                note = soup.new_tag('div')  # One footnote.
                for node in nodes:
                    note.append(node.extract())
                for back in note.select('span.backrefs, span.fn-backref, '
                                        'em > a.fn-backref'):
                    back.decompose()  # Links back to the references.
                kids = [k for k in note.contents if str(k).strip()]
                if not any(getattr(k, 'name', None) in BLOCK_TAGS
                           for k in kids):
                    note.name = 'p'  # Inline text only (old table form).
                    first = note
                elif getattr(kids[0], 'name', None) == 'p':
                    first = kids[0]
                else:
                    first = soup.new_tag('p')
                    note.insert(0, first)
                first.insert(0, NavigableString(f'[{text}] '))
                div.append(note)
            e.replace_with(div)

    def convert_api_objects(self, soup, objects):
        """Each API object (class, method, function ...) becomes a heading
        with its name and a code block with its signature. The heading
        level is one below the heading of its section, plus one for each
        object between the section and the object. A heading in the
        description of an object ("Example") goes below the object heading.
        """
        keep = {id(dl) for dl in objects}
        levels = []
        for dl in objects:  # Outer objects come first.
            section = dl.find_parent(_is_section)
            depth = 0
            for p in dl.parents:
                if p is section:
                    break
                depth += id(p) in keep
            head = (section.find(HEADING) if section is not None else None)
            level = min(6, (int(head.name[1]) if head else 1) + 1 + depth)
            levels.append(level)
            for dd in dl.find_all('dd', recursive=False):
                for h in dd.find_all(HEADING):
                    h.name = f'h{min(6, max(int(h.name[1]), level + 1))}'
        for dl, level in zip(objects, levels):
            # The <dl> of an object in an alias directive has no class. Its
            # terms have the domain ("sig sig-object cpp").
            classes = dl.get('class', []) + dl.find(
                'dt', recursive=False).get('class', [])
            # "describe" and "object" are generic. Sphinx 2 and older
            # (dl.function) have no domain class. There an option or an
            # envvar is a std object, the id of a C object starts with
            # "c.", and the id of a C++ object with "_CPPv". Other objects
            # are Python.
            ident = dl.find('dt', recursive=False).get('id') or ''
            domain = next((c for c in classes if c in DOMAINS),
                          '' if {'describe', 'object', 'option', 'cmdoption',
                                 'envvar'} & set(classes) else
                          'c' if ident.startswith('c.') else
                          'cpp' if ident.startswith('_CPPv') else 'py')
            for i, dt in enumerate(dl.find_all('dt', recursive=False)):
                new = []
                if i == 0:  # More terms are more signatures of one object.
                    new.append(self.object_heading(soup, dt, domain, level))
                sig = signature(dt)  # It changes dt. The heading comes first.
                if sig:
                    new.append(_new_tag(soup, 'pre', sig, **{
                        'data-lang': DOMAIN_LANG.get(domain, '')}))
                for n in reversed(new):
                    dt.insert_after(n)
                dt.decompose()
                self.stats['api_objects'] += 1
        for dl in objects:
            for dd in dl.find_all('dd', recursive=False):
                self._flatten_dd(dd)
            dl.unwrap()

    @staticmethod
    def object_heading(soup, dt, domain, level):
        """A heading with the object name in code. A Python id is the full
        name ("pkg.Point.move"), but only if it ends with the shown name:
        Sphinx changes some ids ("id0" for a second description, "cafe"
        for "café", "private" for "_private"). Other ids are encoded (C++).
        Else the heading has the shown name."""
        # The name is on the last line that has one. A C++ template line
        # ("template<typename T>") comes first and names T.
        lines = [[]]
        for e in dt.descendants:
            if getattr(e, 'name', None) == 'br':
                lines.append([])
            elif (getattr(e, 'name', None) and
                  set(e.get('class', [])) & NAME_CLASSES and
                  e.find_parent(class_=list(NAME_CLASSES)) is None):
                lines[-1].append(e.get_text())
        name = ' '.join(''.join(next(
            (l for l in reversed(lines) if l), [])).split())
        ident = dt.get('id') or ''
        if domain == 'py' and name and (ident == name or
                                        ident.endswith('.' + name)):
            name = ident
        heading = soup.new_tag(f'h{level}')
        heading.append(_new_tag(soup, 'code', name or ' '.join(
            dt.get_text().split())))
        return heading


def main(argv=None) -> int:
    parser = flare2md.make_parser(
        'Convert Sphinx HTML builds to Markdown for RAG.',
        'a Sphinx HTML build (with searchindex.js or _static/'
        'documentation_options.js) or a folder that holds '
        'several builds')
    args = parser.parse_args(argv)
    config = make_config(args.source_dir, args.out_dir, args.product,
                         args.version, args.source_prefix)
    if config.src.is_dir() and not config.builds:
        print('warning: no searchindex.js or _static/documentation_options.js '
              'found. This may not be a Sphinx build.', file=sys.stderr)
    return flare2md.run_cli(parser, args, config)


if __name__ == '__main__':
    # Worker processes find SphinxPage as sphinx2md.SphinxPage.
    import sphinx2md  # pylint: disable=import-self
    sys.exit(sphinx2md.main())
