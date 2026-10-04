import json
from decimal import Decimal

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
from filings_companion.contact import get_contact, set_contact, user_agent, validate_contact
from filings_companion.errors import (
    ContactNotConfigured,
    HttpError,
    OriginNotAllowed,
    ParseError,
    RateLimited,
    ResponseTooLarge,
    ValidationError,
)
from filings_companion.identifiers import (
    cik10,
    cusip_is_valid,
    isin_is_valid,
    validate_identifier,
    validate_identifiers,
)
from filings_companion.money import decimal_str
from filings_companion.openfigi import (
    batches,
    build_job,
    map_identifiers,
    parse_mapping_response,
)
from filings_companion.sec import (
    companyconcept_url,
    companyfacts_url,
    latest_annual,
    lookup_ticker,
    parse_company_concept,
    parse_company_facts,
    parse_company_tickers,
    parse_submissions,
    submissions_url,
)
from filings_companion.transport import Fetcher, origin_of


# ------------------------------------------------------------------ identifiers
class TestIdentifiers:
    def test_cik10(self):
        assert cik10(320193) == "0000320193"
        assert cik10("0000320193") == "0000320193"
        for bad in ["", "abc", "12345678901", "0", "-5", True, 1.5]:
            with pytest.raises(ValidationError):
                cik10(bad)

    def test_isin_and_cusip_checksums(self):
        assert isin_is_valid("US0378331005")  # a published, valid ISIN
        assert not isin_is_valid("US0378331006")
        assert not isin_is_valid("US037833100")
        assert cusip_is_valid("037833100")
        assert not cusip_is_valid("037833101")
        assert not cusip_is_valid("03783310")

    def test_validate_identifier_normalises(self):
        assert validate_identifier({"type": "TICKER", "value": " brk.b "}) == {
            "type": "ticker",
            "value": "BRK.B",
        }
        assert validate_identifier({"type": "cik", "value": "320193"})["value"] == "0000320193"
        assert validate_identifier({"type": "figi", "value": "bbg000b9xry4"})["type"] == "figi"

    @pytest.mark.parametrize(
        "raw",
        [
            {"type": "isin", "value": "US0378331006"},
            {"type": "ticker", "value": "has space"},
            {"type": "ticker", "value": "1ABC"},
            {"type": "figi", "value": "XXX000B9XRY4"},
            {"type": "ssn", "value": "1"},
            {"value": "AAPL"},
            "AAPL",
        ],
    )
    def test_invalid_identifiers(self, raw):
        with pytest.raises(ValidationError):
            validate_identifier(raw)

    def test_list_rules(self):
        ok_ = [{"type": "ticker", "value": "AAPL"}, {"type": "ticker", "value": "aapl"}]
        assert len(validate_identifiers(ok_)) == 1  # deduplicated
        for bad in [[], "AAPL", [{"type": "ticker", "value": f"T{i}"} for i in range(11)]]:
            with pytest.raises(ValidationError):
                validate_identifiers(bad)


# ------------------------------------------------------------------ contact
class TestContact:
    def test_valid(self):
        assert validate_contact("  Jane   Tester  jane@tester-mail.org ") == (
            "Jane Tester jane@tester-mail.org"
        )

    @pytest.mark.parametrize(
        "bad",
        [
            "", "short", "no email here at all", "Jane jane@example.com", "Jane j@foo.test",
            "REPLACE_ME me@real-domain.org", "yourname@gmail.com please", None, 42,
            "Jane j@tester-mail.org\r\nX-Evil: 1", "Jane j@tester-mail.org\x00", "x" * 200,
        ],
    )  # fmt: skip
    def test_rejected(self, bad):
        with pytest.raises(ContactNotConfigured):
            validate_contact(bad)

    def test_user_agent_declares_contact_and_app(self):
        ua = user_agent(CONTACT)
        assert ua.startswith(CONTACT) and "HelloHQ-Filings-Companion/" in ua

    def test_storage_roundtrip_and_unset(self):
        host = FakeHost(contact=None)
        with pytest.raises(ContactNotConfigured):
            get_contact(host)
        assert set_contact(host, CONTACT) == CONTACT
        assert get_contact(host) == CONTACT
        with pytest.raises(ContactNotConfigured):
            set_contact(host, "example@example.org")
        host.storage["contact"] = "tampered"
        with pytest.raises(ContactNotConfigured):
            get_contact(host)

    def test_no_hard_coded_contact_anywhere_in_shipped_code(self):
        import re
        from pathlib import Path

        root = Path(transport.__file__).resolve().parents[1]
        files = [
            *(root / "filings_companion").glob("*.py"),
            root / "plugin.py",
            root / "manifest.json",
        ]
        found = {
            m
            for f in files
            for m in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", f.read_text())
            if not m.endswith("@yourdomain.com")  # the only illustrative placeholder allowed
        }
        assert found == set()


