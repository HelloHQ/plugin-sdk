// The author enrollment record of the plugin registry (authors/<slug>.json) and
// the proof of possession it carries.
//
// This file is the Dart twin of the registry's scripts/author-cert.mjs: the
// enrollment statement (`enrollmentStatement`), the record rules
// (`enrollmentProblems`, `prefixProblems`, the slug rule, the key sizes) and
// the way the registry's own files are laid out. The registry is the source of
// truth; `hqplugin keys enroll` validates here first and then runs the
// registry's own `scripts/check-authors.mjs` when it is given a checkout, and
// the tests pin this file to the registry's output.
import 'dart:convert';
import 'dart:typed_data';

import 'author_signing.dart';

/// The first line of the enrollment statement (ENROLLMENT_VERSION).
const String kEnrollmentStatementVersion = 'hellohq-author-enrollment/v1';

/// HelloHQ's own namespace. An author prefix must not be this or sit under it.
const String kReservedIdPrefix = 'com.hellohq.';

/// The only tier an author enrollment can carry.
const String kAuthorTier = 'author';

/// The GitHub OIDC issuer; its subject is the numeric account id.
const String kGitHubIssuer = 'https://github.com';

final RegExp _slug = RegExp(r'^[a-z][a-z0-9-]{1,38}[a-z0-9]$');
final RegExp _prefix = RegExp(r'^[a-z][a-z0-9-]*(\.[a-z0-9-]+)*\.$');
final RegExp _base64 = RegExp(r'^[A-Za-z0-9+/]+={0,2}$');
final RegExp _lineBreak = RegExp(r'[\r\n]');
final RegExp _gitHubLogin = RegExp(
  r'^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$',
);
final RegExp _gitHubId = RegExp(r'^[1-9][0-9]*$');

/// Whether [value] is a GitHub login (1-39 letters, digits and inner hyphens).
bool isGitHubLogin(String value) => _gitHubLogin.hasMatch(value);

/// Whether [value] is a numeric GitHub account id.
bool isGitHubAccountId(String value) => _gitHubId.hasMatch(value);

/// The enrollment statement bytes for [author] (a decoded authors/<slug>.json,
/// the proof is ignored), exactly as the registry's `enrollmentStatement()`
/// builds them: UTF-8, LF lines
///
///     hellohq-author-enrollment/v1
///     slug=…  oidc_issuer=…  oidc_subject=…  ed25519=…  ml-dsa-65=…
///     id_prefixes=<prefixes joined by ",">  tier=…
///
/// Throws [StatementException] with the registry's wording when a value is not
/// a single-line string.
Uint8List enrollmentStatement(Map<String, dynamic> author) {
  Object? at(Object? m, String key) => m is Map ? m[key] : null;

  String line(String name, Object? value) {
    if (value is! String || _lineBreak.hasMatch(value)) {
      throw StatementException(
        'enrollment statement: $name must be a single-line string',
      );
    }
    return '$name=$value\n';
  }

  final prefixes = author['id_prefixes'];
  final joined = prefixes == null
      ? ''
      : prefixes is List
      ? prefixes.map((e) => e is String ? e : '$e').join(',')
      : '$prefixes';
  final identity = author['identity'];
  final keys = author['public_keys'];
  final text =
      '$kEnrollmentStatementVersion\n'
      '${line('slug', author['slug'])}'
      '${line('oidc_issuer', at(identity, 'oidc_issuer'))}'
      '${line('oidc_subject', at(identity, 'oidc_subject'))}'
      '${line('ed25519', at(keys, HybridAlgorithm.ed25519.sigAlg))}'
      '${line('ml-dsa-65', at(keys, HybridAlgorithm.mlDsa65.sigAlg))}'
      '${line('id_prefixes', joined)}'
      '${line('tier', author['tier'])}';
  return Uint8List.fromList(utf8.encode(text));
}

/// Problems with a slug, or [] when it is 3-40 chars of `a-z`, `0-9` and `-`
/// that start with a letter and do not end with `-`.
List<String> slugProblems(Object? slug) =>
    slug is String && _slug.hasMatch(slug)
    ? const []
    : const ['slug must be 3-40 chars of a-z, 0-9, "-"'];

/// Problems with [prefixes], or [] when it is 1-8 unique, sorted, dotted
/// prefixes (each ending in "." and carrying at least two labels). The
/// registry's `prefixProblems`, message for message.
List<String> prefixProblems(Object? prefixes) {
  if (prefixes is! List || prefixes.isEmpty || prefixes.length > 8) {
    return const ['id_prefixes must be 1 to 8 prefixes'];
  }
  final out = <String>[];
  for (final p in prefixes) {
    if (p is! String || !_prefix.hasMatch(p)) {
      out.add('id_prefixes entries must be dotted prefixes ending in "."');
      continue;
    }
    if (p.split('.').where((label) => label.isNotEmpty).length < 2) {
      out.add('id_prefixes entry "$p" must have at least two labels');
    }
    if (p == kReservedIdPrefix || p.startsWith(kReservedIdPrefix)) {
      out.add('id_prefixes entry "$p" is reserved for HelloHQ');
    }
  }
  final strings = [for (final p in prefixes) '$p'];
  final sorted = [...strings]..sort();
  var inOrder = strings.toSet().length == strings.length;
  for (var i = 0; inOrder && i < strings.length; i++) {
    inOrder = strings[i] == sorted[i];
  }
  if (!inOrder) out.add('id_prefixes must be unique and sorted');
  return out;
}

