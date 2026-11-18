"""Semantic and source-preservation regressions for offline code highlighting."""

from html.parser import HTMLParser
from pathlib import Path
import sys
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'paper-code-audit' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from syntax_highlight import highlight_lines


class HighlightParser(HTMLParser):
    """Read highlighted text while rejecting markup originating in source code."""

    def __init__(self, fragment):
        super().__init__(convert_charrefs=True)
        self.text_parts = []
        self.spans = []
        self.stack = []
        self.feed(fragment)
        self.close()
        if self.stack:
            raise AssertionError('Highlight spans must close within each source line')

    @property
    def text(self):
        return ''.join(self.text_parts)

    def handle_starttag(self, tag, attrs):
        if tag != 'span' or len(attrs) != 1 or attrs[0][0] != 'class':
            raise AssertionError(('Unexpected highlight markup', tag, attrs))
        classes = (attrs[0][1] or '').split()
        if not classes or not all(name.startswith('tok-') for name in classes):
            raise AssertionError(('Unexpected highlight classes', classes))
        span = {'classes': classes, 'text': ''}
        self.spans.append(span)
        self.stack.append(span)

    def handle_endtag(self, tag):
        if tag != 'span' or not self.stack:
            raise AssertionError(('Unbalanced highlight markup', tag))
        self.stack.pop()

    def handle_startendtag(self, tag, attrs):
        raise AssertionError(('Unexpected self-closing markup', tag, attrs))

    def handle_data(self, data):
        self.text_parts.append(data)
        for span in self.stack:
            span['text'] += data

    def handle_comment(self, data):
        raise AssertionError(('Unexpected HTML comment', data))

    def handle_decl(self, decl):
        raise AssertionError(('Unexpected HTML declaration', decl))

    def handle_pi(self, data):
        raise AssertionError(('Unexpected processing instruction', data))


