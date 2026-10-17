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
// `--sign-key <dir>` (or $HQPLUGIN_AUTHOR_KEY_DIR) is for an author who holds a
// hybrid key enrolled in the registry (`hqplugin keys generate`, then an
// authors/<slug>.json PR). Publish then also writes
// `plugins/<id>/author-signatures.json` beside the manifest: Ed25519 and
// ML-DSA-65 signatures over the artifact statement (author_signing.dart), made
// with OpenSSL 3.5+. The statement includes `trust_tier`, and the registry team
// sets `verified` at merge, so the statement is built from the registry
// manifest AS IT WILL BE with `trust_tier` forced to `verified`; the manifest in
// the PR itself keeps `trust_tier` unset. Before anything is pushed both
// signatures are re-verified with the key's public half, and the registry's own
// `scripts/author-signatures.mjs check` runs on a scratch copy of the clone
// whose manifest says `verified`. Private keys are only ever paths passed to
// openssl; they are never read, printed or put in the PR. `--first-party` is a
// different path (signed by the HelloHQ signer) and excludes `--sign-key`.
//
// Every external command (gh, git, node) goes through one injectable
// [PublishCommandRunner] and every download through one [PublishHttpGet], so
// the tests are hermetic.
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:path/path.dart' as p;

import 'author_signing.dart';
import 'publish_fetch.dart';
import 'publish_icon.dart';
import 'publish_manifest.dart';

export 'publish_fetch.dart';
export 'publish_icon.dart';
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

/// The owner of [kRegistryRepo]; the head owner of a PR pushed there directly.
final String _registryOwner = kRegistryRepo.split('/').first;

/// A plugin id as the registry schema allows it (manifest.schema.json `id`).
final RegExp kPluginIdPattern = RegExp(
  r'^[a-z][a-z0-9]*(\.[a-z0-9][a-z0-9-]*)+$',
);

