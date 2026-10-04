import json
import re
from pathlib import Path

import pytest
from fakes import CONTACT, FakeHost, ok
from fixtures import (
    CIK,
    FILINGS,
    concept_json,
    fact,
    facts_json,
    figi_item,
    figi_response,
    submissions_json,
    tickers_json,
)

from filings_companion import transport
from filings_companion.errors import ContactNotConfigured, ValidationError
from filings_companion.sec import TICKERS_URL, companyfacts_url, submissions_url
from filings_companion.service import (
    LABEL,
    configure_contact,
    contact_status,
    lookup_holdings,
)
from filings_companion.transport import ALLOWED_ORIGINS

ROOT = Path(__file__).resolve().parents[1]
FACTS = facts_json(
    {
        ("us-gaap", "Revenues"): {"USD": [fact("2025-09-30", 1000, start="2024-10-01")]},
        ("us-gaap", "NetIncomeLoss"): {"USD": [fact("2025-09-30", 100, start="2024-10-01")]},
    }
)


def routes(extra=None):
    table = {
        TICKERS_URL: ok(
            tickers_json((1234567, "TSTC", "Testco Holdings Inc"), (7, "OTHR", "Other"))
        ),
        submissions_url(CIK): ok(submissions_json(FILINGS)),
        companyfacts_url(CIK): ok(FACTS),
    }
    table.update(extra or {})
    return table


def run(request, host=None, extra=None):
    host = host or FakeHost(routes(extra))
    return lookup_holdings(host, request), host


def ticker_req(**kw):
    return {"identifiers": [{"type": "ticker", "value": "tstc"}], **kw}


