// `hqplugin keys enroll`: build the registry enrollment file for the keys that
// `keys generate` made.
//
// An author is enrolled by a reviewed PR that adds `authors/<slug>.json` to
// the plugin registry: an OIDC identity, the two public keys, the id prefixes
// the author may sign, and a proof that they hold the Ed25519 key (a signature
// over the enrollment statement, see author_enrollment.dart). This command
// writes that file, signed and self-checked.
//
// Everything is validated before anything is signed: the slug, the prefixes
// (sorted for the author), the identity (the numeric GitHub account id, looked
// up through `gh` when only a login is given) and the key sizes. The proof is
// made by OpenSSL with the Ed25519 key and verified again, against the public
// key that goes into the file, from the JSON that is written; a proof that does
// not verify is an internal error (70) and no file is left. With
// `--registry-dir <plugin-registry checkout>` the registry's own
// `scripts/check-authors.mjs` runs on a scratch copy of its enrolled authors
// plus the new file, which also catches a reused key or an overlapping prefix
// before the PR. The private keys are only paths handed to openssl: never read
// into a Dart string, never printed.
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:path/path.dart' as p;

import 'author_enrollment.dart';
import 'author_signing.dart';

/// The registry the enrollment PR goes to.
const String kEnrollRegistryRepo = 'HelloHQ/plugin-registry';

/// What `scripts/check-authors.mjs` prints when every enrollment is sound.
const String _registrySoundLine = 'authors/ is sound';

class _Refusal implements Exception {
  const _Refusal(this.code, this.message);
  final int code;
  final String message;
}

