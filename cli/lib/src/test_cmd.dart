import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:hellohq_plugin_mock_host/mock_host.dart';
import 'package:hqplugin/src/wasm/wasm_runner.dart';

/// A small built-in fixture so `hqplugin test --wasm x --grant read:...` works
/// without a `--fixture` file.
MockHost _demoHost(List<String> grants) => MockHost(
  granted: grants,
  portfolios: const [
    MockPortfolio(
      'ptf_personal',
      'Personal',
      sheets: [
        MockSheet('sh1', 'Investments', 'asset', sections: [
          MockSection('sec1', 'Brokerage', items: [
            MockItem('it1', 'Index Fund'),
            MockItem('it2', 'Tech Stocks'),
          ]),
        ]),
      ],
      totals: {'usd': 125000.0},
    ),
    MockPortfolio('ptf_business', 'Business', totals: {'usd': 48000.0}),
  ],
  currencies: const [
    MockCurrency('usd', 'US Dollar', r'$', 1),
    MockCurrency('eur', 'Euro', '€', 2),
  ],
);

/// Run a Tier-2 plugin against the mock host and print its declarative output.
Future<int> runTest({
  String? wasmPath,
  List<String> grants = const [],
  String? fixturePath,
  String input = '{"function":"main","args":{}}',
  StringSink? out_,
  StringSink? err_,
}) async {
  final o = out_ ?? stdout;
  final e = err_ ?? stderr;

  if (wasmPath == null) {
    e.writeln('test: --wasm is required (Tier-2 plugin).');
    return 64;
  }
  final file = File(wasmPath);
  if (!file.existsSync()) {
    e.writeln('test: no such file: $wasmPath');
    return 66;
  }
  if (!WasmRunner.isRuntimeAvailable) {
    e.writeln(
      'test: the Wasm runtime is unavailable. Run scripts/fetch-wasmtime-libs.sh '
      'in the plugin-sdk repo, or set HQPLUGIN_WASMTIME_LIB.',
    );
    return 69;
  }

  final MockHost host;
  try {
    host = fixturePath == null
        ? _demoHost(grants)
        : _hostFromFixture(fixturePath, grants);
  } on FormatException catch (ex) {
    e.writeln('test: bad --fixture: ${ex.message}');
    return 65;
  }

  o.writeln('test: ${file.path}  grants=${grants.isEmpty ? "(none)" : grants.join(",")}');
  final runner = WasmRunner(resolver: host.resolve, onEvent: host.emit);
  try {
    runner.load(file.readAsBytesSync());
    final outputJson = runner.runBytes(input);

    final Object? decoded;
    try {
      decoded = jsonDecode(outputJson);
    } on FormatException {
      e.writeln('test: plugin returned non-JSON output:\n$outputJson');
      return 70;
    }
    o.writeln('\n── Declarative output ──');
    o.writeln(const JsonEncoder.withIndent('  ').convert(decoded));
    if (host.emittedEvents.isNotEmpty) {
      o.writeln('\n── Emitted events ──');
      for (final ev in host.emittedEvents) {
        o.writeln('  ${ev.name}: ${ev.payload}');
      }
    }
    return 0;
  } on WasmRunError catch (ex) {
    e.writeln('test: plugin failed to run — ${ex.message}');
    return 70;
  } finally {
    runner.dispose();
  }
}

/// Parse a `--fixture` JSON file into a [MockHost]. Schema:
/// ```json
/// {
///   "portfolios": [
///     {"id": "ptf_a", "name": "Alpha", "totals": {"usd": 1000},
///      "sheets": [{"id":"s","name":"S","type":"asset",
///                  "sections":[{"id":"sec","name":"Sec",
///                               "items":[{"id":"it","name":"Cash"}]}]}]}
///   ],
///   "currencies": [{"id":"usd","name":"US Dollar","symbol":"$","rate":1}]
/// }
/// ```
MockHost _hostFromFixture(String path, List<String> grants) {
  final raw = jsonDecode(File(path).readAsStringSync());
  if (raw is! Map<String, dynamic>) {
    throw const FormatException('top level must be an object');
  }
  final portfolios = [
    for (final pf in (raw['portfolios'] as List? ?? const []))
      _portfolio(pf as Map<String, dynamic>),
  ];
  final currencies = [
    for (final c in (raw['currencies'] as List? ?? const []))
      MockCurrency(
        c['id'] as String,
        (c['name'] ?? '') as String,
        (c['symbol'] ?? '') as String,
        (c['rate'] ?? 0) as num,
      ),
  ];
  return MockHost(granted: grants, portfolios: portfolios, currencies: currencies);
}

