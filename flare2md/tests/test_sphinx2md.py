"""Tests for sphinx2md. Run: python -m unittest discover -s tests -v"""

import contextlib
import importlib.util
import io
import os
import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import sphinx2md  # pylint: disable=wrong-import-position

PAGE = """<!DOCTYPE html>
<html><head><title>{title} — Mod Docs V 1 documentation</title></head>
<body><nav class="wy-nav-side"><a class="icon-home" href="{home}">Mod Docs</a>
<ul><li class="toctree-l1 current"><a class="reference internal"
href="#">Reference</a><ul><li class="toctree-l2 current"><a class="current
reference internal" href="#">{title}</a></li></ul></li></ul></nav>
<div role="main"><div itemprop="articleBody">{body}</div></div>
<div id="onthispage_wrapper">On this page</div></body></html>"""

API = """<section id="mod"><h1>Mod<a class="headerlink" href="#mod"></a>
</h1><p>See <a href="../genindex.html">Index</a> and
<a class="reference internal" href="other.html#x">Other</a>.</p>
<dl class="py class"><dt class="sig sig-object py" id="pkg.Point">
<em class="property"><span class="pre">class</span><span class="w"> </span></em>\
<span class="sig-prename descclassname"><span class="pre">pkg.</span></span>\
<span class="sig-name descname"><span class="pre">Point</span></span>\
<a class="headerlink" href="#pkg.Point"></a></dt>
<dd><p>A point.</p>
<dl class="py method"><dt class="sig sig-object py" id="pkg.Point.move">
<span class="sig-name descname"><span class="pre">move</span></span>\
<span class="sig-paren">(</span><em class="sig-param"><span class="n">\
<span class="pre">dx</span></span></em><span class="sig-paren">)</span> \
<span class="sig-return"><span class="sig-return-icon">→</span> \
<span class="sig-return-typehint"><a class="reference external" \
href="https://docs.python.org/3/library/constants.html#None">\
<span class="pre">None</span></a></span></span></dt>
<dd><p>Moves it.</p>
<dl class="field-list simple"><dt class="field-odd">Parameters<span
class="colon">:</span></dt><dd class="field-odd"><p><strong>dx</strong> –
the step</p></dd></dl>
<div class="admonition note"><p class="admonition-title">Note</p>
<p>Slow.</p></div>
<p class="rubric">Example</p>
<div class="doctest highlight-default notranslate"><div class="highlight">
<pre><span></span><span class="gp">&gt;&gt;&gt; </span>p.move(1)
</pre></div></div>
</dd></dl></dd></dl></section>"""

GUIDE = """<section id="guide"><h1>Guide</h1>
<blockquote><div><ol class="arabic simple"><li><p>One</p></li>
<li><p>Two</p></li></ol></div></blockquote>
<div class="literal-block-wrapper docutils container">
<div class="code-block-caption"><span class="caption-text">run.sh</span></div>
<div class="highlight-shell notranslate"><div class="highlight"><pre><span>
</span>ls -l
</pre></div></div></div>
<figure class="align-center"><a class="reference internal image-reference"
href="../_images/a.png"><img alt="../_images/a.png" src="../_images/a.png"/>
</a><figcaption><p>A figure</p></figcaption></figure>
<p>Text after.</p></section>"""