/// Runs `keys enroll`. Exit codes: 0 written, 64 usage, 65 bad data (an
/// invalid slug, prefix or identity, a file that would be overwritten, an
/// unknown GitHub user, the registry's check refusing the enrollment), 66 a
/// key file or the registry checkout is missing, 69 OpenSSL or `gh` missing or
/// unusable, 70 an internal inconsistency (a proof that does not verify).
///
/// All external commands (openssl, gh, node) go through [runner]. [environment]
/// and [currentDirectory] default to the process's.
Future<int> runKeysEnroll({
  String? keysDir,
  String? slug,
  List<String> prefixes = const [],
  String? githubUser,
  String? oidcSubject,
  String? oidcIssuer,
  String? outPath,
  String? opensslPath,
  String? registryDir,
  Map<String, String>? environment,
  String? currentDirectory,
  StringSink? out_,
  StringSink? err_,
  OpenSslRunner runner = _runCommand,
}) async {
  final o = out_ ?? stdout;
  final e = err_ ?? stderr;
  final env = environment ?? Platform.environment;
  final cwd = currentDirectory ?? Directory.current.path;
  String abs(String path) => p.isAbsolute(path) ? path : p.join(cwd, path);

  // ── usage ────────────────────────────────────────────────────────────────
  final keyDirValue = (keysDir != null && keysDir.isNotEmpty)
      ? keysDir
      : env['HQPLUGIN_AUTHOR_KEY_DIR'];
  final usage = <String>[
    if (slug == null || slug.isEmpty) '--slug is required.',
    if (prefixes.isEmpty) '--prefix is required (repeat it for more).',
    if (githubUser != null && oidcSubject != null)
      'give --github-user or --oidc-subject, not both.',
    if (githubUser == null && oidcSubject == null)
      'an identity is required: --github-user <login> (the numeric account '
          'id is looked up with gh) or --oidc-subject <id>.',
    if (githubUser != null && oidcIssuer != null)
      '--oidc-issuer goes with --oidc-subject, not --github-user.',
    if (keyDirValue == null || keyDirValue.isEmpty)
      '--keys <dir> is required (the directory `keys generate` wrote), or set '
          r'$HQPLUGIN_AUTHOR_KEY_DIR.',
  ];
  if (usage.isNotEmpty) {
    for (final u in usage) {
      e.writeln('keys enroll: $u');
    }
    return 64;
  }

  try {
    final theSlug = slug!;
    final sorted = [...prefixes]..sort();

    // ── validate what needs no tool ───────────────────────────────────────
    var login = githubUser;
    if (login != null && login.startsWith('@')) login = login.substring(1);
    final issuer = login != null ? kGitHubIssuer : oidcIssuer ?? kGitHubIssuer;
    final problems = <String>[
      ...slugProblems(theSlug),
      ...prefixProblems(sorted),
      if (login != null && !isGitHubLogin(login))
        '--github-user "$login" is not a GitHub login.',
      if (login == null) ...identityProblems(issuer, oidcSubject),
    ];
    if (problems.isNotEmpty) {
      throw _Refusal(
        65,
        'not a valid enrollment, nothing was signed:\n'
        '${problems.map((x) => '  - $x').join('\n')}',
      );
    }

    // ── where the file goes ───────────────────────────────────────────────
    final target = abs(
      outPath != null && outPath.isNotEmpty ? outPath : '$theSlug.json',
    );
    _requireFreshTarget(target);

    // ── the registry checkout, when given ─────────────────────────────────
    final registry = registryDir != null && registryDir.isNotEmpty
        ? abs(registryDir)
        : null;
    if (registry != null) _requireRegistry(registry, theSlug);

    // ── identity ──────────────────────────────────────────────────────────
    final String subject;
    if (login != null) {
      subject = await _resolveGitHubId(runner, login, o, e);
    } else {
      subject = oidcSubject!;
    }

    // ── keys ──────────────────────────────────────────────────────────────
    final tool = OpenSslTool(
      run: runner,
      executable: resolveOpenSslExecutable(opensslPath, env),
    );
    final signer = await OpenSslHybridSigner.open(tool, abs(keyDirValue!));
    final enrollment = AuthorEnrollment(
      slug: theSlug,
      oidcIssuer: issuer,
      oidcSubject: subject,
      idPrefixes: sorted,
      ed25519PublicKey: signer.publicKeys[HybridAlgorithm.ed25519]!,
      mlDsa65PublicKey: signer.publicKeys[HybridAlgorithm.mlDsa65]!,
    );
    final unsigned = enrollmentProblems(enrollment.toJson());
    if (unsigned.isNotEmpty) {
      throw _Refusal(
        70,
        'the enrollment built from these keys is not sound, nothing was '
        'signed:\n${unsigned.map((x) => '  - $x').join('\n')}',
      );
    }

    // ── proof, self-verified against what is written ──────────────────────
    final statement = enrollment.statement;
    final proof = await tool.sign(
      HybridAlgorithm.ed25519,
      signer.ed25519KeyPath,
      statement,
    );
    final text = enrollment.toFileJson(proof);
    await _selfVerify(tool, enrollment, statement, text);

    // ── the registry's own checks, on a scratch copy ──────────────────────
    final registryNote = registry == null
        ? null
        : await _registryCheck(runner, registry, theSlug, text, e);

    // ── write ─────────────────────────────────────────────────────────────
    try {
      File(target).createSync(exclusive: true);
    } on FileSystemException {
      throw _Refusal(
        65,
        'refusing to overwrite $target (it appeared just now).',
      );
    }
    try {
      File(target).writeAsStringSync(text);
    } catch (_) {
      try {
        File(target).deleteSync();
      } catch (_) {}
      rethrow;
    }

    _report(
      o,
      enrollment: enrollment,
      target: target,
      keyDir: keyDirValue,
      sortedFrom: _sameOrder(prefixes, sorted) ? null : prefixes,
      registryNote: registryNote,
      registryChecked: registry != null,
    );
    return 0;
  } on _Refusal catch (ex) {
    e.writeln('keys enroll: ${ex.message}');
    return ex.code;
  } on SignerException catch (ex) {
    e.writeln('keys enroll: ${ex.message}');
    return ex.code;
  }
}

bool _sameOrder(List<String> a, List<String> b) {
  if (a.length != b.length) return false;
  for (var i = 0; i < a.length; i++) {
    if (a[i] != b[i]) return false;
  }
  return true;
}

