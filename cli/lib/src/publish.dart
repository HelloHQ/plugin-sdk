// `hqplugin publish`: pin a released plugin in the HelloHQ plugin registry.
//
// The registry's integrity model is "the manifest pins the SHA-256 of what its
// URLs serve". So publish never hashes a local file: it downloads the bytes
// wasm_url / ui_bundle_url serve and pins those. The URLs either come from the
// manifest (files already released) or from `--release`, which creates a
// GitHub Release on the author's repo, uploads the local files, and then
// downloads them again (round trip) to pin what GitHub serves.
//
// The registry PR (`--submit`) is built in a clone of the author's fork with
// the registry's own scripts (build-index, check-pr-signatures,
// verify-artifacts), so it passes the same checks registry CI runs.
//
// Every external command (gh, git, node) goes through one injectable
// [PublishCommandRunner] and every download through one [PublishHttpGet], so
// the tests are hermetic.
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:path/path.dart' as p;

import 'publish_fetch.dart';
import 'publish_manifest.dart';

export 'publish_fetch.dart';
export 'publish_manifest.dart';

typedef PublishCommandRunner =
    Future<ProcessResult> Function(
      String executable,
      List<String> arguments, {
      String? workingDirectory,
    });

/// The public registry every PR targets.
const String kRegistryRepo = 'HelloHQ/plugin-registry';

/// The GitHub organization whose members may use `--first-party`.
const String kFirstPartyOrg = 'HelloHQ';

const String _registryGitUrl = 'https://github.com/$kRegistryRepo.git';
const String _registryRawBase =
    'https://raw.githubusercontent.com/$kRegistryRepo/main';

/// How many times a just-uploaded release asset is fetched before giving up
/// (GitHub can take a moment to serve a new asset).
const int _releaseDownloadAttempts = 5;

/// Publish a plugin to the HelloHQ plugin registry.
///
/// Without [submit] (or with [dryRun]) nothing is mutated: the artifacts are
/// downloaded and hashed, the registry manifest is built and diffed against
/// the registry's, and the plan is printed. With [submit], [release] creates
/// the GitHub Release first, then the registry PR is opened (or the open one
/// updated).
///
/// Exit codes follow sysexits: 64 usage, 65 bad data, 66 missing input,
/// 69 a required tool or service unavailable, 77 not permitted; a failing
/// external command returns its own exit code.
Future<int> runPublish({
  String? version,
  String? bump,
  String? wasmPath,
  String? uiBundlePath,
  bool release = false,
  String? repo,
  String? tag,
  bool allowDirty = false,
  bool submit = false,
  bool dryRun = false,
  bool firstParty = false,
  StringSink? out_,
  StringSink? err_,
  PublishCommandRunner commandRunner = _runCommand,
  PublishHttpGet httpGet = defaultHttpGet,
  String? workingDirectory,
  int maxArtifactBytes = kMaxArtifactBytes,
  Duration retryDelay = const Duration(seconds: 2),
}) async {
  final e = err_ ?? stderr;
  final publisher = _Publisher(
    version: version,
    bump: bump,
    wasmPath: wasmPath,
    uiBundlePath: uiBundlePath,
    release: release,
    repo: repo,
    tag: tag,
    allowDirty: allowDirty,
    submit: submit,
    dryRun: dryRun,
    firstParty: firstParty,
    o: out_ ?? stdout,
    e: e,
    run: commandRunner,
    httpGet: httpGet,
    workingDirectory: workingDirectory,
    maxArtifactBytes: maxArtifactBytes,
    retryDelay: retryDelay,
  );
  try {
    return await publisher.publish();
  } on _Exit catch (x) {
    if (x.message != null) e.writeln(x.message);
    return x.code;
  }
}

/// Stops the publish with [code]; [message] goes to stderr.
class _Exit implements Exception {
  const _Exit(this.code, [this.message]);
  final int code;
  final String? message;
}

/// One artifact the manifest pins: the plugin file or the UI bundle.
class _Artifact {
  _Artifact(this.label, this.urlKey, this.hashKey);

  final String label;
  final String urlKey;
  final String hashKey;

  /// `--release`: the local file to upload, and its bytes.
  String? localPath;
  Uint8List? localBytes;

  String? url;

  /// The bytes [url] served (what gets pinned).
  Uint8List? served;

  /// Dry run of a release that does not exist yet: the hash shown is the
  /// local file's, re-checked by download once the release is created.
  bool provisional = false;

  Uint8List get pinnedBytes => served ?? localBytes!;
  String get hash => sha256Hex(pinnedBytes);
}