# ------------------------------------------------------------------ transport
class TestTransport:
    def test_origin_rules(self):
        for ok_url in [
            "https://data.sec.gov/x",
            "https://www.sec.gov/x",
            "https://api.openfigi.com/v3/mapping",
        ]:
            assert origin_of(ok_url)
        for bad in [
            "http://data.sec.gov/x",
            "https://sec.gov/x",
            "https://data.sec.gov.evil.test/x",
            "https://u:p@data.sec.gov/x",
            "https://data.sec.gov:444/x",
        ]:
            with pytest.raises(OriginNotAllowed):
                origin_of(bad)

    def test_sec_requests_carry_user_agent_openfigi_does_not(self):
        host = FakeHost(lambda r: ok("{}"))
        f = Fetcher(host)
        f.request("https://data.sec.gov/a")
        f.request("https://api.openfigi.com/v3/mapping", method="POST", body="[]")
        sec_req, figi_req = host.requests
        assert sec_req.headers["User-Agent"].startswith(CONTACT)
        assert "User-Agent" not in figi_req.headers  # contact is never sent to third parties
        assert figi_req.headers["Content-Type"] == "application/json"

    def test_no_contact_means_no_sec_request(self):
        host = FakeHost(lambda r: ok("{}"), contact=None)
        with pytest.raises(ContactNotConfigured):
            Fetcher(host).request("https://data.sec.gov/a")
        assert host.requests == []
        Fetcher(host).request("https://api.openfigi.com/v3/mapping", method="POST", body="[]")
        assert len(host.requests) == 1  # OpenFIGI needs no contact

    def test_rate_cap_is_enforced_and_never_above_ten(self):
        with pytest.raises(ValidationError):
            Fetcher(FakeHost(), sec_per_second=11)
        with pytest.raises(ValidationError):
            Fetcher(FakeHost(), sec_per_second=0)
        host = FakeHost(lambda r: ok("{}"))
        f = Fetcher(host)  # default 8/s
        for _ in range(100):
            f.request("https://data.sec.gov/a")
        t = host.request_times
        for i in range(len(t) - 10):
            assert t[i + 10] - t[i] >= 1.0 - 1e-9  # never more than 10 in any second
        for i in range(len(t) - 8):
            assert t[i + 8] - t[i] >= 1.0 - 1e-9  # actually held to 8
        assert host.slept > 10

    def test_both_sec_origins_share_one_limiter(self):
        host = FakeHost(lambda r: ok("{}"))
        f = Fetcher(host, sec_per_second=2)
        for url in ["https://data.sec.gov/a", "https://www.sec.gov/b", "https://data.sec.gov/c"]:
            f.request(url)
        assert host.slept == pytest.approx(1.0)

    def test_openfigi_25_per_minute_on_fake_clock(self):
        host = FakeHost(lambda r: ok("[]"))
        f = Fetcher(host)
        for _ in range(26):
            f.request("https://api.openfigi.com/v3/mapping", method="POST", body="[]")
        assert host.slept == pytest.approx(60.5)  # the 26th waits out the window
        t = host.request_times
        assert all(t[i + 25] - t[i] >= 60.0 for i in range(len(t) - 25))

    def test_status_mapping(self):
        def get(status, headers=None, url="https://data.sec.gov/a"):
            return Fetcher(FakeHost(lambda r: ok("", status, headers))).request(url)

        with pytest.raises(RateLimited) as ei:
            get(429, {"ratelimit-reset": "42"}, "https://api.openfigi.com/v3/mapping")
        assert ei.value.retry_after_s == 42
        with pytest.raises(RateLimited) as ei:
            get(429)
        assert ei.value.retry_after_s is None
        with pytest.raises(HttpError) as ei:
            get(403)
        assert ei.value.code == "sec_forbidden" and "User-Agent" in ei.value.message
        with pytest.raises(HttpError) as ei:
            get(302, {"Location": "https://evil.test"})
        assert ei.value.status == 302  # redirects are not followed
        with pytest.raises(HttpError):
            get(500)

    def test_oversize_body(self, monkeypatch):
        monkeypatch.setattr(transport, "MAX_BODY_CHARS", 5)
        with pytest.raises(ResponseTooLarge):
            Fetcher(FakeHost(lambda r: ok("x" * 6))).request("https://data.sec.gov/a")


