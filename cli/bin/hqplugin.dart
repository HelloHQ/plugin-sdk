// hqplugin — HelloHQ plugin CLI.
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
      ..addMultiOption('grant', help: 'Permission id to grant (repeatable).')
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
      return runSidecarTest(
        sidecarPath: sidecar,
        grants: (argResults?['grant'] as List<String>?) ?? const [],
        fixturePath: argResults?['fixture'] as String?,
        aiResponses: (argResults?['ai-response'] as List<String>?) ?? const [],
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
  final description = 'Open a registry PR for a tagged release.';

  _PublishCommand() {
    argParser
      ..addOption('version', help: 'Semver to publish (required, e.g. 0.2.0).')
      ..addOption('wasm',
          help: 'Path to plugin.wasm to hash.',
          defaultsTo: 'plugin.wasm')
      ..addFlag('submit',
          help: 'Open the PR automatically via the `gh` CLI.',
          negatable: false);
  }

  @override
  Future<int> run() async {
    return runPublish(
      version: argResults?['version'] as String?,
      wasmPath: argResults?['wasm'] as String?,
      submit: argResults?['submit'] as bool? ?? false,
    );
  }
}
