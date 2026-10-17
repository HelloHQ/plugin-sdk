# Hello Calvin

`com.calvinx.hello`: a copy of [`hello-python`](../hello-python) published under
a **third-party author namespace** (`com.calvinx.`) instead of HelloHQ's. It
exists to exercise the author-held signing path end to end: the author enrolls
hybrid public keys in the registry (`authors/calvin.json`), signs the plugin
with `hqplugin publish --sign-key`, a maintainer sets `trust_tier: verified` at
merge, and the signing pipeline stamps the author's signatures and certificate.
The app then verifies the chain offline.

It is a Tier-1 Python sidecar, so it needs the Verified tier anyway. It reads
portfolio names and makes no network or file access; the code is the same as
hello-python's (see its README for the build, the tests and the sidecar
protocol).

```bash
./build.sh                    # bundle.py -> ./plugin.py (stdlib only, reproducible)
uv sync --group dev && uv run pytest
```

Released by the tag `hello-calvinx-v<semver>` (`.github/workflows/release-plugin.yml`);
published to the registry with:

```bash
hqplugin publish --release --sign-key <dir with ed25519.pem and ml-dsa-65.pem>
```