void _requireFreshTarget(String target) {
  if (FileSystemEntity.typeSync(target, followLinks: false) !=
      FileSystemEntityType.notFound) {
    throw _Refusal(
      65,
      'refusing to overwrite $target.\n'
      '  Choose another --out, or move that file away first.',
    );
  }
  if (!Directory(p.dirname(target)).existsSync()) {
    throw _Refusal(65, 'the directory ${p.dirname(target)} does not exist.');
  }
}

void _requireRegistry(String registry, String slug) {
  if (!Directory(registry).existsSync() ||
      !File(p.join(registry, 'scripts', 'check-authors.mjs')).existsSync()) {
    throw _Refusal(
      66,
      '--registry-dir $registry is not a plugin-registry checkout (no '
      'scripts/check-authors.mjs).\n'
      '  Clone https://github.com/$kEnrollRegistryRepo, or leave '
      '--registry-dir out: registry CI runs the same checks on your PR.',
    );
  }
  if (File(p.join(registry, 'authors', '$slug.json')).existsSync()) {
    throw _Refusal(
      65,
      'the slug "$slug" is already enrolled in $registry '
      '(authors/$slug.json).\n'
      '  Pick another --slug. (Changing an existing enrollment is a '
      'different PR: edit that file.)',
    );
  }
}

/// The numeric account id of [login], from `gh api users/<login>`. Warns on
/// [err] when `gh` is logged in as somebody else.
Future<String> _resolveGitHubId(
  OpenSslRunner run,
  String login,
  StringSink out,
  StringSink err,
) async {
  Future<ProcessResult> gh(List<String> args) async {
    try {
      return await run('gh', args);
    } on ProcessException catch (ex) {
      throw _Refusal(
        69,
        'gh (the GitHub CLI) was not found (${ex.message}).\n'
        '  Install it from https://cli.github.com and run `gh auth login`, '
        'or pass --oidc-subject <numeric account id> instead of '
        '--github-user (your id is the "id" at '
        'https://api.github.com/users/$login).',
      );
    }
  }

  final r = await gh(['api', 'users/$login', '--jq', '.id']);
  if (r.exitCode != 0) {
    final text = sanitizeOpenSslText('${r.stderr}${r.stdout}');
    if (RegExp(r'\b404\b|Not Found').hasMatch(text)) {
      throw _Refusal(65, 'GitHub has no user "$login".');
    }
    throw _Refusal(
      69,
      'gh could not look up the GitHub user "$login": $text\n'
      '  Is gh logged in (`gh auth login`)? Or pass --oidc-subject '
      '<numeric account id> instead.',
    );
  }
  final id = '${r.stdout}'.trim();
  if (!isGitHubAccountId(id)) {
    throw _Refusal(
      65,
      'gh returned "${sanitizeOpenSslText(id)}" as the id of "$login", not a '
      'numeric GitHub account id.',
    );
  }
  out.writeln('keys enroll: GitHub user $login has account id $id.');

  final who = await gh(['api', 'user', '--jq', '.login']);
  final me = '${who.stdout}'.trim();
  if (who.exitCode != 0 || me.isEmpty) {
    err.writeln(
      'keys enroll: warning: could not tell which GitHub account gh is '
      'logged in as, so cannot check that "$login" is you.',
    );
  } else if (me.toLowerCase() != login.toLowerCase()) {
    err.writeln(
      'keys enroll: warning: gh is logged in as "${sanitizeOpenSslText(me)}" '
      'but you are enrolling GitHub user "$login" (account id $id). This '
      "binds your keys to someone else's account; if that is not what you "
      'meant, stop and rerun with your own login.',
    );
  }
  return id;
}

