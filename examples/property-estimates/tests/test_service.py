import contextlib
import json
import re
from datetime import date
from pathlib import Path

import pytest
from fakes import NOW, FakeHost, ok
from fixtures import (
    DVF_HEADER,
    IE_HEADER,
    dvf_row,
    ie_row,
    sg_page,
    sg_record,
    uk_csv_row,
    uk_item,
    uk_page,
)

from property_estimates.errors import (
    OriginNotAllowed,
    PendingHostSupport,
    ProposalRefused,
    ValidationError,
)
from property_estimates.estimate import LABEL, MIN_SAMPLE_PROPOSE
from property_estimates.proposal import build_valuation_proposal, default_source_key
from property_estimates.service import estimate_property, submit_proposal
from property_estimates.transport import ALLOWED_ORIGINS, Fetcher, window_start

ROOT = Path(__file__).resolve().parents[1]


def sg_host(n, base=500_000):
    page = sg_page([sg_record("2026-08", base + i * 1_000, area="90") for i in range(n)])
    return FakeHost(lambda url: ok(page))


SG_REQ = {"region": "sg_hdb", "town": "BISHAN", "flat_type": "4 ROOM"}


class TestEstimateFlow:
    def test_sg_end_to_end_has_provenance_label_attribution(self):
        host = sg_host(40)
        r = estimate_property(host, SG_REQ)
        assert r["status"] == "ok" and r["region"] == "sg_hdb" and r["currency"] == "SGD"
        assert r["label"] == LABEL
        assert "Singapore Open Data Licence" in r["attribution"]
        assert r["source"]["origin"] == "data.gov.sg"
        assert r["source"]["fetched_at"] == "2026-10-04T12:00:00Z"
        assert r["source"]["requests_made"] == 1
        assert r["source"]["references"][0].startswith("https://data.gov.sg/api/action/")
        assert r["query"] == {
            "region": "sg_hdb", "town": "BISHAN", "flat_type": "4 ROOM", "months": 12
        }  # fmt: skip
        assert r["per_sqm"]["n"] == 40
        assert r["sample"]["date_from"] == "2026-08"

    def test_insufficient_sample_gives_no_figures(self):
        r = estimate_property(sg_host(5), SG_REQ)
        assert r["status"] == "insufficient_sample" and "median" not in r
        assert r["label"] == LABEL and r["attribution"]

    def test_uk_api_attribution_is_ogl(self):
        page = uk_page([uk_item(300_000 + i * 1_000) for i in range(30)])
        r = estimate_property(
            FakeHost(lambda u: ok(page)), {"region": "uk_ppd", "district": "TESTDISTRICT"}
        )
        assert r["status"] == "ok"
        assert r["attribution"] == (
            "Contains HM Land Registry data © Crown copyright and database right 2026. "
            "This data is licensed under the Open Government Licence v3.0."
        )
        assert r["currency"] == "GBP"

    def test_uk_person_provided_csv_makes_no_network_calls(self):
        text = "\n".join(uk_csv_row(300_000 + i * 1000) for i in range(30))
        host = FakeHost()
        r = estimate_property(
            host, {"region": "uk_ppd", "district": "testdistrict", "csv_text": text}
        )
        assert r["status"] == "ok" and host.requests == []
        assert "Open Government Licence v3.0" in r["attribution"]

    def test_fr_end_to_end_via_redirect(self):
        rows = [
            dvf_row(f"m{i}", "2026-03-01", str(300_000 + i * 1000), "Appartement", "50")
            for i in range(25)
        ]
        csv_text = DVF_HEADER + "\n" + "\n".join(rows)
        storage = (
            "https://geo-dvf.s3.sbg.io.cloud.ovh.net:443/latest/csv/2025/communes/75/75101.csv"
        )

        def route(url):
            if url.startswith("https://files.data.gouv.fr"):
                return ok("", 302, {"Location": storage})
            return ok(csv_text)

        r = estimate_property(
            FakeHost(route),
            {"region": "fr_dvf", "commune": "75101", "property_type": "apartment", "years": [2025]},
        )
        assert r["status"] == "ok" and r["currency"] == "EUR"
        assert "Licence Ouverte" in r["attribution"]
        assert r["per_sqm"]["median"] == "6240.00"  # median 312,000 / 50 m2 -> see rows
        blob = json.dumps(r)
        assert "RUE DE TEST" not in blob and "99999000AA0001" not in blob

    def test_ie_end_to_end_from_provided_csv(self):
        text = (
            IE_HEADER
            + "\n"
            + "\n".join(
                ie_row(f"{1 + i % 28:02d}/06/2026", f"€{400_000 + i * 1000:,}.00")
                for i in range(30)
            )
        )
        host = FakeHost()
        r = estimate_property(host, {"region": "ie_ppr", "county": "Dublin", "csv_text": text})
        assert r["status"] == "ok" and host.requests == []
        assert "Property Services Regulatory Authority" in r["attribution"]
        assert r["source"]["origin"].startswith("person-provided")
        assert r["per_sqm"] is None

    def test_unknown_region_and_bad_input(self):
        with pytest.raises(ValidationError):
            estimate_property(FakeHost(), {"region": "mars"})
        with pytest.raises(ValidationError):
            estimate_property(FakeHost(), {"region": "sg_hdb", "town": "BISHAN"})
        with pytest.raises(ValidationError):
            estimate_property(FakeHost(), {"region": "ie_ppr", "county": "Dublin"})

    def test_no_binary_floats_anywhere_in_output(self):
        r = estimate_property(sg_host(40), {**SG_REQ, "include_proposal_preview": True})

        def walk(n):
            assert not isinstance(n, float)
            if isinstance(n, dict):
                list(map(walk, n.values()))
            elif isinstance(n, list):
                list(map(walk, n))

        walk(r)