MockPortfolio _portfolio(Map<String, dynamic> p) => MockPortfolio(
  p['id'] as String,
  (p['name'] ?? '') as String,
  totals: {
    for (final e in (p['totals'] as Map<String, dynamic>? ?? const {}).entries)
      e.key: (e.value as num).toDouble(),
  },
  sheets: [
    for (final s in (p['sheets'] as List? ?? const []))
      MockSheet(
        s['id'] as String,
        (s['name'] ?? '') as String,
        (s['type'] ?? 'asset') as String,
        sections: [
          for (final sec in (s['sections'] as List? ?? const []))
            MockSection(
              sec['id'] as String,
              (sec['name'] ?? '') as String,
              items: [
                for (final it in (sec['items'] as List? ?? const []))
                  MockItem(it['id'] as String, (it['name'] ?? '') as String),
              ],
            ),
        ],
      ),
  ],
);

// ─────────────────────────────────────────────────────────────────────────────
// Tier 1 sidecar runner
// ─────────────────────────────────────────────────────────────────────────────

/// Run a Tier-1 (Python) sidecar plugin against the [MockSidecarHost].
///
/// [sidecarPath] is the path to the plugin's Python entry file (e.g.
/// `plugin.py`) or to the directory that contains it.  The runner locates
/// a `python3` or `python` executable, launches the sidecar, exchanges
/// lifecycle messages, dispatches a `run` call, and prints the result.
///
/// [aiResponses] provides canned AI replies (cycled; the last entry repeats).
/// [storageFixture] pre-seeds the in-memory storage store.
Future<int> runSidecarTest({
  required String sidecarPath,
  List<String> grants = const [],
  String? fixturePath,
  String function = 'run',
  Map<String, dynamic> args = const {},
  List<String> aiResponses = const [],
  Map<String, String> storageFixture = const {},
  StringSink? out_,
  StringSink? err_,
}) async {
  final o = out_ ?? stdout;
  final e = err_ ?? stderr;

  // ── Locate the plugin entry file ──────────────────────────────────────────
  final pluginFile = _resolveSidecarEntry(sidecarPath);
  if (pluginFile == null) {
    e.writeln('test: cannot find plugin entry file at: $sidecarPath');
    e.writeln('      Expected plugin.py (or __main__.py) in the given path.');
    return 66;
  }

  // ── Locate Python interpreter ─────────────────────────────────────────────
  final python = await _findPython();
  if (python == null) {
    e.writeln('test: python3 (or python) not found on PATH.');
    e.writeln('      Install Python 3.9+ and ensure it is on your PATH.');
    return 69;
  }

  // ── Build the mock host ───────────────────────────────────────────────────
  final mockAi = aiResponses.isNotEmpty ? cannedResponses(aiResponses) : null;
  final host = MockSidecarHost(
    granted: grants,
    onAiComplete: mockAi,
    storage: Map<String, String>.of(storageFixture),
  );

  o.writeln(
    'test(sidecar): ${pluginFile.path}  '
    'grants=${grants.isEmpty ? "(none)" : grants.join(",")}',
  );

  // ── Spawn the sidecar process ─────────────────────────────────────────────
  final process = await Process.start(
    python,
    [pluginFile.path],
    workingDirectory: pluginFile.parent.path,
    environment: {
      ...Platform.environment,
      // Ensure unbuffered output so NDJSON lines arrive immediately.
      'PYTHONUNBUFFERED': '1',
    },
  );

  // Collect stderr asynchronously — print it at the end.
  final stderrBuf = StringBuffer();
  process.stderr
      .transform(const Utf8Decoder(allowMalformed: true))
      .listen(stderrBuf.write);

  final stdinSink = process.stdin;
  int exitValue = 0;

  try {
    exitValue = await _runProtocol(
      process: process,
      stdinSink: stdinSink,
      host: host,
      function: function,
      args: args,
      o: o,
      e: e,
    );
  } finally {
    await stdinSink.flush().catchError((_) {});
    await stdinSink.close().catchError((_) {});
    await process.exitCode.timeout(
      const Duration(seconds: 5),
      onTimeout: () {
        process.kill();
        return -1;
      },
    );
    if (stderrBuf.isNotEmpty) {
      e.writeln('\n── Plugin stderr ──');
      e.write(stderrBuf.toString());
    }
    // Print final storage state if non-empty.
    if (host.storage.isNotEmpty) {
      o.writeln('\n── Storage (after run) ──');
      for (final entry in host.storage.entries) {
        o.writeln('  ${entry.key} = ${entry.value}');
      }
    }
  }

  return exitValue;
}

