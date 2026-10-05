import 'dart:io';

import 'package:hqplugin/src/test_cmd.dart';
import 'package:test/test.dart';

/// A working `python3`/`python` of at least [minor] (3.x), or null.
Future<String?> _findPython({int minor = 11}) async {
  for (final c in ['python3', 'python']) {
    try {
      final r = await Process.run(c, [
        '-c',
        'import sys; sys.exit(0 if sys.version_info >= (3, $minor) else 1)',
      ]);
      if (r.exitCode == 0) return c;
    } catch (_) {
      // try next
    }
  }
  return null;
}

// A raw-protocol plugin (no SDK import): ready -> RPC(id) -> one `propose`
// host call -> result(id) carrying the host's whole reply.
String _rawPlugin(String proposalsPython) =>
    '''
import sys, json, datetime

def send(o):
    sys.stdout.write(json.dumps(o) + "\\n")
    sys.stdout.flush()

def stamp(delta=0):
    t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=delta)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")

def valuation(**over):
    p = {
        "kind": "valuation",
        "source_key": "uk-lr:uprn:100023336956",
        "value": {"amount": "412500", "currency": "GBP"},
        "as_of": stamp(),
        "method": "comparable_sales_median",
        "source": {
            "origin": "landregistry.data.gov.uk",
            "reference": "price-paid: 14 sales",
            "fetched_at": stamp(),
        },
    }
    p.update(over)
    return p

send({"type": "ready", "protocol_version": "0.1.0"})
while True:
    line = sys.stdin.readline()
    if not line:
        break
    msg = json.loads(line)
    if msg.get("type") == "shutdown":
        break
    rid = msg.get("id")
    if rid is None:
        continue
    send({"type": "propose", "seq": 5,
          "batch": {"schema": "hellohq.proposal-batch@1", "proposals": $proposalsPython}})
    reply = json.loads(sys.stdin.readline())
    send({"id": rid, "result": reply})
''';

void main() {
  late Directory tmp;
  late String? python;

  setUp(() async {
    tmp = Directory.systemTemp.createTempSync('hqplugin-propose-test-');
    python = await _findPython();
  });
  tearDown(() {
    if (tmp.existsSync()) tmp.deleteSync(recursive: true);
  });

  Future<(int, String, String)> run(
    String pluginSource, {
    List<String> grants = const [],
  }) async {
    File('${tmp.path}/plugin.py').writeAsStringSync(pluginSource);
    final out = StringBuffer();
    final err = StringBuffer();
    final code = await runSidecarTest(
      sidecarPath: tmp.path,
      grants: grants,
      out_: out,
      err_: err,
    );
    return (code, out.toString(), err.toString());
  }

  test('a valid proposal is queued and shown, not saved', () async {
    if (python == null) {
      markTestSkipped('python >= 3.11 not available');
      return;
    }
    final (code, out, err) = await run(
      _rawPlugin('[valuation()]'),
      grants: const ['propose:valuations=home'],
    );
    expect(code, 0, reason: err);
    expect(out, contains('"type": "propose_response"'));
    expect(out, contains('"outcome": "queued"'));
    expect(out, contains('Proposals queued (mock host, not saved)'));
    expect(out, contains('valuation uk-lr:uprn:100023336956  412500 GBP'));
  }, timeout: const Timeout(Duration(seconds: 60)));

  test(
    'an invalid proposal gets its reason code from the closed set',
    () async {
      if (python == null) {
        markTestSkipped('python >= 3.11 not available');
        return;
      }
      final (code, out, err) = await run(
        _rawPlugin('[valuation(as_of="2026-09-30")]'),
        grants: const ['propose:valuations=home'],
      );
      expect(code, 0, reason: err);
      expect(out, contains('"outcome": "invalid"'));
      expect(out, contains('"reason": "bad_as_of"'));
      expect(out, isNot(contains('Proposals queued')));
    },
    timeout: const Timeout(Duration(seconds: 60)),
  );

  test('without the permission the host answers permission_denied', () async {
    if (python == null) {
      markTestSkipped('python >= 3.11 not available');
      return;
    }
    final (code, out, err) = await run(_rawPlugin('[valuation()]'));
    expect(code, 0, reason: err);
    expect(out, contains('"error_code": "permission_denied"'));
    expect(out, isNot(contains('"receipts"')));
  }, timeout: const Timeout(Duration(seconds: 60)));

  test(
    'a propose grant without scope.kinds warns and is not a grant',
    () async {
      if (python == null) {
        markTestSkipped('python >= 3.11 not available');
        return;
      }
      final (code, out, err) = await run(
        _rawPlugin('[valuation()]'),
        grants: const ['propose:valuations'],
      );
      expect(code, 0, reason: err);
      expect(err, contains('propose:valuations has no scope.kinds'));
      expect(out, contains('"error_code": "permission_denied"'));
    },
    timeout: const Timeout(Duration(seconds: 60)),
  );

  test('the Python SDK host.propose works against the mock host', () async {
    if (python == null) {
      markTestSkipped('python >= 3.11 not available');
      return;
    }
    final (code, out, err) = await run(
      '''
from datetime import datetime, timezone
from hellohq_plugin_sdk import host, serve, Money, Source, Valuation, ProposePermissionDenied

def dispatch(function, args):
    now = datetime.now(timezone.utc)
    batch = [
        Valuation(
            source_key="uk-lr:uprn:100023336956",
            value=Money("412500", "GBP"),
            as_of=now,
            method="comparable_sales_median",
            source=Source("landregistry.data.gov.uk", "14 sales", now),
        )
    ] * 2
    try:
        receipts = host.propose(batch)
    except ProposePermissionDenied:
        return {"status": "permission_denied"}
    return {"status": "submitted", "receipts": [list(r) for r in receipts]}

serve(dispatch)
''',
      grants: const ['propose:valuations=home'],
    );
    expect(code, 0, reason: err);
    // The same proposal twice in one batch: queued, then duplicate.
    expect(out, contains('"status": "submitted"'));
    expect(out, contains('queued'));
    expect(out, contains('duplicate'));
  }, timeout: const Timeout(Duration(seconds: 60)));
}