class SphinxTree:
    """Two small Sphinx builds below one folder."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / 'src'
        self.out = pathlib.Path(self.tmp.name) / 'out'
        build = 'mod/docs/html/'
        self.write(build + 'searchindex.js', 'Search.setIndex({})')
        self.write(build + '_static/documentation_options.js',
                   "const DOCUMENTATION_OPTIONS = {\n    VERSION: 'V 1',\n};")
        self.write(build + 'index.html', PAGE.format(
            title='Mod Docs', home='#', body='<h1>Mod Docs</h1>'))
        self.write(build + 'genindex.html', PAGE.format(
            title='Index', home='index.html', body='<h1>Index</h1>'))
        self.write(build + 'search.html', PAGE.format(
            title='Search', home='index.html', body='<h1>Search</h1>'))
        self.write(build + 'reference/mod.html', PAGE.format(
            title='Mod', home='../index.html', body=API))
        self.write(build + 'reference/other.html', PAGE.format(
            title='Other', home='../index.html', body='<h1>Other</h1>'))
        self.write(build + 'reference/guide.html', PAGE.format(
            title='Guide', home='../index.html', body=GUIDE))
        self.write(build + '_images/a.png', b'PNG')

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding='utf-8')

    def convert(self, **kwargs):
        config = sphinx2md.make_config(self.root, self.out, **kwargs)
        return sphinx2md.flare2md.convert(config, jobs=1)

    def md(self, rel):
        text = (self.out / 'mod/docs/html' / rel).read_bytes().decode('utf-8')
        head, body = text.split('\n---\n', 1)
        return head, body.lstrip('\n')

    def close(self):
        self.tmp.cleanup()


class SphinxTest(unittest.TestCase):

    def setUp(self):
        self.tree = SphinxTree()
        self.addCleanup(self.tree.close)
        self.counts = self.tree.convert()

    def test_generated_pages_are_skipped(self):
        self.assertEqual(self.counts['errors'], 0)
        self.assertEqual(self.counts['converted'], 4)
        self.assertFalse((self.tree.out / 'mod/docs/html/genindex.md').exists())
        self.assertFalse((self.tree.out / 'mod/docs/html/search.md').exists())

    def test_yaml_header(self):
        head, _ = self.tree.md('reference/mod.md')
        self.assertIn('title: Mod\n', head)
        self.assertIn('source: mod/docs/html/reference/mod.html\n', head)
        self.assertIn('- Mod Docs\n- Reference\n', head)
        self.assertIn("page_id: reference/mod\n", head)
        self.assertIn("version: V 1\n", head)
        self.assertTrue(head.endswith('project: Mod Docs'), head)

    def test_api_objects(self):
        md = self.tree.md('reference/mod.md')[1]
        self.assertNotIn('', md)
        self.assertIn('## `pkg.Point`\n\n```python\nclass pkg.Point\n```\n\n'
                      'A point.\n\n### `pkg.Point.move`\n\n```python\n'
                      'move(dx) -> None\n```\n\nMoves it.', md)

    def test_field_list_note_and_doctest(self):
        md = self.tree.md('reference/mod.md')[1]
        self.assertIn('**Parameters:**\n\n**dx** – the step', md)
        self.assertIn('> **Note**\n>\n> Slow.', md)
        self.assertIn('**Example**\n\n```pycon\n>>> p.move(1)\n```', md)

    def test_links(self):
        md = self.tree.md('reference/mod.md')[1]
        self.assertIn('See Index and [Other](other.md).', md)

    def test_quote_code_and_figure(self):
        md = self.tree.md('reference/guide.md')[1]
        self.assertIn('# Guide\n\n1. One\n2. Two\n\n**run.sh**\n\n```shell\n'
                      'ls -l\n```', md)
        self.assertIn('[![a](../_images/a.png)](../_images/a.png)\n\n'
                      'A figure\n\nText after.', md)
        self.assertTrue((self.tree.out / 'mod/docs/html/_images/a.png')
                        .is_file())

    @unittest.skipUnless(importlib.util.find_spec('markdown_it'),
                         'verify.py needs markdown-it-py (Python 3.10+)')
    def test_verify(self):
        import verify  # pylint: disable=import-outside-toplevel

        def run():
            with contextlib.redirect_stdout(io.StringIO()) as text:
                status = verify.main([str(self.tree.root), str(self.tree.out),
                                      '--jobs', '1'])
            return status, text.getvalue()

        status, text = run()
        self.assertEqual(status, 0, text)
        self.assertIn('heading sequence differs: 0', text)
        self.assertIn('links   src=      2 md=      2', text)
        (self.tree.out / 'mod/docs/html/reference/other.md').unlink()
        self.assertEqual(run()[0], 1)



ROUND1 = """<section id="r"><h1>R</h1>
<div class="highlight-python notranslate"><div class="highlight"><pre><span>\
</span><span class="linenos">1</span><span class="k">def</span> f():
<span class="linenos">2</span>    pass
</pre></div></div>
<div class="highlight-python notranslate"><table class="highlighttable"><tr>\
<td class="linenos"><div class="linenodiv"><pre>1</pre></div></td>\
<td class="code"><div class="highlight"><pre><span></span>x = 1
</pre></div></td></tr></table></div>
<div class="highlight-default notranslate"><div class="highlight"><pre><span>\
</span>$ run --now
</pre></div></div>
<p>Inline <span class="math notranslate nohighlight">\\(a_1^2\\)</span>.</p>
<div class="math notranslate nohighlight">
\\[E = mc^2\\]</div>
<p>See <a href="_downloads/abc/data.csv"><code>data</code></a>.</p>
<dl class="cpp class"><dt class="sig sig-object cpp" id="_CPPv4I0E3Box">\
<span class="k">template</span><span class="p">&lt;</span>\
<span class="k">typename</span> <span class="sig-name descname">T</span>\
<span class="p">&gt;</span><br/><span class="k">class</span> \
<span class="sig-name descname">Box</span><br/></dt><dd><p>A box.</p></dd></dl>
<dl class="py method"><dt class="sig sig-object py" id="m.f">\
<span class="sig-name descname">f</span><span class="sig-paren">(</span>
<dl><dd><em class="sig-param">a: int</em>,</dd>
<dd><em class="sig-param">b: str</em>,</dd></dl>
<span class="sig-paren">)</span><a class="reference internal" \
href="_modules/m.html#f"><span class="viewcode-link">\
<span class="pre">[source]</span></span></a></dt><dd><p>F.</p></dd></dl>
<aside class="footnote brackets" id="f1"><span class="label">\
<span class="fn-bracket">[</span><a href="#r1">1</a>\
<span class="fn-bracket">]</span></span><p>Note one.</p></aside>
<dl class="footnote brackets"><dt class="label" id="f2"><span \
class="brackets"><a class="fn-backref" href="#r2">2</a></span></dt>\
<dd><p>Note two.</p></dd></dl>
<table class="docutils footnote" frame="void" id="f3"><tbody><tr>\
<td class="label"><a class="fn-backref" href="#r3">[3]</a></td>\
<td><p>Note three.</p></td></tr></tbody></table>
</section>"""

PLAIN = """<!DOCTYPE html><html><head><title>{title} — Kit 2.0 \
documentation</title></head><body><div class="sphinxsidebar">x</div>
<main role="main"><article class="bd-article">{body}</article>
<footer class="prev-next-footer">next Other page</footer></main></body></html>"""


class SphinxRound1Test(unittest.TestCase):
    """Markup of other themes and Sphinx versions."""

    def setUp(self):
        self.tree = SphinxTree()
        self.addCleanup(self.tree.close)
        b = 'kit/html/'
        self.tree.write(b + 'searchindex.js', 'Search.setIndex({})')
        self.tree.write(b + '_static/documentation_options.js',
                        "VERSION: '2.0',")
        self.tree.write(b + 'index.html', PLAIN.format(
            title='Kit', body='<h1>Kit</h1><div class="toctree-wrapper '
            'compound"><ul><li><a href="guide/r.html#x">R</a></li></ul>'
            '</div>'))
        self.tree.write(b + 'guide/r.html', PLAIN.format(title='R',
                                                         body=ROUND1))
        self.tree.write(b + 'guide/_downloads/abc/data.csv', 'a,b\n')
        self.tree.convert()

    def md(self, rel):
        text = (self.tree.out / 'kit/html' / rel).read_bytes().decode()
        return text.split('\n---\n', 1)

    def test_code_without_line_numbers(self):
        md = self.md('guide/r.md')[1]
        self.assertIn('```python\ndef f():\n    pass\n```', md)
        self.assertIn('```python\nx = 1\n```', md)
        self.assertIn('```\n$ run --now\n```', md)  # No Python tokens.

    def test_math(self):
        md = self.md('guide/r.md')[1]
        self.assertIn('Inline $`a_1^2`$.', md)
        self.assertIn('```math\nE = mc^2\n```', md)

    def test_footnotes(self):
        md = self.md('guide/r.md')[1]
        for n, word in ((1, 'one'), (2, 'two'), (3, 'three')):
            self.assertIn(f'\n[{n}] Note {word}.\n', md)

    def test_other_domains_and_signatures(self):
        md = self.md('guide/r.md')[1]
        self.assertIn('## `Box`\n\n```cpp\ntemplate<typename T>\nclass Box\n'
                      '```', md)
        self.assertIn('## `m.f`\n\n```python\nf(\n    a: int,\n    b: str,\n'
                      ')\n```\n\nF.', md)
        self.assertNotIn('[source]', md)

    def test_theme_article_and_header(self):
        head, md = self.md('guide/r.md')
        self.assertNotIn('Other page', md)
        self.assertIn('breadcrumb:\n- Kit\n', head)
        self.assertIn('project: Kit', head)
        self.assertIn("version: '2.0'", head)

    def test_download_is_copied(self):
        md = self.md('guide/r.md')[1]
        self.assertIn('[`data`](_downloads/abc/data.csv)', md)
        self.assertTrue((self.tree.out / 'kit/html/guide/_downloads/abc/'
                         'data.csv').is_file())

    @unittest.skipUnless(importlib.util.find_spec('markdown_it'),
                         'verify.py needs markdown-it-py (Python 3.10+)')
    def test_verify(self):
        import verify  # pylint: disable=import-outside-toplevel
        with contextlib.redirect_stdout(io.StringIO()) as text:
            status = verify.main([str(self.tree.root), str(self.tree.out),
                                  '--jobs', '1'])
        self.assertEqual(status, 0, text.getvalue())
        self.assertIn('heading sequence differs: 0', text.getvalue())
        self.assertIn('recall     min 1.0000', text.getvalue())


BODY = """<!DOCTYPE html><html><head><title>{title} \u2014 Kit 2.0 \
documentation</title></head><body>{nav}<div class="body" role="main">\
{body}</div></body></html>"""

REVIEW = """<div class="section" id="s"><h1>Old</h1>
<div class="highlight-box docutils container"><p>Boxed text.</p>
<div class="highlight-python notranslate"><div class="highlight"><pre>x = 1
</pre></div></div></div>
<div class="section" id="s2"><h2>Sub</h2>
<dl class="method"><dt id="m.C.f"><code class="descname">f</code>\
<span class="sig-paren">(</span><em>a</em><span class="sig-paren">)</span></dt>
<dd><p>F.</p><div class="section" id="ex"><h3>Example:</h3><p>Use it.</p>\
</div></dd></dl>
<dl class="method"><dt id="m.C.g"><code class="descname">g</code>()</dt>
<dd><p>G.</p></dd></dl>
<h7>Deep</h7>
</div>
<p>Ratio 1<a class="footnote-reference brackets" href="#f1">2</a>.</p>
<dl class="field-list simple"><dt class="field-odd">Returns</dt>
<dd class="field-odd"><p>none</p></dd></dl>
<dl class="glossary"><dt id="term-t"><a href="other/">linked</a> with \
<code>code_x</code></dt><dd><p>Def.</p></dd></dl>
<dl class="footnote brackets"><dt class="label" id="f1"><span \
class="brackets">1</span></dt><dd><p>One.</p></dd><dt class="label" id="f2">\
<span class="brackets">2</span></dt><dd><p>Two.</p></dd></dl>
<div class="graphviz"><object data="_images/g.svg" type="image/svg+xml">\
<p class="warning">digraph g { a -&gt; b }</p></object></div>
<p>To <a href="other/">other</a> and <a href="genindex-A.html">A</a>.</p>
</div>"""

# Sphinx 9 viewcode markup of "class A:\n    def f(self):\n        pass\n\n
# x = 1": a <div> and a "[docs]" link for each object, with newlines.
VIEWCODE9 = """<h1>Source code for m</h1><div class="highlight"><pre>\
<span></span>
<div class="viewcode-block" id="A">
<a class="viewcode-back" href="../index.html#m.A">[docs]</a>
<span class="k">class</span> A:
<div class="viewcode-block" id="A.f">
<a class="viewcode-back" href="../index.html#m.A.f">[docs]</a>
    <span class="k">def</span> f(self):
        pass</div>
</div>


x = 1
</pre></div>"""

# The same source with Sphinx 4 markup: no added newlines.
VIEWCODE4 = """<h1>Source code for m</h1><div class="highlight"><pre>\
<span></span><div class="viewcode-block" id="A"><a class="viewcode-back" \
href="../index.html#m.A">[docs]</a><span class="k">class</span> A:
<div class="viewcode-block" id="A.f"><a class="viewcode-back" \
href="../index.html#m.A.f">[docs]</a>    <span class="k">def</span> f(self):
        pass</div></div>