class TestFlows:
    def test_ticker_flow_with_provenance(self):
        out, host = run(ticker_req())
        assert out["label"] == LABEL and "not verified" in LABEL and "recommendation" in LABEL
        assert "OpenFIGI" in out["source"] and "EDGAR" in out["source"]
        assert out["fetched_at"] == "2026-10-04T12:00:00Z" and out["aborted"] is None
        (res,) = out["results"]
        assert res["status"] == "ok" and res["via"] == "sec_ticker_file" and res["cik"] == CIK
        assert res["company"] == {"cik": CIK, "name": "Testco Holdings", "tickers": ["TSTC"]}
        assert [f["form"] for f in res["filings"]] == [
            "8-K",
            "10-Q",
            "10-K",
        ]  # Form 4 not in defaults
        assert {f["key"] for f in res["facts"]} == {"revenue", "net_income"}
        assert "assets" in res["facts_unavailable"]
        origins = [(s["origin"], s["url"]) for s in res["sources"]]
        assert ("www.sec.gov", TICKERS_URL) in origins
        assert ("data.sec.gov", submissions_url(CIK)) in origins
        assert all(s["fetched_at"] == "2026-10-04T12:00:00Z" for s in res["sources"])
        assert {"cik": CIK} in [s.get("identifier_used") for s in res["sources"]]
        assert res["identifier"] == {"type": "ticker", "value": "TSTC"}
        assert out["requests_made"] == 3 == len(host.requests)

    def test_cik_flow_skips_the_ticker_file_and_facts_can_be_off(self):
        out, host = run(
            {"identifiers": [{"type": "cik", "value": "1234567"}], "include_facts": False}
        )
        assert len(host.requests) == 1 and host.requests[0].url == submissions_url(CIK)
        assert "facts" not in out["results"][0] and out["results"][0]["via"] == "cik"

    def test_isin_flow_via_openfigi_then_sec(self):
        figi = figi_response([figi_item(exch="US"), figi_item(exch="GR", ticker="TS1")])
        out, host = run(
            {"identifiers": [{"type": "isin", "value": "US0378331005"}]},
            extra={"https://api.openfigi.com/v3/mapping": ok(figi)},
        )
        (res,) = out["results"]
        assert res["status"] == "ok" and res["via"] == "openfigi+sec_ticker_file"
        assert res["ticker"] == "TSTC" and res["figi"] == "BBG000000001"
        figi_src = next(s for s in res["sources"] if s["origin"] == "api.openfigi.com")
        assert figi_src["identifier_used"] == {"idType": "ID_ISIN", "idValue": "US0378331005"}
        post = next(r for r in host.requests if r.method == "POST")
        assert json.loads(post.body) == [{"idType": "ID_ISIN", "idValue": "US0378331005"}]
        assert "User-Agent" not in post.headers

    def test_mixed_identifiers_share_requests(self):
        figi = figi_response([figi_item()], "No identifier found.")
        out, host = run(
            {
                "identifiers": [
                    {"type": "isin", "value": "US0378331005"},
                    {"type": "cusip", "value": "037833100"},
                    {"type": "ticker", "value": "OTHR"},
                    {"type": "cik", "value": "1234567"},
                ],
                "include_facts": False,
            },
            extra={
                "https://api.openfigi.com/v3/mapping": ok(figi),
                submissions_url("0000000007"): ok(submissions_json([], cik=7, name="Other")),
            },
        )
        statuses = [r["status"] for r in out["results"]]
        assert statuses == ["ok", "unresolved", "ok", "ok"]
        assert "no match" in out["results"][1]["reason"]
        urls = [r.url for r in host.requests]
        assert urls.count(TICKERS_URL) == 1  # one ticker file fetch for the whole call
        assert sum(r.method == "POST" for r in host.requests) == 1  # one batched OpenFIGI call

    def test_non_us_and_ambiguous_listings_are_unresolved(self):
        non_us = figi_response([figi_item(exch="LN", ticker="TS1")])
        out, _ = run(
            {"identifiers": [{"type": "isin", "value": "US0378331005"}]},
            extra={"https://api.openfigi.com/v3/mapping": ok(non_us)},
        )
        assert (
            out["results"][0]["status"] == "unresolved"
            and "SEC filers only" in out["results"][0]["reason"]
        )
        amb = figi_response([figi_item(ticker="AAA"), figi_item(ticker="BBB")])
        out, _ = run(
            {"identifiers": [{"type": "isin", "value": "US0378331005"}]},
            extra={"https://api.openfigi.com/v3/mapping": ok(amb)},
        )
        assert "ambiguous" in out["results"][0]["reason"]

    def test_unknown_ticker_and_openfigi_item_error(self):
        out, _ = run({"identifiers": [{"type": "ticker", "value": "ZZZZ"}]})
        assert out["results"][0]["status"] == "unresolved"
        assert out["results"][0]["reason"] == "not found in the SEC ticker file"
        err = figi_response(("error", "bad request item"))
        out, _ = run(
            {"identifiers": [{"type": "isin", "value": "US0378331005"}]},
            extra={"https://api.openfigi.com/v3/mapping": ok(err)},
        )
        assert out["results"][0]["reason"] == "bad request item"

    def test_per_company_errors_do_not_sink_the_batch(self):
        out, _ = run(
            {
                "identifiers": [
                    {"type": "cik", "value": "9"},
                    {"type": "cik", "value": "1234567"},
                    {"type": "cik", "value": "8"},
                ],
                "include_facts": False,
            },
            extra={
                submissions_url("0000000009"): ok("", 404),
                submissions_url("0000000008"): ok("{}"),  # wrong shape
            },
        )
        a, b, c = out["results"]
        assert a["status"] == "error" and a["error"]["code"] == "http_error"
        assert b["status"] == "ok"
        assert c["status"] == "error" and c["error"]["code"] == "unexpected_response_shape"

    def test_rate_limit_aborts_and_marks_the_rest_not_attempted(self):
        def r(req):
            if "0000000009" in req.url:
                return ok("", 429, {"ratelimit-reset": "9"})
            return ok(submissions_json(FILINGS))

        host = FakeHost(r)
        out = lookup_holdings(
            host,
            {
                "identifiers": [
                    {"type": "cik", "value": "1234567"},
                    {"type": "cik", "value": "9"},
                    {"type": "cik", "value": "10"},
                ],
                "include_facts": False,
            },
        )
        assert out["aborted"] == {
            "code": "rate_limited",
            "message": out["aborted"]["message"],
            "retry_after_s": "9",
        }
        assert [x["status"] for x in out["results"]] == ["ok", "not_attempted", "not_attempted"]

    def test_facts_fall_back_to_per_concept_requests_for_huge_filers(self, monkeypatch):
        big = FACTS + " " * 1000
        concept = concept_json({"USD": [fact("2025-09-30", 55, start="2024-10-01")]})

        def router(req):
            if req.url == companyfacts_url(CIK):
                return ok(big)
            if "companyconcept" in req.url:
                return ok(concept) if req.url.endswith("/Revenues.json") else ok("", 404)
            return ok(submissions_json(FILINGS))

        monkeypatch.setattr(transport, "MAX_BODY_CHARS", 900)
        host = FakeHost(router)
        out = lookup_holdings(host, {"identifiers": [{"type": "cik", "value": "1234567"}]})
        res = out["results"][0]
        assert [f["key"] for f in res["facts"]] == ["revenue"] and res["facts"][0]["value"] == "55"
        assert "net_income" in res["facts_unavailable"]
        assert any("/companyconcept/" in r.url for r in host.requests)

    def test_output_has_no_floats_and_is_json(self):
        out, _ = run(ticker_req())

        def walk(n):
            assert not isinstance(n, float)
            for v in n.values() if isinstance(n, dict) else n if isinstance(n, list) else []:
                walk(v)

        walk(out)
        assert json.loads(json.dumps(out)) == out

    def test_display_links_are_never_fetched(self):
        out, host = run(ticker_req())
        assert out["results"][0]["filings"][0]["url"].startswith("https://www.sec.gov/Archives/")
        assert not [r for r in host.requests if "/Archives/" in r.url]

    def test_every_request_stays_inside_the_manifest_allowlist(self):
        _, host = run(ticker_req())
        for r in host.requests:
            assert re.match(r"https://([^/]+)/", r.url).group(1) in ALLOWED_ORIGINS


