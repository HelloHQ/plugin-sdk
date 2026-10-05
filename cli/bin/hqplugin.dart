// hqplugin — HelloHQ plugin CLI.
import 'dart:convert';
import 'dart:io';

import 'package:args/command_runner.dart';
import 'package:hqplugin/src/build.dart';
import 'package:hqplugin/src/publish.dart';
import 'package:hqplugin/src/test_cmd.dart';

Future<void> main(List<String> args) async {
  final runner =
      CommandRunner<int>('hqplugin', 'Build, test, and publish HelloHQ plugins.')
        ..addCommand(_BuildCommand())
        ..addCommand(_TestCommand())
        ..addCommand(_PublishCommand());
  try {
    exitCode = await runner.run(args) ?? 0;
  } on UsageException catch (e) {
    stderr.writeln(e);
    exitCode = 64;
  }
}

class _BuildCommand extends Command<int> {
  @override
  final name = 'build';
  @override
  final description = 'Compile a plugin to a .wasm (or package a Python sidecar).';

  _BuildCommand() {
    argParser
      ..addOption('lang', allowed: ['rust', 'go', 'typescript', 'python'])
      ..addOption('entry', help: 'Entry source file.')
      ..addOption('out', defaultsTo: 'plugin.wasm')
      ..addFlag('inference',
          negatable: false,
          help: 'Build the streaming-inference variant (async `run`). For '
              '--lang go this uses the wasi-on-idle Go fork + preview1 adapter '
              '(\$HQ_GO_WASI_ON_IDLE / --go, \$HQ_PLUGIN_WIT / --wit, '
              '\$HQ_WASI_ADAPTER / --adapter).')
      ..addOption('wit',
          help: 'WIT dir defining the inference world (go --inference).')
      ..addOption('adapter',
          help: 'preview1 reactor adapter path (go --inference).')
      ..addOption('go', help: 'Path to a wasi-on-idle Go (go --inference).');
  }

  @override
  Future<int> run() async {
    return runBuild(
      lang: argResults?['lang'] as String?,
      entry: argResults?['entry'] as String?,
      out: argResults?['out'] as String? ?? 'plugin.wasm',
      inference: argResults?['inference'] as bool? ?? false,
      wit: argResults?['wit'] as String?,
      adapter: argResults?['adapter'] as String?,
      goBin: argResults?['go'] as String?,
    );
  }
}

class _TestCommand extends Command<int> {
  @override
  final name = 'test';
  @override
  final description = 'Run a plugin against the mock host with fixture data.';

  _TestCommand() {
    argParser
      ..addOption('wasm', help: 'Tier-2 plugin .wasm to run.')
      ..addOption('sidecar',
          help: 'Tier-1 Python plugin file or directory to run.')
      ..addMultiOption('grant',
          help: 'Permission id to grant (repeatable). A propose permission '
              'needs its kinds: propose:holdings=crypto_ticker,home.')
      ..addOption('fixture', help: 'JSON fixture of portfolios/currencies.')
      ..addOption('input',
          help: 'Run input JSON.', defaultsTo: '{"function":"main","args":{}}')
      ..addMultiOption('ai-response',
          help: 'Canned AI reply string (repeatable, cycles on exhaustion).')
      ..addOption('bundle', help: 'WebView ui.zip to load (not yet supported).');
  }

  @override
  Future<int> run() async {
    final sidecar = argResults?['sidecar'] as String?;
    if (sidecar != null) {
      // The host calls the sidecar's `run` with {"context": …, "input": …};
      // an explicit --input becomes that `input` (the default sends none).
      Object? input;
      if (argResults!.wasParsed('input')) {
        try {
          input = jsonDecode(argResults!['input'] as String);
        } on FormatException catch (ex) {
          stderr.writeln('test: bad --input: ${ex.message}');
          return 65;
        }
      }
      return runSidecarTest(
        sidecarPath: sidecar,
        grants: (argResults?['grant'] as List<String>?) ?? const [],
        fixturePath: argResults?['fixture'] as String?,
        aiResponses: (argResults?['ai-response'] as List<String>?) ?? const [],
        args: input == null ? const {} : {'context': {}, 'input': input},
      );
    }
    if (argResults?['bundle'] != null) {
      stderr.writeln('test: --bundle (WebView) is not yet supported.');
      return 2;
    }
    return runTest(
      wasmPath: argResults?['wasm'] as String?,
      grants: (argResults?['grant'] as List<String>?) ?? const [],
      fixturePath: argResults?['fixture'] as String?,
      input: argResults?['input'] as String? ?? '{"function":"main","args":{}}',
    );
  }
}

class _PublishCommand extends Command<int> {
  @override
  final name = 'publish';
  @override
  final description =
      'Pin a released plugin in the HelloHQ registry and open its PR.';

  @override
  String get invocation => 'hqplugin publish [--release] [--submit] [options]';

  _PublishCommand() {
    argParser
      ..addOption(
        'version',
        help: 'Version to publish (X.Y.Z). Default: manifest.json\'s version.',
      )
      ..addOption(
        'bump',
        allowed: ['patch', 'minor', 'major'],
        help: 'Bump manifest.json\'s version. Excludes --version.',
      )
      ..addFlag(
        'release',
        negatable: false,
        help:
            'Create a GitHub Release on the plugin repo from the local '
            'files, then pin what the release serves. Without it, the '
            'files manifest.json\'s wasm_url / ui_bundle_url serve are '
            'pinned.',
      )
      ..addOption(
        'repo',
        help:
            '--release: the plugin repo (owner/name). Default: inferred '
            'from `git remote get-url origin`.',
      )
      ..addOption('tag', help: '--release: release tag. Default: v<version>.')
      ..addOption(
        'wasm',
        help:
            '--release: plugin file to upload. Default: ./plugin.wasm '
            '(./plugin.py for a sidecar).',
      )
      ..addOption(
        'ui-bundle',
        help:
            '--release: UI bundle to upload. Default: ./ui.zip when '
            'ui_type is webview.',
      )
      ..addOption(
        'icon',
        help:
            '--release: sidebar icon SVG to upload. Default: ./icon.svg when '
            'manifest.json has an https sidebar_icon. With no sidebar_icon, '
            'only an explicit --icon adds one.',
      )
      ..addFlag(
        'allow-dirty',
        negatable: false,
        help: '--release: allow uncommitted changes in the plugin repo.',
      )
      ..addFlag(
        'submit',
        negatable: false,
        help:
            'Create the release (with --release) and open or update the '
            'registry PR through `gh`. Without it nothing is changed.',
      )
      ..addFlag(
        'dry-run',
        negatable: false,
        help:
            'Only print the plan (overrides --submit). Downloads and '
            'hashes the artifacts; no release, fork, push or PR.',
      )
      ..addFlag(
        'first-party',
        negatable: false,
        help:
            'HelloHQ maintainers only (checked against the HelloHQ org): '
            'allow provenance core and keep manifest.json\'s trust_tier.',
      );
  }

  @override
  Future<int> run() async {
    final a = argResults!;
    return runPublish(
      version: a['version'] as String?,
      bump: a['bump'] as String?,
      release: a['release'] as bool,
      repo: a['repo'] as String?,
      tag: a['tag'] as String?,
      wasmPath: a['wasm'] as String?,
      uiBundlePath: a['ui-bundle'] as String?,
      iconPath: a['icon'] as String?,
      allowDirty: a['allow-dirty'] as bool,
      submit: a['submit'] as bool,
      dryRun: a['dry-run'] as bool,
      firstParty: a['first-party'] as bool,
    );
  }
}
