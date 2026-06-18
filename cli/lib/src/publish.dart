import 'dart:convert';
import 'dart:io';

import 'package:path/path.dart' as p;

typedef PublishCommandRunner = Future<ProcessResult> Function(
  String executable,
  List<String> arguments, {
  String? workingDirectory,
});

/// Publish a plugin release to the HelloHQ plugin registry via a GitHub PR.
///
/// The workflow:
///   1. Validate `--version` is a well-formed semver string.
///   2. Locate `manifest.json` in the current directory.
///   3. Gate on provenance/licensing: only `community` (or unset) provenance and
///      non-`commercial` licensing may be published to the public registry —
///      `enterprise`/`core` and commercial plugins are rejected here with the
///      same reasoning the registry CI uses.
///   4. Hash the built `plugin.wasm` (SHA-256 via `shasum`/`sha256sum`).
///   5. Produce a stamped registry-ready manifest (version + hash updated).
///   6. If `gh` is available and the user passes `--submit`, create or reuse the
///      user's registry fork, push a branch there, and open a PR.
///      Otherwise, print the manifest so the author can submit the PR manually.
Future<int> runPublish({
  String? version,
  String? wasmPath,
  bool submit = false,
  StringSink? out_,
  StringSink? err_,
  PublishCommandRunner commandRunner = _runCommand,
  String? workingDirectory,
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
  final manifestFile = _findManifest(workingDirectory);
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

  // ── Provenance + licensing gates ──────────────────────────────────────────
  // Mirror the registry CI and the in-app install rules so authors get the
  // rejection here, before opening a PR that would just fail CI.
  final provenance = (manifest['provenance'] as String?) ?? 'community';
  if (provenance == 'enterprise') {
    e.writeln(
      'publish: provenance "enterprise" plugins are private to the org that '
      'built them and are not published to the public registry.\n'
      '  Deploy them through your organisation\'s plugin policy instead.',
    );
    return 65;
  }
  if (provenance == 'core') {
    e.writeln(
      'publish: provenance "core" is reserved for HelloHQ first-party plugins '
      '(published through an internal signed pipeline, not this command).',
    );
    return 65;
  }

  final licensing =
      (manifest['licensing'] as Map<String, dynamic>?) ?? const {};
  final licenseKind = (licensing['kind'] as String?) ?? 'open_source';
  if (licenseKind == 'commercial') {
    e.writeln(
      'publish: commercial licensing is not yet available — the '
      'HelloHQ-brokered marketplace has not shipped.\n'
      '  Publish as open_source for now (set licensing.kind: "open_source").',
    );
    return 65;
  }
  if (licenseKind == 'open_source' &&
      (licensing['spdx'] == null || (licensing['spdx'] as String).isEmpty)) {
    e.writeln(
      'publish: warning — open-source plugin has no licensing.spdx. Add one '
      '(e.g. "MIT", "Apache-2.0") so users see the licence in the catalog.',
    );
  }

  // ── Hash the wasm artifact ────────────────────────────────────────────────
  // Default to plugin.wasm beside the manifest (the project dir), so publishing
  // works without an explicit --wasm and without depending on the process cwd.
  final wasm = File(wasmPath ?? p.join(workingDirectory ?? '.', 'plugin.wasm'));
  if (!wasm.existsSync()) {
    e.writeln(
      'publish: wasm artifact not found at "${wasm.path}".\n'
      '  Run `hqplugin build` first, or pass --wasm <path>.',
    );
    return 66;
  }

  final sha256 = await _sha256(wasm.path);
  if (sha256 == null) {
    e.writeln(
        'publish: could not compute SHA-256 — shasum/sha256sum not found.');
    return 69;
  }

  // ── Stamp the manifest ─────────────────────────────────────────────────────
  final stamped = Map<String, dynamic>.from(manifest)
    ..['version'] = version
    ..['content_hash_sha256'] = sha256;

  final manifestJson = const JsonEncoder.withIndent('  ').convert(stamped);
  // The registry stores exactly one manifest per plugin at this fixed path;
  // its CI rejects any other layout (dir name must equal the plugin id).
  final registryPath = 'plugins/$pluginId/manifest.json';

  o.writeln('publish: id=$pluginId  version=$version');
  o.writeln('publish: sha256=$sha256');
  if (manifest['min_host_version'] == null) {
    e.writeln(
      'publish: warning — manifest.json has no "min_host_version"; the '
      'registry requires it and CI will reject the PR. Add e.g. '
      '"min_host_version": "1.0.0".',
    );
  }
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
      commandRunner: commandRunner,
    );
    return code;
  }

  // ── Manual path — print the ready-to-submit manifest ─────────────────────
  o.writeln('── Registry manifest ($registryPath) ──');
  o.writeln(manifestJson);
  o.writeln('');
  o.writeln('To publish:');
  o.writeln('  1. Fork https://github.com/HelloHQ/plugin-registry');
  o.writeln('  2. Create $registryPath with the JSON above.');
  o.writeln(
      '  3. Open a pull request — HelloHQ CI validates it automatically.');
  o.writeln('  Or rerun with --submit to have hqplugin open the PR for you.');
  return 0;
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