class _Publisher {
  _Publisher({
    required this.version,
    required this.bump,
    required this.wasmPath,
    required this.uiBundlePath,
    required this.release,
    required this.repo,
    required this.tag,
    required this.allowDirty,
    required this.submit,
    required this.dryRun,
    required this.firstParty,
    required this.o,
    required this.e,
    required this.run,
    required this.httpGet,
    required this.workingDirectory,
    required this.maxArtifactBytes,
    required this.retryDelay,
  });

  String? version;
  final String? bump;
  final String? wasmPath;
  final String? uiBundlePath;
  final bool release;
  String? repo;
  String? tag;
  final bool allowDirty;
  final bool submit;
  final bool dryRun;
  final bool firstParty;
  final StringSink o;
  final StringSink e;
  final PublishCommandRunner run;
  final PublishHttpGet httpGet;
  final String? workingDirectory;
  final int maxArtifactBytes;
  final Duration retryDelay;

  bool get mutate => submit && !dryRun;

  late final File manifestFile;
  late final String pluginDir;
  late final Map<String, dynamic> author;
  late final String id;
  late final String executionMode;
  late final String uiType;

  /// The registry's current manifest for [id] (null: new plugin), and whether
  /// it could be read at all.
  Map<String, dynamic>? upstream;
  String? upstreamRaw;
  bool upstreamKnown = false;

  final _Artifact wasm = _Artifact(
    'plugin file',
    'wasm_url',
    'content_hash_sha256',
  );
  _Artifact? ui;

  String? headSha;
  bool releaseExists = false;
  String? _login;

  late Map<String, dynamic> registry;

  String get registryPath => 'plugins/$id/manifest.json';
  String get branch => 'publish/$id/$version';

  // ── Orchestration ────────────────────────────────────────────────────────

  Future<int> publish() async {
    _validateFlags();
    if (submit && dryRun) {
      o.writeln(
        'publish: --dry-run: planning only; nothing is released, '
        'pushed or opened.',
      );
    }
    _loadManifest();
    _gates();
    _resolveVersion();
    _resolveArtifacts();

    // Reads first, every precondition before the first mutation.
    await _readUpstreamFromRegistry();
    _versionPrecheck();
    if (firstParty) await _checkFirstParty();
    if (mutate) await _requireSubmitTools();
    if (release) await _prepareRelease();

    if (release && !releaseExists) {
      if (mutate) {
        await _createRelease();
      } else {
        for (final a in _releasedArtifacts) {
          a.provisional = true;
        }
      }
    }
    await _downloadArtifacts();

    _composeRegistry();
    if (_alreadyPublished()) return 0;
    _printPlan();

    if (!mutate) {
      o
        ..writeln()
        ..writeln(
          'publish: plan only. No release, fork, push or PR was made. '
          'Rerun with --submit to publish.',
        );
      return 0;
    }
    return _submitToRegistry();
  }

  // ── Flags, manifest, gates ───────────────────────────────────────────────

  void _validateFlags() {
    if (version != null && bump != null) {
      throw const _Exit(
        64,
        'publish: --version and --bump are mutually exclusive; pass one.',
      );
    }
    if (bump != null && !kBumpKinds.contains(bump)) {
      throw _Exit(
        64,
        'publish: --bump must be one of ${kBumpKinds.join(', ')} (got "$bump").',
      );
    }
    if (version != null && Semver.tryParse(version) == null) {
      throw _Exit(
        64,
        'publish: "$version" is not a valid semver (expected X.Y.Z).',
      );
    }
    if (!release) {
      final releaseOnly = [
        if (repo != null) '--repo',
        if (tag != null) '--tag',
        if (wasmPath != null) '--wasm',
        if (uiBundlePath != null) '--ui-bundle',
        if (allowDirty) '--allow-dirty',
      ];
      if (releaseOnly.isNotEmpty) {
        throw _Exit(
          64,
          'publish: ${releaseOnly.join(', ')} only apply with --release. '
          'Without --release, publish pins the files the manifest\'s '
          'wasm_url / ui_bundle_url already serve.',
        );
      }
    }
  }

  void _loadManifest() {
    final found = _findManifest(workingDirectory);
    if (found == null) {
      throw const _Exit(
        66,
        'publish: no manifest.json found in the current directory.\n'
        '  Create one with the fields documented in '
        'https://hellohq.io/docs/plugins/publishing',
      );
    }
    manifestFile = found;
    pluginDir = found.parent.path;
    try {
      author = jsonDecode(found.readAsStringSync()) as Map<String, dynamic>;
    } catch (ex) {
      throw _Exit(65, 'publish: failed to parse ${found.path}: $ex');
    }
    final rawId = author['id'];
    if (rawId is! String || rawId.isEmpty) {
      throw const _Exit(
        65,
        'publish: manifest.json is missing the "id" field.',
      );
    }
    id = rawId;
  }

