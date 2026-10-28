# flare2md

This folder has two converters that share one Markdown engine:

- `flare2md.py` reads a MadCap Flare **WebHelp2** build.
- `sphinx2md.py` reads **Sphinx** HTML builds (see [Sphinx builds](#sphinx-builds-sphinx2mdpy)).

flare2md converts the HTML topics of a MadCap Flare **WebHelp2** build into Markdown files for RAG. It writes one `.md` file for each topic. Each file starts with a YAML header. The tool also copies the images that the pages refer to.

The tool was built for the Keysight ADS 2025 help (`doc/ads`), which is Confluence content that Flare publishes. Other Flare WebHelp2 builds use the same page frame. Most rules for Confluence constructs (note boxes, code panels, equations) do nothing on a build that does not have those constructs.

## Install

Python 3.11.5 or later. There is no install step. The scripts load their packages from `_vendor/`: pure-Python copies of fixed PyPI releases. `_vendor/vendor.txt` lists the versions, and `_vendor/LICENSES/` holds their licences. The versions are fixed because the output depends on the rules of `markdownify`. The scripts use the `_vendor/` copies also when other versions are installed. `_vendor/typing_extensions.py` is not a copy: it takes the five names that `bs4` needs from the `typing` module of Python 3.11.

The YAML header is written without a YAML package. The writer follows the rules of PyYAML's `safe_dump` (which strings get quotes, and which quotes), and a test against PyYAML on 188,000 one-line strings found no difference. One exception: a string with a line break gets double quotes.

`verify.py` and `images_csv.py` also need `markdown-it-py`, which is not vendored:

```sh
python3 -m pip install markdown-it-py==4.2.0
```

Without it, the unit tests of these two tools are skipped.

The output was checked to be byte-identical on Python 3.11 and 3.13 on macOS. An earlier version (with lxml) was also byte-identical on Python 3.12 on Windows 11.

## Run

```sh
python flare2md.py SOURCE_DIR OUT_DIR [--product NAME] [--version V]
                   [--source-prefix PATH] [--jobs N] [--only PAGE ...]
python verify.py SOURCE_DIR OUT_DIR
python images_csv.py MD_DIR CSV_FILE   # see "Image list"
python -m unittest discover -s tests   # tests for all tools
```

`SOURCE_DIR` is the help root (the folder with `Data/HelpSystem.xml`) or a folder below it. The output layout is the layout below `SOURCE_DIR`.

For example, this command reproduces `~/ADS-docs-md/ads2025`:

```sh
python flare2md.py ~/ADS-docs-src/Content/ads2025 ~/ADS-docs-md/ads2025 \
    --product "Keysight ADS" --version 2025 --source-prefix doc/ads
```

| Option | Effect |
|---|---|
| `--product`, `--version` | The values for the YAML header. Default: `#productname` and `#productrelease` in the page's build metadata. |
| `--source-prefix` | A path put before `source` in the YAML header. `source` is the page path relative to the help root. |
| `--jobs` | The number of worker processes. Default: one per CPU. `--jobs 1` runs in one process. The tool uses no more processes than CPUs or pages. |
| `--only` | Converts only the given pages (paths relative to `SOURCE_DIR`, or absolute paths below it). The reports in `_report/` stay as the last full run wrote them. Errors go to stderr. |

The tool never removes pages or images from the output. If `OUT_DIR` holds `.md` files that have no source page, the tool lists them in `_report/stale.txt`. A page that fails keeps its old `.md` and is listed in `errors.txt`, not in `stale.txt`. A page or image that cannot be read (no permission, bad UTF-16) is listed in `errors.txt`, and the run continues. `OUT_DIR` must be a folder that the tool can write to. It must not be `SOURCE_DIR` or a folder above it, also in another letter case on macOS or Windows.

## Image list (`images_csv.py`)

The converters copy the images next to the Markdown. `images_csv.py` lists all images of a converted tree in one CSV file. Another tool can use the file to upload the images, for example to a picture host. That tool can then change the local paths in the pages to URLs. `images_csv.py` itself does not change any file.

```sh
python images_csv.py MD_DIR CSV_FILE
```

The file has one row for each reference to a local image file in a page:

- Markdown images (`![alt](path)`) and links to images (`[text](path)`).
- `src` and `href` in `<img>`, `<a>` and `<source>` tags, in the HTML tables.

References in code, in escaped text and in the YAML header are not references. An image file that no page uses has one row with an empty `page`.

| Column | Contents |
|---|---|
| `image` | The image path relative to `MD_DIR`, with `/`. |
| `file` | The absolute path of the image file. |
| `exists` | `1`, or `0` if the page names a file that is not in `MD_DIR`. The converter lists these files in `_report/missing_images.txt`. |
| `bytes`, `sha256`, `mime` | The size, the SHA-256 hash and the MIME type of the file. Empty if `exists` is `0`. |
| `page` | The `.md` file, relative to `MD_DIR`. |
| `line`, `column` | The position where `ref` starts in the page (1-based, in characters). |
| `kind` | `markdown` or `html`. |
| `ref` | The text of the reference, exactly as the page has it: percent-encoded, with a query or fragment if it has one. In `html`, `ref` is HTML-escaped (`&amp;`). |

To change a reference, replace `ref` at `line` and `column`. Do not replace each `ref` text everywhere in the page: the same text can also be in code. Replace from the end of a line to its start, so that the other columns on that line stay correct. The tool sorts the rows by `image`, `page`, `line` and `column`.

## Use in a RAG pipeline

The pages are CommonMark with GFM tables. We tested them with LlamaIndex (`llama-index-core` 0.14.25, `llama-index-readers-file` 0.7.0). The numbers below are from the ADS 2025 help (7401 pages) and the ADS Python docs (158 pages).

- **YAML header.** `SimpleDirectoryReader` reads a `.md` file as plain text. `MarkdownNodeParser` then makes the YAML header a node of its own, and the other nodes do not get the title or the breadcrumb. `MarkdownReader` also makes the header a document of its own. Move the header into the document metadata before you split the text (see the example below).
- **Do not use `MarkdownReader`.** `SimpleDirectoryReader` reads a `.md` file as plain text, and that is correct. `MarkdownReader` (from `llama-index-readers-file`) changes the text. By default, it removes all links and images. It always removes each `<...>` span: HTML tags, the raw HTML tables and also text such as `\<n>` in `BONDW\<n>`. It also removes the indent of each line outside code and all blank lines. It finds only fences that start at the start of a line, so it changes the code in list items too.
- **Node sizes.** `MarkdownNodeParser` starts a node at each heading. In the ADS help, this gives 40 192 nodes with a median of 25 words. 39 % of the nodes have fewer than 15 words. Most small nodes are short sections of the AEL pages, such as `##### Version Introduced`. A split at heading levels 1 to 3 only gives 23 287 nodes with a median of 67 words. In the Python docs, each API object is a heading, and the median is 11 words (an enum member often has no description). Join small nodes with the nodes next to them under the same parent heading.
- **Large tables.** 58 nodes have more than 5 000 words. Most of them are long parameter tables with one header row. A text splitter cuts such a table, and the parts after the first part have no header row. Split a long table by rows, and repeat the header row in each part.
- **Header path.** `header_path` holds the parent headings, not the heading of the node. The heading of the node is the first line of the node text. A heading can hold Markdown: a link (`##### [Where Used](…)`), a code span or an escape.
- **Escapes.** The pages escape characters that Markdown would misread (`\*`, `\_`, `\<`), and a line break is a `\` at the end of the line. A table cell gets no escapes of block markers (`-`, `#`, `>`), because a cell holds inline content only. Before keyword search (BM25), remove the backslash escapes outside code: a tokenizer splits `\_\_fdd` into other tokens than `__fdd`. Where you show the text, keep the Markdown as it is.
- **Images.** Most alt texts are file names (for example `bbspice-4-1-01`), because the source images have no alt text. `images_csv.py` gives the page, line and column of each image reference, so a later step can write better alt texts.

This example loads a converted tree into LlamaIndex documents. The YAML header becomes metadata:

```python
import pathlib
import re

import yaml
from llama_index.core import Document

FRONT = re.compile(r'\A---\n(.*?)\n---\n', re.S)


def load(md_dir):
    """One Document per page. The YAML header becomes metadata."""
    root = pathlib.Path(md_dir)
    docs = []
    for path in sorted(root.rglob('*.md')):
        if '_report' in path.parts:
            continue
        text = path.read_text(encoding='utf-8')
        m = FRONT.match(text)
        meta = yaml.safe_load(m.group(1)) if m else {}
        # Vector stores want flat values.
        meta['breadcrumb'] = ' > '.join(meta.get('breadcrumb') or [])
        meta['file_path'] = path.relative_to(root).as_posix()
        doc = Document(text=text[m.end():] if m else text, metadata=meta)
        # These values do not help the embedding.
        doc.excluded_embed_metadata_keys = [
            'source', 'page_id', 'file_path', 'product', 'version']
        docs.append(doc)
    return docs
```

## Sphinx builds (`sphinx2md.py`)

```sh
python sphinx2md.py SOURCE_DIR OUT_DIR [--product NAME] [--version V]
                    [--source-prefix PATH] [--jobs N] [--only PAGE ...]
```

`SOURCE_DIR` is one Sphinx HTML build (the folder with `searchindex.js`) or a folder that holds several builds. Give the common folder when the builds link to each other. The options are the options of `flare2md.py`. All Markdown rules below (escapes, emphasis, tables, lists, links, images, reports) are the same.

What is special to Sphinx:

- **Tested builds.** We tested the tool on the ADS 2025 builds (a Keysight theme). We also tested it on one test project. Its builds use Sphinx 9.1 (alabaster, furo, pydata, Read the Docs and classic themes) and Sphinx 4.5 (alabaster and classic). Other Sphinx 9.1 builds use the `dirhtml` builder and a split index. We also converted six real builds from other projects. They are the Python 3.11 documentation (Sphinx 4.5, 499 pages), Open MPI, PMIx, PRRTE, libtiff and lcov. Their themes include Read the Docs and nature. We also built the documentation of attrs (furo, MyST), click, itsdangerous and Jinja (pallets themes) from their source packages. The same is true for pytest (furo, 292 pages), Sphinx itself (sphinx13, 216 pages), Flask, Werkzeug and requests, and for a `singlehtml` build of Flask. Older Sphinx versions (1.8, 2.4 and 3.5) built the test project and the requests docs. A review agent built the docs of the pydata, book, sphinx-design, MyST, togglebutton and copybutton projects, with their kitchen-sink pages. Review agents made test builds in eight languages and with more themes (book, shibuya, piccolo, immaterial, sphinxawesome) and extensions (nbsphinx, imgmath, mermaid, sphinx-inline-tabs). The checker gives exit 0 on all of them.
- **Pages.** The tool reads Sphinx pages with `html5lib`, which uses the HTML5 parsing rules of a browser. The lxml parser would end a `<dt>` at a `<dl>` inside it (a signature with one parameter per line). Python's `html.parser` does not know implied end tags (raw HTML such as `<li>one<li>two`). A build can use the suffix `.htm`. A page is a topic if it has an article. The tool looks for the article in this order: `itemprop="articleBody"`, `div.body`, `article.bd-article`, `article[role=main]`, then `role="main"`. The tool skips `genindex.html` (also the split index, `genindex-A.html`), `search.html` and `*-modindex.html` of each build. It also skips their `dirhtml` form (`genindex/index.html`). It also skips the files in `_static` and `_sources`. A link to a skipped page keeps only its text. A link to a folder (`dirhtml`) points at the `index.md` of the folder.
- **API objects.** An object of any domain (Python, C, C++, JavaScript, std and others) becomes a heading and a code block. The tool finds an object by the term class `sig-object` (Sphinx 4 and later) or by `descname` (older Sphinx). The heading holds the name in code: the name parts of the last signature line. (A C++ template line comes first.) For Python, the `id` is the full name, and the heading uses it if it ends with the shown name. Sphinx changes some ids. Examples are `id0` for a second description of one object, and `cafe` for `café`. The code block holds the signature in the language of the domain (`python`, `c`, `cpp`, `javascript`). The domain class is on the `<dl>`, or on the first `<dt>` (`cpp:alias`). A `describe` or `object` description gets no language. The arrow `→` before the return annotation becomes `->`. An arrow in a default value stays. A line break stays, and a signature with one parameter per line keeps those lines with a 4-space indent. The `[source]` link (viewcode) is removed. The heading level is one below the heading of the section, plus one for each object that holds it. The maximum level is 6. A heading in the description of an object (for example `Example:`) goes one level below the object heading, so it does not end the object. Docutils writes `<h7>` and deeper for deep sections. These become level 6. More signatures of one object (overloads) become more code blocks under one heading. A link in a signature keeps only its text, because a code block cannot hold links. An example is a type that links to the Python docs.
- **Field lists** (`Parameters:`, `Returns:`) and other definition lists: the term becomes a bold paragraph with its links and code, and the definition follows it. CSS shows ` : ` before a classifier (`width : int, optional`, as in numpydoc). The tool writes it. Sphinx writes a parameter type as emphasis in parts, with links between them. The tool makes one emphasis of it: `(*list[[type](…), Attribute]*)`.
- **Sphinx 4 and older** let CSS add some text. The tool writes this text: the brackets of a footnote reference (`1[2]`) and the colon after a field name (`Returns:`).
- **Admonitions** (note, warning, todo, see also) become block quotes with the bold title (`> **Note**`). A title (also of a rubric, a topic, a sidebar, a code caption and a `<summary>`) keeps its links, code and math.
- **Tabs** (sphinx-tabs, sphinx-design, sphinx-inline-tabs). The browser shows one tab at a time. The Markdown has all of them: each tab label is a bold paragraph before its panel. Tabs in raw HTML (a tab list of links) stay as a list. A sphinx-design card title is a bold paragraph too. The link of a clickable card covers the card, and its text is hidden. The card title gets the link: `**[Title](url)**`. myst-nb shows one label of a hide toggle ("Show code cell source"). The tool keeps that label only. The book theme shows a sidenote in an admonition outside the admonition, and CSS hides the copy inside. The tool removes the hidden copy.
- **Lists with letters or roman numbers** (`a.`, `i.`): Markdown numbers an ordered list with digits only. Such a list becomes a bullet list, and each item starts with its label (`- a. Text`). CommonMark allows 9 digits at most, so a list with larger numbers becomes a bullet list with its numbers as labels too. A MyST task list keeps its state (`- [x] Done`).
- **Code.** A highlighted block (`div.highlight-<lexer>` that holds only the code) becomes a fenced block with its Pygments language. Its no-break and zero-width spaces stay, and so do the spaces at line ends, because the code comes from source files. (In the Flare help, they become spaces or are removed.) Line numbers are removed. `default` is `python` only if Pygments found Python tokens. Without tokens, Sphinx showed the code as plain text, so the block gets no language. A doctest block (or a block that starts with `>>> `) is `pycon`. A code caption becomes a bold paragraph before the block. On a viewcode page (`_modules`), the tool removes the `[docs]` links and the newlines that `sphinx.ext.viewcode` adds. Thus the code is the source file. A mermaid diagram (`pre.mermaid`) is a `mermaid` code block. nbsphinx shows the prompt of each notebook cell (`[1]:`) in its own code block. The tool removes these prompts.
- **Math** keeps its LaTeX without the `\(` `\)` and `\[` `\]` delimiters. With MathJax, the LaTeX is text. With imgmath, it is the alt text of the image, and the tool does not copy the image. Inline math becomes `` $`a^2`$ ``, and a math block becomes a `math` code block. GitHub shows both forms as math, and CommonMark shows the LaTeX as code with no escapes. An equation number (`(1)`) is a paragraph after its block, because `:eq:` references show it.
- **Old Sphinx versions.** Sphinx 1.8 writes field lists, option lists and the docinfo as tables (the HTML4 writer). The tool converts them as the `<dl>` that newer versions write, so the Markdown is the same. In Sphinx 3.5 and older, a figure caption is a `p.caption`. It stays a plain paragraph after the image. Sphinx 2.4 and older give an object no domain class. There the tool finds C objects by their id (`c.add`) and C++ objects by `_CPPv`. Options and environment variables get no language.
- **Footnotes and citations** become one paragraph each, `[1] Text`. The tool reads the markup of all docutils versions (`aside`, `dl` and `table`). The label does not hold the links back to the references (`(1,2)`).
- **Downloads.** A link to a local file (for example `:download:`) points at a copy of the file in the output. A copied `.md` file (a MyST source) gets the name `<name>.md.txt`. Thus it is no page in the output, and it cannot replace a converted page.
- **Forms.** A form in an article (raw HTML) is content, so its text stays. The tool removes the input controls (`input`, `select`, `textarea`). The browser does not show a `<title>` in the body, so the tool removes it. An icon (`<svg>`) has no text, and the tool removes it. An abbreviation gets its expansion: `LIFO (last-in, first-out)`. In a signature or in code it does not: the `*` and `/` separators of a Python signature are abbreviations too. The browser does not show `<noscript>` or `<template>`, so the tool removes them. An embedded page (`<iframe>`, for example a YouTube video) becomes a link with its title, or else with its URL.
- **Other markup.** A docutils block quote is an indent, so the tool removes the quote and keeps the content. An epigraph, a pull quote and highlights stay block quotes. A rubric, a toctree caption, a topic or sidebar title, a table caption and a `<summary>` become bold paragraphs. A table caption goes before its table. A `contents` directive is a list of anchor links to the headings of the page. Markdown has no anchors, so the tool removes the list, as the Flare converter removes "On this page". A figure is a paragraph with its image, and the caption follows. An image whose alt text is its file path gets the file name as alt text. An SVG diagram in an `<object>` (graphviz, inheritance diagram) becomes an image. The browser does not show the fallback text in the `<object>`, so the tool removes it. The tool also removes the anchor icons (`¶`). Some themes put the previous and next links in the article (sphinx13 `div.related-pages`). The tool removes all `role="navigation"` parts, and each `<nav>` in the article (the breadcrumb bar of sphinx-nefertiti).
- **YAML header.** `title` is the text of the first `<h1>` of the article, because Sphinx makes `<title>` from it. (The haiku theme has another `<h1>` in its page header.) A page without `<h1>` uses the part of `<title>` before the last ` — ` (furo: ` - `, sphinxawesome: ` | `). If that part is the Sphinx placeholder `<no title>` (also translated), the title is the document name. `breadcrumb` comes from the theme's breadcrumb bar, without its "View page source" link. If there is no bar, it comes from the "related" bar of the basic layout. Examples are the classic and nature themes and the Python docs. This bar holds the parents (`nav-item-1`, `nav-item-2` ...), and the tool puts the project name first. Else it comes from the sidebar: the project name, then the `toctree-lN` items that are marked `current`. If the theme has none of these, it comes from the toctrees. It holds the titles of the pages on the toctree path from the root `index.html` to the page. Each title is the `<h1>` of the article of that page. `page_id` is the Sphinx document name, also for the `dirhtml` builder (`guide/install`, not `guide/install/index`). `version` is the build's `VERSION` (from `_static/documentation_options.js`) unless `--version` is given. `project` is the text of the home link, or else the project part of `<title>` that the pages of the build share ("Kit 3.0 documentation" gives `Kit`). If the titles have no project part (`html_title = ""`, or a `singlehtml` build of one page), it is the `# Project:` line of `objects.inv`. The tool removes "documentation" in any case and a version that agrees with the build version: "Click Documentation (8.5.x)" gives `Click`. A translated build (`LANGUAGE` in `documentation_options.js`) has a translated `<title>`, for example "Kit 3.0 ドキュメント" or "Documentation Kit 3.0". The tool knows the 40 translations of Sphinx 9.1. The project part is the part after a separator that most titles share, and of those the longest. Thus a project name can hold a separator ("Foo - Bar"). The root page is the page that starts the next/prev chain of the build (`root_doc`, also when it is not `index`). A build in a folder of another build has its own project, root page and breadcrumbs. A page that no visible toctree lists gets the project as its breadcrumb. `product` is the project name unless `--product` is given.

## What the tool reads

- `Data/HelpSystem.xml`: the TOC file (`Toc`) and the search page (`SearchUrl`, which is skipped).
- The TOC (`Data/Tocs/<name>.js` and its chunk files): the breadcrumb of a page that has no `data-mc-toc-path`.
- Each `.htm` or `.html` file below `SOURCE_DIR`. A file is a topic if it has `div#mc-main-content` or if it is in the TOC. Other files (frames such as `Default.htm`) are skipped and listed in `_report/skipped.txt`.

## Output

- `<page>.md` for each topic, with this YAML header:

  ```yaml
  title: Set the Number of Harmonics
  source: doc/ads/Content/ads2025/cktsimhb/Set_the_Number_of_Harmonics.html
  breadcrumb: [Home, Simulation, Simulation-Analog RF, Harmonic Balance Simulation, ...]
  section: cktsimhb
  page_id: '861433733'
  product: Keysight ADS
  version: '2025'
  ```

  `section` is the first folder of the page below the topic folder. The topic folder is the deepest folder that holds all TOC pages, for example `Content/ads2025` if `SOURCE_DIR` is the help root.
- The images that the pages refer to, at the same relative paths.
- `_report/pages.csv`: counts for each page, such as word recall, tables, code blocks, notes and images.
- `_report/missing_images.txt`, `errors.txt`, `skipped.txt`, `stale.txt` and `collisions.txt`. `collisions.txt` lists pages whose `.md` paths differ only in case or in the Unicode form of a letter (`café` in NFC and NFD, `ß` and `ss`). macOS and Windows store such paths as one file.

YAML values that a YAML 1.2 reader reads as numbers (`1e3`, `0o17`) are quoted.

All text files are UTF-8 with `\n` line ends on every platform.

## Conversion rules

- **Tables.** A simple table becomes a pipe table. Some tables stay as a clean HTML `<table>`. These are tables with merged cells, nested tables, code, headings or nested lists in cells, and tables with more than one header row. A cell line cannot show a nested list, and a pipe table has one header row. A row is the header only if the source marks it so. It has `<th>` cells, or all-bold text (also in paragraphs) above a row that is not all bold. In a list of bold names, all rows are bold, and the first row is data. Otherwise the table gets an empty header row. A table with no cells is removed, because a browser shows nothing. Full-width title rows move above the table. `<tfoot>` rows go last. If all rows of a table move out, its caption becomes a paragraph before them.
- **Raw HTML tables** keep the indent or quote prefix of their place, so a table inside a list item or a note stays there. A `<div>` next to other content becomes a `<p>`, so its text does not join the text around it. A blank line would end the HTML block. Thus, in a code block inside such a table, a line end that closes a blank line becomes `&#10;`. A `#` at the start of a code line there becomes `&#35;`, so that a heading splitter does not take the line for a heading.
- **Notes.** Note, Caution and Warning boxes become `> **Note:**` block quotes.
- **Definition lists** (`<dl>`) become a bold paragraph for each term, followed by its definition. The Sphinx converter uses the same rule.
- **Code.** `<kbd>`, `<samp>` and `<tt>` are inline code. A code panel becomes a fenced code block, with its title in bold. Code text is kept exactly, including blank lines. The spaces at line ends are removed (help editors add them). Code that holds three or more backticks gets a longer fence. Inline code with line breaks becomes a code block if it is the whole paragraph. Otherwise it becomes one code span for each line. Two code spans that touch become one span, also when a removed link was between them (`` `a``b` `` would show the backticks). A code span at the start of a line can have a three-backtick delimiter. That is no fence, because a fence line has no backtick after the fence (CommonMark). A heading splitter finds fences only at the start of a line. Thus, a fence at the start of a list item starts on the line after the bullet. In the first item of a nested list, the bullet line holds `<!-- -->`. An empty `-` under the text of the parent item would make that text a heading.
- **Equations.** The source has equations as images only. They stay as images with the alt text `equation`.
- **Superscript and subscript** stay as `<sup>` and `<sub>`.
- **Lists.** Text after a nested list in the same item becomes a paragraph of the outer item. Without it, Markdown joins the text to the last nested item. A list that directly follows a list of the same kind (also through wrapper `<div>` elements) gets an empty `<!-- -->` comment before it. Without it, Markdown joins the two lists, and it numbers a second ordered list on from the first. Empty code blocks are dropped.
- **Escapes.** The tool escapes text that Markdown would misread. Examples: `\<library name>`, `2\. Click`, `\===`, `\[Default]: none` (a link definition, also when it is split over elements), `1\. Click` split over elements, `\~~~`, `\-- -`, `## Port \#`, `\&ohm;`, `a\*b`, `\_x\_`, `x[1\](y)` and a source backslash at the end of a text. The tool also escapes `[` and `]` in link text, and `[`, `]` and `\` in image alt text. It escapes `|` in a pipe-table cell. A pipe-table cell holds inline content only, so it gets no escapes of block markers (`-`, `1.`, `#`, `>`, `=`). Raw HTML tables need no escapes and get none. A `<` before a letter is escaped also when the letter is in the next element (`List\<int>`). GFM also reads `~~x~~` and `~x~` as strikethrough. If a text has two runs of one or two tildes, the tool escapes them (`DQ0\~DQ7`). `[x]` at the start of a list item would be a GFM task box, so it becomes `\[x]`. A real checkbox (a MyST task list) stays `[x]`. A `!` just before a link would make it an image, so it becomes `\!` (`Go\![page](x.md)`). A line that would be a table delimiter row becomes `\-|-` or `\|:--|`. In strikethrough text, each `~` is escaped (`~~a\~b~~`). An empty element before the text (`<font></font>2. Click`) does not stop the escape at the line start.
- **Emphasis.** Emphasis inside emphasis of the same kind, empty emphasis and emphasis that holds a block are removed, because Markdown would show them wrongly. Touching emphasis of the same kind is merged (`<b>a</b><b>b</b>` gives `**ab**`), also across zero-width spaces and comments. Touching strikethrough is merged too (`~~ab~~`). Two `<br>` in a row make a blank line, which ends emphasis or a link in Markdown. Thus they split the element: `<b>Title<br><br>Body</b>` gives `**Title**` and `**Body**` as two paragraphs. Emphasis that `*` cannot mark is written as HTML. Examples are punctuation inside and a letter outside (`<strong>Note:</strong>Text`), and emphasis that touches emphasis of the other kind (`<strong>Note:</strong>*text*`).
- **Links.** Links between pages point at the `.md` files. Paths are percent-encoded (`abs%28%29.md`). Old `qthelp://x/doc/<path>` links point at the local page if it exists. Dead links (`createpage.action`, `#` anchors, `file:`, drive paths such as `C:\x.pdf`, `javascript:`) keep only their text. A link whose text is its URL is a normal `[url](url)` link, not an autolink. `|` in a URL is written `%7C`, so it does not end a table cell. A link with a URL scheme (`https:`, `mailto:`, `tel:`, `ircs:` and others) keeps its URL. A `\` in a URL would be an escape. In the path of an `http`, `https` or `ftp` URL it becomes `/`, as a browser reads it. In other places it becomes `%5C`. An `&` before an entity name and `;` (`a&amp;b.png`) becomes `%26`, because Markdown decodes entities in a link. Image paths lose a query string (`a.png?version=1`). An image in a `data:` URI (base64 text in the page) becomes its alt text. A `<video>` or `<audio>` becomes a link to its file, with the file name as text. A link without the page suffix (Sphinx `html_link_suffix = ''`, also for a name with dots such as `pkg.google`) points at the page if `<name>.html` exists. Links to other local files (PDF, images) are percent-encoded too. A link that holds blocks (a card of `<div>` elements) becomes one link with its text on one line. A Markdown link holds inline text only. macOS and Windows find `GUIDE.html` for `guide.html`, and macOS finds a name in NFD for one in NFC. Other systems do not. Thus a link or image path gets the name of the file on the disk. A path can leave `SOURCE_DIR` and come back (`../a/x.png` in `a`). A `\` in a path is read as `/`, and a path that starts with `/` starts at the help root. A query string (`?q=1`) does not stop a page link from pointing at the `.md` file. A link or emphasis that is the whole content of inline code moves outside it (`` [`x`](url) ``, ``*`x`*``). A link or emphasis on only part of the code keeps only its text, because a Markdown code span holds only text.

## Checks

`verify.py` checks the output of both converters. It shares no code with `flare2md.py` or `sphinx2md.py`. For a Sphinx page, it first writes the API objects of the source page as the rules above say (heading and signature). The generated Sphinx pages are no topics, and links to them do not count. The checker renders each `.md` file with markdown-it (CommonMark and GFM tables) and compares the result with the source page. It compares these items:

- the words, and the punctuation that escaping touches,
- the counts of tables, images, code blocks, links and list items,
- the heading sequence,
- the numbers in the text and the numbers of ordered list items, exactly,
- the text of each code block (in Sphinx exactly, in Flare without the spaces at line ends, and a signature without its spaces),
- the target of each link and image: the page or file, or the URL as a browser reads it,
- the YAML `title` (Flare: `<title>`, Sphinx: the `<h1>` of the article) and an added H1, and the Flare breadcrumb (`data-mc-toc-path`).

It also checks the YAML header, links and image paths, and it reports a build whose pages do not agree on `project`. A link target must be a file with exactly the name in the link, also on a disk that ignores case. The checker reads the encodings that the converter reads. It renders GFM strikethrough too, so an unescaped `~~x~~` shows as lost `~` characters. It reads the text of a code block as one run, because a highlight span can end inside a word. The exit status is 1 if a hard check fails. These are the hard checks:

- no `.md` files at all,
- a topic page (one with `div#mc-main-content` or a Sphinx article body) without a `.md` file,
- a `.md` file without a source page, or a file that cannot be checked (also a source page that cannot be read),
- bad YAML (also a Sphinx page without `project`), or an unbalanced code fence,
- a lost image (also a copied image file that is gone),
- an empty code block or link,
- a list item that is lost or shows a different number or nesting depth than in the source.

The checker compares Unicode words, so lost characters such as `Ω` show in the recall. It identifies a list item by its own text, without nested items and tables, and without spaces. The text of a full-width table row counts as item text, because the converter moves those rows out of the table. Link and image targets come from the rendered page, so the checker also checks a link around an image. A link in an SVG drawing (an image map) has no text. The checker expects its URL as the text, because the converter writes the URL. On a viewcode page, the checker removes the line breaks of the `[docs]` markup with its own rule.

The checker reports the words that its hidden-content rules remove, for each rule. A rule that the checker and the converter share would hide a loss from the word check, so read this line. It also reports HTML pages with 20 or more words that have no main content and no `.md` file. A redirect page (`<meta http-equiv="refresh">`) does not count.

The other numbers are a report to read. Some differences come from the source itself, for example a missing image file in the install.

## Limits

- The tool reads Flare WebHelp2 output only. It does not read Flare project sources or other help formats.
- Breadcrumbs, `section` and `qthelp://` links need the help system files. Without `Data/HelpSystem.xml`, breadcrumbs come only from the pages.
- An equation has no LaTeX source in the help, so the Markdown cannot give one.
- Line ends in pages become LF before parsing, as the HTML standard says. Thus the result does not depend on the parser. (The lxml wheels for Windows, which an earlier version used, keep CR.)
- Pages are read as UTF-8 or UTF-16. A page that is not valid UTF-8 is read in the charset that its `<meta>` declares, or else in cp1252. As in browsers, `iso-8859-1` and `us-ascii` are read as cp1252.
- Images and pages outside `SOURCE_DIR` are not copied or converted. A reference to them is listed as missing. Give the folder that holds them (for example the help root) as `SOURCE_DIR`.
- Text between two `$` signs (shell variables such as `$HOME/x … $PATH`) stays as it is. Some renderers (GitHub, pandoc) read it as a math formula. Math is not part of CommonMark or the GFM spec, and escapes would add backslashes to many paths.
- Links to anchors on the same page (`#name`, also `?tab=1#name`) keep only their text. Links to another page lose the anchor (`page.md`, not `page.md#name`), because Markdown renderers make their own heading anchors.
- If `Data/HelpSystem.xml` or the TOC cannot be read, the tool prints a warning and takes breadcrumbs only from the pages.
- On Windows, output paths longer than 260 characters need the Windows long-path setting (`LongPathsEnabled`).
- A pipe table has no column alignment (`text-right` cells), and a line block (`| text`) becomes one paragraph for each line, without its indent. The text stays.
- Content that inline CSS hides (`style="display:none"`) stays, because page CSS can show it again (the xarray HTML view).
- An element with the `hidden` attribute stays, because JavaScript can show it. sphinx-tabs hides each closed tab panel in this way.
- In Sphinx 2.4 and older, a JavaScript object has the markup of a Python object. Its signature gets the language `python`.
- The reports go to `OUT_DIR/_report`. If `SOURCE_DIR` has a `_report` folder, its pages go there too, and the tool prints a warning.
- In a Flare page, an ordered list with numbers of more than 9 digits stays an ordered list. CommonMark does not read it as a list. (The Sphinx converter makes it a bullet list.)
- In a Flare page, a link to a skipped page points at a `.md` file that does not exist. Examples of skipped pages are frame pages and the search page. The checker reports it as `md_broken`.
- A page that is valid UTF-8 is read as UTF-8, also if its `<meta>` declares another charset. A browser would use the declared charset.
- Lists or `<div>` elements nested more than about 250 levels deep give a `RecursionError` for that page. The page is listed in `errors.txt`.