class TestGuards:
    def test_contact_required_before_any_request(self):
        host = FakeHost(routes(), contact=None)
        with pytest.raises(ContactNotConfigured):
            lookup_holdings(host, ticker_req())
        assert host.requests == []

    @pytest.mark.parametrize(
        "bad",
        [
            {},
            {"identifiers": []},
            {"identifiers": [{"type": "ticker", "value": "A"}], "filings_limit": 0},
            {"identifiers": [{"type": "ticker", "value": "A"}], "filings_limit": 11},
            {"identifiers": [{"type": "ticker", "value": "A"}], "filings_limit": True},
            {"identifiers": [{"type": "ticker", "value": "A"}], "include_facts": "yes"},
            {"identifiers": [{"type": "ticker", "value": "A"}], "forms": [1]},
        ],
    )
    def test_bad_requests(self, bad):
        with pytest.raises(ValidationError):
            lookup_holdings(FakeHost(routes()), bad)

    def test_configure_and_status(self):
        host = FakeHost(contact=None)
        assert contact_status(host)["configured"] is False
        assert configure_contact(host, {"contact": CONTACT}) == {
            "configured": True,
            "contact": CONTACT,
        }
        assert contact_status(host) == {"configured": True, "contact": CONTACT}
        with pytest.raises(ContactNotConfigured):
            configure_contact(host, {"contact": "me@example.com"})

    def test_sec_request_rate_on_a_big_batch_stays_under_ten_per_second(self):
        ids = [{"type": "cik", "value": str(i + 100)} for i in range(10)]
        host = FakeHost(
            lambda req: ok(submissions_json(FILINGS)) if "submissions" in req.url else ok(FACTS)
        )
        out = lookup_holdings(host, {"identifiers": ids})
        assert out["requests_made"] == 20
        t = host.request_times
        assert all(t[i + 10] - t[i] >= 1.0 for i in range(len(t) - 10))


class TestManifest:
    manifest = json.loads((ROOT / "manifest.json").read_text())

    def test_origins_exact_and_match_code(self):
        perm = next(p for p in self.manifest["permissions"] if p["id"] == "network:fetch")
        assert perm["scope"]["origins"] == list(ALLOWED_ORIGINS)
        assert not any("*" in o or "/" in o or ":" in o for o in perm["scope"]["origins"])

    def test_permissions_are_minimal(self):
        ids = [p["id"] for p in self.manifest["permissions"]]
        assert ids == ["network:fetch", "plugin:storage"]  # no propose, no reads, no AI
        assert "trust_tier" not in self.manifest
        assert self.manifest["execution_mode"] == "sidecar"
        assert re.fullmatch(r"[a-z][a-z0-9]*(\.[a-z0-9][a-z0-9-]*)+", self.manifest["id"])
        assert len(self.manifest["description"]) <= 200