# ------------------------------------------------------------------ SEC parsing
class TestSubmissions:
    def test_parse_filters_limit_and_urls(self):
        sub = parse_submissions(submissions_json(FILINGS), forms=("10-K", "10-Q", "8-K"), limit=2)
        assert sub["cik"] == CIK and sub["name"] == "Testco Holdings" and sub["tickers"] == ["TSTC"]
        assert [f.form for f in sub["filings"]] == ["8-K", "10-Q"]
        f = sub["filings"][0]
        assert (
            f.url
            == "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000010/tstc-8k.htm"
        )
        assert f.report_date == "2026-08-30" and f.filed == "2026-09-01"

    def test_no_form_filter_and_missing_report_date(self):
        sub = parse_submissions(submissions_json(FILINGS), forms=None, limit=10)
        assert [f.form for f in sub["filings"]] == ["8-K", "10-Q", "10-K", "4"]
        assert sub["filings"][3].report_date is None

    def test_empty_recent(self):
        sub = parse_submissions(submissions_json([]))
        assert sub["filings"] == []

    def test_unsafe_document_name_gives_no_url(self):
        f = [("0001234567-26-000010", "8-K", "2026-09-01", "", "../../etc/passwd")]
        assert parse_submissions(submissions_json(f))["filings"][0].url is None

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda d: d.pop("cik"),
            lambda d: d.pop("name"),
            lambda d: d.pop("filings"),
            lambda d: d["filings"].pop("recent"),
            lambda d: d["filings"]["recent"].pop("form"),
            lambda d: d["filings"]["recent"].update(form=["8-K"]),  # length mismatch
            lambda d: d["filings"]["recent"].update(accessionNumber=["bad"] * 4),
            lambda d: d["filings"]["recent"].update(filingDate=["yesterday"] * 4),
            lambda d: d["filings"]["recent"].update(reportDate="x"),
        ],
    )
    def test_shape_errors(self, mutate):
        doc = json.loads(submissions_json(FILINGS))
        mutate(doc)
        with pytest.raises(ParseError):
            parse_submissions(json.dumps(doc), forms=None)

    @pytest.mark.parametrize("text", ["", "[]", "null", "{bad"])
    def test_not_an_object(self, text):
        with pytest.raises(ParseError):
            parse_submissions(text)

    def test_huge_filing_list(self):
        many = [(f"0001234567-26-{i:06d}", "8-K", "2026-09-01", "", "d.htm") for i in range(50_000)]
        sub = parse_submissions(submissions_json(many), limit=10)
        assert len(sub["filings"]) == 10


