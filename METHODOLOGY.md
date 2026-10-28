# Method: ADS 2025 documentation to Markdown for RAG

This document tells how the ADS 2025 help and the ADS Python API documents became Markdown files, and how we checked the result. It uses the ASD-STE100 writing style (short sentences, active voice).

## 1. Summary

We converted two document sets into Markdown files for a RAG system:

| Set | Source format | Pages | Images | Output folder |
|---|---|---|---|---|
| ADS 2025 help (`doc/ads`) | MadCap Flare WebHelp2 HTML | 7,401 | 12,433 | `ads2025/` |
| ADS Python API (`ael`, `ann`, `dataset`, `dds`, `de`) | Sphinx HTML | 158 | 10 | `ads2025-python/` |

Each source page gives one Markdown file. Each file starts with a YAML header that tells the title, the source path and the place of the page in the document tree. The converters copy the images next to the Markdown files.

An independent checker compares each Markdown file with its source page. It finds no hard failures in the two sets. Both converters give byte-identical output on macOS (Python 3.10, 3.11 and 3.13). We also ran an earlier version of `flare2md.py` (with the lxml parser) on Windows 11 (Python 3.12), and its output was byte-identical to the macOS output of that version.

## 2. Terms

| Term | Meaning in this document |
|---|---|
| RAG | Retrieval-augmented generation. A language model gets text parts from a search index and uses them in its answer. |
| Markdown | A plain-text format for documents. We use CommonMark with GFM tables. |
| CommonMark | The exact specification of Markdown that we follow. |
| GFM | GitHub Flavored Markdown. We use only its pipe tables. |
| YAML header | The block between two `---` lines at the start of each file. It holds the page data. |
| Flare WebHelp2 | The HTML help format of MadCap Flare. The ADS help uses it. |
| Sphinx | The documentation tool of the Python API documents. |
| DOM | The tree of HTML elements that a parser makes from a page. |
| Converter | `flare2md.py` or `sphinx2md.py`. A converter writes the Markdown files. |
| Checker | `verify.py`. It reads the source pages and the Markdown files and compares them. |
| Hard failure | A checker result that makes the checker exit with status 1. |

## 3. Decisions

The user made these decisions before the work started:

1. The job is to parse only. We do not cut the pages into chunks. A later step can make chunks, for example with the LlamaIndex `MarkdownNodeParser`, which cuts at headings.
2. The converters copy the images next to the Markdown files. Image links are relative paths.
3. We do not use LlamaParse. It is a cloud service, and an upload of the documents can be a licence risk.

We also made these design decisions:

1. We wrote a small converter for each source format. One shared Markdown engine holds all the Markdown rules.
2. The checker shares no code with the converters. Thus one error cannot be in both programs.
3. We fixed all package versions, because the output depends on the rules of `markdownify`. The packages are pure-Python copies in `flare2md/_vendor/` (`vendor.txt` lists the versions), so a conversion needs no install. The converters write the YAML header with their own small writer, which follows the rules of PyYAML, and `verify.py` reads it with its own small reader. Only the check tools need an install: `markdown-it-py`. Both converters read pages with `html5lib`, because only an HTML5 parser reads Sphinx signatures and raw HTML the way a browser does. html5lib is pure Python, so it can be vendored. An earlier version read the help pages with lxml (a C library). The change to html5lib changed 15 help pages only: html5lib adds a `<tbody>` to each raw HTML table that has none in the source, as a browser does.
4. The output layout is the source layout. Thus a relative link in the source stays valid in the output.

## 4. A worked example

This example shows one API object from the Sphinx source (`dds/.../reference/dds/point.html`). The HTML has more `<span>` elements than shown here.

```html
<dl class="py method">
  <dt class="sig sig-object py" id="keysight.ads.dds.Point.astuple">
    <span class="sig-name descname">astuple</span>()
    <span class="sig-return">→ tuple[int, int]</span>
    <a class="headerlink" href="#keysight.ads.dds.Point.astuple">¶</a>
  </dt>
  <dd></dd>
</dl>
```

The converter does these steps:

1. It removes the anchor icon (`¶`).
2. It reads the full name from the `id` attribute.
3. It sets the heading level. The class heading is level 2, so the method heading is level 3.
4. It writes the signature text in a Python code block. The arrow `→` becomes `->`.

The result is:

````markdown
### `keysight.ads.dds.Point.astuple`

```python
astuple() -> tuple[int, int]
```
````

The checker makes the same change to its copy of the source page. Then it compares the words, the headings and the counts of the two pages.

A Flare note box shows a second rule. The source is a two-cell table with an icon (`note.svg`) and the text. The converter writes a block quote (the text is an example):

```markdown
> **Note:**
>
> Install the license before you start ADS.
```

## 5. Conversion method

The converters do these steps for each page:

1. **Find the pages.** The converter reads all `.htm` and `.html` files below the source folder.
   - **Flare:** a page is a topic if it has `div#mc-main-content` or if it is in the TOC.
   - **Sphinx:** a page is a topic if it has an article body. The index page, the search page and the module index of each build are not topics.