x = 1
</pre></div>"""

RTD_NAV = """<div class="wy-side-nav-search"><a class="icon icon-home" \
href="index.html"> Kit</a></div><div role="navigation"><ul \
class="wy-breadcrumbs"><li><a class="icon icon-home" aria-label="Home" \
href="index.html"></a></li><li class="breadcrumb-item active">R</li>\
<li class="wy-breadcrumbs-aside"><a href="_sources/r.rst.txt">View page \
source</a></li></ul></div>"""


class SphinxReviewTest(unittest.TestCase):
    """The defects that the review agent found in round S1."""

    def setUp(self):
        self.tree = SphinxTree()
        self.addCleanup(self.tree.close)
        b = 'kit/'
        self.tree.write(b + 'searchindex.js', 'Search.setIndex({})')
        self.tree.write(b + 'index.html', BODY.format(
            title='Kit', nav='', body='<h1>Kit</h1>'))
        self.tree.write(b + 'genindex-A.html', BODY.format(
            title='Index', nav='', body='<h1>Index A</h1>'))
        self.tree.write(b + 'old.html', BODY.format(
            title='Old', nav='', body=REVIEW))
        self.tree.write(b + 'other/index.html', BODY.format(
            title='Other', nav='', body='<h1>Other</h1>'))
        self.tree.write(b + '_images/g.svg', '<svg/>')
        self.tree.write(b + '_modules/m9.html', BODY.format(
            title='m', nav='', body=VIEWCODE9))
        self.tree.write(b + '_modules/m4.html', BODY.format(
            title='m', nav='', body=VIEWCODE4))
        self.tree.write(b + 'r.html', BODY.format(
            title='R \u2014 part one', nav=RTD_NAV, body='<p>No heading.</p>'))
        self.tree.convert()

    def md(self, rel):
        text = (self.tree.out / 'kit' / rel).read_bytes().decode('utf-8')
        return text.split('\n---\n', 1)

    def test_container_with_highlight_class_keeps_its_text(self):
        md = self.md('old.md')[1]
        self.assertIn('Boxed text.\n\n```python\nx = 1\n```', md)

    def test_old_markup_objects_and_levels(self):
        md = self.md('old.md')[1]
        self.assertIn('### `m.C.f`\n\n```python\nf(a)\n```\n\nF.\n\n'
                      '#### Example:\n\nUse it.\n\n### `m.C.g`', md)
        self.assertIn('\n###### Deep\n', md)

    def test_css_text_of_old_sphinx(self):
        md = self.md('old.md')[1]
        self.assertIn('Ratio 1[2].', md)
        self.assertIn('**Returns:**', md)
        self.assertIn('\n[1] One.\n\n[2] Two.\n', md)

    def test_term_keeps_link_and_code(self):
        md = self.md('old.md')[1]
        self.assertIn('**[linked](other/index.md) with `code_x`**', md)

    def test_svg_object_and_folder_links(self):
        md = self.md('old.md')[1]
        self.assertIn('![g](_images/g.svg)', md)
        self.assertNotIn('digraph', md)
        self.assertTrue((self.tree.out / 'kit/_images/g.svg').is_file())
        self.assertIn('To [other](other/index.md) and A.', md)
        self.assertFalse((self.tree.out / 'kit/genindex-A.md').exists())

    def test_viewcode_source_is_the_file(self):
        source = 'class A:\n    def f(self):\n        pass\n\nx = 1\n'
        for page in ('_modules/m9.md', '_modules/m4.md'):
            self.assertIn(f'```\n{source}```', self.md(page)[1], page)

    def test_rtd_breadcrumb_and_title(self):
        head, md = self.md('r.md')
        self.assertIn('title: R \u2014 part one\n', head)
        self.assertIn('breadcrumb:\n- Kit\n', head)
        self.assertNotIn('View page source', head)
        self.assertTrue(head.endswith('\nproject: Kit'), head)
        self.assertTrue(md.lstrip().startswith('# R \u2014 part one\n'))

    @unittest.skipUnless(importlib.util.find_spec('markdown_it'),
                         'verify.py needs markdown-it-py (Python 3.10+)')
    def test_verify(self):
        import verify  # pylint: disable=import-outside-toplevel
        for delete in (False, True):
            if delete:  # A page with only a div.body must count as a topic.
                (self.tree.out / 'kit/old.md').unlink()
            with contextlib.redirect_stdout(io.StringIO()) as text:
                status = verify.main([str(self.tree.root),
                                      str(self.tree.out), '--jobs', '1'])
            self.assertEqual(status, int(delete), text.getvalue())
            if not delete:
                self.assertIn('heading sequence differs: 0', text.getvalue())
                self.assertIn('recall     min 1.0000', text.getvalue())


RELATED = """<div class="related" role="navigation"><ul><li class="right">\
<a href="genindex.html">index</a></li><li class="nav-item nav-item-0">\
<a href="index.html">Kit 2.0 documentation</a> &#187;</li>\
<li class="nav-item nav-item-1"><a href="lib.html">Library</a> &#187;</li>\
<li class="nav-item nav-item-this"><a href="">Json</a></li></ul></div>"""

FORM = """<h1>Tool</h1><title>Hidden title</title><form action="#">\
<p>Fill in the <b>fields</b>.</p><label>Name</label>\
<input type="text" name="n"/><select><option>a</option></select>\
<textarea>x</textarea></form>
<table class="docutils"><caption>Cap</caption><tr><td>1</td></tr></table>
<ul><li><p>Item</p><table class="docutils"><caption>Table cap</caption>\
<thead><tr><th>A</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table>\
</li></ul>"""


class SphinxRound2Test(unittest.TestCase):
    """Markup of real builds (round S2)."""

    def setUp(self):
        self.tree = SphinxTree()
        self.addCleanup(self.tree.close)
        b = 'kit/'
        self.tree.write(b + 'searchindex.js', 'Search.setIndex({})')
        self.tree.write(b + '_static/documentation_options.js',
                        "VERSION: '2.0',")
        self.tree.write(b + 'index.html', BODY.format(
            title='Kit', nav='', body='<h1>Kit</h1>'))
        self.tree.write(b + 'json.html', BODY.format(
            title='Json', nav=RELATED, body='<h1>Json</h1>'))
        self.tree.write(b + 'tool.html', BODY.format(
            title='Tool', nav='', body=FORM))
        self.tree.convert()

    def md(self, rel):
        text = (self.tree.out / 'kit' / rel).read_bytes().decode('utf-8')
        return text.split('\n---\n', 1)

    def test_related_bar_breadcrumb(self):
        head = self.md('json.md')[0]
        self.assertIn('breadcrumb:\n- Kit\n- Library\nsection', head)

    def test_form_text_stays(self):
        md = self.md('tool.md')[1]
        self.assertIn('Fill in the **fields**.', md)
        self.assertIn('Name', md)
        self.assertNotIn('Hidden title', md)

    @unittest.skipUnless(importlib.util.find_spec('markdown_it'),
                         'verify.py needs markdown-it-py (Python 3.10+)')
    def test_verify(self):
        import verify  # pylint: disable=import-outside-toplevel
        with contextlib.redirect_stdout(io.StringIO()) as text:
            status = verify.main([str(self.tree.root), str(self.tree.out),
                                  '--jobs', '1'])
        self.assertEqual(status, 0, text.getvalue())
        self.assertIn('recall     min 1.0000', text.getvalue())
        # The caption in a list item is no table text (a pipe table).
        self.assertIn('wrong list nesting 0', text.getvalue())


AGENT2 = """<h1>Agent</h1>
<dl class="simple"><dt><strong>width</strong><span class="classifier">int, \
optional</span></dt><dd><p>The width.</p></dd></dl>
<div class="sphinx-tabs docutils container"><div role="tablist">\
<button aria-controls="panel-0" id="tab-0" role="tab">Apples</button>\
<button aria-controls="panel-1" id="tab-1" role="tab">Pears</button></div>\
<div aria-labelledby="tab-0" id="panel-0" role="tabpanel"><p>Green.</p></div>\
<div aria-labelledby="tab-1" id="panel-1" role="tabpanel"><p>Yellow.</p></div>\
</div>
<div class="sd-tab-set docutils"><input checked="checked" type="radio"/>
<label class="sd-tab-label">
<svg class="sd-octicon"><path d="M0"></path></svg>
<svg class="sd-octicon"><path d="M0"></path></svg>
 First</label><div class="sd-tab-content docutils"><p>Body1.</p></div></div>