class SyntaxHighlightTests(unittest.TestCase):
    def highlighted(self, path, lines):
        output = highlight_lines(path, lines)
        self.assertIsInstance(output, list)
        self.assertEqual(len(output), len(lines), 'One fragment is required per source line')
        parsed = []
        for line_number, (original, fragment) in enumerate(zip(lines, output), 1):
            with self.subTest(path=path, line=line_number):
                self.assertIsInstance(fragment, str)
                line = HighlightParser(fragment)
                self.assertEqual(line.text, original, 'Highlighting must preserve exact source text')
                parsed.append(line)
        return parsed

    def assert_token(self, lines, category, text):
        found = [span['text'] for line in lines for span in line.spans
                 if 'tok-' + category in span['classes']]
        self.assertTrue(any(text in value for value in found),
                        (category, text, found))

    def assert_not_token(self, lines, category, text):
        found = [span['text'] for line in lines for span in line.spans
                 if 'tok-' + category in span['classes']]
        self.assertFalse(any(text in value for value in found),
                         (category, text, found))

    def test_empty_file_has_no_fragments(self):
        self.assertEqual(highlight_lines('empty.py', []), [])

    def test_whitespace_unicode_entities_and_line_endings_are_exact(self):
        source = [
            '',
            '\t  \r\n',
            '\tvalue = "中文 <&> &amp; &#x41; \\\"quoted\\\""  \r\n',
            '\n',
            "# tab\tand 'quotes', <> & entities: &lt;\n",
            'last = 1\t  ',
        ]
        for path in ('model.py', 'config.json', 'config.yaml', 'source.cpp',
                     'script.sh', 'unknown.source'):
            with self.subTest(path=path):
                self.highlighted(path, source)

    def test_source_cannot_inject_html_or_script(self):
        source = [
            'payload = "</span><script>alert(1)</script><img src=x onerror=alert(2)>"\n',
            '# <!-- source comment --> <svg onload="alert(3)"> &amp;\n',
            '"<span class=tok-keyword onclick=alert(4)>伪装</span>"',
        ]
        for path in ('model.py', 'config.json', 'config.yaml', 'source.js',
                     'script.sh', 'source.unknown'):
            with self.subTest(path=path):
                self.highlighted(path, source)

    def test_python_core_categories_and_decorator(self):
        lines = self.highlighted('model.py', [
            '@register("实验")\n',
            'class Model:\n',
            '    def forward(self, values):\n',
            '        label = "中文 <&>"\n',
            '        count = 42\n',
            '        return len(values)  # retain <tag> &amp;\n',
        ])
        for keyword in ('class', 'def', 'return'):
            self.assert_token(lines, 'keyword', keyword)
        self.assert_token(lines, 'decorator', 'register')
        self.assert_token(lines, 'class', 'Model')
        self.assert_token(lines, 'function', 'forward')
        self.assert_token(lines, 'builtin', 'len')
        self.assert_token(lines, 'string', '中文 <&>')
        self.assert_token(lines, 'number', '42')
        self.assert_token(lines, 'comment', '# retain <tag> &amp;')

    def test_python_docstring_uses_context_before_excerpt(self):
        full_file = [
            'def describe():\n',
            '    """A docstring starts before the excerpt.\n',
            '    def counterfeit(): <script> &amp; 中文\n',
            '    return 100  # still prose, not executable code\n',
            '    """\n',
            '    return 3\n',
        ]
        highlighted = self.highlighted('model.py', full_file)
        excerpt = highlighted[2:4]
        self.assert_token(excerpt, 'string', 'def counterfeit():')
        self.assert_token(excerpt, 'string', 'return 100')
        self.assert_not_token(excerpt, 'keyword', 'def')
        self.assert_not_token(excerpt, 'comment', '# still prose')
        self.assert_token(highlighted[5:], 'keyword', 'return')

    def test_python_fstrings_preserve_original_characters(self):
        self.highlighted('formatting.py', [
            'greeting = f"你好 {name!r:>10} {{literal}} <&>"\n',
            'message = f\'value={mapping["key"]!r}; &amp;\'\n',
            'multiline = f"""first {value}\n',
            '\tsecond {{braces}} \'quote\' <tag>\n',
            '"""',
        ])

    def test_incomplete_python_remains_readable(self):
        examples = [
            ['def unfinished(\n', '\tvalue = 1\n'],
            ['message = "unterminated <tag> &amp;\n'],
            ['def broken():\n', '    """unterminated docstring\n',
             '\t中文 & <script>\n'],
            ['if True:\n', '    first = 1\n', '  second = 2\n'],
        ]
        for source in examples:
            with self.subTest(source=source):
                self.highlighted('incomplete.py', source)

    def test_json_properties_values_numbers_and_literals(self):
        lines = self.highlighted('config.json', [
            '{\n',
            '  "title": "中文 <tag> &amp;",\n',
            '  "count": -12.5e+2,\n',
            '  "active": true, "disabled": false, "empty": null\n',
            '}\n',
        ])
        for name in ('title', 'count', 'active'):
            self.assert_token(lines, 'property', name)
        self.assert_token(lines, 'string', '中文 <tag> &amp;')
        self.assert_token(lines, 'number', '12.5e+2')
        for literal in ('true', 'false', 'null'):
            self.assert_token(lines, 'keyword', literal)

    def test_yaml_quotes_protect_hashes_from_comment_detection(self):
        lines = self.highlighted('config.yaml', [
            'name: "中文 # still string <&>"\n',
            "description: '引用 &amp; <tag>'\n",
            'enabled: true\n',
            'threshold: 0.25\n',
            '# actual YAML comment\n',
        ])
        self.assert_token(lines, 'property', 'name')
        self.assert_token(lines, 'string', '# still string')
        self.assert_token(lines, 'string', '引用 &amp; <tag>')
        self.assert_not_token(lines, 'comment', '# still string')
        self.assert_token(lines, 'comment', '# actual YAML comment')

    def test_c_like_strings_protect_double_slashes(self):
        source = [
            'const url = "https://example.test/path//part"; // actual comment\n',
        ]
        for path in ('source.c', 'source.cpp', 'source.js', 'source.ts'):
            with self.subTest(path=path):
                lines = self.highlighted(path, source)
                self.assert_token(lines, 'string', 'https://example.test/path//part')
                self.assert_not_token(lines, 'comment', 'example.test')
                self.assert_token(lines, 'comment', '// actual comment')

    def test_shell_comments_and_quoted_hashes(self):
        lines = self.highlighted('run.sh', [
            '#!/bin/sh\n',
            'value="中文 # literal <&>"\n',
            'printf \'%s\\n\' "$value" # actual shell comment\n',
        ])
        self.assert_token(lines, 'string', '# literal')
        self.assert_not_token(lines, 'comment', '# literal')
        self.assert_token(lines, 'comment', '# actual shell comment')

    def test_unknown_extension_is_escaped_plain_text(self):
        lines = self.highlighted('notes.unrecognized', [
            '\t<script>alert("x")</script> &amp; &#x41; 中文 \' &\n',
            '',
            "  <tag data-x='&'>",
        ])
        self.assertTrue(all(not line.spans for line in lines))


if __name__ == '__main__':
    unittest.main()
