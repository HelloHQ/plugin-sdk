// The sidebar icon rules mirror HelloHQ/plugin-registry
// scripts/verify-artifacts.mjs. These cases are the registry's own
// (tests/verify-artifacts.test.mjs, "sidebar_icon" section), so a rule that
// drifts from the registry fails here.
import 'dart:convert';

import 'package:hqplugin/src/publish.dart';
import 'package:test/test.dart';

/// The registry test's ICON: a plain SVG using in-file references only
/// (`href="#p"`, `xlink:href="#p"`, `url(#g)`) and `xmlns` URLs.
const registryIcon =
    '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 24 24">'
    '<defs><linearGradient id="g"><stop offset="0"/></linearGradient><path id="p" d="M0 0h24v24H0z"/></defs>'
    '<use href="#p" fill="url(#g)"/><use xlink:href="#p"/><path d="M4 4h16v16H4z" fill="currentColor"/></svg>';

void main() {
  test('the registry\'s plain SVG icon passes', () {
    expect(svgIconProblem(utf8.encode(registryIcon)), isNull);
  });

  test('the size cap matches the registry (64 KiB)', () {
    expect(kMaxIconBytes, 65536);
  });

  test('unsafe or non-SVG icons are refused (the registry\'s cases)', () {
    final bad = <String, RegExp>{
      '<svg><script>alert(1)</script></svg>': RegExp('<script>'),
      '<svg><foreignObject><div/></foreignObject></svg>': RegExp(
        'foreignObject',
      ),
      '<svg onload="x()"></svg>': RegExp('event-handler'),
      '<svg><a href="https://evil.example/">x</a></svg>': RegExp(
        r'outside the file \(href\)',
      ),
      '<svg><use xlink:href="https://evil.example/s.svg#a"/></svg>': RegExp(
        r'outside the file \(href\)',
      ),
      '<svg><path fill="url(https://evil.example/p)"/></svg>': RegExp(
        r'outside the file \(url\(\)\)',
      ),
      '<svg><style>@import "https://evil.example/x.css";</style></svg>': RegExp(
        '@import|element that can load',
      ),
      '<svg><image width="1"/></svg>': RegExp('element that can load'),
      '<!DOCTYPE svg [<!ENTITY x "y">]><svg/>': RegExp('entity|DOCTYPE'),
      '<svg><a href="javascript:alert(1)"/></svg>': RegExp('javascript:|href'),
      '<html><body/></html>': RegExp('no <svg> element'),
    };
    bad.forEach((text, why) {
      expect(
        svgIconProblem(utf8.encode(text)) ?? 'ok',
        matches(why),
        reason: text,
      );
    });
    expect(svgIconProblem([0xff, 0xfe, 0x00]), matches('not UTF-8'));
  });

  test('matching is case-insensitive, like the registry\'s /i patterns', () {
    expect(
      svgIconProblem(utf8.encode('<SVG><SCRIPT/></SVG>')),
      'contains <script>',
    );
    expect(
      svgIconProblem(utf8.encode('<svg OnClick="x()"/>')),
      'has an event-handler attribute',
    );
  });

  test('the committed hello-world icon passes', () {
    // Same file the hello-world release uploads as icon.svg.
    const helloWorldIcon =
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="currentColor" fill-rule="evenodd" d="M6 3H18Z"/></svg>';
    expect(svgIconProblem(utf8.encode(helloWorldIcon)), isNull);
  });

  test('isAbsoluteIconUrl uses the registry\'s scheme test', () {
    for (final url in [
      'https://example.com/icon.svg',
      'http://example.com/icon.svg',
      'data:image/svg+xml,<svg/>',
      'file:///etc/icon.svg',
      'HTTPS://example.com/icon.svg',
    ]) {
      expect(isAbsoluteIconUrl(url), isTrue, reason: url);
    }
    for (final path in [
      'icons/plugin.svg',
      'icon.svg',
      './icon.svg',
      '/icon.svg',
    ]) {
      expect(isAbsoluteIconUrl(path), isFalse, reason: path);
    }
  });

  group('registryManifestProblems: sidebar_icon', () {
    Map<String, dynamic> m(Map<String, dynamic> over) => {
      'wasm_url': 'https://example.com/plugin.wasm',
      'content_hash_sha256': 'a' * 64,
      ...over,
    };

    test('an https icon with a real hash passes', () {
      expect(
        registryManifestProblems(
          m({
            'sidebar_icon': 'https://example.com/icon.svg',
            'sidebar_icon_hash_sha256': 'b' * 64,
          }),
        ),
        isEmpty,
      );
    });

    test('an https icon without a hash, or with the placeholder, fails', () {
      expect(
        registryManifestProblems(
          m({'sidebar_icon': 'https://example.com/icon.svg'}),
        ),
        [contains('needs sidebar_icon_hash_sha256')],
      );
      expect(
        registryManifestProblems(
          m({
            'sidebar_icon': 'https://example.com/icon.svg',
            'sidebar_icon_hash_sha256': '0' * 64,
          }),
        ),
        [contains('placeholder')],
      );
    });

    test('a non-https absolute icon fails', () {
      for (final url in [
        'http://example.com/icon.svg',
        'data:image/svg+xml,<svg/>',
        'file:///etc/icon.svg',
      ]) {
        expect(registryManifestProblems(m({'sidebar_icon': url})), [
          contains('must be an https URL'),
        ], reason: url);
      }
    });

    test('a bundle-path icon needs no hash; a stray hash fails', () {
      expect(
        registryManifestProblems(m({'sidebar_icon': 'icons/plugin.svg'})),
        isEmpty,
      );
      expect(
        registryManifestProblems(
          m({
            'sidebar_icon': 'icons/plugin.svg',
            'sidebar_icon_hash_sha256': 'b' * 64,
          }),
        ),
        [contains('applies only to an https sidebar_icon')],
      );
      expect(
        registryManifestProblems(m({'sidebar_icon_hash_sha256': 'b' * 64})),
        [contains('sidebar_icon is not')],
      );
    });
  });

  group('buildRegistryManifest: sidebar_icon', () {
    Map<String, dynamic> build(
      Map<String, dynamic> author, {
      String? url,
      String? hash,
    }) => buildRegistryManifest(
      author: author,
      upstream: null,
      version: '1.0.1',
      wasmUrl: 'https://example.com/plugin.wasm',
      contentHash: 'a' * 64,
      sidebarIconUrl: url,
      sidebarIconHash: hash,
    );

    test('pins the icon right after sidebar_icon', () {
      final m = build(
        {
          'id': 'x',
          'sidebar_icon': 'https://example.com/v1/icon.svg',
          'entry_function': 'run',
        },
        url: 'https://example.com/v2/icon.svg',
        hash: 'c' * 64,
      );
      expect(m['sidebar_icon'], 'https://example.com/v2/icon.svg');
      final keys = m.keys.toList();
      expect(
        keys.indexOf('sidebar_icon_hash_sha256'),
        keys.indexOf('sidebar_icon') + 1,
      );
      expect(
        keys.last,
        'content_hash_sha256',
        reason: 'new keys append; existing keys keep their order',
      );
    });

    test('drops a stale hash when there is no https icon', () {
      final m = build({
        'sidebar_icon': 'icons/plugin.svg',
        'sidebar_icon_hash_sha256': 'c' * 64,
      });
      expect(m['sidebar_icon'], 'icons/plugin.svg');
      expect(m.containsKey('sidebar_icon_hash_sha256'), isFalse);
      expect(
        build({
          'sidebar_icon_hash_sha256': 'c' * 64,
        }).containsKey('sidebar_icon_hash_sha256'),
        isFalse,
      );
    });
  });
}