2. **Read the page.** The converter reads UTF-8 and UTF-16. Some pages are UTF-16 text behind a UTF-8 BOM, and the converter fixes them. The converter reads a page that is not valid UTF-8 in its declared charset, or in cp1252. Line ends become LF before parsing, as the HTML standard says.
3. **Find the main content.** The converter uses only the content area. The navigation, the page header and footer, the scripts and the theme text stay out.
4. **Clean the DOM for the format.**
   - **Flare:** note boxes become block quotes. Code panels become code blocks. Equation images get the alt text `equation`.
   - **Sphinx:** API objects of all domains become headings and signatures. Field lists become bold terms. Admonitions become block quotes. Highlighted code becomes code blocks with their language. Math keeps its LaTeX, and footnotes become paragraphs. The Sphinx rules also read the markup of other themes and of Sphinx 4 (see the README).
5. **Apply the shared Markdown rules.** See section 6.
6. **Write the file.** The converter writes the YAML header and the Markdown body as UTF-8 with LF line ends.
7. **Copy the images.** The converter copies each image that a page refers to, at the same relative path.
8. **Write the reports.** The `_report/` folder holds the statistics of each page and the list of missing images. It also lists the errors, the skipped pages, the stale files and the case collisions.

## 6. Shared Markdown rules

Markdown can misread text that looks like Markdown syntax. The shared engine prevents this with these rules:

1. **Escapes.** The engine escapes text that Markdown would read as syntax. Examples are `2\. Click`, `\<library name>`, `a\*b`, `\[Default]: none` and `## Port \#`.
2. **Emphasis.** Markdown cannot show all HTML emphasis. The engine merges emphasis that touches emphasis of the same kind. It removes empty emphasis and emphasis that holds a block. If `*` cannot mark the emphasis (for example `<b>Note:</b>Text`), the engine writes HTML `<strong>`.
3. **Tables.** A simple table becomes a pipe table. A table with merged cells, nested tables, code or headings in cells stays as clean HTML. Full-width title rows move out of the table.
4. **Lists.** A comment separates two lists of the same kind, because Markdown would join them. Text after a nested list becomes a paragraph of the outer item.
5. **Code.** Code text stays exactly as in the source. Code with three backticks gets a longer fence.
6. **Links.** Links between pages point at the `.md` files. Paths are percent-encoded. A dead link keeps only its text. A link to a page that is not converted keeps only its text too.
7. **Images.** Image paths are relative and percent-encoded. An empty alt text gets the file name.

## 7. Check method

We used seven checks. Each check can find errors that the other checks do not find.

1. **Unit tests.** 180 tests cover the rules for both formats, the command line, the checker and the image list (`images_csv.py`). We ran all tests after each change. We also undid each fix of round S6 and of the RAG review in a copy of the tools. Each time, a test failed.
2. **Independent checker.** `verify.py` renders each Markdown file with markdown-it and compares the result with the source page. It compares:
   - the words (recall, precision and order),
   - the punctuation that escapes touch,
   - the counts of tables, images, code blocks, links and list items,
   - the heading sequence,
   - the number and the nesting depth of each list item.

   It also checks the YAML header, the links and the image files. A lost item, a lost image, a bad YAML header or an unbalanced code fence is a hard failure.
3. **Second Markdown engine.** We also rendered all pages with pandoc and compared the text with markdown-it. This check finds Markdown that two engines read differently.
4. **Output comparison.** After each change, we compared the new output with the old output. We read each changed page and compared it with the source.
5. **Repeatability.** We ran the converters with Python 3.10, 3.11 and 3.13. The earlier lxml version also ran in one process (`--jobs 1`), with Python 3.9 and on Windows. The output must be byte-identical each time.
6. **Bug hunts.** We did ten rounds of bug hunts on the Flare converter. In each round we searched for defects. In three rounds, independent review agents also tried to break the converter with small test pages. Then we fixed the defects, added tests and ran all checks again. The work stopped when a check found a new real defect, and the user decided the next step.
7. **Other Sphinx builds.** The ADS Python builds use one theme. To make `sphinx2md.py` work on other Sphinx documents, we built one test project with Sphinx 9.1 and 4.5, five themes, the `dirhtml` builder and a split index. Then we converted and checked these builds. We also converted six real builds of other projects: the Python 3.11 documentation, Open MPI, PMIx, PRRTE, libtiff and lcov. We built nine more from their source packages: attrs, click, itsdangerous, Jinja, pytest, Sphinx itself, Flask, Werkzeug and requests. We also built them with Sphinx 1.8, 2.4 and 3.5, and as one `singlehtml` page. Review agents also made test builds with common extensions (sphinx-design, sphinx-tabs, MyST, numpydoc, nbsphinx, imgmath), more themes and eight languages.

## 8. Defects found by the checks

The checks found these classes of defects. We fixed all of them, and a test covers each fix.

