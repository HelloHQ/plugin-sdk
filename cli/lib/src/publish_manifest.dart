// Pure rules behind `hqplugin publish`: versions, the registry copy of a
// manifest, the permission delta, and the registry PR text. Nothing here does
// I/O, so every rule is unit-tested directly.
import 'dart:convert';

import 'publish_icon.dart' show isAbsoluteIconUrl;

/// The all-zero hash the example manifests carry before a release exists. The
/// registry refuses it (scripts/verify-artifacts.mjs), so publish never pins it.
final String kPlaceholderHash = '0' * 64;

final RegExp _hex64 = RegExp(r'^[a-f0-9]{64}$');

// ─────────────────────────────────────────────────────────────────────────────
// Semver
// ─────────────────────────────────────────────────────────────────────────────

/// A semantic version (https://semver.org). Build metadata is kept for
/// printing but ignored by [compareTo], as semver precedence requires.
class Semver implements Comparable<Semver> {
  const Semver(
    this.major,
    this.minor,
    this.patch, [
    this.prerelease = const [],
    this.build = '',
  ]);

  final int major;
  final int minor;
  final int patch;
  final List<String> prerelease;
  final String build;

  static final _pattern = RegExp(
    r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)'
    r'(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?'
    r'(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$',
  );

  /// Parses [v], or returns null when it is not a semantic version.
  static Semver? tryParse(String? v) {
    if (v == null) return null;
    final m = _pattern.firstMatch(v);
    if (m == null) return null;
    return Semver(
      int.parse(m.group(1)!),
      int.parse(m.group(2)!),
      int.parse(m.group(3)!),
      m.group(4)?.split('.') ?? const [],
      m.group(5) ?? '',
    );
  }

  bool get isPrerelease => prerelease.isNotEmpty;

  @override
  int compareTo(Semver other) {
    for (final d in [
      major - other.major,
      minor - other.minor,
      patch - other.patch,
    ]) {
      if (d != 0) return d.sign;
    }
    // A release has higher precedence than any of its prereleases.
    if (prerelease.isEmpty || other.prerelease.isEmpty) {
      return (other.prerelease.length.clamp(0, 1) -
          prerelease.length.clamp(0, 1));
    }
    for (var i = 0; i < prerelease.length && i < other.prerelease.length; i++) {
      final a = prerelease[i];
      final b = other.prerelease[i];
      final an = int.tryParse(a);
      final bn = int.tryParse(b);
      int c;
      if (an != null && bn != null) {
        c = an.compareTo(bn);
      } else if (an != null) {
        c = -1; // numeric identifiers sort before alphanumeric ones
      } else if (bn != null) {
        c = 1;
      } else {
        c = a.compareTo(b);
      }
      if (c != 0) return c.sign;
    }
    return (prerelease.length - other.prerelease.length).sign;
  }

  @override
  String toString() =>
      '$major.$minor.$patch'
      '${prerelease.isEmpty ? '' : '-${prerelease.join('.')}'}'
      '${build.isEmpty ? '' : '+$build'}';
}

/// The bump kinds `--bump` accepts.
const List<String> kBumpKinds = ['patch', 'minor', 'major'];