  /// Provenance and licensing: the same rules the registry CI applies, so an
  /// author gets the rejection here instead of a failing PR.
  void _gates() {
    final provenance = (author['provenance'] as String?) ?? 'community';
    if (provenance == 'enterprise') {
      throw const _Exit(
        65,
        'publish: provenance "enterprise" plugins are private to the org that '
        'built them and are not published to the public registry.\n'
        '  Deploy them through your organisation\'s plugin policy instead.',
      );
    }
    if (provenance == 'core' && !firstParty) {
      throw const _Exit(
        65,
        'publish: provenance "core" is reserved for HelloHQ first-party '
        'plugins. HelloHQ maintainers publish them with --first-party.',
      );
    }
    final licensing =
        (author['licensing'] as Map<String, dynamic>?) ?? const {};
    final licenseKind = (licensing['kind'] as String?) ?? 'open_source';
    if (licenseKind == 'commercial') {
      throw const _Exit(
        65,
        'publish: commercial licensing is not yet available — the '
        'HelloHQ-brokered marketplace has not shipped.\n'
        '  Publish as open_source for now (set licensing.kind: "open_source").',
      );
    }
    if (licenseKind == 'open_source' &&
        (licensing['spdx'] == null || (licensing['spdx'] as String).isEmpty)) {
      e.writeln(
        'publish: warning — open-source plugin has no licensing.spdx. Add one '
        '(e.g. "MIT", "Apache-2.0") so users see the licence in the catalog.',
      );
    }
  }

  void _resolveVersion() {
    final current = author['version'];
    if (bump != null) {
      if (current is! String || Semver.tryParse(current) == null) {
        throw _Exit(
          65,
          'publish: --bump needs a semver "version" in manifest.json '
          '(found ${jsonEncode(current)}).',
        );
      }
      version = bumpVersion(current, bump!);
      o.writeln('publish: version $current → $version (--bump $bump)');
    } else if (version == null) {
      if (current is! String || Semver.tryParse(current) == null) {
        throw const _Exit(
          64,
          'publish: pass --version <x.y.z> or --bump patch|minor|major '
          '(manifest.json has no semver "version").',
        );
      }
      version = current;
    }
  }

  void _resolveArtifacts() {
    executionMode = (author['execution_mode'] as String?) ?? 'wasm';
    if (executionMode != 'wasm' && executionMode != 'sidecar') {
      throw _Exit(
        65,
        'publish: unknown execution_mode "$executionMode" (wasm or sidecar).',
      );
    }
    uiType = (author['ui_type'] as String?) ?? 'declarative';
    final webview = uiType == 'webview';
    final hasUiUrl = author['ui_bundle_url'] != null;
    final uploadUi = release && (uiBundlePath != null || webview);
    if (!uploadUi && author['ui_bundle_hash_sha256'] != null && !hasUiUrl) {
      throw const _Exit(
        65,
        'publish: manifest.json has ui_bundle_hash_sha256 but no '
        'ui_bundle_url; they come together.',
      );
    }
    if (uploadUi || hasUiUrl) {
      ui = _Artifact('UI bundle', 'ui_bundle_url', 'ui_bundle_hash_sha256');
    }
    if (webview && !uploadUi && !hasUiUrl) {
      throw const _Exit(
        65,
        'publish: ui_type "webview" needs a UI bundle: set ui_bundle_url to '
        'the released ui.zip, or pass --release to upload ./ui.zip.',
      );
    }
    if (!release) {
      final url = author['wasm_url'];
      if (url is! String || url.isEmpty) {
        throw const _Exit(
          65,
          'publish: manifest.json has no wasm_url. Point it at the released '
          'file, or pass --release to create a GitHub Release from the '
          'local file.',
        );
      }
      wasm.url = url;
    } else {
      wasm.localPath = _resolveLocal(
        wasmPath,
        executionMode == 'sidecar' ? 'plugin.py' : 'plugin.wasm',
      );
    }
    final u = ui;
    if (u != null) {
      if (uploadUi) {
        u.localPath = _resolveLocal(uiBundlePath, 'ui.zip');
      } else {
        u.url = author['ui_bundle_url'] as String;
      }
    }
  }

  String _resolveLocal(String? given, String fallback) {
    if (given == null) return p.join(pluginDir, fallback);
    return p.isAbsolute(given)
        ? given
        : p.join(workingDirectory ?? Directory.current.path, given);
  }

  List<_Artifact> get _artifacts => [wasm, if (ui != null) ui!];
  List<_Artifact> get _releasedArtifacts =>
      _artifacts.where((a) => a.localPath != null).toList();

  // ── The registry's current manifest ──────────────────────────────────────

