// hqplugin — HelloHQ plugin CLI.
//
// Status: command surface scaffold (Phase 4 — begin). Each command parses and
// prints its plan; the build/test/publish implementations land incrementally.
import 'dart:io';

import 'package:args/command_runner.dart';

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
    stdout.writeln('build: lang=${argResults?['lang']} '
        'entry=${argResults?['entry']} out=${argResults?['out']}');
    stderr.writeln('hqplugin build is not yet implemented (Phase 4).');
    return 2;
  }
}

class _TestCommand extends Command<int> {
  @override
  final name = 'test';
  @override
  final description = 'Run a plugin against the mock host with fixture data.';

  _TestCommand() {
    argParser
      ..addOption('wasm')
      ..addOption('sidecar')
      ..addOption('bundle', help: 'WebView ui.zip to load.');
  }

  @override
  Future<int> run() async {
    stdout.writeln('test: wasm=${argResults?['wasm']} '
        'sidecar=${argResults?['sidecar']} bundle=${argResults?['bundle']}');
    stderr.writeln('hqplugin test is not yet implemented (Phase 4).');
    return 2;
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