<ul><li>one<li>two</ul>
<ul class="contains-task-list"><li class="task-list-item"><input \
class="task-list-item-checkbox" disabled="disabled" type="checkbox"> todo</li>\
<li class="task-list-item"><input class="task-list-item-checkbox" \
checked="checked" disabled="disabled" type="checkbox"> done</li></ul>
<ol class="loweralpha simple"><li><p>alpha</p></li><li><p>beta</p></li></ol>
<dl class="py function"><dt class="sig sig-object py" id="id0">\
<span class="sig-name descname">twice</span><span class="sig-paren">(</span>\
<em class="sig-param">x=<span class="default_value">'→'</span></em>\
<span class="sig-paren">)</span> <span class="sig-return">\
<span class="sig-return-icon">→</span> int</span></dt><dd></dd></dl>
<dl class="describe"><dt class="sig sig-object" id="PAPER">\
<span class="sig-name descname">PAPER</span></dt><dd></dd></dl>
<div class="admonition note"><p class="admonition-title">See \
<a href="index.html">Kit</a> and <code>x</code></p><p>Text.</p></div>
<p><abbr title="last-in, first-out">LIFO</abbr> and \
<span class="guilabel"><span class="accelerator">C</span>ancel</span>.</p>
<div class="highlight-python notranslate"><div class="highlight"><pre>\
<span></span><span class="n">s</span> = "a\xa0b"
</pre></div></div>"""


class SphinxAgent2Test(unittest.TestCase):
    """The defects that the review agent found in round S2."""

    def setUp(self):
        self.tree = SphinxTree()
        self.addCleanup(self.tree.close)
        b = 'kit/'
        self.tree.write(b + 'searchindex.js', 'Search.setIndex({})')
        self.tree.write(b + 'index.html', BODY.format(
            title='Kit', nav='', body='<h1>Kit</h1>'))
        self.tree.write(b + 'agent.html', BODY.format(
            title='Agent', nav='', body=AGENT2))
        self.tree.convert()

    def test_agent_findings(self):
        md = (self.tree.out / 'kit/agent.md').read_text(encoding='utf-8')
        for want in ('**width : int, optional**',
                     '**Apples**\n\nGreen.\n\n**Pears**\n\nYellow.',
                     '\n**First**\n\nBody1.', '- one\n- two\n',
                     '- [ ] todo\n- [x] done\n', '- a. alpha\n- b. beta\n',
                     "## `twice`\n\n```python\ntwice(x='→') -> int\n```",
                     '## `PAPER`\n\n```\nPAPER\n```',
                     '> **See [Kit](index.md) and `x`**',
                     'LIFO (last-in, first-out) and Cancel.',
                     's = "a\xa0b"'):
            self.assertIn(want, md)

    @unittest.skipUnless(importlib.util.find_spec('markdown_it'),
                         'verify.py needs markdown-it-py (Python 3.10+)')
    def test_verify(self):
        import verify  # pylint: disable=import-outside-toplevel
        with contextlib.redirect_stdout(io.StringIO()) as text:
            status = verify.main([str(self.tree.root), str(self.tree.out),
                                  '--jobs', '1'])
        self.assertEqual(status, 0, text.getvalue())
        self.assertIn('recall     min 1.0000', text.getvalue())
        self.assertIn('precision  min 1.0000', text.getvalue())


def nav_page(title, body, nav='', head_title=None):
    return ('<!DOCTYPE html><html><head><title>%s</title></head><body>%s'
            '<article role="main">%s</article></body></html>' % (
                head_title or title + ' - Kit 3.0 documentation', nav, body))


class SphinxBuildShapeTest(unittest.TestCase):
    """Builds of other shapes: furo titles, .htm pages, dirhtml, toctree
    breadcrumbs and the pydata breadcrumb bar."""

    def setUp(self):
        self.tree = SphinxTree()
        self.addCleanup(self.tree.close)
        toc = ('<div class="toctree-wrapper compound"><ul><li class="toctree'
               '-l1"><a href="{0}">G</a><ul><li class="toctree-l2"><a href='
               '"{1}">I</a></li></ul></li></ul></div>')
        # A furo build with .htm pages.
        f = 'furo/'
        self.tree.write(f + 'searchindex.js', 'Search.setIndex({})')
        self.tree.write(f + '_static/documentation_options.js',
                        "VERSION: '3.0',")
        self.tree.write(f + 'index.htm', nav_page(
            'Kit', '<h1>Home</h1>' + toc.format('g/index.htm', 'g/i.htm'),
            head_title='Kit 3.0 documentation'))
        self.tree.write(f + 'genindex.htm', nav_page('Index', '<h1>I</h1>'))
        self.tree.write(f + 'g/index.htm', nav_page(
            'Guide', '<h1>Guide</h1><div class="toctree-wrapper"><ul><li>'
            '<a href="i.htm">I</a></li></ul></div>'))
        self.tree.write(f + 'g/i.htm', nav_page(
            'Usage — the basics', '<h1>Usage — the basics</h1>'))
        # A dirhtml build with the pydata breadcrumb bar.
        d = 'dir/'
        crumbs = ('<ul class="bd-breadcrumbs"><li class="breadcrumb-item '
                  'breadcrumb-home"><a class="nav-link" href="../../">'
                  '<i class="fa-home"></i></a></li><li class="breadcrumb-item'
                  '"><a href="../">Guide</a></li><li class="breadcrumb-item '
                  'active">Install</li></ul>')
        self.tree.write(d + 'searchindex.js', 'Search.setIndex({})')
        self.tree.write(d + '_static/documentation_options.js',
                        "VERSION: '3.0',")
        self.tree.write(d + 'index.html', nav_page('Kit', '<h1>Kit</h1>'))
        self.tree.write(d + 'genindex/index.html', nav_page('I', '<h1>I</h1>'))
        self.tree.write(d + 'guide/index.html', nav_page('Guide',
                                                         '<h1>Guide</h1>'))
        self.tree.write(d + 'guide/install/index.html', nav_page(
            'Install', '<h1>Install</h1>', nav=crumbs))
        self.tree.convert()

    def head(self, rel):
        text = (self.tree.out / rel).read_text(encoding='utf-8')
        return text.split('\n---\n', 1)[0]

    def test_furo_htm_build(self):
        self.assertFalse((self.tree.out / 'furo/genindex.md').exists())
        head = self.head('furo/g/i.md')
        self.assertIn('title: Usage — the basics\n', head)
        self.assertIn('breadcrumb:\n- Kit\n- Guide\n', head)
        self.assertTrue(head.endswith('project: Kit'), head)
        self.assertTrue(self.head('furo/index.md').endswith('project: Kit'))

    def test_singlehtml_build(self):
        # No searchindex.js: _static/documentation_options.js marks it.
        self.tree.write('one/_static/documentation_options.js',
                        "VERSION: '4.0',")
        self.tree.write('one/index.html', nav_page(
            'Kit', '<h1>Kit</h1>', head_title='Kit 4.0 documentation'))
        config = sphinx2md.make_config(self.tree.root / 'one',
                                       self.tree.out / 'one')
        sphinx2md.flare2md.convert(config, jobs=1)
        head = self.head('one/index.md')
        self.assertIn("version: '4.0'\n", head)
        self.assertTrue(head.endswith('project: Kit'), head)

    def test_dirhtml_build(self):
        self.assertFalse((self.tree.out / 'dir/genindex/index.md').exists())
        head = self.head('dir/guide/install/index.md')
        self.assertIn('breadcrumb:\n- Kit\n- Guide\n', head)
        self.assertIn('page_id: guide/install\n', head)
        # The first folder below the source folder is the build.
        self.assertIn('section: dir\n', head)
        self.assertIn('page_id: guide\n', self.head('dir/guide/index.md'))



REAL3 = """<h1>Real</h1>
<nav class="contents local" id="contents"><ul><li><a class="reference \
internal" href="#a">Part A</a></li></ul></nav>
<div class="contents local topic"><ul><li><a href="#a">Old A</a></li></ul>\
</div>
<table class="docutils"><caption><span class="caption-text">Examples</span>\
<a class="headerlink" href="#id4">\u00b6</a></caption><thead><tr><th>A</th>\
</tr></thead><tbody><tr><td>1</td></tr></tbody></table>
<p><strong>what</strong> (<em>list</em><em>[</em><a class="reference \
external" href="https://example.com/t"><em>type</em></a><em>, </em>\
<em>attrs.Attribute</em><em>]</em>) \u2013 What to include.</p>
<div class="highlight-jinja notranslate"><div class="highlight"><pre>\
<span class="si">{% f</span><span class="s1">or x in y %}</span>
</pre></div></div>"""


class SphinxRound3Test(unittest.TestCase):
    """Defects found on real builds (click, attrs, Jinja) in round S3."""

    def setUp(self):
        self.tree = SphinxTree()
        self.addCleanup(self.tree.close)
        b = 'kit/'
        self.tree.write(b + 'searchindex.js', 'Search.setIndex({})')
        self.tree.write(b + '_static/documentation_options.js',
                        "VERSION: '8.5.0',")
        self.tree.write(b + 'index.html', nav_page(
            'Kit', '<h1>Kit</h1>',
            head_title='Kit \u2014 Kit Documentation (8.5.x)'))
        self.tree.write(b + 'real.html', nav_page(
            'Real', REAL3, head_title='Real \u2014 Kit Documentation (8.5.x)'))
        self.tree.convert()

    def test_real_build_findings(self):
        md = (self.tree.out / 'kit/real.md').read_text(encoding='utf-8')
        self.assertTrue(md.split('\n---\n')[0].endswith('project: Kit'), md)
        self.assertNotIn('Part A', md)
        self.assertNotIn('Old A', md)
        self.assertIn('**Examples**\n\n| A |', md)
        self.assertIn('**what** (*list[[type](https://example.com/t), '
                      'attrs.Attribute]*)', md)

    def test_drop_version(self):
        for name, version, want in (
                ('Kit Documentation (8.5.x)', '8.5.0', 'Kit'),
                ('Kit v1.2 documentation', '1.2.3', 'Kit'),
                ('Kit 2.0 documentation', '1.2', 'Kit 2.0'),
                ('Web 2', '2.1', 'Web 2'),
                ('3.11.9 Documentation', '3.11.9', '3.11.9'),
                ('Kit (beta)', '1.0', 'Kit (beta)')):
            self.assertEqual(sphinx2md.drop_version(name, version), want)

    @unittest.skipUnless(importlib.util.find_spec('markdown_it'),
                         'verify.py needs markdown-it-py (Python 3.10+)')
    def test_verify(self):
        import verify  # pylint: disable=import-outside-toplevel
        with contextlib.redirect_stdout(io.StringIO()) as text:
            status = verify.main([str(self.tree.root), str(self.tree.out),
                                  '--jobs', '1'])
        self.assertEqual(status, 0, text.getvalue())
        self.assertIn('recall     min 1.0000', text.getvalue())
        self.assertIn('precision  min 1.0000', text.getvalue())


AGENT3 = """<h1>Agent</h1>
<p>Inline <img class="math" src="_images/math/a.png" alt="a^2"/> and \
<span class="math notranslate nohighlight">\\(b\\)</span>.</p>
<div class="math" id="equation-pyth"><p><span class="eqno">(1)<a \
class="headerlink" href="#equation-pyth">\u00b6</a></span><img \
src="_images/math/b.png" alt="a^2 + b^2 = c^2"/></p></div>
<div class="math"><p><span class="math">e = mc^2</span></p></div>
<p>Dot <img src="data:image/png;base64,iVBORw0KGgo=" alt="dot"/>.</p>
<div class="nbinput docutils container"><div class="prompt highlight-none \
notranslate"><div class="highlight"><pre><span></span>[1]:
</pre></div></div><div class="input_area highlight-none notranslate">\
<div class="highlight"><pre><span></span>print(1)
</pre></div></div></div>
<ul class="nav nav-tabs" role="tablist"><li><a href="#w" role="tab">\
Windows label</a></li></ul>
<table class="docutils"><thead><tr><th>H1</th><th>H2</th></tr><tr>\
<th>sub1</th><th>sub2</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr>\
</tbody></table>
<pre class="mermaid">graph TD
A --&gt; B</pre>
<div class="tab-set docutils container"><input checked="True" \
class="tab-input" type="radio"/><label class="tab-label">Linux</label>\
<div class="tab-content docutils container"><p>Run it.</p></div></div>
<div class="sd-card docutils"><div class="sd-card-body docutils">\
<div class="sd-card-title sd-font-weight-bold docutils">Getting started</div>\
<p class="sd-card-text">Learn.</p></div></div>
<p><video src="_static/movie.mp4" controls>Your browser does not support \
video.</video></p>
<ol start="1234567890"><li><p>big</p></li><li><p>next</p></li></ol>
<blockquote class="epigraph"><div><p>Be here.</p></div></blockquote>"""


def chain_page(title, body, head_title, nxt=None, prev=None):
    links = ''.join('<link rel="%s" href="%s"/>' % (rel, href)
                    for rel, href in (('next', nxt), ('prev', prev)) if href)
    return ('<!DOCTYPE html><html><head><title>%s</title>%s</head><body>'
            '<article role="main">%s</article></body></html>' % (
                head_title, links, body))


class SphinxAgent3Test(unittest.TestCase):
    """The defects that the review agent found in round S3."""

    def setUp(self):
        self.tree = SphinxTree()
        self.addCleanup(self.tree.close)
        # A Japanese build with root_doc 'main', a hidden root toctree,
        # links without a suffix and a page without a title.
        b = 'ja/'
        title = 'Kit 3.0 \u30c9\u30ad\u30e5\u30e1\u30f3\u30c8'
        self.tree.write(b + 'searchindex.js', 'Search.setIndex({})')
        self.tree.write(b + '_static/documentation_options.js',
                        "VERSION: '3.0',\n    LANGUAGE: 'ja',")
        self.tree.write(b + 'main.html', chain_page(
            'Kit', '<h1>Kit</h1><p><a href="guide">Guide</a> '
            '<a href="genindex">Index</a></p>', title, nxt='guide'))
        self.tree.write(b + 'genindex.html', chain_page('I', '<h1>I</h1>',
                                                        title))
        self.tree.write(b + 'guide.html', chain_page(
            'Guide', '<h1>Guide</h1>', 'Guide \u2014 ' + title,
            nxt='notitle', prev='main'))
        self.tree.write(b + 'notitle.html', chain_page(
            '', '<p>No title.</p>', '&lt;no title&gt; \u2014 ' + title,
            prev='guide'))
        # sphinxawesome titles; a project name with " - ".
        for b, sep in (('awe/', ' | '), ('furo/', ' - ')):
            name = 'Foo - Bar 1.0 documentation'
            self.tree.write(b + 'searchindex.js', 'Search.setIndex({})')
            self.tree.write(b + '_static/documentation_options.js',
                            "VERSION: '1.0',")
            self.tree.write(b + 'index.html', chain_page(
                'Foo', '<h1>Home</h1>', name, nxt='a.html'))
            self.tree.write(b + 'a.html', chain_page(
                'A', '<h1>A</h1>', 'A' + sep + name, prev='index.html'))
            self.tree.write(b + 'b.html', chain_page(
                'B', '<h1>B</h1>', 'B' + sep + name))
        self.tree.write('agent/searchindex.js', 'Search.setIndex({})')
        self.tree.write('agent/index.html', BODY.format(
            title='Agent', nav='', body=AGENT3))
        self.tree.convert()

    def read(self, rel):
        return (self.tree.out / rel).read_text(encoding='utf-8')

    def test_agent_findings(self):
        md = self.read('agent/index.md')
        for want in ('Inline $`a^2`$ and $`b`$.',
                     '```math\na^2 + b^2 = c^2\n```\n\n(1)\n\n'
                     '```math\ne = mc^2\n```', 'Dot dot.',
                     '- Windows label', '<th>sub1</th>',
                     '```mermaid\ngraph TD\nA --> B\n```',
                     '**Linux**\n\nRun it.',
                     '**Getting started**\n\nLearn.',
                     '[movie.mp4](_static/movie.mp4)',
                     '- 1234567890. big\n- 1234567891. next',
                     '> Be here.'):
            self.assertIn(want, md)
        for unwanted in ('[1]:', 'base64', 'browser does not'):
            self.assertNotIn(unwanted, md)

    def test_language_root_doc_and_link_suffix(self):
        main = self.read('ja/main.md')
        self.assertIn('[Guide](guide.md) Index', main)
        self.assertIn('breadcrumb: []\n', main)
        self.assertIn('project: Kit\n', main)
        guide = self.read('ja/guide.md')
        self.assertIn('breadcrumb:\n- Kit\nsection', guide)
        notitle = self.read('ja/notitle.md')
        self.assertIn('title: notitle\n', notitle)

    def test_title_separators(self):
        for b in ('awe', 'furo'):
            for page in ('index', 'a', 'b'):
                self.assertIn('project: Foo - Bar\n',
                              self.read('%s/%s.md' % (b, page)))

    @unittest.skipUnless(importlib.util.find_spec('markdown_it'),
                         'verify.py needs markdown-it-py (Python 3.10+)')
    def test_verify(self):
        import verify  # pylint: disable=import-outside-toplevel
        with contextlib.redirect_stdout(io.StringIO()) as text:
            status = verify.main([str(self.tree.root), str(self.tree.out),
                                  '--jobs', '1'])
        self.assertEqual(status, 0, text.getvalue())
        self.assertIn('recall     min 1.0000', text.getvalue())
        self.assertIn('builds with more than one project 0', text.getvalue())



AGENT4 = """<h1>Agent</h1>
<p>A type: <span class="sig-inline">MySortedContainer<span class="p">&lt;\
</span><span class="kt">int</span><span class="p">&gt;</span></span>.</p>
<div class="nav"><a href="guide.html"><div class="t">Previous</div><div>Guide</div></a></div>
<div class="related-pages" role="navigation"><a href="guide.html">Next \
page</a></div>
<p><img src="../a/_images/pic.png" alt="back"></p>
<pre>""" + ''.join('<span class="n">x%d</span><span class="o">.</span>' % i
              for i in range(1000)) + """</pre>