/// Re-reads the JSON that is about to be written and checks that the proof in
/// it verifies, over the statement rebuilt from that JSON, under the public
/// key in that JSON. Throws a 70 [_Refusal] when anything differs.
Future<void> _selfVerify(
  OpenSslTool tool,
  AuthorEnrollment enrollment,
  Uint8List statement,
  String text,
) async {
  _Refusal bad(String why) => _Refusal(
    70,
    'the enrollment proof did not check out ($why); nothing was written.',
  );
  final Object? parsed;
  try {
    parsed = jsonDecode(text);
  } on FormatException {
    throw bad('the file is not valid JSON');
  }
  if (parsed is! Map<String, dynamic>) throw bad('the file is not an object');
  final problems = enrollmentProblems(parsed);
  if (problems.isNotEmpty) throw bad(problems.join('; '));
  final rebuilt = enrollmentStatement(parsed);
  if (!_sameBytes(rebuilt, statement)) {
    throw bad(
      'the written record has a different statement than the one signed',
    );
  }
  final proof = (parsed['proof'] as Map?)?[HybridAlgorithm.ed25519.sigAlg];
  final keys = parsed['public_keys'] as Map;
  final Uint8List sig;
  final Uint8List key;
  try {
    sig = base64.decode(proof as String);
    key = base64.decode(keys[HybridAlgorithm.ed25519.sigAlg] as String);
  } on Object {
    throw bad('the proof or the public key is not base64');
  }
  if (sig.length != HybridAlgorithm.ed25519.signatureBytes) {
    throw bad('the proof is ${sig.length} bytes');
  }
  if (!_sameBytes(key, enrollment.ed25519PublicKey) ||
      !await tool.verifyWithPublicKey(
        HybridAlgorithm.ed25519,
        key,
        rebuilt,
        sig,
      )) {
    throw bad('proof.ed25519 does not verify under public_keys.ed25519');
  }
}

bool _sameBytes(List<int> a, List<int> b) {
  if (a.length != b.length) return false;
  for (var i = 0; i < a.length; i++) {
    if (a[i] != b[i]) return false;
  }
  return true;
}

/// Runs the registry's `scripts/check-authors.mjs` on a scratch copy of
/// [registry]'s scripts and enrolled authors plus the new file. Returns the
/// line to show on success (or a warning when node is missing), throws a 65
/// [_Refusal] carrying the registry's own output when it refuses.
Future<String> _registryCheck(
  OpenSslRunner run,
  String registry,
  String slug,
  String text,
  StringSink err,
) async {
  try {
    final v = await run('node', const ['--version']);
    if (v.exitCode != 0) throw const ProcessException('node', ['--version']);
  } on ProcessException {
    err.writeln(
      'keys enroll: warning: node was not found, so the registry checks '
      '(shared keys, overlapping prefixes) were skipped here; registry CI '
      'runs them on your PR. Install Node.js to run them now.',
    );
    return 'skipped (node is not installed)';
  }
  final scratch = Directory.systemTemp.createTempSync('hqplugin-enroll-');
  try {
    // The real path: the registry script only runs its checks when it is the
    // main module, and node compares argv[1] with the module's real path, so a
    // symlinked temp directory (macOS /var -> /private/var) would make it exit
    // 0 having checked nothing.
    final root = scratch.resolveSymbolicLinksSync();
    _copyTree(
      Directory(p.join(registry, 'scripts')),
      Directory(p.join(root, 'scripts')),
    );
    final authors = Directory(p.join(root, 'authors'))..createSync();
    final existing = Directory(p.join(registry, 'authors'));
    var enrolled = 0;
    if (existing.existsSync()) {
      for (final f in existing.listSync().whereType<File>()) {
        if (f.path.endsWith('.json')) {
          f.copySync(p.join(authors.path, p.basename(f.path)));
          enrolled++;
        }
      }
    }
    File(p.join(authors.path, '$slug.json')).writeAsStringSync(text);
    final ProcessResult r;
    try {
      r = await run('node', [
        p.join(root, 'scripts', 'check-authors.mjs'),
        root,
      ]);
    } on ProcessException {
      err.writeln(
        'keys enroll: warning: node could not be run; the registry '
        'checks were skipped.',
      );
      return 'skipped (node could not be run)';
    }
    final output = '${r.stdout}${r.stderr}'.trim();
    if (r.exitCode != 0) {
      final lines = const LineSplitter()
          .convert(output)
          .map((l) => '  $l')
          .join('\n');
      throw _Refusal(
        65,
        "the registry's own check (scripts/check-authors.mjs) refused this "
        'enrollment; no file was written:\n'
        '${lines.isEmpty ? '  (exit code ${r.exitCode})' : lines}',
      );
    }
    // A check that exits 0 without saying so may not have run at all.
    final said = const LineSplitter()
        .convert(output)
        .where((l) => l.trim() == _registrySoundLine);
    if (said.isEmpty) {
      throw _Refusal(
        70,
        'the registry check (scripts/check-authors.mjs) exited 0 without '
        'reporting "$_registrySoundLine", so it may not have run; nothing was '
        'written.'
        '${output.isEmpty ? '' : '\n  Its output: ${sanitizeOpenSslText(output)}'}',
      );
    }
    return '$_registrySoundLine (checked with $enrolled other enrolled '
        'author${enrolled == 1 ? '' : 's'})';
  } finally {
    try {
      scratch.deleteSync(recursive: true);
    } catch (_) {}
  }
}