/// Whether [branch] is a branch publish may (force-)push: exactly
/// `publish/<plugin id>/<semver>`. Every push goes through this check, so
/// publish can never force-push `main` or any other branch, even with push
/// access to the registry itself.
bool isPublishBranch(String branch) {
  final parts = branch.split('/');
  return parts.length == 3 &&
      parts[0] == 'publish' &&
      kPluginIdPattern.hasMatch(parts[1]) &&
      Semver.tryParse(parts[2]) != null &&
      !branch.contains('..');
}

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
/// With [signKeyDir] (default: `$HQPLUGIN_AUTHOR_KEY_DIR` in [environment]) the
/// PR also carries the author's signatures (see the file header). [opensslPath]
/// (default: `$HQPLUGIN_OPENSSL`, then `openssl`) names the OpenSSL 3.5+ binary.
///
/// Exit codes follow sysexits: 64 usage, 65 bad data (including a signing key
/// the registry does not know), 66 missing input, 69 a required tool or
/// service unavailable (including OpenSSL older than 3.5 and a registry that
/// does not support author signatures yet), 70 an internal refusal (a push of
/// a branch that is not `publish/<id>/<version>`, a signature that does not
/// verify), 77 not permitted; a failing external command returns its own exit
/// code.
Future<int> runPublish({
  String? version,
  String? bump,
  String? wasmPath,
  String? uiBundlePath,
  String? iconPath,
  bool release = false,
  String? repo,
  String? tag,
  bool allowDirty = false,
  bool submit = false,
  bool dryRun = false,
  bool firstParty = false,
  String? signKeyDir,
  String? opensslPath,
  Map<String, String>? environment,
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
    iconPath: iconPath,
    release: release,
    repo: repo,
    tag: tag,
    allowDirty: allowDirty,
    submit: submit,
    dryRun: dryRun,
    firstParty: firstParty,
    signKeyDirFlag: signKeyDir,
    opensslPath: opensslPath,
    environment: environment ?? Platform.environment,
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

/// One artifact the manifest pins: the plugin file, the UI bundle or the
/// sidebar icon.
class _Artifact {
  _Artifact(
    this.label,
    this.urlKey,
    this.hashKey, {
    required this.flag,
    required this.maxBytes,
    required this.problem,
  });

  final String label;
  final String urlKey;
  final String hashKey;

  /// The `--release` flag that names the local file.
  final String flag;

  /// The registry's size limit for this artifact.
  final int maxBytes;

  /// Why some bytes cannot be pinned as this artifact, or null.
  final String? Function(List<int> bytes) problem;

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
    required this.iconPath,
    required this.release,
    required this.repo,
    required this.tag,
    required this.allowDirty,
    required this.submit,
    required this.dryRun,
    required this.firstParty,
    required this.signKeyDirFlag,
    required this.opensslPath,
    required this.environment,
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
  final String? iconPath;
  final bool release;
  String? repo;
  String? tag;
  final bool allowDirty;
  final bool submit;
  final bool dryRun;
  final bool firstParty;
  final String? signKeyDirFlag;
  final String? opensslPath;
  final Map<String, String> environment;
  final StringSink o;
  final StringSink e;
  final PublishCommandRunner run;
  final PublishHttpGet httpGet;
  final String? workingDirectory;
  final int maxArtifactBytes;
  final Duration retryDelay;

  bool get mutate => submit && !dryRun;

  /// The author key directory: `--sign-key`, else `$HQPLUGIN_AUTHOR_KEY_DIR`
  /// (not for `--first-party`, which HelloHQ signs), made absolute. Null: this
  /// publish carries no author signatures.
  String? get signKeyDir {
    var dir = signKeyDirFlag;
    if (dir == null || dir.isEmpty) {
      final fromEnv = environment['HQPLUGIN_AUTHOR_KEY_DIR'];
      if (firstParty || fromEnv == null || fromEnv.isEmpty) return null;
      dir = fromEnv;
    }
    return p.isAbsolute(dir)
        ? dir
        : p.join(workingDirectory ?? Directory.current.path, dir);
  }

  /// The signer, once [_requireAuthorSigning] opened the keys.
  HybridSigner? _signer;

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

  late final _Artifact wasm = _Artifact(
    'plugin file',
    'wasm_url',
    'content_hash_sha256',
    flag: '--wasm',
    maxBytes: maxArtifactBytes,
    problem: (b) => pluginFileProblem(b, executionMode),
  );
  _Artifact? ui;
  _Artifact? icon;

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
    if (signKeyDir != null) await _requireAuthorSigning();
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
    if (_alreadyPublished()) {
      if (signKeyDir != null) {
        o.writeln(
          'publish: nothing was signed: the registry already has this '
          'version.',
        );
      }
      return 0;
    }
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
    if (firstParty && signKeyDirFlag != null && signKeyDirFlag!.isNotEmpty) {
      throw const _Exit(
        64,
        'publish: --sign-key cannot be combined with --first-party: '
        'first-party plugins are signed by the HelloHQ signer, not by an '
        'author key. Drop one of the two flags.',
      );
    }
    if (firstParty && (environment['HQPLUGIN_AUTHOR_KEY_DIR'] ?? '') != '') {
      o.writeln(
        'publish: ignoring \$HQPLUGIN_AUTHOR_KEY_DIR: --first-party plugins '
        'are signed by the HelloHQ signer.',
      );
    }
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
        if (iconPath != null) '--icon',
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
    if (!kPluginIdPattern.hasMatch(rawId) || rawId.length > 128) {
      throw _Exit(
        65,
        'publish: manifest.json id "$rawId" is not a registry plugin id '
        '(reverse-domain, lowercase: e.g. com.example.my-plugin).',
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
      ui = _Artifact(
        'UI bundle',
        'ui_bundle_url',
        'ui_bundle_hash_sha256',
        flag: '--ui-bundle',
        maxBytes: maxArtifactBytes,
        problem: artifactHashProblem,
      );
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
    _resolveIcon();
  }

  /// The sidebar icon (rules: registry verify-artifacts.mjs, publish_icon.dart).
  ///
  /// - An https `sidebar_icon` is downloaded and pinned. With `--release` the
  ///   local icon (`--icon`, default `./icon.svg` beside manifest.json) is
  ///   uploaded instead and `sidebar_icon` repointed at the new release; with
  ///   no local icon the existing URL is pinned as served.
  /// - With no `sidebar_icon`, an icon is added only by an explicit `--icon`
  ///   (never silently from a stray ./icon.svg).
  /// - A relative `sidebar_icon` is a path inside the UI bundle: no hash.
  /// - Any other scheme is refused.
  void _resolveIcon() {
    final raw = author['sidebar_icon'];
    if (raw != null && raw is! String) {
      throw const _Exit(65, 'publish: sidebar_icon must be a string.');
    }
    final value = raw as String?;
    final staleHash = author['sidebar_icon_hash_sha256'] != null;
    if (value != null && isAbsoluteIconUrl(value)) {
      if (!value.startsWith('https://')) {
        throw _Exit(
          65,
          'publish: sidebar_icon must be an https URL (or a path inside the UI '
          'bundle); got "$value".',
        );
      }
    } else if (value != null) {
      if (iconPath != null) {
        throw _Exit(
          64,
          'publish: --icon uploads an icon for an https sidebar_icon, but '
          'manifest.json\'s sidebar_icon "$value" is a path inside the UI '
          'bundle. Drop --icon, or remove sidebar_icon to publish the icon '
          'as a release asset.',
        );
      }
      if (staleHash) {
        e.writeln(
          'publish: warning — dropping sidebar_icon_hash_sha256: a bundle-path '
          'icon is pinned by the UI bundle, not by its own hash.',
        );
      }
      if (uiType != 'webview') {
        e.writeln(
          'publish: warning — sidebar_icon "$value" is a path inside the UI '
          'bundle, but ui_type is "$uiType", so the app shows a generic icon. '
          'Use an https URL (or --release with an icon.svg).',
        );
      }
      return;
    } else {
      if (staleHash) {
        e.writeln(
          'publish: warning — dropping sidebar_icon_hash_sha256: '
          'manifest.json has no sidebar_icon.',
        );
      }
      if (iconPath == null) {
        if (release && File(p.join(pluginDir, 'icon.svg')).existsSync()) {
          o.writeln(
            'publish: note — ./icon.svg is not uploaded: manifest.json has no '
            'sidebar_icon. Pass --icon icon.svg to publish it as the icon.',
          );
        }
        return;
      }
    }
    final a = icon = _Artifact(
      'sidebar icon',
      'sidebar_icon',
      'sidebar_icon_hash_sha256',
      flag: '--icon',
      maxBytes: kMaxIconBytes,
      problem: (b) => artifactHashProblem(b) ?? svgIconProblem(b),
    );
    final local = _resolveLocal(iconPath, 'icon.svg');
    if (release && (iconPath != null || File(local).existsSync())) {
      a.localPath = local;
    } else {
      a.url = value;
    }
  }

  String _resolveLocal(String? given, String fallback) {
    if (given == null) return p.join(pluginDir, fallback);
    return p.isAbsolute(given)
        ? given
        : p.join(workingDirectory ?? Directory.current.path, given);
  }

  List<_Artifact> get _artifacts => [
    wasm,
    if (ui != null) ui!,
    if (icon != null) icon!,
  ];
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

  // ── Author signing ───────────────────────────────────────────────────────

  /// Everything an author-signed publish needs, checked before the first
  /// mutation so no release is created for a publish that cannot be signed:
  /// the plugin is not Official, the registry understands author signatures,
  /// and the keys work (OpenSSL 3.5+, right key types, probe sign + verify).
  Future<void> _requireAuthorSigning() async {
    _refuseOfficialUpstream();
    await _requireRegistrySupport();
    final tool = OpenSslTool(
      run: run,
      executable: resolveOpenSslExecutable(opensslPath, environment),
    );
    _signer = await _signing(() => OpenSslHybridSigner.open(tool, signKeyDir!));
    o.writeln(
      'publish: author signing with the keys in $signKeyDir '
      '(${tool.version})',
    );
  }

  /// Official plugins are signed by the HelloHQ signer, never by an author key.
  void _refuseOfficialUpstream() {
    if (upstream?['trust_tier'] == 'official') {
      throw _Exit(
        65,
        'publish: the registry lists $id as an Official plugin, which the '
        'HelloHQ signer signs; an author-held key cannot sign it. Drop '
        '--sign-key.',
      );
    }
  }

  static const String _noAuthorSignatures =
      'publish: the registry does not support author signatures yet '
      '(it has no scripts/author-signatures.mjs), so a PR with '
      'author-signatures.json would be rejected. Nothing was released, '
      'pushed or opened; publish without --sign-key, or wait until the '
      'registry supports them.';

  Future<void> _requireRegistrySupport() async {
    final url = '$_registryRawBase/scripts/author-signatures.mjs';
    try {
      await downloadHttps(url, httpGet, maxBytes: 1024 * 1024);
    } on DownloadException catch (ex) {
      if (ex.statusCode == 404) throw const _Exit(69, _noAuthorSignatures);
      e.writeln(
        'publish: warning — could not check that the registry supports '
        'author signatures ($ex); the clone is checked again before the push.',
      );
    }
  }

  /// Runs [body], turning a [SignerException] into the matching [_Exit].
  Future<T> _signing<T>(Future<T> Function() body) async {
    try {
      return await body();
    } on SignerException catch (ex) {
      throw _Exit(ex.code, 'publish: ${ex.message}');
    }
  }

  /// The statement an author signs: the registry manifest as it will be once
  /// the registry team has set `trust_tier: verified` (the PR itself leaves
  /// the tier unset).
  Uint8List get _authorStatement {
    try {
      return artifactStatement(verifiedTierView(registry));
    } on StatementException catch (ex) {
      throw _Exit(65, 'publish: $ex');
    }
  }

  String get _sidecarPath => 'plugins/$id/author-signatures.json';

  /// Signs the statement, writes [sidecar] (replacing any earlier one) and
  /// re-verifies exactly what was written with the keys' public halves.
  Future<void> _signIntoClone(File sidecar) async {
    final signer = _signer!;
    final statement = _authorStatement;
    final replaced = sidecar.existsSync();
    final sigs = await _signing(() => signer.sign(statement));
    sidecar.writeAsStringSync(sigs.toSidecarJson());
    final AuthorSignatures written;
    try {
      written = AuthorSignatures.parseSidecar(sidecar.readAsStringSync());
    } on FormatException catch (ex) {
      throw _Exit(70, 'publish: wrote an unusable $_sidecarPath: $ex');
    }
    if (!await _signing(() => signer.verify(statement, written))) {
      throw _Exit(
        70,
        'publish: the author signatures do not verify under the key\'s '
        'public half; nothing was pushed.',
      );
    }
    o.writeln(
      'publish: signed statement ${sha256Hex(statement)} '
      '(ed25519 + ml-dsa-65, verified locally); ${replaced ? 'replaced' : 'wrote'} '
      '$_sidecarPath',
    );
  }

  /// The registry's own `author-signatures.mjs check` on a scratch copy of the
  /// clone whose manifest says `trust_tier: verified` (the tier the statement
  /// covers and the registry sets at merge). The PR's real manifest stays
  /// untouched. Only the plugin being published and the enrolled authors are
  /// in the copy, so other plugins cannot add noise.
  Future<void> _registryAuthorCheck(String clone, String tmpPath) async {
    final scratch = Directory(p.join(tmpPath, 'author-check'));
    final plugin = Directory(p.join(scratch.path, 'plugins', id))
      ..createSync(recursive: true);
    final authors = Directory(p.join(clone, 'authors'));
    if (authors.existsSync()) {
      final dest = Directory(p.join(scratch.path, 'authors'))..createSync();
      for (final f in authors.listSync().whereType<File>()) {
        if (f.path.endsWith('.json')) {
          f.copySync(p.join(dest.path, p.basename(f.path)));
        }
      }
    }
    File(
      p.join(plugin.path, 'manifest.json'),
    ).writeAsStringSync(encodeManifest(verifiedTierView(registry)));
    File(
      p.join(clone, 'plugins', id, 'author-signatures.json'),
    ).copySync(p.join(plugin.path, 'author-signatures.json'));

    o.writeln(
      'publish: node scripts/author-signatures.mjs check (trust_tier '
      'verified, as the registry sets it)',
    );
    final r = await run('node', [
      'scripts/author-signatures.mjs',
      'check',
      scratch.path,
    ], workingDirectory: clone);
    final text = '${r.stdout}${r.stderr}'.trim();
    if (r.exitCode == 0) {
      for (final line in const LineSplitter().convert(text)) {
        o.writeln('publish:   $line');
      }
      return;
    }
    final indented = const LineSplitter()
        .convert(text)
        .map((l) => '  $l')
        .join('\n');
    final notEnrolled = RegExp(r'matched 0\b').hasMatch(text);
    throw _Exit(
      65,
      'publish: the registry\'s author-signatures check refused the '
      'signatures; nothing was pushed:\n$indented'
      '${notEnrolled ? '\n  This signing key is not an enrolled author key '
                'whose id_prefixes cover $id. Enroll the public keys '
                '(`hqplugin keys generate` writes public-keys.json) with a '
                'reviewed PR to the registry\'s authors/; see authors/README.md '
                'in https://github.com/$kRegistryRepo.' : ''}',
    );
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
          '  Run `hqplugin build` first, or pass ${a.flag} <path>.',
        );
      }
      final bytes = file.readAsBytesSync();
      if (bytes.length > a.maxBytes) {
        throw _Exit(
          65,
          'publish: ${a.localPath} is larger than ${a.maxBytes} bytes '
          '(the registry limit).',
        );
      }
      final problem = a.problem(bytes);
      if (problem != null) {
        throw _Exit(65, 'publish: ${a.localPath} $problem.');
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

    final i = icon;
    final releasePrefix = 'https://github.com/$repo/releases/download/';
    if (i != null &&
        i.localPath == null &&
        i.url!.startsWith(releasePrefix) &&
        !i.url!.startsWith('$releasePrefix${Uri.encodeComponent(tag!)}/')) {
      e.writeln(
        'publish: warning — sidebar_icon still points at another release '
        '(${i.url}). Put icon.svg beside manifest.json or pass --icon <path> '
        'to publish it with $tag.',
      );
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
      final problem = a.problem(bytes);
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
        return await downloadHttps(a.url!, httpGet, maxBytes: a.maxBytes);
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
      sidebarIconUrl: icon?.url,
      sidebarIconHash: icon?.hash,
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
    if (signKeyDir != null) {
      final statement = _authorStatement;
      o
        ..writeln()
        ..writeln('── Author signatures ──')
        ..writeln(
          'publish: author signatures: ${mutate ? 'will sign' : 'would sign'} '
          'statement ${sha256Hex(statement)}',
        )
        ..writeln(
          'publish:   ed25519 + ml-dsa-65, written to $_sidecarPath in the '
          'registry PR',
        )
        ..writeln(
          'publish:   the statement names trust_tier=verified, the tier the '
          'registry sets at merge; the manifest in the PR leaves trust_tier '
          'unset',
        );
      for (final line in const LineSplitter().convert(utf8.decode(statement))) {
        o.writeln('    $line');
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
    authorStatementSha256: signKeyDir == null
        ? null
        : sha256Hex(_authorStatement),
  );

  // ── --submit: the registry PR ────────────────────────────────────────────

  /// Whether the gh user can push to [kRegistryRepo] itself (a maintainer).
  /// Any failure to tell counts as no: the fork path then applies.
  Future<bool> _canPushToRegistry() async {
    final r = await run('gh', [
      'api',
      'repos/$kRegistryRepo',
      '--jq',
      '.permissions.push',
    ]);
    if (r.exitCode != 0) {
      e.writeln(
        'publish: warning — could not read your permissions on '
        '$kRegistryRepo; using the fork path.',
      );
      return false;
    }
    return '${r.stdout}'.trim() == 'true';
  }

  /// Ensures `<login>/plugin-registry` is a fork of the registry (creating it
  /// when missing) and returns its name.
  Future<String> _ensureFork(String login) async {
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
    return forkRepo;
  }

  Future<int> _submitToRegistry() async {
    final login = await _ghLogin();
    // A maintainer pushes the branch to the registry itself, so publishing
    // never creates a personal fork as a side effect; everyone else uses
    // their fork (created on first use).
    final direct = await _canPushToRegistry();
    final String pushRepo;
    final String headOwner;
    if (direct) {
      pushRepo = kRegistryRepo;
      headOwner = _registryOwner;
      o.writeln(
        'publish: $login can push to $kRegistryRepo: pushing $branch there '
        'directly (no fork).',
      );
    } else {
      pushRepo = await _ensureFork(login);
      headOwner = login;
      o.writeln(
        'publish: $login cannot push to $kRegistryRepo: using the fork '
        '$pushRepo.',
      );
    }

    final tmp = await Directory.systemTemp.createTemp('hqplugin-registry-');
    try {
      final clone = p.join(tmp.path, 'registry');
      o.writeln('publish: cloning $pushRepo ...');
      _check(
        await run('gh', ['repo', 'clone', pushRepo, clone]),
        'publish: failed to clone $pushRepo.',
      );

      Future<ProcessResult> git(List<String> args) =>
          run('git', args, workingDirectory: clone);

      // `gh repo clone` of a fork usually adds `upstream` itself. (In the
      // direct path `upstream` is the same repo as `origin`.)
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
        if (signKeyDir != null) _refuseOfficialUpstream();
        _composeRegistry();
        if (_alreadyPublished()) return 0;
      }

      _check(
        await git(['checkout', '-B', branch, 'upstream/main']),
        'publish: failed to create branch $branch.',
      );
      if (signKeyDir != null &&
          !File(
            p.join(clone, 'scripts', 'author-signatures.mjs'),
          ).existsSync()) {
        throw const _Exit(69, _noAuthorSignatures);
      }

      final dest = File(p.join(clone, 'plugins', id, 'manifest.json'));
      dest.parent.createSync(recursive: true);
      dest.writeAsStringSync(encodeManifest(registry));
      final sidecar = File(
        p.join(clone, 'plugins', id, 'author-signatures.json'),
      );
      if (signKeyDir != null) {
        await _signIntoClone(sidecar);
      } else if (sidecar.existsSync()) {
        e.writeln(
          'publish: warning — the registry has an author-signatures.json for '
          '$id, and this version changes what it signs, so registry CI will '
          'reject the PR. Publish with --sign-key to sign it again.',
        );
      }
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

      if (signKeyDir != null) await _registryAuthorCheck(clone, tmp.path);

      _check(
        await git([
          'add',
          registryPath,
          if (signKeyDir != null) _sidecarPath,
          'index.json',
        ]),
        'publish: git add failed.',
      );
      _check(
        await git(['commit', '-m', _prTitle]),
        'publish: git commit failed.',
      );
      await _pushPublishBranch(git, pushRepo);

      final existing = await _openPrFor(headOwner);
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
        direct ? branch : '$login:$branch',
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

  /// Force-pushes [branch] (and only it) to `origin` ([pushRepo]): a re-run
  /// of the same version updates the open PR. Refuses any branch that is not
  /// `publish/<id>/<version>`, so `main` can never be force-pushed.
  Future<void> _pushPublishBranch(
    Future<ProcessResult> Function(List<String>) git,
    String pushRepo,
  ) async {
    if (!isPublishBranch(branch)) {
      throw _Exit(
        70,
        'publish: refusing to push "$branch": publish only pushes '
        'publish/<id>/<version> branches.',
      );
    }
    _check(
      await git([
        'push',
        '--force',
        '-u',
        'origin',
        '$branch:refs/heads/$branch',
      ]),
      'publish: failed to push $branch to $pushRepo.',
    );
  }

  /// The open registry PR whose head is [branch] in [headOwner]'s repo (the
  /// registry's own owner for a direct push, the user for a fork), if any.
  Future<String?> _openPrFor(String headOwner) async {
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
        if (owner == headOwner) return pr['url'] as String?;
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
