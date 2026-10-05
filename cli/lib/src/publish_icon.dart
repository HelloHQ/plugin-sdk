// The sidebar icon rules `hqplugin publish` applies before pinning
// `sidebar_icon_hash_sha256`.
//
// SOURCE OF TRUTH: HelloHQ/plugin-registry scripts/verify-artifacts.mjs
// (`MAX_ICON_BYTES`, `svgIconProblem`, and the sidebar_icon branch of
// `artifactProblems`). Registry CI enforces those; this file mirrors them so an
// author finds out before opening the PR. Keep the two in step: the regular
// expressions below are copied verbatim (Dart's RegExp is ECMAScript syntax),
// and test/publish_icon_test.dart uses the registry test's cases
// (tests/verify-artifacts.test.mjs, "unsafe or non-SVG icons are refused").
import 'dart:convert';

/// The registry's icon size limit (verify-artifacts.mjs MAX_ICON_BYTES).
const int kMaxIconBytes = 64 * 1024;

/// True when [value] is an absolute URL (has a scheme), as the registry tests
/// it. Anything else is a path inside the UI bundle.
bool isAbsoluteIconUrl(String value) =>
    RegExp(r'^[a-z][a-z0-9+.-]*:', caseSensitive: false).hasMatch(value);

final RegExp _svgElement = RegExp(r'<svg[\s>/]', caseSensitive: false);

/// What the registry refuses in an icon, in its order: (pattern, reason).
final List<(RegExp, String)> _bannedInIcon = [
  for (final (pattern, why) in const [
    (r'<script', 'contains <script>'),
    (r'<foreignObject', 'contains <foreignObject>'),
    (r'<!ENTITY', 'declares an entity'),
    (r'<!DOCTYPE', 'has a DOCTYPE'),
    (r'\son[a-z]+\s*=', 'has an event-handler attribute'),
    (r'@import', 'uses @import'),
    (r'javascript:', 'contains a javascript: URL'),
    (
      r'''(?:xlink:)?href\s*=\s*["'](?!#)''',
      'references something outside the file (href)',
    ),
    (
      r'''url\(\s*["']?(?!#)''',
      'references something outside the file (url())',
    ),
    (
      r'''<(?:image|use|iframe|embed|object|audio|video|link|style)\b(?![^>]*href\s*=\s*["']#)''',
      'contains an element that can load external content',
    ),
  ])
    (RegExp(pattern, caseSensitive: false), why),
];

/// Why [bytes] is not a plain, self-contained SVG icon, or null. Text checks,
/// deliberately strict: an icon needs none of these.
String? svgIconProblem(List<int> bytes) {
  String text;
  try {
    text = utf8.decode(bytes);
  } on FormatException {
    return 'is not UTF-8 text';
  }
  if (!_svgElement.hasMatch(text)) return 'has no <svg> element';
  for (final (re, why) in _bannedInIcon) {
    if (re.hasMatch(text)) return why;
  }
  return null;
}