class TestFacts:
    REV = ("us-gaap", "Revenues")

    def test_latest_annual_wins_over_restatements_and_quarters(self):
        text = facts_json(
            {
                self.REV: {
                    "USD": [
                        fact("2024-09-30", 900, fy=2024, filed="2024-11-01", start="2023-10-01"),
                        fact("2025-09-30", 1000, filed="2025-11-10", start="2024-10-01"),
                        fact(
                            "2025-09-30",
                            1100,
                            form="10-K/A",
                            filed="2026-01-05",
                            start="2024-10-01",
                        ),
                        fact("2026-06-30", 700, form="10-Q", fp="Q3", fy=2026, filed="2026-08-05"),
                    ]
                }
            }
        )
        _, facts, unavailable = parse_company_facts(text)
        rev = next(f for f in facts if f["key"] == "revenue")
        assert rev["value"] == "1100" and rev["form"] == "10-K/A"  # latest filing for latest period
        assert rev["period_end"] == "2025-09-30" and rev["period_start"] == "2024-10-01"
        assert rev["unit"] == "USD" and rev["concept"] == "us-gaap:Revenues"
        assert rev["accession"] == "0001234567-25-000003" and rev["filed"] == "2026-01-05"
        assert "revenue" not in unavailable and "assets" in unavailable

    def test_fallback_concept_and_explicit_unavailable(self):
        text = facts_json(
            {
                ("us-gaap", "SalesRevenueNet"): {"USD": [fact("2025-09-30", 5)]},
                ("us-gaap", "Assets"): {"EUR": [fact("2025-09-30", 5)]},  # wrong unit: not used
                ("us-gaap", "Liabilities"): {"USD": [fact("2025-06-30", 5, form="10-Q", fp="Q3")]},
            }
        )
        _, facts, unavailable = parse_company_facts(text)
        assert [f["concept"] for f in facts] == ["us-gaap:SalesRevenueNet"]
        assert set(unavailable) == {
            "net_income",
            "assets",
            "liabilities",
            "equity",
            "eps_diluted",
            "shares_outstanding",
        }

    def test_decimals_stay_exact_and_strings(self):
        text = facts_json(
            {
                ("us-gaap", "EarningsPerShareDiluted"): {
                    "USD/shares": [fact("2025-09-30", Decimal("6.13"))]
                },
                ("us-gaap", "NetIncomeLoss"): {"USD": [fact("2025-09-30", -123456789012345678)]},
                ("dei", "EntityCommonStockSharesOutstanding"): {
                    "shares": [fact("2025-10-20", 15000000000)]
                },
            }
        )
        _, facts, _ = parse_company_facts(text)
        by = {f["key"]: f for f in facts}
        assert by["eps_diluted"]["value"] == "6.13" and by["eps_diluted"]["unit"] == "USD/shares"
        assert by["net_income"]["value"] == "-123456789012345678"
        assert by["shares_outstanding"]["value"] == "15000000000"
        assert all(isinstance(f["value"], str) for f in facts)

    def test_decimal_str(self):
        assert decimal_str(Decimal("1.50"), what="x") == "1.5"
        assert decimal_str(Decimal("1E+3"), what="x") == "1000"
        assert decimal_str(Decimal("-0.0"), what="x") == "0"
        assert decimal_str(5, what="x") == "5"
        for bad in [1.5, True, "5", None, Decimal("NaN")]:
            with pytest.raises(ParseError):
                decimal_str(bad, what="x")

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda d: d.pop("facts"),
            lambda d: d.pop("entityName"),
            lambda d: d.update(facts=[]),
            lambda d: d["facts"]["us-gaap"]["Revenues"].pop("units"),
            lambda d: d["facts"]["us-gaap"]["Revenues"]["units"]["USD"][0].pop("val"),
            lambda d: d["facts"]["us-gaap"]["Revenues"]["units"].update(USD={"a": 1}),
            lambda d: d["facts"]["us-gaap"]["Revenues"]["units"]["USD"][0].update(val="12"),
            lambda d: d["facts"]["us-gaap"]["Revenues"]["units"]["USD"][0].update(end="soon"),
        ],
    )
    def test_shape_errors_are_explicit(self, mutate):
        doc = json.loads(facts_json({self.REV: {"USD": [fact("2025-09-30", 5)]}}))
        mutate(doc)
        with pytest.raises(ParseError):
            parse_company_facts(json.dumps(doc))

    def test_empty_facts_is_all_unavailable(self):
        _, facts, unavailable = parse_company_facts(facts_json({}))
        assert facts == [] and len(unavailable) == 7

    def test_huge_facts_document(self):
        entries = [
            fact(f"{2000 + i % 25}-09-30", i, filed=f"{2001 + i % 25}-11-01") for i in range(20_000)
        ]
        _, facts, _ = parse_company_facts(facts_json({self.REV: {"USD": entries}}))
        assert facts[0]["period_end"] == "2024-09-30"

    def test_latest_annual_helper(self):
        assert latest_annual([], what="x") is None
        with pytest.raises(ParseError):
            latest_annual("nope", what="x")

    def test_concept_parse(self):
        text = concept_json({"USD": [fact("2025-09-30", 77)]})
        got = parse_company_concept(
            text, key="revenue", label="Revenue", taxonomy="us-gaap", tag="Revenues", unit="USD"
        )
        assert got["value"] == "77" and got["concept"] == "us-gaap:Revenues"
        assert (
            parse_company_concept(
                text, key="k", label="l", taxonomy="us-gaap", tag="Revenues", unit="EUR"
            )
            is None
        )
        with pytest.raises(ParseError):
            parse_company_concept("{}", key="k", label="l", taxonomy="a", tag="b", unit="USD")

    def test_urls(self):
        assert submissions_url(320193) == "https://data.sec.gov/submissions/CIK0000320193.json"
        assert (
            companyfacts_url("320193")
            == "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
        )
        assert companyconcept_url(320193, "us-gaap", "Assets").endswith(
            "/companyconcept/CIK0000320193/us-gaap/Assets.json"
        )
        for tax, tag in [("US GAAP", "x"), ("us-gaap", "../x"), ("us-gaap", "")]:
            with pytest.raises(ParseError):
                companyconcept_url(1, tax, tag)