| Check that found it | Defect | Fix |
|---|---|---|
| Checker (word recall) | Escapes, emphasis and code lost text or showed wrong text | Escape and emphasis rules (section 6) |
| Checker (list numbers) | Markdown joined two lists, and the second list had wrong numbers | A comment between the lists |
| Checker (list items) | Text after a nested list joined the last nested item (14 pages) | The text becomes a paragraph of the outer item |
| Second engine | An empty strikethrough became the text `~~\~~` | The engine removes empty strikethrough |
| Windows run | The Windows lxml build keeps CR characters. Code blocks got an extra blank line (219 pages). | Line ends become LF before parsing |
| Review agents | 33 latent converter defects that the pages did not show yet, and 19 checker and command-line defects | Rules and tests for each defect |
| Other Sphinx builds, real builds, review agents | 149 defects in `sphinx2md.py`, the shared engine and the checks. Examples are line numbers in code, lost multi-line signatures and theme navigation in the text. Others are C++ objects as plain text, a lost form with 808 words and joined numpydoc types (`widthint`). Round S3 found lost imgmath equations and wrong project names in translated builds. Round S4 found a page that took minutes and one bad file that stopped the whole run. It also found file names that work only on a disk that ignores case. Round S5 found the markup of old Sphinx versions, a MyST source copied as a page, and GFM strikethrough in plain text. The double check of round S5 found a defect in a new rule of that round: it removed the closed tab panels of sphinx-tabs. Four of them also changed the ADS Python output (23 lines). One changed 29 help pages: a table with a nested list in a cell is now an HTML table. In round S6, three review agents examined the engine, the Sphinx converter and the checker. They found 29 defects. Examples are a `!` before a link (Markdown reads an image), two `<br>` in bold text (a blank line ends the bold), and `&amp;` in a file name. The checker did not compare numbers, code text or link targets. The new checks found two more defects: back links in footnote labels and spaces in titles (`attrs : Classes`). The review of the output found three defects in new rules of that round. | Rules and tests for each defect |
| RAG review (LlamaIndex) | A fence on a bullet line (``4. ``` ``) put the heading splitter of LlamaIndex out of step. Thus, 37 headings in 4 pages did not start a node. A `#` line in a code block in an HTML table became a heading. Table cells had 50,627 escapes that had no function. | The fence starts on the next line. The `#` becomes `&#35;`. Cells get no escapes of block markers. A census compares the splitter headings with the markdown-it headings on all pages: 0 differences. |

We also found defects in the checker itself. For example, it identified list items by their full text, and that made false alarms. We fixed these defects too. Then we made sure that the checker still finds the real defects in the old output.

## 9. Results

**ADS 2025 help (`ads2025/`):**

- 7,401 pages, 0 errors, 12,433 images.
- 3,967 pipe tables and 310 HTML tables.
- 3,575 notes, 3,786 code blocks and 293 equation images.
- The checker gives 0 hard failures.

**ADS Python API (`ads2025-python/`):**

- 158 pages, 0 errors, 10 images.
- 3,052 API objects, 131 code blocks, 23 notes and 4 tables.
- The word recall is 0.9965 or more on each page.
- The checker gives 0 hard failures, and all counts agree with the source.
- pandoc and markdown-it show the same text on all pages.

## 10. Known source defects and limits

These items come from the source or from Markdown. They are not converter errors.

1. 38 image references in the help point at files that are not in the install.
2. One help page (`Setting_up_Basic_DDR4_Signal_Simulation_for_Compliance_Tests`) lost 8 characters in the source. They show as `�`.
3. The source truncated some examples, for example `Is=1.5e-` on the BJT page.
4. One help link points at `4%2A4_Butler_Matrix.html`, but the file is `4_4_Butler_Matrix.html`. The link stays broken, as in the source.
5. The help has equations as images only. The Markdown cannot give LaTeX.
6. A code block cannot hold links. Links in code and in API signatures keep only their text.
7. Links to anchors lose the anchor, because each Markdown renderer makes its own heading anchors.
8. Text between two `$` signs stays as it is. Some renderers (GitHub, pandoc) can read it as math.
9. Some help pages have mojibake in the source (for example `50Î©` for `50Ω`, in about 47 source files). The Markdown keeps it.
10. In the source, each `<sub>` and `<sup>` after text has a space before it (`10 <sup>-4</sup>`). A browser shows that space, so the Markdown keeps it.

## 11. How to run the work again

```sh
cd ~/ADS-docs-md/flare2md
python3 -m pip install markdown-it-py==4.2.0   # for verify.py only
python3 -m unittest discover -s tests   # Python 3.10 or later

python3 flare2md.py ~/ADS-docs-src/Content/ads2025 ../ads2025 \
    --product "Keysight ADS" --version 2025 --source-prefix doc/ads
python3 verify.py ~/ADS-docs-src/Content/ads2025 ../ads2025

python3 sphinx2md.py ~/ADS-docs-src-py ../ads2025-python \
    --product "Keysight ADS" --version 2025
python3 verify.py ~/ADS-docs-src-py ../ads2025-python
```

The checker must exit with status 0. `flare2md/README.md` gives all options and all conversion rules.
