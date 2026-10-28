"""Tests for images_csv. Run: python -m unittest discover -s tests -v"""

import contextlib
import csv
import hashlib
import importlib.util
import io
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

HAVE_MARKDOWN_IT = importlib.util.find_spec('markdown_it') is not None
if HAVE_MARKDOWN_IT:
    import images_csv  # pylint: disable=wrong-import-position

PAGE = """---
title: 'Page ](../img/plot.png)'
---

# Page

![Plot](../img/plot.png) and [full size](../img/plot.png "Big").

![Space](../img/my%20pic.png?v=1) ![Remote](https://example.com/x.png)

Text \\](../img/plot.png) is not a link. [Data](../files/data.txt)

Missing ![Gone](../img/gone.png) and [anchor](#top).

`![code](../img/plot.png)` stays. ``a ` ![c2](../img/plot.png)`` too.

```md
![fence](../img/plot.png)
```

| A | B |
| --- | --- |
| ![Cell](<../img/plot.png>) | x |

<table><tr><td><img alt="raw" src="../img/plot.png"/></td>
<td><a href='../img/my%20pic.png'>pic</a></td></tr></table>

~~~
![tilde fence](../img/plot.png)
~~~

    ![indented code](../img/plot.png)
"""


@unittest.skipUnless(HAVE_MARKDOWN_IT, 'needs markdown-it-py')
class ImagesCsvTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name) / 'md'
        for name in ('img/plot.png', 'img/my pic.png', 'img/unused.gif',
                     'files/data.txt', '_report/x.png'):
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_bytes(name.encode())
        (self.root / 'guide').mkdir()
        (self.root / 'guide/page.md').write_text(PAGE, encoding='utf-8')

    def rows(self):
        out = pathlib.Path(self.tmp.name) / 'images.csv'
        with contextlib.redirect_stdout(io.StringIO()) as text:
            status = images_csv.main([str(self.root), str(out)])
        self.assertEqual(status, 0)
        self.assertIn('images 3  references 7  pages with images 1  '
                      'unused images 1  references to missing files 1',
                      text.getvalue())
        with open(out, encoding='utf-8', newline='') as f:
            return list(csv.DictReader(f))

    def test_references(self):
        got = [(r['image'], r['line'], r['column'], r['kind'], r['ref'])
               for r in self.rows()]
        self.assertEqual(got, [
            ('img/gone.png', '13', '17', 'markdown', '../img/gone.png'),
            ('img/my pic.png', '9', '10', 'markdown',
             '../img/my%20pic.png?v=1'),
            ('img/my pic.png', '26', '14', 'html', '../img/my%20pic.png'),
            ('img/plot.png', '7', '9', 'markdown', '../img/plot.png'),
            ('img/plot.png', '7', '42', 'markdown', '../img/plot.png'),
            ('img/plot.png', '23', '12', 'markdown', '../img/plot.png'),
            ('img/plot.png', '25', '36', 'html', '../img/plot.png'),
            ('img/unused.gif', '', '', '', '')])

    def test_ref_is_at_line_and_column(self):
        lines = PAGE.split('\n')
        for r in self.rows():
            if r['page']:
                self.assertEqual(r['page'], 'guide/page.md')
                line = lines[int(r['line']) - 1]
                self.assertTrue(line[int(r['column']) - 1:].startswith(
                    r['ref']), r)

    def test_file_info(self):
        rows = {r['image']: r for r in self.rows()}
        plot = rows['img/plot.png']
        self.assertEqual(plot['file'], str(self.root.resolve() /
                                           'img/plot.png'))
        self.assertEqual((plot['exists'], plot['bytes'], plot['mime']),
                         ('1', str(len(b'img/plot.png')), 'image/png'))
        self.assertEqual(plot['sha256'],
                         hashlib.sha256(b'img/plot.png').hexdigest())
        gone = rows['img/gone.png']
        self.assertEqual((gone['exists'], gone['bytes'], gone['sha256']),
                         ('0', '', ''))
        # The old reports are no part of the tree.
        self.assertNotIn('_report/x.png', rows)


if __name__ == '__main__':
    unittest.main()
