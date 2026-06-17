import 'dart:io';

import 'package:hqplugin/src/test_cmd.dart';
import 'package:test/test.dart';

/// Returns a working `python3`/`python` 3.x executable, or null if none.
Future<String?> _findPython() async {
  for (final c in ['python3', 'python']) {
    try {
      final r = await Process.run(c, ['--version']);
      if (r.exitCode == 0 &&
          ('${r.stdout}${r.stderr}').contains('Python 3')) {
        return c;
      }
    } catch (_) {
      // try next
    }
  }
  return null;
}

void main() {
  late Directory tmp;

  setUp(() {
    tmp = Directory.systemTemp.createTempSync('hqplugin-sidecar-test-');
  });
  tearDown(() {
    if (tmp.existsSync()) tmp.deleteSync(recursive: true);
  });

  // A raw-protocol Python plugin (no SDK import) so the test verifies the CLI
  // driver against the exact wire format `serve()` speaks: ready → RPC(id) →
  // result(id), with a mid-dispatch ai_complete host call.
  File _writeRawPlugin(String body) {
    final f = File('${tmp.path}/plugin.py');
    f.writeAsStringSync(body);
    return f;
  }

  test('runSidecarTest drives the id/result protocol and answers ai_complete',
      () async {
    final python = await _findPython();
    if (python == null) {
      markTestSkipped('python3 not available');
      return;
    }

    _writeRawPlugin('''
import sys, json

def send(o):
    sys.stdout.write(json.dumps(o) + "\\n")
    sys.stdout.flush()

def readline():
    return sys.stdin.readline()

send({"type": "ready", "protocol_version": "0.1.0"})
while True:
    line = readline()
    if not line:
        break
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    if msg.get("type") == "shutdown":
        break
    rid = msg.get("id")
    if rid is None:
        continue
    # Mid-dispatch host call: ask the (mock) host AI backend.
    send({"type": "ai_complete", "seq": 1,
          "messages": [{"role": "user", "content": "hi"}],
          "opts": {"max_tokens": 8}})
    resp = json.loads(readline())
    send({"id": rid, "result": {"kind": "text", "value": resp.get("content", "")}})
''');

    final out = StringBuffer();
    final err = StringBuffer();
    final code = await runSidecarTest(
      sidecarPath: tmp.path,
      grants: const ['ai:inference'],
      aiResponses: const ['hello from mock'],
      out_: out,
      err_: err,
    );

    expect(code, 0, reason: 'stderr:\n$err');
    expect(out.toString(), contains('hello from mock'));
  }, timeout: const Timeout(Duration(seconds: 60)));

  test('runSidecarTest surfaces a structured plugin error', () async {
    final python = await _findPython();
    if (python == null) {
      markTestSkipped('python3 not available');
      return;
    }

    _writeRawPlugin('''
import sys, json

def send(o):
    sys.stdout.write(json.dumps(o) + "\\n")
    sys.stdout.flush()

send({"type": "ready", "protocol_version": "0.1.0"})
while True:
    line = sys.stdin.readline()
    if not line:
        break
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    if msg.get("type") == "shutdown":
        break
    rid = msg.get("id")
    if rid is None:
        continue
    send({"id": rid, "error": {"code": "execution_failed", "message": "boom"}})
''');

    final out = StringBuffer();
    final err = StringBuffer();
    final code = await runSidecarTest(
      sidecarPath: tmp.path,
      out_: out,
      err_: err,
    );

    expect(code, 70);
    expect(err.toString(), contains('boom'));
    expect(err.toString(), contains('execution_failed'));
  }, timeout: const Timeout(Duration(seconds: 60)));
}