class TestProposal:
    def test_preview_from_enough_samples(self):
        r = estimate_property(sg_host(40), {**SG_REQ, "include_proposal_preview": True})
        p = r["proposal_preview"]
        assert p["kind"] == "valuation" and p["method"] == "comparable_sales_median"
        assert p["source_key"] == "sg_hdb:flat_type:4-ROOM:town:BISHAN"
        assert re.fullmatch(r"[A-Za-z0-9:._/-]{1,256}", p["source_key"])
        assert p["value"]["currency"] == "SGD"
        assert p["value"]["amount"].endswith("00.00") and "item" not in json.dumps(p).lower()
        assert p["source"]["origin"] == "data.gov.sg"
        assert p["source"]["fetched_at"] == "2026-10-04T12:00:00Z"
        assert p["as_of"] == "2026-10-04"
        assert p["confidence"]["level"] == "medium" and "Based on 40" in p["confidence"]["note"]
        assert p["disclaimer"] == LABEL and p["attribution"] and "rounded" in p["rounding"]
        assert "40 comparable sales 2026-08..2026-08" in p["source"]["reference"]

    def test_value_is_coarsened_median(self):
        r = estimate_property(sg_host(40, base=500_123), SG_REQ)
        p = build_valuation_proposal(r, region="sg_hdb")
        assert r["median"] == "519623.00" and p["value"]["amount"] == "520000.00"

    def test_refused_below_proposal_minimum(self):
        r = estimate_property(sg_host(MIN_SAMPLE_PROPOSE - 1), SG_REQ)
        assert r["status"] == "ok"  # shown, but...
        with pytest.raises(ProposalRefused):
            build_valuation_proposal(r, region="sg_hdb")
        r2 = estimate_property(
            sg_host(MIN_SAMPLE_PROPOSE - 1), {**SG_REQ, "include_proposal_preview": True}
        )
        assert r2["proposal_preview"] is None and "30" in r2["proposal_preview_refused"]
        insufficient = estimate_property(sg_host(3), SG_REQ)
        with pytest.raises(ProposalRefused):
            build_valuation_proposal(insufficient, region="sg_hdb")

    def test_custom_source_key_validation(self):
        r = estimate_property(sg_host(40), SG_REQ)
        assert (
            build_valuation_proposal(r, region="sg_hdb", source_key="my:key/1")["source_key"]
            == "my:key/1"
        )
        for bad in ["has space", "x" * 300, "bad\nkey", "emoji☃"]:
            with pytest.raises(ValidationError):
                build_valuation_proposal(r, region="sg_hdb", source_key=bad)

    def test_default_key_never_includes_free_text_beyond_query(self):
        key = default_source_key("uk_ppd", {"district": "LEEDS", "months": 12, "town": ""})
        assert key == "uk_ppd:district:LEEDS"

    def test_submit_is_blocked_until_host_supports_it(self):
        r = estimate_property(sg_host(40), SG_REQ)
        host = FakeHost()
        with pytest.raises(PendingHostSupport) as ei:
            submit_proposal(host, r)
        assert ei.value.code == "pending_host_support" and host.proposed == []

    def test_submit_against_a_host_that_implements_it(self):
        r = estimate_property(sg_host(40), SG_REQ)
        host = FakeHost()
        host.propose_supported = True
        receipts = submit_proposal(host, r)
        assert [x.outcome for x in receipts] == ["queued"]
        sent = host.proposed[0][0]
        assert sent["kind"] == "valuation" and "item_id" not in sent


