"""Tests for flare2md. Run: python -m unittest discover -s tests -v"""

import contextlib
import csv
import importlib.util
import io
import pathlib
import sys
import tempfile
import textwrap
import time
import unittest
import unittest.mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import flare2md  # pylint: disable=wrong-import-position

HELP_SYSTEM = ('\ufeff<?xml version="1.0" encoding="utf-8"?>\n'
               '<WebHelpSystem DefaultUrl="Content/doc/Home.html" '
               'Toc="Data/Tocs/doc.js" SearchUrl="Content/Search.htm">'
               '</WebHelpSystem>\n')
TOC_TREE = ("define({numchunks:1,prefix:'doc_Chunk',chunkstart:["
            "'/Content/doc/Home.html'],tree:{n:[{i:0,c:0,n:[{i:1,c:0},"
            "{i:2,c:0}]}]}});")
TOC_CHUNK = ("define({'/Content/doc/Home.html':{i:[0],t:['Home'],b:['']},"
             "'/Content/doc/sec/Page.html':{i:[1],t:['Page'],b:['']},"
             "'/Content/doc/sec/NoMain.html':{i:[2],t:['No main'],b:['']}});")
PAGE = """<!DOCTYPE html>
<html data-mc-toc-path="{toc}"><head><title>{title}</title></head><body>
<div id="mc-main-content">{body}
<div id="buildmetadata"><li id="productname"> Prod </li>
<li id="productrelease"> 9 </li></div></div>
<input id="pageid" value="42"/></body></html>"""


def page(body, title='T', toc='Home|Sec'):
    return PAGE.format(body=body, title=title, toc=toc)