""" + ''.join('<p>Paragraph %d.</p>\n' % i for i in range(3000))


def case_insensitive(folder: pathlib.Path) -> bool:
    probe = folder / 'CaseProbe'
    probe.write_text('x', encoding='utf-8')
    try:
        return (folder / 'caseprobe').exists()
    finally:
        probe.unlink()


class SphinxAgent4Test(unittest.TestCase):
    """The defects found in round S4: bad files, nested builds, names on
    the disk, escapes across text nodes, links that hold blocks and run
    time on big pages."""

    @classmethod
    def setUpClass(cls):
        cls.tree = SphinxTree()
        cls.addClassCleanup(cls.tree.close)
        b = 'a/'
        cls.tree.write(b + 'searchindex.js', 'Search.setIndex({})')
        cls.tree.write(b + '_static/documentation_options.js',
                        "VERSION: '2.0',")
        cls.tree.write(b + 'index.html', BODY.format(
            title='Kit', nav='', body=AGENT4))
        cls.tree.write(b + 'guide.html', BODY.format(
            title='Guide', nav='', body='<h1>Guide</h1><p>'
            '<img src="_IMAGES/PIC.PNG" alt="p"> <a href="GUIDE.html">me</a>'
            '</p>'))
        cls.tree.write(b + '_images/pic.png', b'PNG')
        # Bad files: a folder with a page suffix, an odd UTF-16 page and a
        # link to nothing.
        (cls.tree.root / b / 'folder.html').mkdir()
        cls.tree.write(b + 'odd.html', b'\xff\xfe<\x00p\x00>\x00x')
        (cls.tree.root / b / 'dangling.html').symlink_to('nothing.html')
        # A build in a folder of another build, with more pages.
        n = b + 'other/'
        cls.tree.write(n + 'searchindex.js', 'Search.setIndex({})')
        cls.tree.write(n + '_static/documentation_options.js',
                        "VERSION: '9.0',")
        for page in ('index', 'p1', 'p2', 'p3'):
            cls.tree.write(n + page + '.html', (
                '<!DOCTYPE html><html><head><title>%s — Other 9.0 '
                'documentation</title></head><body><div class="body" '
                'role="main"><h1>%s</h1></div></body></html>') % (page, page))
        started = time.monotonic()
        cls.counts = cls.tree.convert(version='1e3')
        cls.seconds = time.monotonic() - started

    def read(self, rel):
        return (self.tree.out / rel).read_text(encoding='utf-8')

    def test_escape_across_text_nodes(self):
        self.assertIn('A type: MySortedContainer\\<int>.',
                      self.read('a/index.md'))

    def test_link_with_blocks_and_hidden_navigation(self):
        md = self.read('a/index.md')
        self.assertIn('[Previous Guide](guide.md)', md)
        self.assertNotIn('Next page', md)

    def test_big_page_time(self):
        # 3,000 paragraphs and a block of 2,000 code tokens.
        self.assertLess(self.seconds, 30)
        self.assertIn('Paragraph 2999.', self.read('a/index.md'))

    def test_space_between_blocks_is_not_replaced(self):
        # A replace looks up the place of the text among its siblings. One
        # for each space between 20,000 paragraphs took minutes.
        config = sphinx2md.make_config(self.tree.root / 'a', self.tree.out)
        page = sphinx2md.SphinxPage('index.html', config)
        soup = sphinx2md.flare2md.BeautifulSoup(
            '<div><p>a  b</p>\n\n<p>c</p></div>', 'html.parser')
        space = soup.div.contents[1]
        page.prepare_text(soup.div)
        self.assertIs(soup.div.contents[1], space)
        self.assertEqual(soup.p.string, 'a b')

    def test_code_is_not_searched_for_split_markers(self):
        # Each "." in a big <pre> looked back over all the text before it.
        config = sphinx2md.make_config(self.tree.root / 'a', self.tree.out)
        page = sphinx2md.SphinxPage('index.html', config)
        soup = sphinx2md.flare2md.BeautifulSoup(
            '<div><pre><span>1</span><span>.</span> x</pre></div>',
            'html.parser')
        dot = soup.pre.contents[1].string
        page.escape_line_starts(soup.div)
        self.assertIs(soup.pre.contents[1].string, dot)

    def test_bad_files_do_not_stop_the_run(self):
        self.assertEqual(self.counts['errors'], 1)
        errors = self.read('_report/errors.txt')
        self.assertIn('a/odd.html\tUnicodeDecodeError', errors)
        self.assertTrue((self.tree.out / 'a/guide.md').exists())

    def test_nested_build_keeps_its_own_metadata(self):
        self.assertIn('project: Kit\n', self.read('a/guide.md'))
        self.assertIn('project: Other\n', self.read('a/other/p1.md'))

    def test_path_that_leaves_source_and_comes_back(self):
        config = sphinx2md.make_config(self.tree.root / 'a', self.tree.out)
        page = sphinx2md.SphinxPage('index.html', config)
        self.assertEqual(str(page.resolve('../a/_images/pic.png')),
                         '_images/pic.png')

    def test_yaml_quotes_numbers_of_yaml_1_2(self):
        self.assertIn("version: '1e3'\n", self.read('a/guide.md'))

    def test_names_on_the_disk(self):
        if not case_insensitive(self.tree.root):
            self.skipTest('the disk knows case')
        md = self.read('a/guide.md')
        self.assertIn('![p](_images/pic.png)', md)
        self.assertIn('[me](guide.md)', md)
        self.assertEqual(sorted(p.name for p in (self.tree.out / 'a').iterdir()
                                if p.name.lower() == '_images'), ['_images'])

    def test_collisions_know_unicode(self):
        groups = sphinx2md.flare2md.find_collisions(
            ['café.html', 'café.htm', 'ß.htm', 'ss.html',
             'x.html'])
        self.assertEqual(len(groups), 2)

    def test_cli_errors(self):
        afile = self.tree.root / 'afile'
        afile.write_text('x', encoding='utf-8')
        src = self.tree.root / 'a'
        for argv in ([str(src), str(afile)],
                     [str(src), str(src).upper()]):
            if argv[1] == str(src).upper() and not case_insensitive(src):
                continue
            with self.subTest(argv=argv), \
                    contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as cm:
                sphinx2md.main(argv)
            self.assertEqual(cm.exception.code, 2)

    def test_jobs_are_capped(self):
        with tempfile.TemporaryDirectory() as out:
            config = sphinx2md.make_config(self.tree.root, out)
            counts = sphinx2md.flare2md.convert(config, ['a/guide.html'],
                                                jobs=100000)
        self.assertEqual(counts['converted'], 1)

    @unittest.skipIf(os.name == 'nt' or os.geteuid() == 0,
                     'needs file permissions')
    def test_image_that_cannot_be_read(self):
        pic = self.tree.root / 'a/_images/pic.png'
        pic.chmod(0)
        self.addCleanup(pic.chmod, 0o644)
        with tempfile.TemporaryDirectory() as out:
            config = sphinx2md.make_config(self.tree.root, out)
            with contextlib.redirect_stderr(io.StringIO()):
                counts = sphinx2md.flare2md.convert(config, jobs=1)
            errors = (pathlib.Path(out) / '_report/errors.txt').read_text(
                encoding='utf-8')
        self.assertEqual(counts['errors'], 2)
        self.assertIn('a/_images/pic.png\tPermissionError', errors)

    @unittest.skipUnless(importlib.util.find_spec('markdown_it'),
                         'verify.py needs markdown-it-py (Python 3.10+)')
    def test_verify(self):
        import verify  # pylint: disable=import-outside-toplevel
        self.tree.write('a/latin.html', BODY.format(
            title='Latin', nav='', body='<h1>Café crème</h1><p>'
            'Déjà vu – naïve.</p>').encode('cp1252'))
        config = sphinx2md.make_config(self.tree.root, self.tree.out)
        sphinx2md.flare2md.convert(config, ['a/latin.html'], jobs=1)
        with contextlib.redirect_stdout(io.StringIO()) as text:
            status = verify.main([str(self.tree.root), str(self.tree.out),
                                  '--jobs', '1'])
        # odd.html cannot be read: one crash, no traceback.
        self.assertEqual(status, 1, text.getvalue())
        self.assertIn('files that crashed the check 1', text.getvalue())
        self.assertIn('recall     min 1.0000', text.getvalue())


# The HTML4 writer of Sphinx 1.8 and the markup of Sphinx 2 and 3.
OLD = """<div class="section" id="old"><h1>Old</h1>
<table class="docutils field-list" frame="void" rules="none">
<col class="field-name" /><col class="field-body" /><tbody valign="top">
<tr class="field-odd field"><th class="field-name">Parameters:</th>\
<td class="field-body"><ul class="first last simple"><li><strong>name</strong>\
 – The name.</li><li><strong>size</strong> – The size.</li></ul></td></tr>