/// [current] bumped by [kind] (`patch`, `minor` or `major`), with npm's rule
/// for prereleases: a prerelease of the target version becomes that version
/// (`1.3.0-beta.1` + minor → `1.3.0`), otherwise the field is incremented
/// (`1.2.3-beta` + minor → `1.3.0`). Build metadata is dropped.
///
/// Throws [FormatException] when [current] is not semver or [kind] unknown.
String bumpVersion(String current, String kind) {
  final v = Semver.tryParse(current);
  if (v == null) {
    throw FormatException('"$current" is not a semantic version (X.Y.Z)');
  }
  switch (kind) {
    case 'patch':
      return v.isPrerelease
          ? '${v.major}.${v.minor}.${v.patch}'
          : '${v.major}.${v.minor}.${v.patch + 1}';
    case 'minor':
      return v.isPrerelease && v.patch == 0
          ? '${v.major}.${v.minor}.0'
          : '${v.major}.${v.minor + 1}.0';
    case 'major':
      return v.isPrerelease && v.minor == 0 && v.patch == 0
          ? '${v.major}.0.0'
          : '${v.major + 1}.0.0';
  }
  throw FormatException(
    'unknown bump "$kind" (expected patch, minor or major)',
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// The registry copy of a manifest
// ─────────────────────────────────────────────────────────────────────────────

/// The manifest as written to `plugins/<id>/manifest.json` in the registry.
///
/// Starts from the author's [author] manifest and applies the registry's
/// rules:
///  - [version], the artifact URLs and the hashes computed from the bytes those
///    URLs serve are set (a UI bundle's URL and hash are both set or both
///    removed);
///  - an https `sidebar_icon` is set to [sidebarIconUrl] and pinned by
///    `sidebar_icon_hash_sha256` (placed right after it); any other icon (a
///    path inside the UI bundle, or none) keeps no icon hash;
///  - `signatures` are always removed: a PR never writes them, and the signing
///    pipeline re-signs after merge;
///  - `trust_tier` and `publisher_signing_key_id` are set by the registry team,
///    never by the author: copied from [upstream] (the registry's current
///    manifest) on an update, removed for a new plugin. With [firstParty]
///    (HelloHQ maintainers publishing a `core` plugin) the author's values are
///    kept.
Map<String, dynamic> buildRegistryManifest({
  required Map<String, dynamic> author,
  required Map<String, dynamic>? upstream,
  required String version,
  required String wasmUrl,
  required String contentHash,
  String? uiBundleUrl,
  String? uiBundleHash,
  String? sidebarIconUrl,
  String? sidebarIconHash,
  bool firstParty = false,
}) {
  final m = Map<String, dynamic>.from(author)
    ..['version'] = version
    ..['wasm_url'] = wasmUrl
    ..['content_hash_sha256'] = contentHash
    ..remove('signatures');
  if (uiBundleUrl != null && uiBundleHash != null) {
    m['ui_bundle_url'] = uiBundleUrl;
    m['ui_bundle_hash_sha256'] = uiBundleHash;
  } else {
    m
      ..remove('ui_bundle_url')
      ..remove('ui_bundle_hash_sha256');
  }
  m.remove('sidebar_icon_hash_sha256');
  if (sidebarIconUrl != null && sidebarIconHash != null) {
    m['sidebar_icon'] = sidebarIconUrl;
    final pinned = <String, dynamic>{};
    for (final entry in m.entries) {
      pinned[entry.key] = entry.value;
      if (entry.key == 'sidebar_icon') {
        pinned['sidebar_icon_hash_sha256'] = sidebarIconHash;
      }
    }
    m
      ..clear()
      ..addAll(pinned);
  }
  if (!firstParty) {
    for (final key in const ['trust_tier', 'publisher_signing_key_id']) {
      final upstreamValue = upstream?[key];
      if (upstreamValue != null) {
        m[key] = upstreamValue;
      } else {
        m.remove(key);
      }
    }
  }
  return m;
}

/// Problems that make [m] unpublishable (empty when there are none): a missing,
/// malformed or placeholder hash, a non-https URL, a UI bundle URL without its
/// hash (or the reverse), or a sidebar icon hash that does not go with an https
/// `sidebar_icon`. The registry CI refuses each of these (an https icon
/// without a hash is only a registry warning today, but publish always pins
/// one).
List<String> registryManifestProblems(Map<String, dynamic> m) {
  final problems = <String>[];
  void hash(String key, Object? value) {
    if (value is! String || !_hex64.hasMatch(value)) {
      problems.add('$key must be 64 lowercase hex characters');
    } else if (value == kPlaceholderHash) {
      problems.add('$key is the all-zero placeholder; the registry refuses it');
    }
  }

  void url(String key, Object? value) {
    if (value is! String || !value.startsWith('https://')) {
      problems.add('$key must be an https URL');
    }
  }

  url('wasm_url', m['wasm_url']);
  hash('content_hash_sha256', m['content_hash_sha256']);
  final hasUiUrl = m['ui_bundle_url'] != null;
  final hasUiHash = m['ui_bundle_hash_sha256'] != null;
  if (hasUiUrl != hasUiHash) {
    problems.add(
      'ui_bundle_url and ui_bundle_hash_sha256 must both be set or both be absent',
    );
  } else if (hasUiUrl) {
    url('ui_bundle_url', m['ui_bundle_url']);
    hash('ui_bundle_hash_sha256', m['ui_bundle_hash_sha256']);
  }
  final icon = m['sidebar_icon'];
  final iconHash = m['sidebar_icon_hash_sha256'];
  if (icon is String && isAbsoluteIconUrl(icon)) {
    if (!icon.startsWith('https://')) {
      problems.add(
        'sidebar_icon must be an https URL (or a path inside the UI bundle)',
      );
    } else if (iconHash == null) {
      problems.add('an https sidebar_icon needs sidebar_icon_hash_sha256');
    } else {
      hash('sidebar_icon_hash_sha256', iconHash);
    }
  } else if (iconHash != null) {
    problems.add(
      icon == null
          ? 'sidebar_icon_hash_sha256 is set but sidebar_icon is not'
          : 'sidebar_icon_hash_sha256 applies only to an https sidebar_icon; '
                'a bundle-path icon is pinned by the UI bundle',
    );
  }
  if (m.containsKey('signatures')) {
    problems.add(
      'signatures are written only by the registry signing pipeline',
    );
  }
  return problems;
}

/// The registry's file format: 2-space JSON and a trailing newline.
String encodeManifest(Map<String, dynamic> m) =>
    '${const JsonEncoder.withIndent('  ').convert(m)}\n';

/// Whether [a] and [b] say the same thing, ignoring key order and the
/// signing pipeline's `signatures` (a re-run must not count them as a change).
bool sameRegistryContent(Map<String, dynamic> a, Map<String, dynamic> b) {
  Map<String, dynamic> strip(Map<String, dynamic> m) =>
      Map<String, dynamic>.from(m)..remove('signatures');
  return jsonEncode(_canonical(strip(a))) == jsonEncode(_canonical(strip(b)));
}

Object? _canonical(Object? v) {
  if (v is Map) {
    final keys = v.keys.map((k) => '$k').toList()..sort();
    return {for (final k in keys) k: _canonical(v[k])};
  }
  if (v is List) return v.map(_canonical).toList();
  return v;
}

String _compactJson(Object? v) => jsonEncode(_canonical(v));

// ─────────────────────────────────────────────────────────────────────────────
// Permissions
// ─────────────────────────────────────────────────────────────────────────────

/// One permission as the manifest declares it: `{"id": …, "scope": …}`.
class PermissionEntry {
  const PermissionEntry(this.id, this.scope);
  final String id;
  final Object? scope;
}

List<PermissionEntry> _permissions(Map<String, dynamic>? m) {
  final raw = m?['permissions'];
  if (raw is! List) return const [];
  return [
    for (final p in raw)
      if (p is String)
        PermissionEntry(p, null)
      else if (p is Map && p['id'] is String)
        PermissionEntry(p['id'] as String, p['scope']),
  ];
}

/// How an update changes the declared permissions.
class PermissionDelta {
  const PermissionDelta(this.added, this.removed, this.scopeChanged);

  /// Permissions the update declares that the registry's version did not.
  final List<PermissionEntry> added;

  /// Permission ids the registry's version declared that the update drops.
  final List<String> removed;

  /// Permissions in both whose scope changed: (id, old scope, new scope).
  final List<(String, Object?, Object?)> scopeChanged;

  bool get isEmpty => added.isEmpty && removed.isEmpty && scopeChanged.isEmpty;
}

/// The permission delta from [before] (the registry's manifest) to [after].
PermissionDelta permissionDelta(
  Map<String, dynamic>? before,
  Map<String, dynamic> after,
) {
  final old = {for (final p in _permissions(before)) p.id: p};
  final now = {for (final p in _permissions(after)) p.id: p};
  return PermissionDelta(
    [
      for (final p in now.values)
        if (!old.containsKey(p.id)) p,
    ],
    [
      for (final id in old.keys)
        if (!now.containsKey(id)) id,
    ],
    [
      for (final p in now.values)
        if (old.containsKey(p.id) &&
            _compactJson(old[p.id]!.scope) != _compactJson(p.scope))
          (p.id, old[p.id]!.scope, p.scope),
    ],
  );
}

/// Verified-only capabilities [m] declares, as the registry CI lists them
/// (validate.yml step 6): sidecar execution, `ai:inference`,
/// `write:external_output`, any `propose:*` permission, and a WebView UI.
List<String> verifiedOnlyCapabilities(Map<String, dynamic> m) {
  return [
    if ((m['execution_mode'] ?? 'wasm') == 'sidecar') 'execution_mode: sidecar',
    for (final p in _permissions(m))
      if (p.id == 'ai:inference' ||
          p.id == 'write:external_output' ||
          p.id.startsWith('propose:'))
        p.id,
    if ((m['ui_type'] ?? 'declarative') == 'webview') 'ui_type: webview',
  ];
}

// ─────────────────────────────────────────────────────────────────────────────
// The registry PR
// ─────────────────────────────────────────────────────────────────────────────

/// The PR title (also the commit message): `Add plugin: <id> <version>` for a
/// new plugin, `Update plugin: <id> <old> → <new>` for an update.
String registryPrTitle({
  required String id,
  required String version,
  required String? upstreamVersion,
}) => upstreamVersion == null
    ? 'Add plugin: $id $version'
    : 'Update plugin: $id $upstreamVersion → $version';

/// The PR body: new vs update, the permission delta (updates), the pinned
/// hashes with the URLs they were downloaded from, and a note when the plugin
/// declares Verified-only capabilities.
String registryPrBody({
  required Map<String, dynamic> manifest,
  required Map<String, dynamic>? upstream,
  required Map<String, int> artifactSizes,
  bool firstParty = false,
}) {
  final b = StringBuffer();
  final id = manifest['id'];
  final version = manifest['version'];
  if (upstream == null) {
    b.writeln('**New plugin** `$id` $version.');
  } else {
    b.writeln('**Update** of `$id`: ${upstream['version']} → $version.');
  }
  b
    ..writeln()
    ..writeln('Opened by `hqplugin publish`.')
    ..writeln()
    ..writeln('### Artifacts')
    ..writeln()
    ..writeln('Each hash is the SHA-256 of the bytes downloaded from its URL.')
    ..writeln();
  void artifact(String urlKey, String hashKey) {
    final url = manifest[urlKey];
    if (url == null || manifest[hashKey] == null) return;
    final size = artifactSizes[urlKey];
    b
      ..writeln('- `$urlKey`: $url')
      ..writeln(
        '  - `$hashKey`: `${manifest[hashKey]}`'
        '${size == null ? '' : ' ($size bytes)'}',
      );
  }

  artifact('wasm_url', 'content_hash_sha256');
  artifact('ui_bundle_url', 'ui_bundle_hash_sha256');
  artifact('sidebar_icon', 'sidebar_icon_hash_sha256');

  b
    ..writeln()
    ..writeln('### Permissions')
    ..writeln();
  String scope(Object? s) => s == null ? '' : ' — scope `${_compactJson(s)}`';
  if (upstream == null) {
    final perms = _permissions(manifest);
    if (perms.isEmpty) b.writeln('None.');
    for (final p in perms) {
      b.writeln('- `${p.id}`${scope(p.scope)}');
    }
  } else {
    final delta = permissionDelta(upstream, manifest);
    if (delta.isEmpty) b.writeln('No permission changes.');
    for (final p in delta.added) {
      b.writeln('- Added: `${p.id}`${scope(p.scope)}');
    }
    for (final id in delta.removed) {
      b.writeln('- Removed: `$id`');
    }
    for (final (id, before, after) in delta.scopeChanged) {
      b.writeln(
        '- Scope changed: `$id`: `${_compactJson(before)}` → '
        '`${_compactJson(after)}`',
      );
    }
  }

  b
    ..writeln()
    ..writeln('### Trust tier')
    ..writeln();
  if (firstParty) {
    b.writeln(
      'First-party publish (`--first-party`): `trust_tier` '
      '(`${manifest['trust_tier'] ?? 'community'}`) and provenance '
      '(`${manifest['provenance'] ?? 'community'}`) come from the '
      'HelloHQ manifest.',
    );
  } else if (upstream != null && manifest['trust_tier'] != null) {
    b.writeln(
      '`trust_tier` (`${manifest['trust_tier']}`) is carried over '
      'from the registry; this PR does not change it.',
    );
  } else {
    b.writeln('`trust_tier` is not set; the registry team assigns it.');
  }
  final verified = verifiedOnlyCapabilities(manifest);
  if (verified.isNotEmpty) {
    final caps = verified.map((c) => '`$c`').join(', ');
    final tier = manifest['trust_tier'];
    b.writeln();
    if (tier == 'verified' || tier == 'official') {
      b.writeln(
        'This plugin declares Verified-only capabilities ($caps); '
        'its `trust_tier` (`$tier`) allows them.',
      );
    } else {
      b.writeln(
        'This plugin declares Verified-only capabilities ($caps). '
        'The registry team reviews them and assigns the tier; CI fails '
        'until `trust_tier` is `verified`.',
      );
    }
  }

  final lic = (manifest['licensing'] as Map?) ?? const {};
  final spdx = lic['spdx'];
  b
    ..writeln()
    ..writeln('### Provenance and licensing')
    ..writeln()
    ..writeln('- provenance: `${manifest['provenance'] ?? 'community'}`')
    ..writeln(
      '- licensing: `${lic['kind'] ?? 'open_source'}`'
      '${spdx == null ? '' : ' (`$spdx`)'}',
    )
    ..writeln()
    ..writeln(
      '`signatures` are left out; the signing pipeline signs the '
      'merged manifest.',
    );
  return b.toString();
}

// ─────────────────────────────────────────────────────────────────────────────
// Diff
// ─────────────────────────────────────────────────────────────────────────────

/// A line diff of [before] → [after] (LCS based): unchanged lines start with
/// two spaces, removed ones with `- `, added ones with `+ `.
List<String> lineDiff(String before, String after) {
  final a = before.isEmpty ? <String>[] : before.trimRight().split('\n');
  final b = after.isEmpty ? <String>[] : after.trimRight().split('\n');
  final lcs = List.generate(a.length + 1, (_) => List.filled(b.length + 1, 0));
  for (var i = a.length - 1; i >= 0; i--) {
    for (var j = b.length - 1; j >= 0; j--) {
      lcs[i][j] = a[i] == b[j]
          ? lcs[i + 1][j + 1] + 1
          : (lcs[i + 1][j] > lcs[i][j + 1] ? lcs[i + 1][j] : lcs[i][j + 1]);
    }
  }
  final out = <String>[];
  var i = 0, j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] == b[j]) {
      out.add('  ${a[i]}');
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.add('- ${a[i++]}');
    } else {
      out.add('+ ${b[j++]}');
    }
  }
  while (i < a.length) {
    out.add('- ${a[i++]}');
  }
  while (j < b.length) {
    out.add('+ ${b[j++]}');
  }
  return out;
}