class TestManifestAndOrigins:
    manifest = json.loads((ROOT / "manifest.json").read_text())

    def perm(self, pid):
        return next(p for p in self.manifest["permissions"] if p["id"] == pid)

    def test_origins_are_exact_and_match_code(self):
        origins = self.perm("network:fetch")["scope"]["origins"]
        assert origins == list(ALLOWED_ORIGINS)
        assert not any("*" in o or "/" in o or ":" in o for o in origins)
        assert 1 <= len(origins) <= 10

    def test_declared_permissions(self):
        ids = [p["id"] for p in self.manifest["permissions"]]
        assert ids == ["network:fetch", "read:external_input", "propose:valuations"]
        assert self.perm("propose:valuations")["scope"] == {"kinds": ["home"]}
        assert self.manifest["execution_mode"] == "sidecar"
        assert "trust_tier" not in self.manifest  # assigned by the registry, never self-set
        assert "write:external_output" not in ids and "ai:inference" not in ids

    def test_identity_fields(self):
        m = self.manifest
        assert re.fullmatch(r"^[a-z][a-z0-9]*(\.[a-z0-9][a-z0-9-]*)+$", m["id"])
        assert len(m["name"]) <= 60 and len(m["description"]) <= 200
        assert m["content_hash_sha256"] == "0" * 64  # placeholder until release

    def test_every_built_url_stays_inside_the_allowlist(self):
        urls = []
        r = estimate_property(sg_host(40), SG_REQ)
        urls += r["source"]["references"]
        host = FakeHost(lambda u: ok("", 404))
        for req in [
            {"region": "uk_ppd", "district": "LEEDS"},
            {"region": "fr_dvf", "commune": "75101", "property_type": "house", "years": [2025]},
        ]:
            with contextlib.suppress(Exception):  # only the URLs requested matter here
                estimate_property(host, req)
        urls += [rq.url for rq in host.requests]
        assert urls
        for u in urls:
            assert re.match(r"https://([^/:?]+)", u).group(1) in ALLOWED_ORIGINS

    def test_unlisted_origin_is_refused_by_the_core(self):
        f = Fetcher(FakeHost())
        with pytest.raises(OriginNotAllowed):
            f.get("https://example.com/x", accept="x")


def test_window_start_matches_now():
    assert window_start(NOW.date(), 12) == date(2025, 10, 1)
