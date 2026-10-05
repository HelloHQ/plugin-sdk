// Downloading and checking the artifacts `hqplugin publish` pins.
//
// The pinned hashes are computed from the bytes the release URLs serve, never
// from a local file: what the app installs is what the URL serves, and the
// registry CI (scripts/verify-artifacts.mjs) re-downloads the same URLs. These
// rules mirror that script: https only, redirects followed, at most 64 MiB, a
// Wasm plugin must start with the `\0asm` magic number.
import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

import 'publish_manifest.dart' show kPlaceholderHash;

/// The registry's per-artifact size limit (verify-artifacts.mjs
/// MAX_ARTIFACT_BYTES).
const int kMaxArtifactBytes = 64 * 1024 * 1024;

/// Redirect hops followed before giving up (GitHub release assets take one).
const int kMaxRedirects = 10;

/// One HTTP response, redirects NOT followed.
class HttpHop {
  const HttpHop(
    this.statusCode, {
    this.location,
    this.body = const [],
    this.truncated = false,
  });

  final int statusCode;

  /// The `Location` header of a redirect.
  final String? location;

  /// The body, or its first `maxBytes + 1` bytes when [truncated].
  final List<int> body;

  /// True when the body was longer than the `maxBytes` the caller allowed and
  /// reading stopped.
  final bool truncated;
}

/// Performs ONE GET of [url] without following redirects, reading at most
/// [maxBytes] + 1 bytes of body. Injected so tests never touch the network.
typedef PublishHttpGet =
    Future<HttpHop> Function(Uri url, {required int maxBytes});

/// A download that failed; [statusCode] is the final HTTP status, if any.
class DownloadException implements Exception {
  const DownloadException(
    this.message, {
    this.statusCode,
    this.refused = false,
  });
  final String message;
  final int? statusCode;

  /// True when the URL or its content breaks a registry rule (not https, a
  /// non-https redirect, too large), as opposed to the server being
  /// unreachable or answering with an error.
  final bool refused;

  @override
  String toString() => message;
}

/// Downloads [url] over https, following up to [kMaxRedirects] redirects (each
/// hop must also be https), and refuses a body larger than [maxBytes].
Future<Uint8List> downloadHttps(
  String url,
  PublishHttpGet get, {
  int maxBytes = kMaxArtifactBytes,
}) async {
  final parsed = Uri.tryParse(url);
  if (parsed == null || parsed.scheme != 'https' || parsed.host.isEmpty) {
    throw DownloadException('not an https URL: $url', refused: true);
  }
  var current = parsed;
  for (var hop = 0; hop <= kMaxRedirects; hop++) {
    HttpHop r;
    try {
      r = await get(current, maxBytes: maxBytes);
    } on DownloadException {
      rethrow;
    } catch (e) {
      throw DownloadException('could not download $url ($e)');
    }
    if (r.statusCode >= 300 && r.statusCode < 400 && r.location != null) {
      final next = current.resolve(r.location!);
      if (next.scheme != 'https') {
        throw DownloadException(
          '$url redirects to a non-https URL ($next)',
          refused: true,
        );
      }
      current = next;
      continue;
    }
    if (r.statusCode != 200) {
      throw DownloadException(
        'HTTP ${r.statusCode} from $url',
        statusCode: r.statusCode,
      );
    }
    if (r.truncated || r.body.length > maxBytes) {
      throw DownloadException(
        '$url is larger than $maxBytes bytes (the registry limit)',
        refused: true,
      );
    }
    return Uint8List.fromList(r.body);
  }
  throw DownloadException('$url redirected more than $kMaxRedirects times');
}

/// SHA-256 of [bytes] as 64 lowercase hex characters.
String sha256Hex(List<int> bytes) => sha256.convert(bytes).toString();

const List<int> _wasmMagic = [0x00, 0x61, 0x73, 0x6d];

bool _startsWithWasmMagic(List<int> bytes) {
  if (bytes.length < 4) return false;
  for (var i = 0; i < 4; i++) {
    if (bytes[i] != _wasmMagic[i]) return false;
  }
  return true;
}

/// Why the plugin file [bytes] cannot be pinned for [executionMode], or null.
///
/// A Wasm plugin must be a WebAssembly binary (`\0asm` magic, at least the
/// 8-byte header); verify-artifacts.mjs checks the same before running
/// `wasm-tools validate`. A sidecar plugin ships Python source, so a Wasm
/// binary there is a mistake.
String? pluginFileProblem(List<int> bytes, String executionMode) {
  if (bytes.isEmpty) return 'is empty';
  if (executionMode == 'sidecar') {
    return _startsWithWasmMagic(bytes)
        ? 'is a WebAssembly binary, but execution_mode is "sidecar" '
              '(a sidecar ships its Python source)'
        : null;
  }
  if (bytes.length < 8 || !_startsWithWasmMagic(bytes)) {
    return 'is not a WebAssembly binary (missing the \\0asm magic number)';
  }
  return null;
}

/// Why [bytes] cannot be pinned at all (any artifact), or null.
String? artifactHashProblem(List<int> bytes) {
  if (bytes.isEmpty) return 'is empty';
  if (sha256Hex(bytes) == kPlaceholderHash) {
    return 'hashes to the all-zero placeholder';
  }
  return null;
}

/// The real [PublishHttpGet]: dart:io, no redirects followed, the body read
/// only up to [maxBytes] + 1 bytes.
Future<HttpHop> defaultHttpGet(Uri url, {required int maxBytes}) async {
  final client = HttpClient()
    ..connectionTimeout = const Duration(seconds: 30)
    ..userAgent = 'hqplugin-publish';
  try {
    final request = await client.getUrl(url);
    request.followRedirects = false;
    final response = await request.close().timeout(const Duration(seconds: 60));
    if (response.statusCode >= 300 && response.statusCode < 400) {
      final location = response.headers.value(HttpHeaders.locationHeader);
      await response.drain<void>();
      return HttpHop(response.statusCode, location: location);
    }
    if (response.contentLength > maxBytes) {
      return HttpHop(response.statusCode, truncated: true);
    }
    final body = BytesBuilder(copy: false);
    var truncated = false;
    await for (final chunk in response.timeout(const Duration(minutes: 5))) {
      body.add(chunk);
      if (body.length > maxBytes) {
        truncated = true;
        break; // cancels the subscription
      }
    }
    return HttpHop(
      response.statusCode,
      body: body.takeBytes(),
      truncated: truncated,
    );
  } finally {
    client.close(force: true);
  }
}