  /// Reads plugins/<id>/manifest.json from the registry's main branch over
  /// https (a read, so dry runs do it too). A 404 means a new plugin. The
  /// clone made by --submit re-reads it from git and wins.
  Future<void> _readUpstreamFromRegistry() async {
    final url = '$_registryRawBase/plugins/$id/manifest.json';
    try {
      final bytes = await downloadHttps(url, httpGet, maxBytes: 1024 * 1024);
      _setUpstream(utf8.decode(bytes));
    } on DownloadException catch (ex) {
      if (ex.statusCode == 404) {
        _setUpstream(null);
      } else {
        e.writeln(
          'publish: warning — could not read the registry\'s current '
          'manifest ($ex); treating $id as a new plugin for this plan.',
        );
      }
    } on FormatException catch (ex) {
      throw _Exit(
        69,
        'publish: the registry\'s $registryPath is not JSON: $ex',
      );
    }
  }

  void _setUpstream(String? raw) {
    upstreamKnown = true;
    upstreamRaw = raw;
    upstream = raw == null ? null : jsonDecode(raw) as Map<String, dynamic>;
  }

  bool get _upstreamHasRealHash {
    final h = upstream?['content_hash_sha256'];
    return h is String && h != kPlaceholderHash;
  }

  /// A version below the registry's can never merge (CI: version must
  /// strictly increase). The same version is only a re-run of an identical
  /// publish, decided once the hashes are known ([_alreadyPublished]).
  void _versionPrecheck() {
    final up = Semver.tryParse(upstream?['version'] as String?);
    if (up == null || !_upstreamHasRealHash) return;
    if (Semver.tryParse(version)!.compareTo(up) < 0) {
      throw _Exit(
        65,
        'publish: version $version is below the registry\'s $up for $id; '
        'the registry only accepts a higher version. Use --bump or a '
        'higher --version.',
      );
    }
  }

  // ── Preconditions ────────────────────────────────────────────────────────

  Future<void> _requireGh(String why) async {
    if (!await _hasExecutable('gh')) {
      throw _Exit(
        69,
        'publish: $why requires the GitHub CLI (gh).\n'
        '  Install from https://cli.github.com and run `gh auth login` first.',
      );
    }
  }

  Future<String> _ghLogin() async {
    if (_login != null) return _login!;
    final r = await run('gh', ['api', 'user', '--jq', '.login']);
    _check(r, 'publish: failed to resolve the authenticated GitHub user.');
    final login = '${r.stdout}'.trim();
    if (login.isEmpty) {
      throw const _Exit(
        69,
        'publish: GitHub CLI returned an empty authenticated user.',
      );
    }
    return _login = login;
  }

  /// `--first-party` is for HelloHQ maintainers. Registry CI and review are
  /// the real gate; this only stops accidental use.
  Future<void> _checkFirstParty() async {
    await _requireGh('--first-party');
    final login = await _ghLogin();
    final r = await run('gh', [
      'api',
      'orgs/$kFirstPartyOrg/members/$login',
      '--silent',
    ]);
    if (r.exitCode != 0) {
      throw _Exit(
        77,
        'publish: --first-party is for HelloHQ maintainers, and $login is '
        'not a member of the $kFirstPartyOrg GitHub organization.\n'
        '  (A private membership needs a gh token with read:org.)',
      );
    }
    o.writeln('publish: --first-party: $login is a $kFirstPartyOrg member');
  }

  Future<void> _requireSubmitTools() async {
    await _requireGh('--submit');
    if (!await _hasExecutable('node')) {
      throw const _Exit(
        69,
        'publish: --submit requires Node.js (node) to run the registry\'s '
        'own checks (build-index, check-pr-signatures, verify-artifacts).\n'
        '  Install it from https://nodejs.org and rerun.',
      );
    }
  }

  // ── --release ────────────────────────────────────────────────────────────

