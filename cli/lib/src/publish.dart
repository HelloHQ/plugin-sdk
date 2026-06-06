import 'dart:convert';
import 'dart:io';

import 'package:path/path.dart' as p;

/// Publish a plugin release to the HelloHQ plugin registry via a GitHub PR.
///
/// The workflow:
///   1. Validate `--version` is a well-formed semver string.
///   2. Locate `manifest.json` in the current directory.
///   3. Hash the built `plugin.wasm` (SHA-256 via `shasum`/`sha256sum`).
///   4. Produce a stamped registry-ready manifest (version + hash updated).
///   5. If `gh` is available and the user passes `--submit`, clone the registry
///      repo to a temp dir, write the manifest, and open a PR via `gh pr create`.
///      Otherwise, print the manifest so the author can submit the PR manually.
Future<int> runPublish({
  String? version,
  String? wasmPath,
  bool submit = false,
  StringSink? out_,
  StringSink? err_,
}) async {
  final o = out_ ?? stdout;
  final e = err_ ?? stderr;

  // ── Validate version ───────────────────────────────────────────────────────
  if (version == null || version.isEmpty) {
    e.writeln('publish: --version is required (e.g. --version 0.2.0).');
    return 64;
  }
  if (!_isSemver(version)) {
    e.writeln('publish: "$version" is not a valid semver (expected X.Y.Z).');
    return 64;
  }

  // ── Locate manifest.json ───────────────────────────────────────────────────
  final manifestFile = _findManifest();
  if (manifestFile == null) {
    e.writeln(
      'publish: no manifest.json found in the current directory.\n'
      '  Create one with the fields documented in '
      'https://hellohq.io/docs/plugins/publishing',
    );
    return 66;
  }

  final Map<String, dynamic> manifest;
  try {
    manifest =
        jsonDecode(manifestFile.readAsStringSync()) as Map<String, dynamic>;
  } catch (ex) {
    e.writeln('publish: failed to parse ${manifestFile.path}: $ex');
    return 65;
  }

  final pluginId = manifest['id'] as String?;
  if (pluginId == null || pluginId.isEmpty) {
    e.writeln('publish: manifest.json is missing the "id" field.');
    return 65;
  }

  // ── Hash the wasm artifact ────────────────────────────────────────────────
  final wasm = File(wasmPath ?? 'plugin.wasm');
  if (!wasm.existsSync()) {
    e.writeln(
      'publish: wasm artifact not found at "${wasm.path}".\n'
      '  Run `hqplugin build` first, or pass --wasm <path>.',
    );
    return 66;
  }

  final sha256 = await _sha256(wasm.path);
  if (sha256 == null) {
    e.writeln('publish: could not compute SHA-256 — shasum/sha256sum not found.');
    return 69;
  }

  // ── Stamp the manifest ─────────────────────────────────────────────────────
  final stamped = Map<String, dynamic>.from(manifest)
    ..['version'] = version
    ..['content_hash_sha256'] = sha256;

  final manifestJson = const JsonEncoder.withIndent('  ').convert(stamped);
  final registryPath = 'plugins/$pluginId/$version.json';

  o.writeln('publish: id=$pluginId  version=$version');
  o.writeln('publish: sha256=$sha256');
  o.writeln('');

  // ── Submit (optional, requires `gh`) ──────────────────────────────────────
  if (submit) {
    final code = await _openPR(
      pluginId: pluginId,
      version: version,
      manifestJson: manifestJson,
      registryPath: registryPath,
      o: o,
      e: e,
    );
    return code;
  }

  // ── Manual path — print the ready-to-submit manifest ─────────────────────
  o.writeln('── Registry manifest ($registryPath) ──');
  o.writeln(manifestJson);
  o.writeln('');
  o.writeln('To publish:');
  o.writeln('  1. Fork https://github.com/HelloHQ/plugin-registry');
  o.writeln('  2. Create plugins/$pluginId/$version.json with the JSON above.');
  o.writeln('  3. Open a pull request — HelloHQ CI validates it automatically.');
  o.writeln('  Or rerun with --submit to have hqplugin open the PR for you.');
  return 0;
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

/// Walks up from cwd looking for `manifest.json`.
File? _findManifest() {
  var dir = Directory.current;
  for (var i = 0; i < 4; i++) {
    final candidate = File(p.join(dir.path, 'manifest.json'));
    if (candidate.existsSync()) return candidate;
    final parent = dir.parent;
    if (parent.path == dir.path) break;
    dir = parent;
  }
  return null;
}

/// Returns true for bare X.Y.Z and pre-release variants like 1.0.0-beta.1.
bool _isSemver(String v) {
  return RegExp(
    r'^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$',
  ).hasMatch(v);
}

/// Computes SHA-256 of [path] using the platform's shasum/sha256sum.
Future<String?> _sha256(String path) async {
  for (final cmd in ['shasum -a 256', 'sha256sum']) {
    final parts = cmd.split(' ');
    try {
      final r = await Process.run(parts[0], [...parts.skip(1), path]);
      if (r.exitCode == 0) {
        final out = '${r.stdout}'.trim();
        // Both tools output "<hash>  <filename>" — grab the first token.
        final hash = out.split(RegExp(r'\s+')).first;
        if (hash.length == 64) return hash;
      }
    } catch (_) {
      continue;
    }
  }
  return null;
}

/// Clones the plugin-registry fork, writes the manifest, and opens a PR.
Future<int> _openPR({
  required String pluginId,
  required String version,
  required String manifestJson,
  required String registryPath,
  required StringSink o,
  required StringSink e,
}) async {
  if (!await _hasExecutable('gh')) {
    e.writeln(
      'publish: --submit requires the GitHub CLI (gh).\n'
      '  Install from https://cli.github.com and run `gh auth login` first.',
    );
    return 69;
  }

  final tmp = await Directory.systemTemp.createTemp('hqplugin-registry-');
  try {
    o.writeln('publish: cloning HelloHQ/plugin-registry …');
    final cloneResult = await Process.run(
      'gh',
      ['repo', 'clone', 'HelloHQ/plugin-registry', tmp.path],
    );
    if (cloneResult.exitCode != 0) {
      e.write(cloneResult.stderr);
      e.writeln('publish: failed to clone plugin-registry.');
      return cloneResult.exitCode;
    }

    final branch = 'plugin/$pluginId-$version';
    await _git(['checkout', '-b', branch], tmp.path);

    final dest = File(p.join(tmp.path, registryPath));
    dest.parent.createSync(recursive: true);
    dest.writeAsStringSync(manifestJson);

    await _git(['add', registryPath], tmp.path);
    await _git(
      ['commit', '-m', 'plugin: add $pluginId $version'],
      tmp.path,
    );
    await _git(['push', '-u', 'origin', branch], tmp.path);

    final prResult = await Process.run(
      'gh',
      [
        'pr',
        'create',
        '--repo',
        'HelloHQ/plugin-registry',
        '--title',
        'Add plugin: $pluginId $version',
        '--body',
        'Automated submission via `hqplugin publish --version $version --submit`.',
      ],
      workingDirectory: tmp.path,
    );
    if (prResult.exitCode != 0) {
      e.write(prResult.stderr);
      return prResult.exitCode;
    }
    o.writeln('publish: ✓ PR opened');
    o.write(prResult.stdout);
    return 0;
  } finally {
    await tmp.delete(recursive: true).catchError((_) => tmp);
  }
}

Future<void> _git(List<String> args, String workingDir) async {
  await Process.run('git', args, workingDirectory: workingDir);
}

Future<bool> _hasExecutable(String name) async {
  try {
    final r = await Process.run(name, ['--version']);
    return r.exitCode == 0;
  } catch (_) {
    return false;
  }
}