/// Drive one request/response lifecycle over the sidecar stdio pipes.
Future<int> _runProtocol({
  required Process process,
  required IOSink stdinSink,
  required MockSidecarHost host,
  required String function,
  required Map<String, dynamic> args,
  required StringSink o,
  required StringSink e,
}) async {
  // The seq for our top-level call.
  const callSeq = 1;

  final lines = process.stdout
      .transform(const Utf8Decoder(allowMalformed: true))
      .transform(const LineSplitter());

  // Completer that resolves when we get the result/error for callSeq.
  final resultCompleter = Completer<Map<String, dynamic>>();

  // We process stdout line by line.  Host calls from the plugin are answered
  // inline; lifecycle messages are handled per their type.
  bool ready = false;

  final sub = lines.listen(
    (line) {
      if (line.isEmpty) return;

      Map<String, dynamic> msg;
      try {
        msg = jsonDecode(line) as Map<String, dynamic>;
      } catch (_) {
        // Plugin emitted non-JSON (e.g. a print() debug line). Ignore.
        return;
      }

      final type = msg['type'] as String?;

      // ── Lifecycle ────────────────────────────────────────────────────────
      if (type == 'ready') {
        ready = true;
        // Send our call.
        stdinSink.writeln(jsonEncode({
          'type': 'call',
          'seq': callSeq,
          'function': function,
          'args': args,
        }));
        return;
      }

      if (type == 'result' && msg['seq'] == callSeq) {
        resultCompleter.complete(msg);
        return;
      }

      if (type == 'error' && msg['seq'] == callSeq) {
        resultCompleter.complete(msg);
        return;
      }

      if (type == 'event') {
        // Plugin emitted an event — capture it, answer nothing.
        return;
      }

      // ── Synchronous host calls ───────────────────────────────────────────
      final response = host.handleLine(line);
      if (response != null) {
        stdinSink.writeln(response);
      }
    },
    onDone: () {
      if (!resultCompleter.isCompleted) {
        resultCompleter.completeError(
          StateError('sidecar exited without returning a result'),
        );
      }
    },
    onError: (Object err) {
      if (!resultCompleter.isCompleted) {
        resultCompleter.completeError(err);
      }
    },
    cancelOnError: false,
  );

  try {
    // Wait for result with a timeout.
    final result = await resultCompleter.future.timeout(
      const Duration(seconds: 30),
      onTimeout: () => throw TimeoutException('plugin timed out after 30 s'),
    );

    // Send shutdown.
    stdinSink.writeln(jsonEncode({'type': 'shutdown'}));

    if (result['type'] == 'error') {
      e.writeln('\n── Plugin error ──');
      e.writeln('  code: ${result['error_code'] ?? 'unknown'}');
      e.writeln('  message: ${result['error'] ?? result['message'] ?? '(no message)'}');
      return 70;
    }

    // Success.
    final data = result['data'];
    o.writeln('\n── Declarative output ──');
    o.writeln(const JsonEncoder.withIndent('  ').convert(data));
    return 0;
  } on TimeoutException catch (ex) {
    e.writeln('test: ${ex.message}');
    process.kill();
    return 70;
  } on StateError catch (ex) {
    if (!ready) {
      e.writeln('test: sidecar exited before sending "ready" — check stderr above.');
    } else {
      e.writeln('test: ${ex.message}');
    }
    return 70;
  } finally {
    await sub.cancel();
  }
}

/// Find the Python entry file for [path].
///
/// Accepts a `.py` file directly or a directory containing `plugin.py` or
/// `__main__.py`.
File? _resolveSidecarEntry(String path) {
  final f = File(path);
  if (f.existsSync() && path.endsWith('.py')) return f;

  final dir = Directory(path);
  if (dir.existsSync()) {
    for (final name in ['plugin.py', '__main__.py']) {
      final candidate = File('${dir.path}/$name');
      if (candidate.existsSync()) return candidate;
    }
  }
  return null;
}

/// Return the path to a Python 3 interpreter, or null if none is on PATH.
Future<String?> _findPython() async {
  for (final candidate in ['python3', 'python']) {
    try {
      final result = await Process.run(candidate, ['--version']);
      if (result.exitCode == 0) {
        final version = (result.stdout as String).trim().isEmpty
            ? (result.stderr as String).trim()
            : (result.stdout as String).trim();
        // Require Python 3.
        if (version.contains('Python 3')) return candidate;
      }
    } catch (_) {
      // Not on PATH — try next.
    }
  }
  return null;
}