void _copyTree(Directory from, Directory to) {
  to.createSync(recursive: true);
  for (final entity in from.listSync(followLinks: false)) {
    final name = p.basename(entity.path);
    if (entity is File) {
      entity.copySync(p.join(to.path, name));
    } else if (entity is Directory) {
      _copyTree(entity, Directory(p.join(to.path, name)));
    }
  }
}

void _report(
  StringSink o, {
  required AuthorEnrollment enrollment,
  required String target,
  required String keyDir,
  required List<String>? sortedFrom,
  required String? registryNote,
  required bool registryChecked,
}) {
  final slug = enrollment.slug;
  o
    ..writeln()
    ..writeln('keys enroll: wrote $target')
    ..writeln('  slug:      $slug')
    ..writeln(
      '  identity:  ${enrollment.oidcIssuer} '
      'subject ${enrollment.oidcSubject}',
    )
    ..writeln('  prefixes:  ${enrollment.idPrefixes.join(', ')}')
    ..writeln(
      '  The proof was made with your Ed25519 key and verified against the '
      'public key in the file.',
    );
  if (sortedFrom != null) {
    o.writeln(
      '  Note: the registry requires id_prefixes sorted, so --prefix values '
      'were sorted for you (given: ${sortedFrom.join(', ')}).',
    );
  }
  if (registryChecked) {
    o.writeln("  Registry check: $registryNote.");
  } else {
    o.writeln(
      '  Not checked against other enrolled authors (a reused key, an '
      'overlapping prefix); registry CI does that on your PR. Pass '
      '--registry-dir <plugin-registry checkout> to check now.',
    );
  }
  final nameOk = p.basename(target) == '$slug.json';
  o
    ..writeln()
    ..writeln('Enroll with a pull request (one author per PR):')
    ..writeln('  1. Fork and clone https://github.com/$kEnrollRegistryRepo.')
    ..writeln(
      '  2. Put the file at authors/$slug.json in your clone'
      '${nameOk ? '' : ' (rename it: the file name must be the slug)'}, '
      'commit it on its own, and open the PR against main.',
    )
    ..writeln(
      '  3. CI re-runs the registry\'s checks: the record, your proof, no '
      'shared keys, no overlapping prefixes.',
    )
    ..writeln(
      '  4. A maintainer reviews it. After the merge the registry\'s signer '
      'issues your certificate; its schedule is best effort, so ask a '
      'maintainer to dispatch the signer workflow to have it issued straight '
      'away.',
    )
    ..writeln()
    ..writeln(
      'Your private keys never leave this machine: the file holds public '
      'keys and a signature, nothing else.',
    )
    ..writeln(
      'Once the certificate is issued, publish with '
      '`hqplugin publish --sign-key $keyDir`.',
    );
}

Future<ProcessResult> _runCommand(String executable, List<String> arguments) =>
    Process.run(executable, arguments);