<tr class="field-even field"><th class="field-name" colspan="2">A very long \
field name:</th></tr>
<tr class="field-even field"><td>&#160;</td><td class="field-body">Long \
body.</td></tr>
</tbody></table>
<table class="docutils option-list" frame="void" rules="none">
<col class="option" /><col class="description" /><tbody valign="top">
<tr><td class="option-group"><kbd><span class="option">-a</span></kbd></td>
<td>An option.</td></tr>
</tbody></table>
<div class="figure align-default" id="f1"><img alt="pic" src="pic.png" />
<p class="caption"><span class="caption-text">The caption.</span></p></div>
<dl class="function"><dt id="c.add"><em class="property">int </em>\
<code class="descname">add</code><span class="sig-paren">(</span>\
<em>int a</em><span class="sig-paren">)</span></dt><dd><p>Adds.</p></dd></dl>
<dl class="class"><dt id="_CPPv4I0E3Box"><em class="property">class </em>\
<code class="descname">Box</code></dt><dd><p>A box.</p></dd></dl>
<dl class="option"><dt id="cmdoption-v"><code class="descname">-v</code>\
</dt><dd><p>Verbose.</p></dd></dl>
<dl class="function"><dt id="pkg.run"><code class="descclassname">pkg.\
</code><code class="descname">run</code><span class="sig-paren">(</span>\
<span class="sig-paren">)</span></dt><dd><p>Runs.</p></dd></dl>
</div>"""


KIT5 = """<h1>Kit</h1>
<dl class="py function"><dt class="sig sig-object py" id="f">\
<span class="sig-name descname"><span class="pre">f</span></span>\
<span class="sig-paren">(</span><em class="sig-param"><span class="n">a</span>\
</em>, <em class="sig-param"><span class="positional-only-separator o">\
<abbr title="Positional-only parameter separator (PEP 570)">/</abbr></span>\
</em>, <em class="sig-param"><span class="n">b</span></em>, \
<em class="sig-param"><span class="keyword-only-separator o"><abbr \
title="Keyword-only parameters separator (PEP 3102)">*</abbr></span></em>, \
<em class="sig-param"><span class="n">c</span><span class="o">=</span>\
<span class="default_value">1</span></em><span class="sig-paren">)</span>\
</dt><dd><p>Runs <abbr title="radio frequency">RF</abbr>.</p></dd></dl>
<p><a class="reference download internal" href="notes.md">notes</a> \
<a class="reference download internal" href="guide.md">raw guide</a></p>
<iframe src="https://example.com/embed/1" title="Demo video"></iframe>
<ul><li><p>[x] means yes</p></li>
<li class="task-list-item"><input class="task-list-item-checkbox" \
type="checkbox" checked="checked" disabled="disabled"> Done</li></ul>
<p>Prices ~~10~~ and ~5~.</p><p>~/bin is a path.</p>
<noscript><p>NOSCRIPT</p></noscript><template><p>TEMPLATE</p></template>
<div class="sphinx-tabs docutils container"><div role="tablist">\
<button aria-controls="panel-0-0" id="tab-0-0" role="tab">Unix</button>\
<button aria-controls="panel-0-1" id="tab-0-1" role="tab">Windows</button>\
</div><div aria-labelledby="tab-0-0" class="sphinx-tabs-panel" id="panel-0-0" \
role="tabpanel"><p>Open panel.</p></div><div aria-labelledby="tab-0-1" \
class="sphinx-tabs-panel" hidden="true" id="panel-0-1" role="tabpanel">\
<p>Closed panel.</p></div></div>
<details><summary><p class="collapsed admonition-title">Show code cell \
source</p><p class="expanded admonition-title">Hide code cell source</p>\
</summary><div class="highlight-none notranslate"><div class="highlight">\
<pre>x = 1</pre></div></div></details>
<p>A note<span class="sidenote d-n"><sup>1</sup>COPY</span>.</p>
<div class="sd-card docutils"><div class="sd-card-body docutils">\
<div class="sd-card-title docutils">Card</div><p class="sd-card-text">Body.\
</p></div><a class="sd-stretched-link sd-hide-link-text reference external" \
href="https://example.com"><span>LINKTEXT</span></a></div>
<p><a href="?tab=1#x">Same page</a> The On this page panel lists it.</p>
<aside class="footnote brackets" id="a" role="doc-footnote">
<span class="label"><span class="fn-bracket">[</span>1<span \
class="fn-bracket">]</span></span>
<span class="backrefs">(<a role="doc-backlink" href="#id1">1</a>,\
<a role="doc-backlink" href="#id2">2</a>)</span>
<p>The shared note.</p></aside>
"""


class SphinxRound5Test(unittest.TestCase):
    """Round S5: old Sphinx markup and singlehtml builds."""

    @classmethod
    def setUpClass(cls):
        cls.tree = SphinxTree()
        cls.addClassCleanup(cls.tree.close)
        b = 'old/'
        cls.tree.write(b + 'searchindex.js', 'Search.setIndex({})')
        cls.tree.write(b + 'index.html', BODY.format(title='Old', nav='',
                                                     body=OLD))
        cls.tree.write(b + 'pic.png', b'PNG')
        # A singlehtml build: one page, and its <title> is the project
        # title only. objects.inv has the project.
        b = 'single/'
        cls.tree.write(b + 'searchindex.js', 'Search.setIndex({})')
        cls.tree.write(b + 'objects.inv', b'# Sphinx inventory version 2\n'
                       b'# Project: Flask\n# Version: 3.1\nx\x9c\x00')
        cls.tree.write(b + '_static/documentation_options.js',
                       "VERSION: '3.1.1',")
        cls.tree.write(b + 'index.html', (
            '<!DOCTYPE html><html><head><title>Flask Documentation (3.1.x)'
            '</title></head><body><div class="body" role="main"><section '
            'id="w"><h1>Welcome</h1><p>Text.</p></section></div></body>'
            '</html>'))
        # html_title = "": <title> is the page title only.
        b = 'notitle/'
        cls.tree.write(b + 'searchindex.js', 'Search.setIndex({})')
        cls.tree.write(b + 'objects.inv', b'# Sphinx inventory version 2\n'
                       b'# Project: MyST Parser\n')
        for page in ('index', 'guide'):
            cls.tree.write(b + page + '.html', chain_page(
                page.title(), '<h1>%s</h1>' % page.title(), page.title()))
        cls.tree.write('kit/searchindex.js', 'Search.setIndex({})')
        cls.tree.write('kit/index.html', BODY.format(title='Kit', nav='',
                                                     body=KIT5))
        cls.tree.write('kit/notes.md', '# Raw MyST\n')
        cls.tree.write('kit/guide.md', '# RAW\n')
        cls.tree.write('kit/guide.html', BODY.format(
            title='Guide', nav='', body='<h1>Guide</h1><p>Text.</p>'))
        cls.tree.convert()

    def read(self, rel):
        return (self.tree.out / rel).read_text(encoding='utf-8')

    def test_signature_has_no_abbr_title(self):
        self.assertIn('f(a, /, b, *, c=1)', self.read('kit/index.md'))
        self.assertIn('RF (radio frequency)', self.read('kit/index.md'))

    def test_md_files_are_copied_as_txt(self):
        md = self.read('kit/index.md')
        self.assertIn('[notes](notes.md.txt)', md)
        self.assertIn('[raw guide](guide.md.txt)', md)
        self.assertIn('title: Guide', self.read('kit/guide.md'))
        self.assertTrue((self.tree.out / 'kit/notes.md.txt').is_file())

    def test_empty_html_title_project(self):
        for page in ('index', 'guide'):
            self.assertIn('project: MyST Parser\n',
                          self.read('notitle/%s.md' % page))

    def test_embeds_hidden_and_gfm(self):
        md = self.read('kit/index.md')
        for want in ('[Demo video](https://example.com/embed/1)',
                     '- \\[x] means yes', '- [x] Done',
                     'Prices \\~\\~10\\~\\~ and \\~5\\~.', '~/bin is a path.',
                     'Show code cell source', '**[Card](https://example.com)**',
                     '[1] The shared note.',
                     # The JavaScript shows a hidden tab panel.
                     '**Windows**\n\nClosed panel.'):
            self.assertIn(want, md)
        for unwanted in ('NOSCRIPT', 'TEMPLATE', 'Hide code cell',
                         'COPY', 'LINKTEXT', '?tab'):
            self.assertNotIn(unwanted, md)

    def test_html4_field_and_option_lists(self):
        md = self.read('old/index.md')
        for want in ('**Parameters:**\n\n- **name** – The name.\n'
                     '- **size** – The size.',
                     '**A very long field name:**\n\nLong body.',
                     '**`-a`**\n\nAn option.'):
            self.assertIn(want, md)
        self.assertNotIn('|', md)

    def test_old_figure_caption_is_text(self):
        self.assertIn('![pic](pic.png)\n\nThe caption.\n',
                      self.read('old/index.md'))

    def test_old_domains(self):
        md = self.read('old/index.md')
        for want in ('## `add`\n\n```c\nint add(int a)\n```',
                     '## `Box`\n\n```cpp\nclass Box\n```',
                     '## `-v`\n\n```\n-v\n```',
                     '## `pkg.run`\n\n```python\npkg.run()\n```'):
            self.assertIn(want, md)

    def test_singlehtml_project(self):
        md = self.read('single/index.md')
        self.assertIn('product: Flask\n', md)
        self.assertIn('project: Flask\n', md)

    @unittest.skipUnless(importlib.util.find_spec('markdown_it'),
                         'verify.py needs markdown-it-py (Python 3.10+)')
    def test_verify(self):
        import verify  # pylint: disable=import-outside-toplevel
        with contextlib.redirect_stdout(io.StringIO()) as text:
            status = verify.main([str(self.tree.root), str(self.tree.out),
                                  '--jobs', '1'])
        self.assertEqual(status, 0, text.getvalue())
        for want in ('recall     min 1.0000', 'heading sequence differs: 0',
                     'tables  src=      0 md=      0', 'leaks 0'):
            self.assertIn(want, text.getvalue())
        self.assertRegex(text.getvalue(),
                         r'links +src= +\d+ md= +\d+ +pages differ: 0 ')
        # A page without a project is a hard failure.
        with tempfile.TemporaryDirectory() as out:
            out = pathlib.Path(out)
            page = out / 'single/index.md'
            page.parent.mkdir()
            page.write_text(self.read('single/index.md').replace(
                'project: Flask', 'project: null'), encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()) as text:
                verify.main([str(self.tree.root / 'single'), str(out / 'single'),
                             '--jobs', '1'])
        self.assertIn('bad 1', text.getvalue())
        # GFM strikethrough that the converter did not escape loses "~".
        with tempfile.TemporaryDirectory() as out:
            out = pathlib.Path(out)
            page = out / 'kit/index.md'
            page.parent.mkdir()
            page.write_text(self.read('kit/index.md').replace(
                '\\~\\~10\\~\\~', '~~10~~'), encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()) as text:
                verify.main([str(self.tree.root / 'kit'), str(out / 'kit'),
                             '--jobs', '1'])
        self.assertIn("index.md {'~': (7, 3)}", text.getvalue())
        # The text of a hidden tab panel counts.
        with tempfile.TemporaryDirectory() as out:
            out = pathlib.Path(out)
            page = out / 'kit/index.md'
            page.parent.mkdir()
            page.write_text(self.read('kit/index.md').replace(
                'Closed panel.', ''), encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()) as text:
                verify.main([str(self.tree.root / 'kit'), str(out / 'kit'),
                             '--jobs', '1'])
        self.assertNotIn('recall     min 1.0000', text.getvalue())


HAIKU = """<!DOCTYPE html><html><head><title>{title}</title>{links}</head>
<body><div class="header" role="banner"><h1 class="heading"><a href="{home}">\
<span>Repro documentation</span></a></h1></div><div class="content">\
<div class="body" role="main">{body}</div></div></body></html>"""

KIT6 = """<section id="k"><h1>Kit six</h1>
<nav aria-label="breadcrumb"><ol class="breadcrumb"><li><a href="index.html">\
Start</a></li><li>A page</li></ol></nav>
<table class="docutils align-default"><tbody><tr class="row-odd"><td><p>\
<strong>A</strong></p></td><td><p><strong>B</strong></p></td></tr>\
<tr class="row-even"><td><p>1</p></td><td><p>2</p></td></tr></tbody></table>
<dl><dt class="sig sig-object cpp" id="_CPPv4N1C1fEd"><span class="kt">\
<span class="pre">void</span></span><span class="w"> </span>\
<span class="sig-name descname"><span class="n"><span class="pre">f</span>\
</span></span><span class="sig-paren">(</span><span class="kt">\
<span class="pre">double</span></span><span class="w"> </span>\
<span class="n sig-param"><span class="pre">d</span></span>\
<span class="sig-paren">)</span></dt><dd><p>Alias.</p></dd></dl>
<dl><dt class="sig sig-object cpp"><span class="kt"><span class="pre">void\
</span></span><span class="w"> </span><a class="reference internal" \
href="#_CPPv4N1C1fEd"><span class="n"><span class="pre">g</span></span></a>\
<span class="p"><span class="pre">(</span></span><span class="kt">\
<span class="pre">int</span></span><span class="p"><span class="pre">)</span>\
</span><br/></dt><dd></dd></dl>
<div class="highlight-text notranslate"><div class="highlight"><pre><span>\
</span>a\x20\x20
b
</pre></div></div>
<ul class="simple"><li><p>An item <span class="math notranslate nohighlight">\
\\(E = mc^2\\)</span> here.</p></li></ul>
<p><a class="reference external" href="tel:+123">Call</a> and \
<a class="reference internal" href="#term-role"><span class="xref std \
std-term">role</span></a>s matter at 150 MHz.</p>
<div class="highlight-shell-session notranslate"><div class="highlight"><pre>\
<span></span><span class="gp">$ </span>hello
<span class="go">Name [Default]:\x20</span>
</pre></div></div>
<p>See<a class="footnote-reference brackets" href="#f1" id="id1">1</a> and \
<a class="footnote-reference brackets" href="#f1" id="id2">1</a>.</p>
<dl class="footnote brackets">
<dt class="label" id="f1"><span class="brackets">1</span>\
<span class="fn-backref">(<a href="#id1">1</a>,<a href="#id2">2</a>)</span></dt>
<dd><p>The note.</p></dd></dl>
<section id="g"><h2><span>Generic Alias</span>, Union<a class="headerlink" \
href="#g">¶</a></h2><p>Text.</p></section></section>"""


class SphinxRound6Test(unittest.TestCase):
    """The defects that the review agents found in round S6."""

    @classmethod
    def setUpClass(cls):
        cls.tree = SphinxTree()
        cls.addClassCleanup(cls.tree.close)
        # html_link_suffix = '': links have no suffix, names have dots.
        cls.tree.write('nos/searchindex.js', 'Search.setIndex({})')
        cls.tree.write('nos/index.html', BODY.format(
            title='Nos', nav='', body=(
                '<h1>Nos</h1><p><a class="reference internal" '
                'href="pkg.google">pkg.google</a> and <a class="reference '
                'internal" href="plain">Plain</a></p>')))
        for name in ('pkg.google', 'plain'):
            cls.tree.write('nos/%s.html' % name, BODY.format(
                title=name, nav='', body='<h1>%s</h1>' % name))
        # The haiku theme: an <h1> in the page header.
        cls.tree.write('hk/searchindex.js', 'Search.setIndex({})')
        toc = ('<div class="toctree-wrapper compound"><ul><li class="'
               'toctree-l1"><a class="reference internal" href="{0}">{1}'
               '</a></li></ul></div>')
        for rel, title, home, body, links in (
                ('index.html', 'Repro', 'index.html',
                 toc.format('guide/index.html', 'Guide'),
                 '<link rel="next" href="guide/index.html"/>'),
                ('guide/index.html', 'Guide', '../index.html',
                 toc.format('install.html', 'Install'),
                 '<link rel="prev" href="../index.html"/>'),
                ('guide/install.html', 'Install', '../index.html', '', '')):
            cls.tree.write('hk/' + rel, HAIKU.format(
                title=title + ' — Repro documentation', links=links,
                home=home, body='<h1>%s</h1>%s' % (title, body)))
        cls.tree.write('kit6/searchindex.js', 'Search.setIndex({})')
        cls.tree.write('kit6/index.html', BODY.format(title='Kit six', nav='',
                                                      body=KIT6))
        cls.tree.write('kit6/attrs.html', BODY.format(
            title='attrs: Classes', nav='', body=(
                '<h1><code class="docutils literal"><span class="pre">attrs'
                '</span></code>: Classes<a class="headerlink" href="#a">'
                '\u00b6</a></h1>')))
        # A viewcode page of Sphinx 9: the "[docs]" markup adds lines.
        cls.tree.write('kit6/_modules/m.html', BODY.format(
            title='m', nav='', body=(
                '<h1>Source code for m</h1><div class="highlight"><pre><span>'
                '</span><span class="kn">import</span> os\n\n<div class="'
                'viewcode-block" id="f">\n<a class="viewcode-back" href="'
                '../index.html#m.f">[docs]</a>\n<span class="k">def</span> '
                'f():\n    pass\n</div>\n\nX = 1\n</pre></div>')))
        # A redirect (sphinx-reredirects) has no article and no .md file.
        cls.tree.write('kit6/old.html', (
            '<html><head><noscript><meta http-equiv="refresh" content="0; '
            'url=index.html"/></noscript></head><body><p>You should have '
            'been redirected.</p><a href="index.html">If not, click here '
            'to continue.</a></body></html>'))
        cls.tree.convert()

    def read(self, rel):
        return (self.tree.out / rel).read_text(encoding='utf-8')

    def test_link_without_suffix_to_dotted_name(self):
        self.assertIn('[pkg.google](pkg.google.md) and [Plain](plain.md)',
                      self.read('nos/index.md'))

    def test_breadcrumb_title_from_article(self):
        self.assertIn('breadcrumb:\n- Repro\n- Guide\n',
                      self.read('hk/guide/install.md'))

    def test_nav_bar_bold_header_alias_and_code(self):
        md = self.read('kit6/index.md')
        self.assertNotIn('Start', md)
        self.assertIn('| **A** | **B** |\n| --- | --- |\n| 1 | 2 |', md)
        self.assertIn('```cpp\nvoid f(double d)\n```', md)
        self.assertIn('```\na  \nb\n```', md)
        self.assertIn('$ hello\nName [Default]: \n```', md)
        self.assertIn('## `void g(int)`\n\n```cpp\nvoid g(int)\n```', md)
        self.assertIn('```\nimport os\n\ndef f():\n    pass\n\nX = 1\n```',
                      self.read('kit6/_modules/m.md'))

    def test_footnote_label_without_back_references(self):
        self.assertIn('[1] The note.', self.read('kit6/index.md'))

    def test_title_with_code(self):
        self.assertIn('title: \'attrs: Classes\'\n',
                      self.read('kit6/attrs.md'))

    def verify(self, build, out=None):
        import verify  # pylint: disable=import-outside-toplevel
        with contextlib.redirect_stdout(io.StringIO()) as text:
            status = verify.main([str(self.tree.root / build),
                                  str(out or self.tree.out / build),
                                  '--jobs', '1'])
        return status, text.getvalue()

    @unittest.skipUnless(importlib.util.find_spec('markdown_it'),
                         'needs markdown-it-py')
    def test_verify(self):
        for build in ('nos', 'hk', 'kit6'):
            status, text = self.verify(build)
            self.assertEqual(status, 0, text)
            for want in ('recall     min 1.0000', 'heading sequence differs: 0',
                         'numbers differ: 0', 'code blocks differ: 0',
                         'link and image targets differ: 0',
                         'titles differ: 0',
                         'html pages not checked (no main content): 0'):
                self.assertIn(want, text, build)
            # A tel: link is no broken file.
            self.assertNotIn('broken', text.split('\nlinks ', 1)[1].split(
                '\n', 1)[0])
        self.assertIn("content removed by hidden rules: 3 words [('nav', 3)]",
                      text)

    @unittest.skipUnless(importlib.util.find_spec('markdown_it'),
                         'needs markdown-it-py')
    def test_verify_sees_changed_values(self):
        md = self.read('kit6/index.md')
        for old, new, want in (
                ('150 MHz', '1500 MHz', 'numbers differ: 1'),
                ('void f(double d)\n```', 'void f(double)\n```',
                 'code blocks differ: 1'),
                ('a  \nb\n```', 'a\n  b\n```', 'code blocks differ: 1'),
                ('](tel:+123)', '](tel:+124)',
                 'link and image targets differ: 1'),
                ('title: Kit six', 'title: Kit 6', 'titles differ: 1'),
                ('[1] The note.', '[1(1,2)] The note.', 'numbers differ: 1'),
                ('Name [Default]: \n', 'Name [Default]:\n',
                 'code blocks differ: 1')):
            self.assertIn(old, md)
            with tempfile.TemporaryDirectory() as out:
                out = pathlib.Path(out) / 'kit6'
                out.mkdir()
                (out / 'index.md').write_text(md.replace(old, new),
                                              encoding='utf-8')
                self.assertIn(want, self.verify('kit6', out)[1], old)


if __name__ == '__main__':
    unittest.main()
