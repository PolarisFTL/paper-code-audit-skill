"""Report regressions using synthetic source, PDF metadata, and image fixtures."""

import base64
import hashlib
from html.parser import HTMLParser
from pathlib import Path
import unittest
from unittest.mock import patch

import test_audit_core as audit_tests


# A 1 x 1 software-test PNG, not a crop or image taken from a research paper.
FIXTURE_PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk'
    '+A8AAQUBAScY42YAAAAASUVORK5CYII='
)


class Element:
    def __init__(self, tag, attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    @property
    def text(self):
        return ''.join(child.text if isinstance(child, Element) else child
                       for child in self.children)


class ReportParser(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
            'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, output):
        super().__init__(convert_charrefs=True)
        self.root = Element('document')
        self.stack = [self.root]
        self.feed(output)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Element(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Element(tag, attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def elements(root, tag=None, class_name=None, **attrs):
    """Find descendants by structure without depending on HTML whitespace."""
    found = []
    for child in root.children:
        if not isinstance(child, Element):
            continue
        classes = child.attrs.get('class', '').split()
        if ((tag is None or child.tag == tag)
                and (class_name is None or class_name in classes)
                and all(child.attrs.get(key) == value for key, value in attrs.items())):
            found.append(child)
        found.extend(elements(child, tag, class_name, **attrs))
    return found


class ReportLayoutTests(unittest.TestCase):
    # Reuse only the synthetic fixture helpers, without inheriting their tests.
    setUp = audit_tests.EvidenceTests.setUp
    write = audit_tests.EvidenceTests.write
    save = audit_tests.EvidenceTests.save

    def image(self, name='architecture.png'):
        path = self.root / name
        path.write_bytes(FIXTURE_PNG)
        return path

    def figure(self, name='architecture.png', **changes):
        self.image(name)
        result = {'path': name, 'caption': 'Synthetic architecture crop',
                  'source_page': 1, 'cropped': True}
        result.update(changes)
        return result

    def report(self):
        self.save()
        output, _ = audit_tests.build(self.path)
        return ReportParser(output).root

    def one(self, root, tag=None, class_name=None, **attrs):
        found = elements(root, tag, class_name, **attrs)
        self.assertEqual(len(found), 1, (tag, class_name, attrs, len(found)))
        return found[0]

    def assert_single_figure(self, root, caption):
        figures = elements(root, tag='figure', class_name='architecture-figure')
        self.assertEqual(len(figures), 1)
        self.assertEqual(len(elements(root, tag='img')), 1)
        image = self.one(figures[0], tag='img')
        self.assertEqual(image.attrs['alt'], caption)
        prefix, encoded = image.attrs['src'].split(',', 1)
        self.assertEqual(prefix, 'data:image/png;base64')
        self.assertEqual(base64.b64decode(encoded, validate=True), FIXTURE_PNG)
        self.assertEqual(self.one(figures[0], tag='figcaption').text, caption)

    def test_unclassified_legacy_pages_are_neither_read_nor_embedded(self):
        full_page = self.image('legacy-full-page.png')
        self.audit['figures'] = [
            {'path': full_page.name, 'caption': 'Unclassified full-page fixture'},
            {'path': 'missing-old-page.png', 'caption': 'Unavailable old page'},
        ]
        original_read_bytes = Path.read_bytes

        def reject_legacy_read(path):
            if path.resolve() == full_page.resolve():
                raise AssertionError('An unclassified legacy page must not be read')
            return original_read_bytes(path)

        with patch.object(Path, 'read_bytes', reject_legacy_read):
            root = self.report()
        self.assertEqual(elements(root, tag='img'), [])
        self.assertEqual(elements(root, tag='figure'), [])
        self.one(root, class_name='diagram-note')
        self.assertNotIn('Unclassified full-page fixture', root.text)
        self.assertNotIn('Unavailable old page', root.text)

    def test_explicit_cropped_architecture_is_the_only_image(self):
        selected = self.figure()
        self.audit['architecture_figure'] = selected
        self.audit['figures'] = [
            {'path': 'unavailable-full-page.png', 'caption': 'Old page'},
        ]
        self.assert_single_figure(self.report(), selected['caption'])

    def test_preferred_figure_takes_priority_over_ambiguous_legacy(self):
        selected = self.figure(caption='Explicit preferred crop')
        self.audit['architecture_figure'] = selected
        # Neither duplicate legacy selection nor its unavailable files can win.
        self.audit['figures'] = [
            {'path': name, 'caption': name, 'role': 'architecture', 'cropped': True}
            for name in ('missing-first.png', 'missing-second.png')
        ]
        self.assert_single_figure(self.report(), selected['caption'])

    def test_explicit_architecture_requires_a_true_crop_marker(self):
        selected = self.figure()
        for cropped in (False, None, 'true', 1):
            with self.subTest(cropped=cropped):
                self.audit['architecture_figure'] = dict(selected, cropped=cropped)
                self.save()
                with self.assertRaises(audit_tests.AuditError):
                    audit_tests.read_audit(self.path)
        self.audit['architecture_figure'] = dict(selected)
        del self.audit['architecture_figure']['cropped']
        self.save()
        with self.assertRaises(audit_tests.AuditError):
            audit_tests.read_audit(self.path)

    def test_architecture_source_page_must_be_valid_for_the_pdf(self):
        selected = self.figure()
        for field in ('architecture_figure', 'figures'):
            for page in (None, 0, -1, 2, True, '1'):
                with self.subTest(field=field, page=page):
                    figure = dict(selected, role='architecture', source_page=page)
                    self.audit.pop('architecture_figure', None)
                    self.audit.pop('figures', None)
                    self.audit[field] = figure if field == 'architecture_figure' else [figure]
                    self.save()
                    with self.assertRaises(audit_tests.AuditError):
                        audit_tests.read_audit(self.path)

    def test_explicit_architecture_requires_its_physical_page(self):
        self.audit['architecture_figure'] = self.figure()
        del self.audit['architecture_figure']['source_page']
        self.save()
        with self.assertRaises(audit_tests.AuditError):
            audit_tests.read_audit(self.path)

    def test_legacy_fallback_requires_both_architecture_role_and_crop(self):
        ignored = [
            {'role': 'architecture', 'cropped': False},
            {'role': 'architecture'},
            {'cropped': True},
            {'role': 'module', 'cropped': True},
            {'role': 'architecture', 'cropped': 'true'},
        ]
        self.audit['figures'] = [dict(item, path='missing-ignored.png', caption='Ignored fixture')
                                 for item in ignored]
        self.assertEqual(elements(self.report(), tag='img'), [])
        selected = self.figure('legacy-crop.png', role='architecture', caption='Legacy marked crop')
        self.audit['figures'].append(selected)
        self.assert_single_figure(self.report(), selected['caption'])

    def test_multiple_cropped_legacy_architectures_are_rejected(self):
        self.audit['figures'] = [
            self.figure('first.png', role='architecture'),
            self.figure('second.png', role='architecture'),
        ]
        self.save()
        with self.assertRaises(audit_tests.AuditError):
            audit_tests.read_audit(self.path)

    def test_narrative_maps_to_actual_source_in_the_right_column(self):
        claim = self.audit['claims'][0]
        claim['paper']['statement'] = 'PAPER_REQUIREMENT_SENTINEL'
        claim['analysis'] = 'ASSESSMENT_SENTINEL'
        claim['code'] = [
            {'path': 'model.py', 'start_line': 1, 'end_line': 1, 'note': 'Forward entry mapping'},
            {'path': 'model.py', 'start_line': 2, 'end_line': 2, 'note': 'Return value mapping'},
        ]
        root = self.report()
        card = self.one(root, tag='article', id='claim-' + claim['id'])
        grid = self.one(card, class_name='match-grid')
        narrative = self.one(grid, class_name='match-narrative')
        code = self.one(grid, class_name='match-code')
        self.assertIn(narrative, grid.children)
        self.assertIn(code, grid.children)
        self.assertIn(claim['paper']['statement'], narrative.text)
        self.assertIn(claim['analysis'], narrative.text)
        self.assertEqual(card.text.count(claim['analysis']), 1)
        self.assertNotIn(claim['analysis'], code.text)
        self.assertNotIn(claim['paper']['statement'], code.text)
        self.assertEqual(elements(narrative, tag='pre'), [])
        self.assertEqual(elements(narrative, class_name='evidence'), [])
        self.assertEqual(elements(code, class_name='code-map'), [])
        self.assertEqual(len(elements(code, tag='pre')), 2)
        mapping = self.one(narrative, tag='ol', class_name='code-map')
        links = elements(mapping, tag='a')
        self.assertEqual(len(links), len(claim['code']))
        self.assertEqual(len(elements(code, class_name='evidence')), len(links))
        actual_lines = self.code.read_text(encoding='utf-8').splitlines()
        for link, evidence in zip(links, claim['code']):
            self.assertIn(evidence['note'], link.text)
            self.assertEqual(self.one(link, class_name='map-path').text, evidence['path'])
            self.assertTrue(link.attrs['href'].startswith('#'))
            target = self.one(code, class_name='evidence', id=link.attrs['href'][1:])
            self.assertEqual(target.attrs['data-source-path'], evidence['path'])
            self.assertEqual(int(target.attrs['data-start-line']), evidence['start_line'])
            self.assertEqual(int(target.attrs['data-end-line']), evidence['end_line'])
            excerpt = self.one(target, tag='pre')
            for line in actual_lines[evidence['start_line'] - 1:evidence['end_line']]:
                self.assertIn(line, excerpt.text)
                self.assertNotIn(line, narrative.text)
            self.assertNotIn(evidence['note'], code.text)

    def test_html_escaping_preserves_text_attributes_and_source(self):
        self.audit['title'] = '<script id="injected-title">alert("fixture")</script>'
        claim = self.audit['claims'][0]
        claim['requirement'] = 'A <img id="injected-requirement" src="x"> & "requirement"'
        claim['paper']['statement'] = '<b id="injected-paper">Paper & context</b>'
        claim['analysis'] = '<iframe id="injected-analysis">Assessment & "detail"</iframe>'
        claim['code'][0]['note'] = '<span id="injected-map">Mapping & "note"</span>'
        source = 'def forward(x):\n    return \'<img id="injected-source" src="x"> & "value"\'\n'
        self.code.write_text(source, encoding='utf-8')
        self.write('source_manifest.json', {'files': [{
            'path': 'model.py', 'bytes': self.code.stat().st_size,
            'sha256': hashlib.sha256(self.code.read_bytes()).hexdigest(),
        }]})
        caption = '<svg id="injected-caption"></svg> & "caption"'
        self.audit['architecture_figure'] = self.figure(caption=caption)
        root = self.report()
        self.assertEqual(self.one(root, tag='title').text, self.audit['title'])
        self.assertEqual(self.one(root, tag='h1').text, self.audit['title'])
        card = self.one(root, tag='article', id='claim-' + claim['id'])
        self.assertEqual(card.attrs['data-search'], ' '.join(
            [claim['requirement'], claim['analysis'], claim['paper']['statement']]))
        narrative = self.one(card, class_name='match-narrative')
        self.assertIn(claim['paper']['statement'], narrative.text)
        self.assertIn(claim['analysis'], narrative.text)
        self.assertIn(claim['code'][0]['note'], narrative.text)
        excerpt = self.one(self.one(card, class_name='match-code'), tag='pre')
        self.assertIn(source.splitlines()[1], excerpt.text)
        self.assert_single_figure(root, caption)
        self.assertFalse([node for node in elements(root)
                          if node.attrs.get('id', '').startswith('injected-')])


if __name__ == '__main__':
    unittest.main()