/// Problems with the OIDC identity. Beyond the registry's rules (issuer an
/// https URL, subject present, no line breaks), a GitHub subject must be the
/// numeric account id: the login can be renamed or taken over, the id cannot.
List<String> identityProblems(Object? issuer, Object? subject) {
  final out = <String>[];
  for (final (name, value) in [
    ('oidc_issuer', issuer),
    ('oidc_subject', subject),
  ]) {
    if (value is! String || value.isEmpty || _lineBreak.hasMatch(value)) {
      out.add('identity.$name is required');
    }
  }
  if (issuer is String && !issuer.startsWith('https://')) {
    out.add('identity.oidc_issuer must be an https URL');
  }
  if (issuer == kGitHubIssuer &&
      subject is String &&
      subject.isNotEmpty &&
      !isGitHubAccountId(subject)) {
    out.add(
      'identity.oidc_subject must be the numeric GitHub account id, not a '
      'login ("$subject")',
    );
  }
  return out;
}

/// Problems with one raw public key as it is written (canonical base64 of
/// exactly the algorithm's size).
List<String> publicKeyProblems(HybridAlgorithm alg, Object? key) {
  if (key is String && _base64.hasMatch(key)) {
    try {
      final bytes = base64.decode(key);
      if (bytes.length == alg.publicKeyBytes && base64.encode(bytes) == key) {
        return const [];
      }
    } on FormatException {
      // Falls through to the problem below.
    }
  }
  return [
    'public_keys.${alg.sigAlg} must be ${alg.publicKeyBytes} bytes, base64',
  ];
}

/// Problems with an enrollment record, [] when sound. The shape rules of the
/// registry's `enrollmentProblems` (slug, identity, prefixes, tier, key sizes,
/// no unknown keys) without the proof check, which needs a verifier: see
/// `OpenSslTool.verifyWithPublicKey`. [slugFromFile] is the file name without
/// `.json`.
List<String> enrollmentProblems(Object? author, {String? slugFromFile}) {
  if (author is! Map) return const ['not an object'];
  final out = <String>[...slugProblems(author['slug'])];
  if (slugFromFile != null && author['slug'] != slugFromFile) {
    out.add(
      'slug "${author['slug']}" must match the file name "$slugFromFile"',
    );
  }
  final identity = author['identity'];
  out.addAll(
    identityProblems(
      identity is Map ? identity['oidc_issuer'] : null,
      identity is Map ? identity['oidc_subject'] : null,
    ),
  );
  out.addAll(prefixProblems(author['id_prefixes']));
  if (author['tier'] != kAuthorTier) out.add('tier must be "author"');
  final keys = author['public_keys'];
  final keyMap = keys is Map ? keys : const {};
  for (final alg in HybridAlgorithm.all) {
    out.addAll(publicKeyProblems(alg, keyMap[alg.sigAlg]));
  }
  final known = {for (final a in HybridAlgorithm.all) a.sigAlg};
  final extra = keyMap.keys.where((k) => !known.contains(k)).toList();
  if (extra.isNotEmpty) out.add('unknown public_keys: ${extra.join(', ')}');
  return out;
}

/// One author enrollment, ready to be signed and written.
class AuthorEnrollment {
  AuthorEnrollment({
    required this.slug,
    required this.oidcIssuer,
    required this.oidcSubject,
    required List<String> idPrefixes,
    required this.ed25519PublicKey,
    required this.mlDsa65PublicKey,
  }) : idPrefixes = List.unmodifiable(idPrefixes);

  final String slug;
  final String oidcIssuer;
  final String oidcSubject;

  /// Sorted and unique, as the registry requires.
  final List<String> idPrefixes;
  final Uint8List ed25519PublicKey;
  final Uint8List mlDsa65PublicKey;

  /// The record without its proof, keys in the registry's order.
  Map<String, dynamic> toJson() => {
    'slug': slug,
    'identity': {'oidc_issuer': oidcIssuer, 'oidc_subject': oidcSubject},
    'id_prefixes': idPrefixes,
    'public_keys': {
      HybridAlgorithm.ed25519.sigAlg: base64.encode(ed25519PublicKey),
      HybridAlgorithm.mlDsa65.sigAlg: base64.encode(mlDsa65PublicKey),
    },
    'tier': kAuthorTier,
  };

  /// The bytes the author's Ed25519 key signs.
  Uint8List get statement => enrollmentStatement(toJson());

  /// The file the registry reads: [toJson] plus `proof.ed25519`, 2-space JSON,
  /// each prefix on its own line, a trailing newline (the layout of the
  /// registry's own authors/*.json).
  String toFileJson(List<int> ed25519Proof) =>
      '${const JsonEncoder.withIndent('  ').convert({
        ...toJson(),
        'proof': {HybridAlgorithm.ed25519.sigAlg: base64.encode(ed25519Proof)},
      })}\n';
}