class HelpTree:
    """A small Flare WebHelp2 tree in a temporary folder."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / 'help'
        self.content = self.root / 'Content' / 'doc'
        self.out = pathlib.Path(self.tmp.name) / 'out'
        self.write('Data/HelpSystem.xml', HELP_SYSTEM)
        self.write('Data/Tocs/doc.js', TOC_TREE)
        self.write('Data/Tocs/doc_Chunk0.js', TOC_CHUNK)
        self.write('Content/Search.htm',
                   '<html><div id="mc-main-content">x</div></html>')
        self.write('Content/doc/Home.html', page('<h1>Home</h1>'))

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding='utf-8')
        return path

    def convert(self, src=None, **kwargs):
        config = flare2md.make_config(src or self.content, self.out, **kwargs)
        return flare2md.convert(config, jobs=1)

    def md(self, rel):
        # Bytes, not text mode, so that a CR in the output shows.
        text = (self.out / rel).read_bytes().decode('utf-8')
        head, body = text.split('\n---\n', 1)
        return head, body.lstrip('\n')

    def close(self):
        self.tmp.cleanup()


class ConvertTest(unittest.TestCase):
    """Converts one page body and checks the Markdown."""

    def setUp(self):
        self.tree = HelpTree()
        self.addCleanup(self.tree.close)

    def body(self, html):
        self.tree.write('Content/doc/sec/Page.html', page(html))
        counts = self.tree.convert()
        self.assertEqual(counts['errors'], 0)
        return self.tree.md('sec/Page.md')[1]

    def test_text_that_looks_like_markdown_is_escaped(self):
        md = self.body('<h1>T</h1><p>2. Click</p><p>=====</p>'
                       '<p>Use &lt;library name&gt; and a*b*c and _x_.</p>'
                       '<p>Type &amp;ohm; here.</p>')
        self.assertIn('2\\. Click', md)
        self.assertIn('\\=====', md)
        self.assertIn('\\<library name>', md)
        self.assertIn('a\\*b\\*c', md)
        self.assertIn('\\_x\\_', md)
        self.assertIn('\\&ohm;', md)
        md = self.body('<h1>T</h1><p>PortZ[1](real) and y[1][3]</p>')
        self.assertIn('PortZ[1\\](real) and y[1][3]', md)

    def test_source_backslash_is_kept(self):
        md = self.body('<h1>T</h1><p>C:\\dir\\</p><p>a\\*b</p>')
        self.assertIn('C:\\dir\\\\', md)
        self.assertIn('a\\\\\\*b', md)

    def test_nested_and_empty_emphasis(self):
        md = self.body('<h1>T</h1><p><em><em>x</em></em> '
                       '<strong> <br/></strong>y <b>a<b>b</b></b></p>')
        self.assertIn('*x*', md)
        self.assertNotIn('**x**', md)
        self.assertNotIn('** **', md)
        self.assertIn('**ab**', md)

    def test_emphasis_around_a_block_is_removed(self):
        md = self.body('<h1>T</h1><p>a <strong><br/><br/>'
                       '<img src="i.png"/></strong></p>'
                       '<div><em>x<p>block</p></em></div>')
        self.assertIn('a\n\n**![i](i.png)**', md)
        self.assertIn('x\n\nblock', md)

    def test_break_at_link_edge(self):
        md = self.body('<h1>T</h1><p><a href="http://e.com/a">text<br/></a>'
                       ' more</p>')
        self.assertIn('[text](http://e.com/a)', md)

    def test_inline_code_with_breaks(self):
        md = self.body('<h1>T</h1><p><code>line one<br/>line two</code></p>'
                       '<p>See <code>a<br/>b</code> here.</p>')
        self.assertIn('```\nline one\nline two\n```', md)
        self.assertIn('`a`\\\n`b`', md)

    def test_link_and_emphasis_in_code_move_outside(self):
        md = self.body('<h1>T</h1><p><code><a href="http://e.com">x</a></code>'
                       ' <code><strong><em> y </em></strong></code></p>')
        self.assertIn('[`x`](http://e.com)', md)
        self.assertIn('***`y`***', md)

    def test_code_with_fence_inside(self):
        md = self.body('<h1>T</h1><pre>md:\n```\nx  y\n```</pre><p>after</p>')
        self.assertIn('````\nmd:\n```\nx  y\n```\n````\n\nafter', md)

    def test_touching_code_spans_merge(self):
        md = self.body('<h1>T</h1><p><code>a</code><code>b</code></p>')
        self.assertIn('`ab`', md)

    def test_note_box(self):
        md = self.body(
            '<h1>T</h1><table class="note_table"><tr>'
            '<td class="note_td_one"><img src="warning.svg"/></td>'
            '<td class="note_td_two">Hot surface.</td></tr></table>')
        self.assertIn('> **Warning:**\n>\n> Hot surface.', md)

    def test_code_panel(self):
        md = self.body(
            '<h1>T</h1><div class="code panel"><div class="codeHeader">'
            'Example</div><pre data-syntaxhighlighter-params="brush: java">'
            'a = 1\n\n\nb = 2</pre></div>')
        self.assertIn('**Example**\n\n```\na = 1\n\n\nb = 2\n```', md)

    def test_simple_table(self):
        md = self.body('<h1>T</h1><table><tr><td>a|b</td><td>c</td></tr>'
                       '<tr><td>d</td></tr></table>')
        self.assertIn('| | |\n| --- | --- |\n| a\\|b | c |\n| d | |', md)

    def test_header_row_from_th(self):
        md = self.body('<h1>T</h1><table><tr><th>H1</th><th>H2</th></tr>'
                       '<tr><td>1</td><td>2</td></tr></table>')
        self.assertIn('| H1 | H2 |\n| --- | --- |\n| 1 | 2 |', md)

    def test_complex_table_is_html_without_escapes(self):
        md = self.body('<h1>T</h1><table><tr><td rowspan="2">2. x</td>'
                       '<td><a href="http://e.com"></a>y</td></tr>'
                       '<tr><td>z</td></tr></table>')
        self.assertIn('<td rowspan="2">2. x</td>', md)
        self.assertNotIn('\\', md)
        self.assertNotIn('<a', md)

    def test_raw_table_at_list_item_start(self):
        md = self.body('<h1>T</h1><ol><li><table><tr><td rowspan="2">a</td>'
                       '<td>b</td></tr><tr><td>c</td></tr></table></li>'
                       '<li>next</li></ol>')
        self.assertIn('1. <table>\n   <tbody>\n   <tr><td rowspan="2">a</td>'
                      '<td>b</td></tr>\n   <tr><td>c</td></tr>\n   </tbody>\n'
                      '   </table>\n2. next', md)

    def test_blank_lines_in_raw_table_code(self):
        md = self.body('<h1>T</h1><table><tr><td rowspan="2"><pre>a\n\n\n'
                       '  b</pre></td><td>x</td></tr><tr><td>y</td></tr>'
                       '</table>')
        self.assertIn('<pre>a\n&#10;&#10;  b</pre>', md)

    def test_links(self):
        md = self.body(
            '<h1>T</h1><p><a href="../Home.html">home</a> '
            '<a href="f(x).html#top">f</a> '
            '<a href="http://e.com/a b(1)">ext</a> '
            '<a href="/x/createpage.action?t=1">dead</a> '
            '<a href="qthelp://p/doc/Home.html">qt</a> '
            '<a href="qthelp://p/doc/none.html">gone</a></p>')
        self.assertIn('[home](../Home.md)', md)
        self.assertIn('[f](f%28x%29.md)', md)
        self.assertIn('[ext](http://e.com/a%20b%281%29)', md)
        self.assertIn(' dead ', md)
        self.assertIn('[qt](../Home.md)', md)
        self.assertIn(' gone', md)

    def test_images_are_copied_or_reported(self):
        self.tree.write('Content/doc/img/a (1).png', b'PNG')
        md = self.body('<h1>T</h1><p><img src="../img/a (1).png"/>'
                       '<img src="../img/none.png"/></p>')
        self.assertIn('![a (1)](../img/a%20%281%29.png)', md)
        self.assertEqual((self.tree.out / 'img' / 'a (1).png').read_bytes(),
                         b'PNG')
        missing = (self.tree.out / '_report' / 'missing_images.txt'
                   ).read_text(encoding='utf-8')
        self.assertIn('sec/Page.html\t../img/none.png', missing)

    def test_adjacent_ordered_lists_stay_apart(self):
        md = self.body('<h1>T</h1><ol><li>a</li><li>b</li></ol>'
                       '<ol><li>c</li></ol><p>x</p><ol><li>d</li></ol>')
        self.assertIn('1. a\n2. b\n\n<!-- -->\n\n1. c\n\nx\n\n1. d', md)
        md = self.body('<h1>T</h1><div><ol><li>a</li></ol></div><div></div>'
                       '<div><div><ol><li>b</li></ol></div></div>'
                       '<ol><li><ol><li>c</li></ol></li></ol>')
        self.assertIn('1. a\n\n<!-- -->\n\n1. b\n\n<!-- -->\n\n1. 1. c', md)

    def test_empty_code_block_is_dropped(self):
        md = self.body('<h1>T</h1><p>x</p><pre><br/></pre>')
        self.assertNotIn('```', md)

    def test_nested_list_in_note_keeps_indent(self):
        md = self.body(
            '<h1>T</h1><table class="note_table"><tr>'
            '<td class="note_td_one"><img src="note.svg"/></td>'
            '<td class="note_td_two"><ul><li>a<ul><li>b</li></ul></li></ul>'
            '</td></tr></table>')
        self.assertIn('> - a\n>   - b', md)

    def test_break_at_block_start(self):
        md = self.body('<h1>T</h1><p>x</p><p><span><br/> Next</span></p>')
        self.assertIn('x\n\nNext', md)

    # ---- Round 7 review findings -------------------------------------------

    def test_r7_link_definition_text(self):
        md = self.body('<h1>T</h1><p>[Default]: none</p>')
        self.assertIn('\\[Default]: none', md)

    def test_r7_touching_emphasis_merges(self):
        md = self.body('<h1>T</h1><p><em>a</em><em>b</em> '
                       '<b>abc</b><b>def</b></p>')
        self.assertIn('*ab* **abcdef**', md)

    def test_r7_emphasis_that_cannot_flank(self):
        md = self.body('<h1>T</h1><p><b>Note:</b>Text and x<b>(y)</b>z '
                       'and <b>ok</b>.</p>')
        self.assertIn('<strong>Note:</strong>Text and x<strong>(y)</strong>z'
                      ' and **ok**.', md)
        md = self.body('<h1>T</h1><table><tr><td><b>Note:</b><br/>x</td>'
                       '<td>y</td></tr></table>')
        self.assertIn('| **Note:** <br> x | y |', md)

    def test_r7_line_start_after_empty_inline_and_hr(self):
        md = self.body('<h1>T</h1><p><span id="x"></span>1. Click</p>'
                       '<div><p>a</p><hr/>2. Next</div>')
        self.assertIn('1\\. Click', md)
        self.assertIn('2\\. Next', md)

    def test_r7_kbd_samp_tt_are_code(self):
        md = self.body('<h1>T</h1><p><kbd>a</kbd><kbd>b</kbd> <tt>c</tt> '
                       '<samp>C:\\dir\\</samp></p>')
        self.assertIn('`ab` `c` `C:\\dir\\`', md)

    def test_r7_divs_in_raw_table_stay_apart(self):
        md = self.body('<h1>T</h1><table><tr><td rowspan="2"><div>one</div>'
                       '<div>two</div></td><td>x</td></tr><tr><td>y</td></tr>'
                       '</table>')
        self.assertIn('<p>one</p><p>two</p>', md)

    def test_r7_pipe_in_image_alt_in_cell(self):
        self.tree.write('Content/doc/sec/a.png', b'PNG')
        md = self.body('<h1>T</h1><table><tr><td><img alt="a|b" src="a.png"/>'
                       '</td><td>c</td></tr></table>')
        self.assertIn('| ![a\\|b](a.png) | c |', md)

    def test_r7_heading_with_trailing_hash(self):
        md = self.body('<h1>T</h1><h2>Port #</h2><h2>C#</h2>')
        self.assertIn('## Port \\#', md)
        self.assertIn('## C#', md)

    def test_r7_code_in_nested_list_keeps_spaces(self):
        md = self.body('<h1>T</h1><ul><li><ul><li><pre>a  b\n\n\nc</pre>'
                       '</li></ul></li></ul><pre>x  =  1\n\n\ny</pre>')
        self.assertIn('a  b\n\n\n', md)
        self.assertIn('x  =  1\n\n\ny', md)

    def test_r7_cp1252_page(self):
        path = self.tree.write('Content/doc/sec/Page.html', b'')
        path.write_bytes(page('<h1>T</h1><p>caf\xe9 25\xb0C \u2013</p>'
                              ).encode('cp1252'))
        self.tree.convert()
        self.assertIn('caf\u00e9 25\u00b0C \u2013',
                      self.tree.md('sec/Page.md')[1])

    def test_r7_table_with_only_full_rows(self):
        md = self.body('<h1>T</h1><table><tr><td colspan="2">Title</td></tr>'
                       '<tr><td colspan="2">Body</td></tr></table>')
        self.assertIn('Title', md)
        self.assertIn('Body', md)

    def test_r7_local_file_links_are_encoded(self):
        md = self.body('<h1>T</h1><p><a href="../files/User Guide.pdf">g</a>'
                       ' <a href="..\\Home.html">h</a> '
                       '<a href="/Content/doc/Home.html">r</a> '
                       '<a href="Page.html?q=1#x">q</a></p>')
        self.assertIn('[g](../files/User%20Guide.pdf)', md)
        self.assertIn('[h](../Home.md)', md)
        self.assertIn('[r](../Home.md)', md)
        self.assertIn('[q](Page.md)', md)

    def test_r7_backslash_image_path(self):
        self.tree.write('Content/doc/img/p.png', b'PNG')
        md = self.body('<h1>T</h1><p><img src="..\\img\\p.png"/></p>')
        self.assertIn('![p](../img/p.png)', md)

    def test_r7_adjacent_bullet_lists_stay_apart(self):
        md = self.body('<h1>T</h1><ul><li>a</li></ul><ul><li>b</li></ul>')
        self.assertIn('- a\n\n<!-- -->\n\n- b', md)

    def test_r7_tfoot_rows_go_last(self):
        md = self.body('<h1>T</h1><table><tfoot><tr><td>f</td><td>g</td></tr>'
                       '</tfoot><tbody><tr><td>a</td><td>b</td></tr></tbody>'
                       '</table>')
        self.assertIn('| a | b |\n| f | g |', md)

    def test_r7_break_before_sup(self):
        md = self.body('<h1>T</h1><p>line<br/><sup>2</sup></p>')
        self.assertIn('line\\\n<sup>2</sup>', md)

    def test_r7_only_accepts_absolute_paths(self):
        self.tree.write('Content/doc/sec/Page.html', page('<h1>T</h1>'))
        cfg = flare2md.make_config(self.tree.content, self.tree.out)
        absolute = str(self.tree.content / 'sec' / 'Page.html')
        self.assertEqual(flare2md.page_rel(cfg, absolute), 'sec/Page.html')
        self.assertEqual(flare2md.page_rel(cfg, 'sec/Page.html'),
                         'sec/Page.html')
        self.assertIsNone(flare2md.page_rel(cfg, '../x.html'))

    def test_r8_pipe_in_url_and_drive_paths(self):
        md = self.body('<h1>T</h1><table><tr><td><a href="https://x.org/a|b">'
                       't</a></td><td>c</td></tr></table>'
                       '<p><a href="C:\\docs\\x.pdf">drive</a></p>')
        self.assertIn('| [t](https://x.org/a%7Cb) | c |', md)
        self.assertIn('\ndrive\n', md)

    def test_r8_emphasis_touching_other_emphasis(self):
        md = self.body('<h1>T</h1><p><b>Note:</b><i>text</i> and '
                       '<b>Run</b><i>(x)</i> and <b>a<i>b</i></b><i>c</i> and '
                       '<b>N:</b><span><b>b</b></span></p>')
        self.assertIn('<strong>Note:</strong>*text*', md)
        self.assertIn('<strong>Run</strong>*(x)*', md)
        self.assertNotIn('****', md)
        self.assertNotIn('**N:****b**', md)
        md = self.body('<h1>T</h1><p><b>X:</b><i> Shape</i> <i>a </i><b>b</b>'
                       '</p>')
        self.assertIn('**X:** *Shape* *a* **b**', md)

    def test_r8_flanking_through_other_inline_tags(self):
        md = self.body('<h1>T</h1><p><b>Note:</b><font>Text</font> and '
                       'x<b><small>(y)</small></b>z</p>')
        self.assertIn('<strong>Note:</strong>Text', md)
        self.assertIn('x<strong>(y)</strong>z', md)

    def test_r8_merge_across_vanishing_gaps(self):
        md = self.body('<h1>T</h1><p><b>a</b>&#8203;<b>b</b> '
                       '<i>c</i><!--x--><i>d</i></p>')
        self.assertIn('**ab** *cd*', md)

    def test_r8_image_src_with_query(self):
        self.tree.write('Content/doc/sec/a.png', b'PNG')
        md = self.body('<h1>T</h1><p><img src="a.png?version=1&amp;api=v2"/>'
                       ' <img src="a.png#x"/></p>')
        self.assertIn('![a](a.png) ![a](a.png)', md)

    def test_r8_link_text_equal_to_href(self):
        md = self.body('<h1>T</h1><p><a href="x.pdf">x.pdf</a></p>')
        self.assertIn('[x.pdf](x.pdf)', md)

    def test_r8_scheme_case_and_script_links(self):
        md = self.body('<h1>T</h1><p><a href="HTTP://www.example.com/">a</a> '
                       '<a href="FILE:///c:/x.pdf">b</a> '
                       '<a href="javascript:void(0)">c</a> '
                       '<a href=" Page.html ">d</a></p>')
        self.assertIn('[a](HTTP://www.example.com/) b c [d](Page.md)', md)

    def test_r8_caption_of_table_with_only_full_rows(self):
        md = self.body('<h1>T</h1><table><caption>Cap</caption><tr>'
                       '<td colspan="2">Title</td></tr><tr><td colspan="2">'
                       'Body</td></tr></table>')
        self.assertIn('Cap\n\nTitle\n\nBody', md)

    def test_r8_latin1_label_is_cp1252(self):
        path = self.tree.write('Content/doc/sec/Page.html', b'')
        path.write_bytes(page('<h1>T</h1><p>\u201cq\u201d</p>').replace(
            '<head>', '<head><meta charset="iso-8859-1">').encode('cp1252'))
        self.tree.convert()
        self.assertIn('\u201cq\u201d', self.tree.md('sec/Page.md')[1])

    def test_r8_split_markers(self):
        md = self.body('<h1>T</h1><p><span>1</span><span>. Click</span></p>'
                       '<p>2<span>. Next</span></p>'
                       '<p>[<b>Default</b>]: none</p>'
                       '<p><span>[Other]</span>: none</p>')
        self.assertIn('1\\. Click', md)
        self.assertIn('2\\. Next', md)
        self.assertIn('\\[**Default**]: none', md)
        self.assertIn('\\[Other]: none', md)

    def test_r8_brackets_in_link_text(self):
        md = self.body('<h1>T</h1><p><a href="Page.html">a]b</a> '
                       '<a href="Page.html">x[y</a></p>')
        self.assertIn('[a\\]b](Page.md) [x\\[y](Page.md)', md)

    def test_r8_more_line_starts(self):
        md = self.body('<h1>T</h1><p>~~~</p><p>-- -</p><p>a<br/>| x</p>')
        self.assertIn('\\~~~', md)
        self.assertIn('\\-- -', md)
        self.assertIn('a\\\n| x', md)

    def test_r9_empty_strikethrough(self):
        md = self.body('<h1>T</h1><p>a</p><p><s><br/></s></p><p><del> </del>b'
                       '</p>')
        self.assertNotIn('~', md)
        self.assertIn('a\n\nb', md)

    def test_r10_text_after_nested_list(self):
        md = self.body('<h1>T</h1><ol><li>Step<ul><li>sub a</li><li>sub b'
                       '</li></ul>Then <b>this</b>.</li><li>Two</li></ol>')
        self.assertIn('   - sub b\n\n   Then **this**.\n2. Two', md)
        md = self.body('<h1>T</h1><ul><li>a<ul><li>b</li></ul> <br/> </li>'
                       '</ul>')
        self.assertIn('- a\n  - b', md)
        self.assertNotIn('<p>', md)

    def test_s2_code_span_at_line_start_is_no_fence(self):
        # "``` a``b ```" opens no fence (backticks in the info string), so
        # the lines after it are no code: tidy() still cleans them.
        md = self.body('<h1>T</h1><ul><li><code>a``b</code> x</li></ul>'
                       '<p>one<br/></p><p>two</p>')
        self.assertIn('- ``` a``b ``` x\n\none\n\ntwo', md)

    def test_s2_code_in_removed_link_merges(self):
        md = self.body('<h1>T</h1><p><a href="#x"><code>a</code></a>'
                       '<code>(b)</code> c</p>')
        self.assertIn('`a(b)` c', md)

    def test_s2_nested_list_in_cell_keeps_html_table(self):
        md = self.body('<h1>T</h1><table><tr><td><ul><li>a<ul><li>b</li></ul>'
                       '</li></ul></td><td>c</td></tr></table>')
        self.assertIn('<table>', md)

    def test_title_becomes_h1(self):
        md = self.body('<h2>Sub</h2>')
        self.assertTrue(md.startswith('# T\n\n## Sub'))


class PageSetTest(unittest.TestCase):
    """Checks the page list, the YAML header and the reports."""

    def setUp(self):
        self.tree = HelpTree()
        self.addCleanup(self.tree.close)

    def test_header_from_page_and_build_metadata(self):
        self.tree.write('Content/doc/sec/Page.html',
                        page('<h1>T</h1>', title='My  page'))
        self.tree.convert(source_prefix='doc/x/')
        head, _ = self.tree.md('sec/Page.md')
        self.assertEqual(head, textwrap.dedent("""\
            ---
            title: My page
            source: doc/x/Content/doc/sec/Page.html
            breadcrumb:
            - Home
            - Sec
            section: sec
            page_id: '42'
            product: Prod
            version: '9'"""))

    def test_product_and_version_options(self):
        self.tree.convert(product='P', version='2025')
        head, _ = self.tree.md('Home.md')
        self.assertIn('product: P\nversion: \'2025\'', head)

    def test_breadcrumb_from_toc_and_no_main_div(self):
        self.tree.write('Content/doc/sec/NoMain.html',
                        '<html><head><title>N</title></head>'
                        '<body><p>Text</p></body></html>')
        self.tree.write('Content/doc/sec/Shell.html',
                        '<html><body>frame</body></html>')
        counts = self.tree.convert()
        head, body = self.tree.md('sec/NoMain.md')
        self.assertIn('breadcrumb:\n- Home\n', head)
        self.assertIn('Text', body)
        self.assertEqual(counts['skipped'], 1)  # Shell.html is not a topic.
        self.assertFalse((self.tree.out / 'sec' / 'Shell.md').exists())

    def test_help_root_as_source(self):
        self.tree.write('Content/doc/sec/Page.html', page('<h1>T</h1>'))
        counts = self.tree.convert(src=self.tree.root)
        self.assertEqual(counts['converted'], 2)  # Search.htm is skipped.
        head, _ = self.tree.md('Content/doc/sec/Page.md')
        self.assertIn('section: sec', head)

    def test_stale_files_are_reported_not_deleted(self):
        stale = self.tree.out / 'old.md'
        stale.parent.mkdir(parents=True)
        stale.write_text('x')
        counts = self.tree.convert()
        self.assertEqual(counts['stale'], 1)
        self.assertTrue(stale.exists())

    def test_case_collisions_are_reported(self):
        self.assertEqual(flare2md.find_collisions(
            ['a/X.html', 'a/x.htm', 'b.html']), [['a/X.html', 'a/x.htm']])

    def test_load_toc(self):
        toc = flare2md.load_toc(self.tree.root / 'Data' / 'Tocs' / 'doc.js')
        self.assertEqual(toc['Content/doc/Home.html'], [])
        self.assertEqual(toc['Content/doc/sec/Page.html'], ['Home'])


class CliTest(unittest.TestCase):
    """The command line, the help-system files and the reports."""

    def setUp(self):
        self.tree = HelpTree()
        self.addCleanup(self.tree.close)
        self.tree.write('Content/doc/sec/Page.html', page('<h1>P</h1>'))

    def main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            status = flare2md.main([str(a) for a in args])
        return status, out.getvalue()

    def run_all(self, *extra):
        return self.main(self.tree.content, self.tree.out, '--jobs', '1',
                         *extra)

    def test_old_reports_are_removed(self):
        self.tree.out.mkdir()
        (self.tree.out / 'old.md').write_text('x')
        self.run_all()
        stale = self.tree.out / '_report' / 'stale.txt'
        self.assertTrue(stale.is_file())
        (self.tree.out / 'old.md').unlink()
        self.run_all()
        self.assertFalse(stale.exists())

    def test_only_keeps_the_full_reports(self):
        self.run_all()
        csv_file = self.tree.out / '_report' / 'pages.csv'
        before = csv_file.read_text()
        _, out = self.run_all('--only', 'Home.html', './Home.html')
        self.assertEqual(csv_file.read_text(), before)
        self.assertIn('pages 1 ', out)
        self.assertIn('collisions 0', out)

    def test_failed_page_is_not_stale(self):
        self.run_all()
        run = flare2md.Page.run

        def fail(page_self):
            if page_self.rel.name == 'Page.html':
                raise ValueError('boom')
            return run(page_self)

        with unittest.mock.patch.object(flare2md.Page, 'run', fail):
            status, out = self.run_all()
        self.assertEqual(status, 1)
        self.assertIn('errors 1', out)
        self.assertIn('stale 0', out)

    def test_out_dir_must_not_hold_the_source(self):
        for out in (self.tree.content, self.tree.root):
            with self.assertRaises(SystemExit):
                self.main(self.tree.content, out)

    def test_jobs_must_be_positive(self):
        with self.assertRaises(SystemExit):
            self.run_all('--jobs', '-1')

    def test_only_skips_the_search_page(self):
        status, out = self.main(self.tree.root, self.tree.out, '--jobs', '1',
                                '--only', 'Content/Search.htm')
        self.assertFalse((self.tree.out / 'Content' / 'Search.md').exists())

    def test_toc_with_spaces(self):
        self.tree.write('Data/Tocs/doc.js', TOC_TREE.replace(
            ':', ': ').replace(',', ', ').replace('{', '{ '))
        self.tree.write('Data/Tocs/doc_Chunk0.js', TOC_CHUNK.replace(
            ':{', ': {').replace(',t:', ', t: '))
        self.tree.write('Content/doc/sec/NoMain.html',
                        '<html><body><p>x</p></body></html>')
        config = flare2md.make_config(self.tree.content, self.tree.out)
        self.assertEqual(config.toc.get('Content/doc/sec/Page.html'),
                         ['Home'])

    def test_bad_help_files_do_not_stop_the_run(self):
        self.tree.write('Data/Tocs/doc.js', "define({numchunks:1});")
        status, _ = self.run_all()
        self.assertEqual(status, 0)
        self.tree.write('Data/HelpSystem.xml', '<WebHelpSystem')
        status, _ = self.run_all()
        self.assertEqual(status, 0)

    def test_page_without_body(self):
        # html5lib makes an empty <body>. The page is no error.
        self.tree.write('Content/doc/sec/NoMain.html',
                        '<html><head><title>x</title></head></html>')
        status, _ = self.run_all()
        self.assertEqual(status, 0)
        self.assertTrue((self.tree.out / 'sec' / 'NoMain.md').exists())


@unittest.skipUnless(importlib.util.find_spec('markdown_it'),
                     'verify.py needs markdown-it-py')
class VerifyTest(unittest.TestCase):
    """verify.py must fail on broken output."""

    def setUp(self):
        self.tree = HelpTree()
        self.addCleanup(self.tree.close)
        self.tree.write('Content/doc/sec/Page.html', page(
            '<h1>P</h1><p>5 \u03a9 at 25 \u00b0C</p><ul><li>One<ul><li>Two'
            '</li></ul></li></ul><p><img src="a.png"/></p>'))
        self.tree.write('Content/doc/sec/a.png', b'PNG')
        self.tree.convert()

    def verify(self, out=None):
        import verify  # pylint: disable=import-outside-toplevel
        with contextlib.redirect_stdout(io.StringIO()) as text, \
                contextlib.redirect_stderr(io.StringIO()):
            status = verify.main([str(self.tree.content),
                                  str(out or self.tree.out), '--jobs', '1'])
        return status, text.getvalue()

    def edit(self, old, new):
        md = self.tree.out / 'sec' / 'Page.md'
        text = md.read_text(encoding='utf-8')
        self.assertIn(old, text)
        md.write_text(text.replace(old, new), encoding='utf-8')

    def test_good_output_passes(self):
        self.assertEqual(self.verify()[0], 0)

    def test_no_output_fails(self):
        self.assertEqual(self.verify(self.tree.out / 'none')[0], 1)

    def test_missing_page_fails(self):
        (self.tree.out / 'Home.md').unlink()
        self.assertEqual(self.verify()[0], 1)

    def test_nested_item_joined_into_parent_fails(self):
        self.edit('  - Two', '       Two')
        self.assertEqual(self.verify()[0], 1)

    def test_lost_non_ascii_text_is_seen(self):
        self.edit('\u03a9', '?')
        self.assertIn('recall     min 0.8', self.verify()[1])

    def test_lost_image_copy_fails(self):
        (self.tree.out / 'sec' / 'a.png').unlink()
        self.assertEqual(self.verify()[0], 1)

    def test_link_around_image_is_checked(self):
        self.edit('![a](a.png)', '[![a](a.png)](../NoSuch.md)')
        self.assertIn("'md_broken': 1", self.verify()[1])

    def test_list_items_match_across_conversion_rules(self):
        # A list directly in a list, a zero-width space, a link edge in
        # code and a full-width table row in an item all pass.
        self.tree.write('Content/doc/sec/L.html', page(
            '<h1>L</h1><ol><li>Under the tab:</li><ul><li>a</li></ul>'
            '<li>Set​ it<pre>x=<a href="mailto:a@b.c">a@b.c</a></pre>'
            '</li><li>As follows:<table><tr><td colspan="2">Term1</td></tr>'
            '<tr><td>p</td><td>q</td></tr></table></li></ol>'))
        self.tree.convert()
        status, text = self.verify()
        self.assertEqual(status, 0, text)

    def test_text_moved_into_nested_item_fails(self):
        self.tree.write('Content/doc/sec/L.html', page(
            '<h1>L</h1><ul><li>Freq<ul><li>Time</li></ul>Some text</li>'
            '</ul>'))
        self.tree.convert()
        md = self.tree.out / 'sec' / 'L.md'
        text = md.read_text(encoding='utf-8')
        self.assertIn('  - Time\n\n  Some text', text)
        md.write_text(text.replace('Time\n\n  Some', 'TimeSome'),
                      encoding='utf-8')
        self.assertEqual(self.verify()[0], 1)

    def test_odd_files_do_not_crash(self):
        (self.tree.out / 'sec' / 'Bad.md').write_bytes(b'\xff\xfe\x00x')
        (self.tree.out / 'dir.md').mkdir()
        status, text = self.verify()
        self.assertEqual(status, 1)
        self.assertIn('crashed', text)


@unittest.skipUnless(importlib.util.find_spec('markdown_it'),
                     'verify.py needs markdown-it-py')
class VerifyRound6Test(unittest.TestCase):
    """The checks that round S6 added: numbers, code, link targets, titles
    and breadcrumbs."""

    verify = VerifyTest.verify

    def setUp(self):
        self.tree = HelpTree()
        self.addCleanup(self.tree.close)
        self.tree.write('Content/doc/sec/Page.html', page(
            '<p>No heading: 150 MHz.</p><ol start="3"><li>Go</li><li>Stop'
            '</li></ol><pre>x = 1<br/>y = 2</pre><p><a href="../Home.html">Home'
            '</a> <a href="http://x.com/a\\b#c\\d">web</a> <svg><a href="'
            'http://youtu.be/8Is"><rect/></a></svg></p>',
            title='Page title', toc='|Home|Sec|'))
        self.tree.convert()

    def test_good_output_passes(self):
        status, text = self.verify()
        self.assertEqual(status, 0, text)
        for want in ('numbers differ: 0', 'code blocks differ: 0',
                     'link and image targets differ: 0', 'titles differ: 0',
                     'breadcrumbs differ: 0'):
            self.assertIn(want, text)

    def test_changes_are_seen(self):
        md = self.tree.out / 'sec' / 'Page.md'
        good = md.read_text(encoding='utf-8')
        for old, new, want in (
                ('150 MHz', '15 MHz', 'numbers differ: 1'),
                ('3. Go', '1. Go', 'numbers differ: 1'),
                ('y = 2', 'y = 3', 'code blocks differ: 1'),
                ('(../Home.md)', '(Home.md)',
                 'link and image targets differ: 1'),
                ('#c%5Cd)', '#c/d)', 'link and image targets differ: 1'),
                ('title: Page title', 'title: Other', 'titles differ: 1'),
                ('# Page title', '# Other', 'titles differ: 1'),
                ('- Sec\n', '- Sect\n', 'breadcrumbs differ: 1'),
                # An added block is in the "pres" count, not here.
                ('```\nx = 1', '```\nextra\n```\n\n```\nx = 1',
                 'code blocks differ: 0')):
            self.assertIn(old, good)
            md.write_text(good.replace(old, new, 1), encoding='utf-8')
            self.assertIn(want, self.verify()[1], old)
        md.write_text(good, encoding='utf-8')

    def test_html_page_without_main_content_is_reported(self):
        words = ' '.join(['word'] * 25)
        self.tree.write('Content/doc/sec/Plain.html',
                        '<html><body><p>%s</p></body></html>' % words)
        self.tree.write('Content/doc/sec/Moved.html', (
            '<html><head><meta http-equiv="refresh" content="0; url=Page.html"'
            '/></head><body><p>%s</p></body></html>' % words))
        self.assertIn("html pages not checked (no main content): 1 "
                      "['sec/Plain.md']", self.verify()[1])


class HelperTest(unittest.TestCase):

    def test_read_html_encodings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'p.html'
            path.write_bytes('\ufeffa\u00e9\x01'.encode('utf-8'))
            self.assertEqual(flare2md.read_html(path), 'a\u00e9')
            path.write_bytes('a\u00e9'.encode('utf-16'))  # Adds a BOM.
            self.assertEqual(flare2md.read_html(path), 'a\u00e9')
            # UTF-16LE text behind a UTF-8 BOM, with lost characters.
            text = 'ab\ubfef\xbd\ubfef\xbdc\ubfef\xbdd'
            path.write_bytes(b'\xef\xbb\xbf' + text.encode('utf-16-le'))
            self.assertEqual(flare2md.read_html(path), 'ab c\ufffdd')
            # Line ends become LF, as in a browser (old libxml2 keeps CR).
            path.write_bytes(b'<pre>\r\na\r\n\r\nb\rc</pre>')
            self.assertEqual(flare2md.read_html(path),
                             '<pre>\na\n\nb\nc</pre>')

    def test_tidy(self):
        md = 'a  b\n\n\n\n```\nx  y\n\n\n```\n\nline \\\n\n- item \\\n- two\n'
        self.assertEqual(flare2md.tidy(md),
                         'a b\n\n```\nx  y\n\n\n```\n\nline\n\n- item\n- two\n')

    def test_tidy_cell_breaks(self):
        br, esc = flare2md.BR, flare2md.ESC
        md = f'| {br}a{br} {br}b{br} | x{esc}|{br}y |\n'
        self.assertEqual(flare2md.tidy(md), '| a <br> b | x\\| <br> y |\n')

    def test_quote_path(self):
        self.assertEqual(flare2md.quote_path('a b/c(1)#.md'),
                         'a%20b/c%281%29%23.md')
        # "&amp;" in a name would be an entity. A plain "&" stays.
        self.assertEqual(flare2md.quote_path('a&amp;b&c.png'),
                         'a%26amp;b&c.png')


class RagReviewTest(unittest.TestCase):
    """Output changes from the review for a RAG pipeline."""

    body = lambda self, html: Round6Test.body(self, html)  # noqa: E731

    def setUp(self):
        self.tree = HelpTree()
        self.addCleanup(self.tree.close)

    def test_cells_get_no_line_start_escapes(self):
        # A pipe-table cell is inline content: no list, heading or quote.
        md = self.body(
            '<h1>T</h1><table><tr><th>A</th><th>B</th></tr>'
            '<tr><td>-</td><td>1. One<br/>- two</td></tr>'
            '<tr><td># x</td><td>&gt; y</td></tr>'
            '<tr><td>+</td><td>===</td></tr>'
            '<tr><td>~~~</td><td>| -</td></tr>'
            '<tr><td colspan="2">- Full row</td></tr></table>'
            '<p>- Not a list</p>')
        self.assertIn('| - | 1. One <br> - two |', md)
        self.assertIn('| # x | > y |', md)
        self.assertIn('| + | === |', md)
        # The last full-width row moves out of the table: it is a
        # paragraph.
        self.assertIn('|\n\n\\- Full row', md)
        # "~~~" and "|" are inline syntax too. They keep the escape.
        self.assertIn('| \\~~~ | \\| - |', md)
        self.assertIn('\\- Not a list', md)

    def test_fence_starts_its_own_line_in_a_list_item(self):
        # A heading splitter (LlamaIndex MarkdownNodeParser) finds a fence
        # only at the start of a line. "4. ```" would put it out of step.
        md = self.body('<h1>T</h1><ol><li>One</li><li><pre>a\n# b</pre>'
                       '<p>After.</p></li></ol><ul><li><pre>c</pre></li></ul>'
                       '<h2>Next</h2>')
        self.assertIn('2.\n   ```\n   a\n   # b\n   ```\n\n   After.', md)
        self.assertIn('-\n  ```\n  c\n  ```', md)
        self.assertNotRegex(md, r'(?m)^ *(?:[-*+]|\d+\.) +```')
        # "Text" and an empty "-" under it would be a heading.
        md = self.body('<h1>T</h1><ul><li>Text<ul><li><pre>x</pre></li>'
                       '<li><pre>y</pre></li></ul></li></ul>')
        self.assertIn('- Text\n  - <!-- -->\n    ```\n    x\n    ```\n'
                      '  -\n    ```\n    y', md)

    def test_hash_at_a_line_start_in_raw_html_pre(self):
        # A heading splitter would take "# b" in an HTML table for a heading.
        md = self.body('<h1>T</h1><table><tr><td><pre># a\n# b\n  # c</pre>'
                       '<p>x</p></td></tr></table>')
        self.assertIn('<pre># a\n&#35; b\n  # c</pre>', md)

    def test_marker_characters_in_the_source_are_removed(self):
        md = self.body('<h1>T</h1><p>a\x04- b \x05c</p>')
        self.assertIn('a- b c', md)


class Round6Test(unittest.TestCase):
    """The engine defects that the review agents found in round S6."""

    def setUp(self):
        self.tree = HelpTree()
        self.addCleanup(self.tree.close)

    def body(self, html):
        self.tree.write('Content/doc/sec/Page.html', page(html))
        counts = self.tree.convert()
        self.assertEqual(counts['errors'], 0)
        return self.tree.md('sec/Page.md')[1]

    def test_bang_before_link(self):
        md = self.body('<h1>T</h1><p>Click here!<a href="http://x.com">now'
                       '</a>, Go!<span><a href="../Home.html">page</a></span>'
                       ' and Hi!</p>')
        self.assertIn('Click here\\![now](http://x.com), Go\\![page]'
                      '(../Home.md) and Hi!', md)

    def test_double_break_in_link_and_emphasis(self):
        md = self.body('<h1>T</h1><p><strong>Title<br/><br/>Body</strong> '
                       'and <a href="http://x.com"><em>one<br/><br/>two</em>'
                       '</a></p>')
        self.assertIn('**Title**\n\n**Body** and [*one*](http://x.com)\n\n'
                      '[*two*](http://x.com)', md)

    def test_empty_wrapper_before_line_start(self):
        md = self.body('<h1>T</h1><p><font></font>2. Click</p>'
                       '<p><small></small># Not a heading</p>')
        self.assertIn('2\\. Click', md)
        self.assertIn('\\# Not a heading', md)

    def test_entity_like_names_and_backslash_urls(self):
        self.tree.write('Content/doc/sec/a&amp;b.png', b'PNG')
        md = self.body('<h1>T</h1><p><img alt="i" src="a&amp;amp;b.png"/> '
                       '<a href="http://x.com/a\\_b?q=1&amp;amp;r=\\2#x\\y">w'
                       '</a> '
                       '<a href="mailto:a\\b@x.com">m</a></p>')
        self.assertIn('![i](a%26amp;b.png)', md)
        self.assertIn('[w](http://x.com/a/_b?q=1%26amp;r=%5C2#x%5Cy)', md)
        self.assertIn('[m](mailto:a%5Cb@x.com)', md)
        self.assertTrue((self.tree.out / 'sec/a&amp;b.png').is_file())

    def test_definition_list(self):
        md = self.body('<h1>T</h1><dl><dt>Term</dt><dd><p>p1</p><ul><li>'
                       'item1</li></ul></dd><dt>T2</dt><dd><pre>code</pre>'
                       '</dd><dt>T3</dt><dd>Plain</dd></dl>')
        self.assertIn('**Term**\n\np1\n\n- item1\n\n**T2**\n\n```\ncode\n```'
                      '\n\n**T3**\n\nPlain', md)

    def test_strikethrough_runs(self):
        md = self.body('<h1>T</h1><p><s>a</s><s>b</s> and <del>a~~b</del> '
                       'and <s>~</s></p>')
        self.assertIn('~~ab~~ and ~~a\\~\\~b~~ and ~~\\~~~', md)

    def test_table_without_cells(self):
        md = self.body('<h1>T</h1><table><tr></tr><tr></tr></table>'
                       '<p>After.</p>')
        self.assertNotIn('|', md)
        self.assertIn('After.', md)

    def test_delimiter_row_text(self):
        md = self.body('<h1>T</h1><p>Name | Value<br/>-|-</p>'
                       '<p>A | B<br/>|:--|</p>')
        self.assertIn('Name | Value\\\n\\-|-', md)
        self.assertIn('A | B\\\n\\|:--|', md)

    def test_bold_first_row_in_paragraph_cells(self):
        md = self.body('<h1>T</h1><table><tr><td><p><b>A</b></p></td><td><p>'
                       '<b>B</b></p></td></tr><tr><td><p>1</p></td><td><p>2'
                       '</p></td></tr></table>')
        self.assertIn('| **A** | **B** |\n| --- | --- |\n| 1 | 2 |', md)

    def test_image_row_is_no_header(self):
        md = self.body('<h1>T</h1><table><tr><td><p><img alt="a" src="a.png"/>'
                       '</p></td><td><p><img alt="b" src="a.png"/></p></td>'
                       '</tr></table>')
        self.assertIn('| | |\n| --- | --- |\n| ![a](a.png) | ![b](a.png) |',
                      md)

    def test_bold_names_are_no_header(self):
        md = self.body('<h1>T</h1><table><tr><td><b>encode</b></td><td></td>'
                       '</tr><tr><td><b>geturl</b></td><td></td></tr></table>')
        self.assertIn('| | |\n| --- | --- |\n| **encode** | |', md)

    def test_line_start_pipe_in_cell_is_escaped_once(self):
        md = self.body('<h1>T</h1><table><tr><th>A</th><th>B</th></tr><tr>'
                       '<td><p>| -</p></td><td>x</td></tr></table>')
        self.assertIn('| \\| - | x |', md)

    def test_other_schemes_stay_urls(self):
        md = self.body('<h1>T</h1><p><a href="irc://irc.x.org/chan">irc</a> '
                       '<a href="tel:+1-555">tel</a></p>')
        self.assertIn('[irc](irc://irc.x.org/chan) [tel](tel:+1-555)', md)

    def test_svg_link_shows_its_url(self):
        md = self.body('<h1>T</h1><p><svg><a href="http://youtu.be/8Is">'
                       '<rect/></a></svg></p>')
        self.assertIn('[http://youtu.be/8Is](http://youtu.be/8Is)', md)

    def test_big_code_block_is_fast(self):
        lines = '\n'.join('line %d' % i for i in range(40000))
        start = time.perf_counter()
        md = self.body('<h1>T</h1><pre>%s</pre>' % lines)
        self.assertLess(time.perf_counter() - start, 4)
        self.assertIn('line 39999\n```', md)
        with open(self.tree.out / '_report/pages.csv', encoding='utf-8') as f:
            row = next(r for r in csv.DictReader(f) if r['page'] ==
                       'sec/Page.html')
        self.assertEqual(row['code_lines_missing'], '0')


if __name__ == '__main__':
    unittest.main()
