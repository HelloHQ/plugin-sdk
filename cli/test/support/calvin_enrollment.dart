// The real, registry-CI-verified enrollment of authors/calvin.json
// (plugin-registry), inlined so the enrollment tests have a golden vector
// without a registry checkout. Public values only. If the registry ever
// re-enrolls calvin with other keys this file does not need to follow: the
// vector is a record that was valid, and the tests that read the registry's
// live file compare against `scripts/author-cert.mjs` instead.
import 'dart:convert';

const String kCalvinEd25519 = 'IG7gBqyqos21cjzXT16i3higYbGC58myfxyuk3mAx1c=';

/// The ML-DSA-65 public key (1952 bytes), in 68-character pieces.
const String kCalvinMlDsa65 =
    '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'
    '7TQeAGv0Rgci+mifI/M=';
const String kCalvinProof =
    'OPifKAnfhg78thh7awho36ozQSNUldPWCs4iw55a5CoAukXo2GH9d0QTuAfkrotrvU3NBJ0IYcEF1gaQPmbiAQ==';

/// authors/calvin.json byte for byte (2-space JSON, one prefix per line,
/// trailing newline).
const String kCalvinFile =
    '''{
  "slug": "calvin",
  "identity": {
    "oidc_issuer": "https://github.com",
    "oidc_subject": "95563"
  },
  "id_prefixes": [
    "com.calvinx."
  ],
  "public_keys": {
    "ed25519": "$kCalvinEd25519",
    "ml-dsa-65": "$kCalvinMlDsa65"
  },
  "tier": "author",
  "proof": {
    "ed25519": "$kCalvinProof"
  }
}
''';

/// `node scripts/author-cert.mjs statement authors/calvin.json`, byte for byte.
const String kCalvinStatement =
    'hellohq-author-enrollment/v1\n'
    'slug=calvin\n'
    'oidc_issuer=https://github.com\n'
    'oidc_subject=95563\n'
    'ed25519=$kCalvinEd25519\n'
    'ml-dsa-65=$kCalvinMlDsa65\n'
    'id_prefixes=com.calvinx.\n'
    'tier=author\n';

Map<String, dynamic> calvinRecord() =>
    jsonDecode(kCalvinFile) as Map<String, dynamic>;
