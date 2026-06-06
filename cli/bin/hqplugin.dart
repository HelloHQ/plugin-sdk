// hqplugin — HelloHQ plugin CLI.
//
// `build` is implemented (Rust → wasm); `test`/`publish` land incrementally.
import 'dart:io';

import 'package:args/command_runner.dart';
import 'package:hqplugin/src/build.dart';
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
      ..addOption('out', defaultsTo: 'plugin.wasm');
  }

  @override
  Future<int> run() async {
    return runBuild(
      lang: argResults?['lang'] as String?,
      entry: argResults?['entry'] as String?,
      out: argResults?['out'] as String? ?? 'plugin.wasm',
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
      ..addMultiOption('grant', help: 'Permission id to grant (repeatable).')
      ..addOption('fixture', help: 'JSON fixture of portfolios/currencies.')
      ..addOption('input',
          help: 'Run input JSON.', defaultsTo: '{"function":"main","args":{}}')
      ..addOption('sidecar')
      ..addOption('bundle', help: 'WebView ui.zip to load.');
  }

  @override
  Future<int> run() async {
    if (argResults?['sidecar'] != null || argResults?['bundle'] != null) {
      stderr.writeln('test: only --wasm (Tier-2) is supported today.');
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
    argParser.addOption('version', help: 'Semver to publish.');
  }

  @override
  Future<int> run() async {
    stdout.writeln('publish: version=${argResults?['version']}');
    stderr.writeln('hqplugin publish is not yet implemented (Phase 4).');
    return 2;
  }
}