class TestTickerFile:
    def test_parse_and_lookup(self):
        table = parse_company_tickers(
            tickers_json((320193, "AAPL", "Apple Inc."), (1067983, "BRK-B", "Berkshire"))
        )
        assert table["AAPL"] == {"cik": "0000320193", "title": "Apple Inc."}
        assert lookup_ticker(table, "brk.b")["cik"] == "0001067983"
        assert lookup_ticker(table, "NOPE") is None

    def test_first_entry_wins_on_duplicate_ticker(self):
        table = parse_company_tickers(tickers_json((1, "DUP", "First"), (2, "DUP", "Second")))
        assert table["DUP"]["title"] == "First"

    @pytest.mark.parametrize(
        "text",
        [
            "[]",
            "",
            '{"0": 1}',
            '{"0": {"ticker": "A", "title": "t"}}',
            '{"0": {"cik_str": "x", "ticker": "A", "title": "t"}}',
        ],
    )
    def test_shape_errors(self, text):
        with pytest.raises(ParseError):
            parse_company_tickers(text)

    def test_empty_object_is_empty_table(self):
        assert parse_company_tickers("{}") == {}


# ------------------------------------------------------------------ OpenFIGI
class TestOpenFigi:
    def test_jobs(self):
        assert build_job({"type": "isin", "value": "US0378331005"}) == {
            "idType": "ID_ISIN",
            "idValue": "US0378331005",
        }
        assert build_job({"type": "cusip", "value": "037833100"})["idType"] == "ID_CUSIP"
        assert build_job({"type": "figi", "value": "BBG000B9XRY4"})["idType"] == "ID_BB_GLOBAL"
        assert build_job({"type": "ticker", "value": "AAPL"}) == {
            "idType": "TICKER",
            "idValue": "AAPL",
            "exchCode": "US",
        }
        with pytest.raises(ValidationError):
            build_job({"type": "cik", "value": "0000000001"})

    def test_batching_respects_keyless_limit(self):
        sizes = [len(b) for b in batches([{"i": i} for i in range(25)])]
        assert sizes == [10, 10, 5]
        assert list(batches([])) == []
        with pytest.raises(ValidationError):
            list(batches([{}], size=100))  # 100 needs an API key; we have none

    def test_parse_valid(self):
        res = parse_mapping_response(
            figi_response([figi_item()], "No identifier found.", ("error", "Invalid idType")),
            expected=3,
        )
        assert res[0].matches[0].ticker == "TSTC" and res[0].matches[0].exch_code == "US"
        assert res[1].warning == "No identifier found." and res[1].matches == ()
        assert res[2].error == "Invalid idType"

    @pytest.mark.parametrize(
        "text,n",
        [
            ("", 1),
            ("{}", 1),
            ("[]", 1),
            (json.dumps([{"data": [], "warning": "x"}]), 1),
            (json.dumps([{}]), 1),
            (json.dumps([1]), 1),
            (json.dumps([{"data": [{"ticker": "A"}]}]), 1),
            (json.dumps([{"data": "x"}]), 1),
        ],
    )
    def test_parse_invalid(self, text, n):
        with pytest.raises(ParseError):
            parse_mapping_response(text, expected=n)

    def test_empty_data_list_is_a_valid_no_match(self):
        assert parse_mapping_response(json.dumps([{"data": []}]), expected=1)[0].matches == ()

    def test_map_identifiers_batches_and_posts(self):
        seen = []

        def route(req):
            jobs = json.loads(req.body)
            seen.append((req.method, req.url, len(jobs)))
            return ok(figi_response(*[[figi_item(ticker=f"T{j['idValue']}")] for j in jobs]))

        host = FakeHost(route)
        jobs = [{"idType": "ID_ISIN", "idValue": f"V{i}"} for i in range(23)]
        out = map_identifiers(Fetcher(host), jobs)
        assert [n for *_, n in seen] == [10, 10, 3]
        assert all(m == "POST" and u == "https://api.openfigi.com/v3/mapping" for m, u, _ in seen)
        assert [r.matches[0].ticker for r in out][:2] == ["TV0", "TV1"] and len(out) == 23

    def test_429_surfaces_with_reset_hint(self):
        host = FakeHost(lambda r: ok("", 429, {"ratelimit-reset": "17"}))
        with pytest.raises(RateLimited) as ei:
            map_identifiers(Fetcher(host), [{"idType": "ID_ISIN", "idValue": "X"}])
        assert ei.value.retry_after_s == 17 and len(host.requests) == 1  # no blind retry