  static final _repoPattern = RegExp(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$');
  static final _assetNamePattern = RegExp(r'^[A-Za-z0-9_.-]+$');

  String _releaseUrl(String basename) =>
      'https://github.com/$repo/releases/download/'
      '${Uri.encodeComponent(tag!)}/$basename';

  Future<void> _prepareRelease() async {
    await _requireGh('--release');
    repo ??= await _inferRepo();
    if (!_repoPattern.hasMatch(repo!)) {
      throw _Exit(64, 'publish: --repo must be owner/name (got "$repo").');
    }
    tag ??= 'v$version';

    final names = <String>{};
    for (final a in _releasedArtifacts) {
      final file = File(a.localPath!);
      if (!file.existsSync()) {
        throw _Exit(
          66,
          'publish: ${a.label} not found at "${a.localPath}".\n'
          '  Run `hqplugin build` first, or pass '
          '${a == wasm ? '--wasm' : '--ui-bundle'} <path>.',
        );
      }
      final bytes = file.readAsBytesSync();
      final problem = a == wasm
          ? pluginFileProblem(bytes, executionMode)
          : artifactHashProblem(bytes);
      if (problem != null) {
        throw _Exit(65, 'publish: ${a.localPath} $problem.');
      }
      if (bytes.length > maxArtifactBytes) {
        throw _Exit(
          65,
          'publish: ${a.localPath} is larger than $maxArtifactBytes bytes '
          '(the registry limit).',
        );
      }
      final name = p.basename(a.localPath!);
      if (!_assetNamePattern.hasMatch(name)) {
        throw _Exit(
          65,
          'publish: release asset name "$name" may only use letters, digits, '
          '".", "_" and "-"; rename the file.',
        );
      }
      if (!names.add(name)) {
        throw _Exit(
          65,
          'publish: two release files are both named "$name"; rename one.',
        );
      }
      a
        ..localBytes = bytes
        ..url = _releaseUrl(name);
    }

    final status = await run('git', [
      'status',
      '--porcelain',
    ], workingDirectory: pluginDir);
    _check(
      status,
      'publish: --release needs the plugin in a git repository ($pluginDir).',
    );
    if ('${status.stdout}'.trim().isNotEmpty) {
      const msg =
          'the git tree has uncommitted changes, so the release would '
          'not match a commit. Commit or stash them, or pass --allow-dirty.';
      if (mutate && !allowDirty) throw const _Exit(65, 'publish: $msg');
      if (!allowDirty)
        e.writeln('publish: warning — --submit will refuse: $msg');
    }
    final head = await run('git', [
      'rev-parse',
      'HEAD',
    ], workingDirectory: pluginDir);
    _check(head, 'publish: could not read the HEAD commit.');
    headSha = '${head.stdout}'.trim();

    final view = await run('gh', [
      'release',
      'view',
      tag!,
      '--repo',
      repo!,
      '--json',
      'isDraft,assets',
    ]);
    if (view.exitCode != 0) {
      if (!'${view.stderr}'.toLowerCase().contains('not found')) {
        _check(view, 'publish: could not look up release $tag on $repo.');
      }
      releaseExists = false;
      final up = Semver.tryParse(upstream?['version'] as String?);
      if (up != null &&
          _upstreamHasRealHash &&
          Semver.tryParse(version)!.compareTo(up) == 0) {
        throw _Exit(
          65,
          'publish: the registry already has $id $version, so a new '
          'release of the same version cannot be published. Bump the '
          'version (--bump patch).',
        );
      }
      return;
    }
    releaseExists = true;
    final info = jsonDecode('${view.stdout}') as Map<String, dynamic>;
    if (info['isDraft'] == true) {
      throw _Exit(
        65,
        'publish: release $tag on $repo is a draft, so its files are not '
        'publicly downloadable. Publish or delete the draft, then rerun.',
      );
    }
    final assets = {
      for (final a in (info['assets'] as List? ?? const []))
        (a as Map)['name'] as String,
    };
    for (final a in _releasedArtifacts) {
      final name = p.basename(a.localPath!);
      if (!assets.contains(name)) {
        throw _Exit(
          65,
          'publish: release $tag on $repo already exists without "$name". '
          'Releases are immutable (the registry pins their bytes), so '
          'publish never adds or replaces assets. Bump the version for a '
          'new release.',
        );
      }
    }
    o.writeln(
      'publish: release $tag already exists on $repo; checking its '
      'assets match the local files',
    );
  }

  Future<String> _inferRepo() async {
    final r = await run('git', [
      'remote',
      'get-url',
      'origin',
    ], workingDirectory: pluginDir);
    if (r.exitCode != 0) {
      throw const _Exit(
        64,
        'publish: could not infer the plugin repo from `git remote get-url '
        'origin`; pass --repo owner/name.',
      );
    }
    final inferred = parseGitHubRepo('${r.stdout}'.trim());
    if (inferred == null) {
      throw _Exit(
        64,
        'publish: origin (${'${r.stdout}'.trim()}) is not a GitHub repo; '
        'pass --repo owner/name.',
      );
    }
    return inferred;
  }

  Future<void> _createRelease() async {
    final files = _releasedArtifacts.map((a) => a.localPath!).toList();
    final notes = StringBuffer(
      'Released by `hqplugin publish` for $id $version.\n\nSHA-256:\n',
    );
    for (final a in _releasedArtifacts) {
      notes.writeln(
        '- ${p.basename(a.localPath!)}: '
        '${sha256Hex(a.localBytes!)}',
      );
    }
    o.writeln('publish: creating release $tag on $repo at $headSha ...');
    final r = await run('gh', [
      'release',
      'create',
      tag!,
      ...files,
      '--repo',
      repo!,
      '--target',
      headSha!,
      '--title',
      '$id v$version',
      '--notes',
      notes.toString(),
    ]);
    _check(
      r,
      'publish: failed to create release $tag on $repo. (Is HEAD $headSha '
      'pushed to $repo?)',
    );
  }

  // ── Download + verify ────────────────────────────────────────────────────

  Future<void> _downloadArtifacts() async {
    for (final a in _artifacts) {
      if (a.provisional) continue;
      final justCreated = release && !releaseExists && a.localPath != null;
      final bytes = await _download(a, retries: justCreated);
      if (a.localBytes != null && !_sameBytes(bytes, a.localBytes!)) {
        throw _Exit(
          65,
          releaseExists && !justCreated
              ? 'publish: release $tag on $repo already has a different '
                    '"${p.basename(a.localPath!)}" than ${a.localPath}. '
                    'Releases are immutable (the registry pins their bytes); '
                    'bump the version for a new release.'
              : 'publish: ${a.url} serves different bytes than the uploaded '
                    '${a.localPath}; refusing to pin it.',
        );
      }
      final problem = a == wasm
          ? pluginFileProblem(bytes, executionMode)
          : artifactHashProblem(bytes);
      if (problem != null) {
        throw _Exit(65, 'publish: ${a.urlKey} ${a.url} $problem.');
      }
      a.served = bytes;
      final pinned = author[a.hashKey];
      if (!release &&
          pinned is String &&
          pinned != kPlaceholderHash &&
          pinned != a.hash) {
        e.writeln(
          'publish: warning — manifest.json pins ${a.hashKey} '
          '$pinned, but ${a.url} serves ${a.hash}. The registry copy pins '
          'the served bytes.',
        );
      }
    }
  }

  Future<Uint8List> _download(_Artifact a, {required bool retries}) async {
    final attempts = retries ? _releaseDownloadAttempts : 1;
    for (var attempt = 1; ; attempt++) {
      try {
        return await downloadHttps(a.url!, httpGet, maxBytes: maxArtifactBytes);
      } on DownloadException catch (ex) {
        if (attempt < attempts && ex.statusCode == 404) {
          await Future<void>.delayed(retryDelay);
          continue;
        }
        throw _Exit(ex.refused ? 65 : 69, 'publish: ${a.label}: $ex');
      }
    }
  }

  static bool _sameBytes(List<int> a, List<int> b) {
    if (a.length != b.length) return false;
    for (var i = 0; i < a.length; i++) {
      if (a[i] != b[i]) return false;
    }
    return true;
  }

  // ── The registry manifest ────────────────────────────────────────────────

  void _composeRegistry() {
    registry = buildRegistryManifest(
      author: author,
      upstream: upstream,
      version: version!,
      wasmUrl: wasm.url!,
      contentHash: wasm.hash,
      uiBundleUrl: ui?.url,
      uiBundleHash: ui?.hash,
      firstParty: firstParty,
    );
    final problems = registryManifestProblems(registry);
    if (problems.isNotEmpty) {
      throw _Exit(
        65,
        'publish: the registry manifest is not publishable:\n  - ${problems.join('\n  - ')}',
      );
    }
  }

  /// True (and says so) when the registry already holds exactly this manifest:
  /// a re-run of a finished publish is a no-op. Throws when the version is not
  /// higher than the registry's but the content differs.
  bool _alreadyPublished() {
    final up = upstream;
    if (up == null) return false;
    if (sameRegistryContent(registry, up)) {
      o.writeln(
        'publish: $id $version is already in the registry with these '
        'exact hashes and URLs; nothing to publish.',
      );
      _printArtifacts();
      return true;
    }
    final upVersion = Semver.tryParse(up['version'] as String?);
    if (upVersion != null &&
        _upstreamHasRealHash &&
        Semver.tryParse(version)!.compareTo(upVersion) <= 0) {
      throw _Exit(
        65,
        'publish: the registry has $id $upVersion; a change must use a '
        'higher version than that (got $version). Use --bump or --version.',
      );
    }
    return false;
  }

  // ── Output ───────────────────────────────────────────────────────────────

  String get _upstreamVersion => '${upstream?['version']}';

  void _printArtifacts() {
    for (final a in _artifacts) {
      final how = a.provisional
          ? 'hash of the local file; re-checked by download once the release '
                'exists'
          : 'downloaded';
      o.writeln('publish: ${a.urlKey} ${a.url}');
      o.writeln(
        'publish:   sha256 ${a.hash} '
        '(${a.pinnedBytes.length} bytes, $how)',
      );
    }
  }

  void _printPlan() {
    o.writeln(
      'publish: $id $version — '
      '${upstream == null ? (upstreamKnown ? 'new plugin' : 'registry state unknown') : 'update of $_upstreamVersion'}',
    );
    if (release) {
      final state = releaseExists
          ? 'exists; its assets match the local files and are reused'
          : (mutate
                ? 'created at $headSha'
                : 'would be created at $headSha with '
                      '${_releasedArtifacts.map((a) => p.basename(a.localPath!)).join(', ')}');
      o.writeln('publish: release $tag on $repo: $state');
    }
    _printArtifacts();
    if (author['min_host_version'] == null) {
      e.writeln(
        'publish: warning — manifest.json has no "min_host_version"; '
        'the registry requires it and CI will reject the PR. Add e.g. '
        '"min_host_version": "1.0.0".',
      );
    }
    final json = encodeManifest(registry);
    o
      ..writeln()
      ..writeln('── Registry manifest ($registryPath) ──')
      ..write(json);
    if (upstream != null) {
      o
        ..writeln()
        ..writeln('── Changes against the registry\'s main ──');
      for (final line in lineDiff(encodeManifest(upstream!), json)) {
        o.writeln(line);
      }
    }
    o
      ..writeln()
      ..writeln('── Registry PR (branch $branch) ──')
      ..writeln(_prTitle)
      ..writeln()
      ..write(_prBody);
  }

  String get _prTitle => registryPrTitle(
    id: id,
    version: version!,
    upstreamVersion: upstream == null ? null : _upstreamVersion,
  );

  String get _prBody => registryPrBody(
    manifest: registry,
    upstream: upstream,
    artifactSizes: {for (final a in _artifacts) a.urlKey: a.pinnedBytes.length},
    firstParty: firstParty,
  );

  // ── --submit: the registry PR ────────────────────────────────────────────

  Future<int> _submitToRegistry() async {
    final login = await _ghLogin();
    final forkRepo = '$login/plugin-registry';
    final forkView = await run('gh', [
      'repo',
      'view',
      forkRepo,
      '--json',
      'parent',
      '--jq',
      '.parent.nameWithOwner',
    ]);
    if (forkView.exitCode == 0) {
      if ('${forkView.stdout}'.trim() != kRegistryRepo) {
        throw _Exit(
          65,
          'publish: $forkRepo exists but is not a fork of $kRegistryRepo.',
        );
      }
    } else {
      o.writeln('publish: creating fork $forkRepo ...');
      _check(
        await run('gh', ['repo', 'fork', kRegistryRepo, '--clone=false']),
        'publish: failed to create the plugin-registry fork.',
      );
    }

    final tmp = await Directory.systemTemp.createTemp('hqplugin-registry-');
    try {
      final clone = p.join(tmp.path, 'registry');
      o.writeln('publish: cloning $forkRepo ...');
      _check(
        await run('gh', ['repo', 'clone', forkRepo, clone]),
        'publish: failed to clone the plugin-registry fork.',
      );

      Future<ProcessResult> git(List<String> args) =>
          run('git', args, workingDirectory: clone);

      // `gh repo clone` of a fork usually adds `upstream` itself.
      if ((await git([
            'remote',
            'add',
            'upstream',
            _registryGitUrl,
          ])).exitCode !=
          0) {
        _check(
          await git(['remote', 'set-url', 'upstream', _registryGitUrl]),
          'publish: failed to configure the upstream registry remote.',
        );
      }
      _check(
        await git(['fetch', 'upstream', 'main']),
        'publish: failed to fetch the upstream registry.',
      );

      // The clone is the authority on the registry's current manifest.
      final show = await git(['show', 'upstream/main:$registryPath']);
      final freshRaw = show.exitCode == 0 ? '${show.stdout}' : null;
      final fresh = freshRaw == null
          ? null
          : jsonDecode(freshRaw) as Map<String, dynamic>;
      final changed =
          !upstreamKnown ||
          (fresh == null) != (upstream == null) ||
          (fresh != null && !sameRegistryContent(fresh, upstream!));
      _setUpstream(freshRaw);
      if (changed) {
        o.writeln(
          'publish: the registry changed since the plan; rebuilding '
          'the registry manifest.',
        );
        _versionPrecheck();
        _composeRegistry();
        if (_alreadyPublished()) return 0;
      }

      _check(
        await git(['checkout', '-B', branch, 'upstream/main']),
        'publish: failed to create branch $branch.',
      );

      final dest = File(p.join(clone, 'plugins', id, 'manifest.json'));
      dest.parent.createSync(recursive: true);
      dest.writeAsStringSync(encodeManifest(registry));
      var baseArg = '';
      if (freshRaw != null) {
        final base = File(p.join(tmp.path, 'base-manifest.json'))
          ..writeAsStringSync(freshRaw);
        baseArg = base.path;
      }

      // The registry's own checks, from the clone, before anything is pushed.
      o.writeln('publish: node scripts/build-index.mjs');
      _check(
        await run('node', ['scripts/build-index.mjs'], workingDirectory: clone),
        'publish: the registry\'s build-index.mjs failed.',
      );

      final sig = await run('node', [
        'scripts/check-pr-signatures.mjs',
        baseArg,
        registryPath,
      ], workingDirectory: clone);
      if (sig.exitCode != 0) {
        throw _Exit(
          65,
          'publish: check-pr-signatures.mjs refused the manifest: '
          '${'${sig.stdout}${sig.stderr}'.trim()}',
        );
      }

      o.writeln('publish: node scripts/verify-artifacts.mjs $registryPath');
      final verify = await run('node', [
        'scripts/verify-artifacts.mjs',
        registryPath,
      ], workingDirectory: clone);
      _handleVerifyArtifacts(verify);

      _check(
        await git(['add', registryPath, 'index.json']),
        'publish: git add failed.',
      );
      _check(
        await git(['commit', '-m', _prTitle]),
        'publish: git commit failed.',
      );
      _check(
        await git(['push', '--force', '-u', 'origin', branch]),
        'publish: failed to push $branch to $forkRepo.',
      );

      final existing = await _openPrFor(login);
      if (existing != null) {
        o
          ..writeln(
            'publish: updated the open PR (its branch was '
            'force-updated)',
          )
          ..writeln(existing);
        return 0;
      }
      final pr = await run('gh', [
        'pr',
        'create',
        '--repo',
        kRegistryRepo,
        '--head',
        '$login:$branch',
        '--base',
        'main',
        '--title',
        _prTitle,
        '--body',
        _prBody,
      ]);
      _check(pr, 'publish: failed to open the registry pull request.');
      o.writeln('publish: PR opened');
      o.write(pr.stdout);
      return 0;
    } finally {
      await tmp.delete(recursive: true).catchError((_) => tmp);
    }
  }

  /// verify-artifacts.mjs failures abort before the push, except a missing
  /// `wasm-tools`: that check is CI's, so it is only a warning here.
  void _handleVerifyArtifacts(ProcessResult r) {
    final output = '${r.stdout}';
    for (final line in const LineSplitter().convert(output)) {
      if (!line.startsWith('::')) o.writeln(line);
    }
    if (r.exitCode == 0) return;
    final errors = [
      for (final line in const LineSplitter().convert(output))
        if (line.startsWith('::error'))
          line.replaceFirst(RegExp(r'^::error[^:]*::'), ''),
    ];
    final wasmToolsMissing = errors
        .where((m) => m.contains('could not run wasm-tools'))
        .toList();
    if (errors.isNotEmpty && wasmToolsMissing.length == errors.length) {
      e.writeln(
        'publish: warning — wasm-tools is not on PATH, so the '
        'WebAssembly validation was skipped here. Registry CI runs it; '
        'install wasm-tools to check before pushing.',
      );
      return;
    }
    throw _Exit(
      65,
      'publish: verify-artifacts.mjs failed; nothing was pushed.\n'
      '${errors.isEmpty ? '${r.stdout}${r.stderr}'.trim() : errors.map((m) => '  - $m').join('\n')}',
    );
  }

  Future<String?> _openPrFor(String login) async {
    final r = await run('gh', [
      'pr',
      'list',
      '--repo',
      kRegistryRepo,
      '--head',
      branch,
      '--state',
      'open',
      '--json',
      'url,headRepositoryOwner',
    ]);
    if (r.exitCode != 0) return null;
    try {
      for (final pr in jsonDecode('${r.stdout}') as List) {
        final owner = ((pr as Map)['headRepositoryOwner'] as Map?)?['login'];
        if (owner == login) return pr['url'] as String?;
      }
    } on FormatException {
      return null;
    }
    return null;
  }

  // ── Process helpers ──────────────────────────────────────────────────────

  Future<bool> _hasExecutable(String name) async {
    try {
      return (await run(name, ['--version'])).exitCode == 0;
    } catch (_) {
      return false;
    }
  }

  /// Throws an [_Exit] carrying the command's stderr and exit code (69 when
  /// it somehow exited 0) unless [r] succeeded.
  void _check(ProcessResult r, String message) {
    if (r.exitCode == 0) return;
    final err = '${r.stderr}';
    throw _Exit(
      r.exitCode,
      '${err.isEmpty ? '' : (err.endsWith('\n') ? err : '$err\n')}$message',
    );
  }
}

/// `owner/name` of a GitHub remote URL (https, ssh or scp-like), or null.
String? parseGitHubRepo(String remote) {
  final m = RegExp(
    r'^(?:https://(?:[^@/]+@)?github\.com/|ssh://git@github\.com/|git@github\.com:)'
    r'([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$',
  ).firstMatch(remote);
  return m == null ? null : '${m.group(1)}/${m.group(2)}';
}

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

Future<ProcessResult> _runCommand(
  String executable,
  List<String> arguments, {
  String? workingDirectory,
}) {
  return Process.run(executable, arguments, workingDirectory: workingDirectory);
}