/// Walks up from [from] (or cwd) looking for `manifest.json`.
File? _findManifest([String? from]) {
  var dir = from == null ? Directory.current : Directory(from);
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
  required PublishCommandRunner commandRunner,
}) async {
  if (!await _hasExecutable('gh', commandRunner)) {
    e.writeln(
      'publish: --submit requires the GitHub CLI (gh).\n'
      '  Install from https://cli.github.com and run `gh auth login` first.',
    );
    return 69;
  }

  final tmp = await Directory.systemTemp.createTemp('hqplugin-registry-');
  try {
    final loginResult = await commandRunner(
      'gh',
      ['api', 'user', '--jq', '.login'],
    );
    if (!_succeeded(loginResult)) {
      return _reportFailure(
        loginResult,
        'publish: failed to resolve the authenticated GitHub user.',
        e,
      );
    }
    final login = '${loginResult.stdout}'.trim();
    if (login.isEmpty) {
      e.writeln('publish: GitHub CLI returned an empty authenticated user.');
      return 69;
    }

    final forkRepo = '$login/plugin-registry';
    final forkResult = await commandRunner(
      'gh',
      [
        'repo',
        'view',
        forkRepo,
        '--json',
        'parent',
        '--jq',
        '.parent.nameWithOwner',
      ],
    );
    if (_succeeded(forkResult)) {
      final parent = '${forkResult.stdout}'.trim();
      if (parent != 'HelloHQ/plugin-registry') {
        e.writeln(
          'publish: $forkRepo exists but is not a fork of '
          'HelloHQ/plugin-registry.',
        );
        return 65;
      }
    } else {
      o.writeln('publish: creating fork $forkRepo ...');
      final createForkResult = await commandRunner(
        'gh',
        ['repo', 'fork', 'HelloHQ/plugin-registry', '--clone=false'],
      );
      if (!_succeeded(createForkResult)) {
        return _reportFailure(
          createForkResult,
          'publish: failed to create plugin-registry fork.',
          e,
        );
      }
    }

    o.writeln('publish: cloning $forkRepo ...');
    final cloneResult = await commandRunner(
      'gh',
      ['repo', 'clone', forkRepo, tmp.path],
    );
    if (!_succeeded(cloneResult)) {
      return _reportFailure(
        cloneResult,
        'publish: failed to clone plugin-registry fork.',
        e,
      );
    }

    final upstreamResult = await commandRunner(
      'git',
      [
        'remote',
        'add',
        'upstream',
        'https://github.com/HelloHQ/plugin-registry.git',
      ],
      workingDirectory: tmp.path,
    );
    if (!_succeeded(upstreamResult)) {
      return _reportFailure(
        upstreamResult,
        'publish: failed to configure the upstream registry remote.',
        e,
      );
    }

    final fetchResult = await commandRunner(
      'git',
      ['fetch', 'upstream', 'main'],
      workingDirectory: tmp.path,
    );
    if (!_succeeded(fetchResult)) {
      return _reportFailure(
        fetchResult,
        'publish: failed to fetch the upstream registry.',
        e,
      );
    }

    final branch = 'plugin/$pluginId-$version';
    final checkoutResult = await commandRunner(
      'git',
      ['checkout', '-b', branch, 'upstream/main'],
      workingDirectory: tmp.path,
    );
    if (!_succeeded(checkoutResult)) {
      return _reportFailure(
        checkoutResult,
        'publish: failed to create branch $branch.',
        e,
      );
    }

    final dest = File(p.join(tmp.path, registryPath));
    dest.parent.createSync(recursive: true);
    dest.writeAsStringSync(manifestJson);

    final addResult = await commandRunner(
      'git',
      ['add', registryPath],
      workingDirectory: tmp.path,
    );
    if (!_succeeded(addResult)) {
      return _reportFailure(addResult, 'publish: git add failed.', e);
    }

    final commitResult = await commandRunner(
      'git',
      ['commit', '-m', 'plugin: add $pluginId $version'],
      workingDirectory: tmp.path,
    );
    if (!_succeeded(commitResult)) {
      return _reportFailure(commitResult, 'publish: git commit failed.', e);
    }

    final pushResult = await commandRunner(
      'git',
      ['push', '-u', 'origin', branch],
      workingDirectory: tmp.path,
    );
    if (!_succeeded(pushResult)) {
      return _reportFailure(
        pushResult,
        'publish: failed to push $branch to $forkRepo.',
        e,
      );
    }

    final prResult = await commandRunner(
      'gh',
      [
        'pr',
        'create',
        '--repo',
        'HelloHQ/plugin-registry',
        '--head',
        '$login:$branch',
        '--base',
        'main',
        '--title',
        'Add plugin: $pluginId $version',
        '--body',
        'Automated submission via `hqplugin publish --version $version --submit`.',
      ],
      workingDirectory: tmp.path,
    );
    if (!_succeeded(prResult)) {
      return _reportFailure(
        prResult,
        'publish: failed to open the registry pull request.',
        e,
      );
    }
    o.writeln('publish: PR opened');
    o.write(prResult.stdout);
    return 0;
  } finally {
    await tmp.delete(recursive: true).catchError((_) => tmp);
  }
}

Future<ProcessResult> _runCommand(
  String executable,
  List<String> arguments, {
  String? workingDirectory,
}) {
  return Process.run(
    executable,
    arguments,
    workingDirectory: workingDirectory,
  );
}

bool _succeeded(ProcessResult result) => result.exitCode == 0;

int _reportFailure(
  ProcessResult result,
  String message,
  StringSink e,
) {
  if ('${result.stderr}'.isNotEmpty) {
    e.write(result.stderr);
    if (!'${result.stderr}'.endsWith('\n')) {
      e.writeln();
    }
  }
  e.writeln(message);
  return result.exitCode == 0 ? 69 : result.exitCode;
}

Future<bool> _hasExecutable(
  String name,
  PublishCommandRunner commandRunner,
) async {
  try {
    final r = await commandRunner(name, ['--version']);
    return r.exitCode == 0;
  } catch (_) {
    return false;
  }
}
