import dataclasses
import json
from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest
from fakes import FakeHost, ok
from fixtures import (
    DVF_HEADER,
    IE_HEADER,
    UK_CSV_HEADER,
    dvf_row,
    ie_row,
    sg_page,
    sg_record,
    uk_csv_row,
    uk_item,
    uk_page,
)

from property_estimates.errors import (
    HttpError,
    InputTooLarge,
    OriginNotAllowed,
    ParseError,
    RateLimited,
    SourceUnavailable,
    ValidationError,
)
from property_estimates.estimate import Sale
from property_estimates.fr_dvf import fr_build_url, fr_collect, fr_validate_params, parse_dvf_csv
from property_estimates.ie_ppr import decode_csv_bytes, ie_validate_params, parse_ppr_csv
from property_estimates.sg_hdb import (
    SG_MAX_PAGES,
    sg_build_url,
    sg_collect,
    sg_parse_page,
    sg_record_to_sale,
    sg_validate_params,
)
from property_estimates.transport import Fetcher, origin_of, window_start
from property_estimates.uk_ppd import (
    parse_api_page,
    parse_ppd_csv,
    uk_build_url,
    uk_collect,
    uk_validate_params,
)

AS_OF = date(2026, 10, 4)
SINCE = date(2025, 10, 1)


# ---------------------------------------------------------------- transport
class TestTransport:
    def test_window_start(self):
        assert window_start(date(2026, 10, 4), 12) == date(2025, 10, 1)
        assert window_start(date(2026, 1, 31), 1) == date(2025, 12, 1)
        assert window_start(date(2026, 3, 1), 14) == date(2025, 1, 1)

    @pytest.mark.parametrize(
        "url",
        [
            "http://data.gov.sg/x",
            "https://evil.example/x",
            "https://data.gov.sg.evil.example/x",
            "https://user:pw@data.gov.sg/x",
            "https://data.gov.sg:8443/x",
            "ftp://data.gov.sg/x",
        ],
    )
    def test_origin_rejections(self, url):
        with pytest.raises(OriginNotAllowed):
            origin_of(url)

    def test_status_mapping(self):
        url = "https://data.gov.sg/a"
        for status, exc in [(429, RateLimited), (500, HttpError), (404, HttpError)]:
            f = Fetcher(FakeHost({url: ok("", status)}))
            with pytest.raises(exc):
                f.get(url, accept="x")

    def test_redirect_followed_only_to_declared_origin(self):
        a = "https://files.data.gouv.fr/geo-dvf/latest/csv/2025/communes/75/75101.csv"
        b = "https://geo-dvf.s3.sbg.io.cloud.ovh.net/latest/csv/2025/communes/75/75101.csv"
        host = FakeHost(
            {
                a: ok("", 302, {"Location": b.replace(".net/", ".net:443/")}),
                b: ok("data"),
            }
        )
        assert Fetcher(host).get(a, accept="text/csv").body == "data"
        assert [r.url for r in host.requests] == [a, b]  # :443 normalised away
        evil = FakeHost({a: ok("", 302, {"location": "https://evil.example/x"})})
        with pytest.raises(OriginNotAllowed):
            Fetcher(evil).get(a, accept="text/csv")

    def test_redirect_loop_is_bounded(self):
        a = "https://data.gov.sg/a"
        host = FakeHost({a: ok("", 302, {"Location": a})})
        with pytest.raises(HttpError):
            Fetcher(host).get(a, accept="x")
        assert len(host.requests) == 2

    def test_redirect_without_location(self):
        a = "https://data.gov.sg/a"
        with pytest.raises(HttpError):
            Fetcher(FakeHost({a: ok("", 302)})).get(a, accept="x")

    def test_rate_limit_applies_across_calls_on_fake_clock(self):
        url = "https://data.gov.sg/a"
        host = FakeHost({url: ok("{}")})
        f = Fetcher(host)
        for _ in range(5):
            f.get(url, accept="x")
        assert host.slept == pytest.approx(10.5)  # 5th call waited for the 4/10.5 s window
        assert f.requests_made == 5

    def test_oversize_body_rejected(self, monkeypatch):
        import property_estimates.transport as t

        monkeypatch.setattr(t, "MAX_BODY_CHARS", 10)
        url = "https://data.gov.sg/a"
        with pytest.raises(HttpError):
            Fetcher(FakeHost({url: ok("x" * 11)})).get(url, accept="x")


# ---------------------------------------------------------------- Singapore
class TestSG:
    def test_validate(self):
        assert sg_validate_params({"town": " bishan ", "flat_type": "4 room"}) == {
            "town": "BISHAN",
            "flat_type": "4 ROOM",
            "months": 12,
        }
        for bad in [
            {"town": "", "flat_type": "4 ROOM"},
            {"town": "BISHAN", "flat_type": "9 ROOM"},
            {"town": "B1SHAN", "flat_type": "4 ROOM"},
            {"town": "BISHAN", "flat_type": "4 ROOM", "months": 0},
            {"town": "BISHAN", "flat_type": "4 ROOM", "months": True},
            {"town": "BISHAN", "flat_type": "4 ROOM", "months": 61},
        ]:
            with pytest.raises(ValidationError):
                sg_validate_params(bad)

    def test_url_is_documented_endpoint(self):
        p = sg_validate_params({"town": "BISHAN", "flat_type": "4 ROOM"})
        url = sg_build_url(p, offset=100)
        parts = urlsplit(url)
        q = parse_qs(parts.query)
        assert (parts.scheme, parts.netloc, parts.path) == (
            "https",
            "data.gov.sg",
            "/api/action/datastore_search",
        )
        assert q["resource_id"] == ["d_8b84c4ee58e3cfc0ece0d773c8ca6abc"]
        assert q["limit"] == ["100"] and q["offset"] == ["100"] and q["sort"] == ["month desc"]
        assert json.loads(q["filters"][0]) == {"flat_type": "4 ROOM", "town": "BISHAN"}

    def test_parse_valid_and_empty(self):
        recs, total = sg_parse_page(sg_page([sg_record("2026-08", 600000)], total=57))
        assert len(recs) == 1 and total == 57
        assert sg_parse_page(sg_page([])) == ([], 0)

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "not json",
            "[]",
            json.dumps({"success": False}),
            json.dumps({"success": True}),
            json.dumps({"success": True, "result": {"records": "x"}}),
        ],
    )
    def test_parse_invalid(self, text):
        with pytest.raises(ParseError):
            sg_parse_page(text)

    def test_record_conversion(self):
        sale, why = sg_record_to_sale(sg_record("2026-08", "600000", "93.5"), since=SINCE)
        assert why is None and sale.price_minor == 60_000_000 and str(sale.area_sqm) == "93.5"
        assert sg_record_to_sale(sg_record("2024-01", 1), since=SINCE)[1] == "outside_window"
        assert sg_record_to_sale(sg_record("bad", 1), since=SINCE)[1] == "bad_month"
        assert sg_record_to_sale(sg_record("2026-08", "abc"), since=SINCE)[1] == "bad_number"
        assert sg_record_to_sale(sg_record("2026-08", "0"), since=SINCE)[1] == "bad_price"
        sale, _ = sg_record_to_sale(sg_record("2026-08", 5, area="0"), since=SINCE)
        assert sale.area_sqm is None

    def test_missing_field_is_an_error_not_a_guess(self):
        rec = sg_record("2026-08", 5)
        del rec["resale_price"]
        with pytest.raises(ParseError):
            sg_record_to_sale(rec, since=SINCE)
        with pytest.raises(ParseError):
            sg_record_to_sale("nope", since=SINCE)

    def test_model_holds_no_address_fields(self):
        assert {f.name for f in dataclasses.fields(Sale)} == {"sold_on", "price_minor", "area_sqm"}

    def test_collect_paginates_until_window_ends(self):
        p = sg_validate_params({"town": "BISHAN", "flat_type": "4 ROOM", "months": 12})
        page1 = sg_page([sg_record("2026-08", 600000 + i) for i in range(100)], total=250)
        page2 = sg_page(
            [sg_record("2025-11", 500000 + i) for i in range(50)]
            + [sg_record("2024-03", 1) for _ in range(50)],
            total=250,
        )
        urls = []

        def route(url):
            urls.append(url)
            return ok(page1 if "offset=0" in url else page2)

        host = FakeHost(route)
        sales, skipped, refs = sg_collect(Fetcher(host), p, as_of=AS_OF)
        assert len(sales) == 150 and len(refs) == 2 and len(urls) == 2
        assert skipped == {}

    def test_collect_is_bounded_even_for_endless_data(self):
        p = sg_validate_params({"town": "BISHAN", "flat_type": "4 ROOM"})
        page = sg_page([sg_record("2026-08", 600000) for _ in range(100)], total=10**9)
        host = FakeHost(lambda url: ok(page))
        sales, _, refs = sg_collect(Fetcher(host), p, as_of=AS_OF)
        assert len(refs) == SG_MAX_PAGES == len(host.requests)
        assert len(sales) == 100 * SG_MAX_PAGES
        # 10 requests at 4 per 10.5 s need at least two full windows of waiting
        assert host.slept >= 21.0

    def test_bad_rows_are_skipped_and_counted(self):
        p = sg_validate_params({"town": "BISHAN", "flat_type": "4 ROOM"})
        page = sg_page([sg_record("2026-08", "x"), sg_record("2026-08", 5)])
        sales, skipped, _ = sg_collect(Fetcher(FakeHost(lambda u: ok(page))), p, as_of=AS_OF)
        assert len(sales) == 1 and skipped == {"bad_number": 1}


# ---------------------------------------------------------------- UK
class TestUK:
    def test_validate(self):
        assert uk_validate_params({"district": "testdistrict", "property_type": "t"}) == {
            "district": "TESTDISTRICT",
            "town": "",
            "property_type": "T",
            "months": 12,
        }
        for bad in [
            {"district": ""},
            {"district": "X1"},
            {"district": "LEEDS", "property_type": "Z"},
            {"district": "LEEDS", "town": "1"},
            {"district": "LEEDS", "months": 100},
        ]:
            with pytest.raises(ValidationError):
                uk_validate_params(bad)

    def test_url(self):
        p = uk_validate_params({"district": "LEEDS", "property_type": "F", "town": "LEEDS"})
        url = uk_build_url(p, since=SINCE, page=2)
        q = parse_qs(urlsplit(url).query)
        assert urlsplit(url).netloc == "landregistry.data.gov.uk"
        assert q["propertyAddress.district"] == ["LEEDS"]
        assert q["propertyType"] == ["http://landregistry.data.gov.uk/def/common/flat-maisonette"]
        assert q["min-transactionDate"] == ["2025-10-01"]
        assert (
            q["_page"] == ["2"] and q["_pageSize"] == ["200"] and q["_sort"] == ["-transactionDate"]
        )

    def test_parse_api_page(self):
        text = uk_page(
            [
                uk_item(250_000),
                uk_item(1, category="additionalPricePaidTransaction"),
                uk_item(2, status="delete"),
                uk_item(3, date_s="31 Dec 2020"),
                uk_item(4, date_s="Fri, 17 May 1996"),
                uk_item(0),
            ],
            has_next=True,
        )
        sales, skipped, nxt = parse_api_page(text, since=SINCE)
        assert [s.price_minor for s in sales] == [25_000_000]
        assert skipped == {
            "non_standard_category": 1,
            "deleted_record": 1,
            "bad_date": 1,
            "outside_window": 1,
            "bad_price": 1,
        }
        assert nxt is True

    @pytest.mark.parametrize(
        "text",
        ["", "{}", json.dumps({"result": {}}), json.dumps({"result": {"items": [1]}}),
         json.dumps({"result": {"items": [{"pricePaid": 1}]}})],
    )  # fmt: skip
    def test_parse_api_invalid(self, text):
        with pytest.raises(ParseError):
            parse_api_page(text, since=SINCE)

    def test_float_price_is_refused(self):
        item = uk_item(1)
        item["pricePaid"] = 250000.5
        text = json.dumps({"result": {"items": [item]}})
        sales, skipped, _ = parse_api_page(text, since=SINCE)
        assert sales == [] and skipped == {"bad_price": 1}

    def test_collect_follows_next(self):
        p = uk_validate_params({"district": "TESTDISTRICT"})
        pages = {
            "_page=0": uk_page([uk_item(300_000 + i) for i in range(5)], has_next=True),
            "_page=1": uk_page([uk_item(400_000 + i) for i in range(5)], has_next=False),
        }
        host = FakeHost(lambda url: ok(next(v for k, v in pages.items() if k in url)))
        sales, _, refs = uk_collect(Fetcher(host), p, as_of=AS_OF)
        assert len(sales) == 10 and len(refs) == 2

    def test_csv_without_header_and_filters(self):
        text = "\n".join(
            [
                uk_csv_row(300_000),
                uk_csv_row(310_000, ptype="F"),
                uk_csv_row(320_000, district="OTHER"),
                uk_csv_row(330_000, town="ELSEWHERE"),
                uk_csv_row(340_000, category="B"),
                uk_csv_row(350_000, status="D"),
                uk_csv_row(360_000, date_s="2019-01-01 00:00"),
                uk_csv_row(370_000, date_s="garbage"),
                "",
            ]
        )
        sales, skipped = parse_ppd_csv(
            text, district="testdistrict", town="TESTVILLE", property_type="t", since=SINCE
        )
        assert [s.price_minor for s in sales] == [30_000_000]
        assert skipped == {
            "non_standard_category": 1,
            "deleted_record": 1,
            "outside_window": 1,
            "bad_date": 1,
        }

    def test_csv_with_header_and_15_columns(self):
        text = UK_CSV_HEADER + "\n" + uk_csv_row(300_000, status=None) + "\n"
        sales, _ = parse_ppd_csv(text, since=SINCE)
        assert len(sales) == 1

    def test_csv_bad_inputs(self):
        assert parse_ppd_csv("", since=SINCE) == ([], {})
        with pytest.raises(ParseError):
            parse_ppd_csv('"a","b","c"\n', since=SINCE)
        with pytest.raises(InputTooLarge):
            parse_ppd_csv(uk_csv_row(1) * 10, since=SINCE, max_chars=50)
        sales, skipped = parse_ppd_csv(uk_csv_row(1).replace('"1","', '"x","', 1), since=SINCE)
        assert sales == [] and skipped == {"bad_price": 1}

    def test_csv_huge_input_is_handled(self):
        rows = "\n".join(uk_csv_row(200_000 + i % 1000) for i in range(60_000))
        sales, _ = parse_ppd_csv(rows, since=SINCE, district="TESTDISTRICT")
        assert len(sales) == 60_000
        assert not hasattr(sales[0], "postcode")


# ---------------------------------------------------------------- France
def _dvf(*rows):
    return DVF_HEADER + "\n" + "\n".join(rows) + "\n"


class TestFR:
    def test_validate(self):
        p = fr_validate_params({"commune": "75101", "property_type": "Apartment"}, as_of=AS_OF)
        assert p["years"] == [2024, 2025] and p["commune"] == "75101"
        assert fr_validate_params({"commune": "2a004", "property_type": "house"}, as_of=AS_OF)
        for bad in [
            {"commune": "7510", "property_type": "house"},
            {"commune": "75101", "property_type": "land"},
            {"commune": "75101", "property_type": "house", "years": []},
            {"commune": "75101", "property_type": "house", "years": [1999]},
            {"commune": "75101", "property_type": "house", "years": [True]},
            {"commune": "75101", "property_type": "house", "years": "2025"},
        ]:
            with pytest.raises(ValidationError):
                fr_validate_params(bad, as_of=AS_OF)

    def test_urls(self):
        base = "https://files.data.gouv.fr/geo-dvf/latest/csv"
        assert fr_build_url("75101", 2025) == f"{base}/2025/communes/75/75101.csv"
        assert fr_build_url("97105", 2024) == f"{base}/2024/communes/971/97105.csv"
        assert fr_build_url("2A004", 2024) == f"{base}/2024/communes/2A/2A004.csv"

    def test_single_dwelling_sales_only(self):
        text = _dvf(
            dvf_row("m1", "2026-03-01", "300000", "Appartement", "50"),
            dvf_row("m1", "2026-03-01", "300000", "Dependance", "0"),  # extra row, same sale
            dvf_row("m2", "2026-03-02", "500000", "Maison", "100"),  # other type
            dvf_row("m3", "2026-03-03", "900000", "Appartement", "50"),
            dvf_row("m3", "2026-03-03", "900000", "Appartement", "60"),  # two dwellings
            dvf_row("m4", "2026-03-04", "100000", "Local industriel. commercial", "30"),
            dvf_row("m5", "2026-03-05", "300000", "Appartement", "50", nature="Echange"),
            dvf_row("m6", "2026-03-06", "", "Appartement", "50"),
            dvf_row("m7", "2026-03-07", "300000", "Appartement", "0"),
            dvf_row("m8", "2019-03-07", "300000", "Appartement", "40"),
            dvf_row("m9", "bad", "300000", "Appartement", "40"),
            dvf_row("m10", "2026-04-07", "250000,50", "Appartement", "40"),
        )
        sales, skipped = parse_dvf_csv(text, property_type="apartment", since=SINCE)
        assert sorted(s.price_minor for s in sales) == [25_000_050, 30_000_000]
        assert skipped == {
            "other_property_type": 1,
            "not_a_single_dwelling": 2,
            "not_a_plain_sale": 1,
            "bad_price": 1,
            "no_floor_area": 1,
            "outside_window": 1,
            "bad_date": 1,
        }

    def test_ambiguous_price_in_one_mutation(self):
        text = _dvf(
            dvf_row("m1", "2026-03-01", "300000", "Appartement", "50"),
            dvf_row("m1", "2026-03-01", "310000", "Dependance", "0"),
        )
        sales, skipped = parse_dvf_csv(text, property_type="apartment", since=SINCE)
        assert sales == [] and skipped == {"ambiguous_price": 1}

    def test_missing_columns_and_empty(self):
        with pytest.raises(ParseError):
            parse_dvf_csv("id_mutation,date_mutation\nx,y\n", property_type="house", since=SINCE)
        with pytest.raises(ParseError):
            parse_dvf_csv("", property_type="house", since=SINCE)
        assert parse_dvf_csv(_dvf(), property_type="house", since=SINCE) == ([], {})
        with pytest.raises(InputTooLarge):
            parse_dvf_csv(_dvf("x" * 100), property_type="house", since=SINCE, max_chars=50)

    def test_output_never_contains_identifying_columns(self):
        text = _dvf(dvf_row("SECRET-MUTATION", "2026-03-01", "300000", "Appartement", "50"))
        sales, _ = parse_dvf_csv(text, property_type="apartment", since=SINCE)
        dumped = repr(sales)
        for needle in ("SECRET-MUTATION", "RUE DE TEST", "99999000AA0001"):
            assert needle not in dumped

    def test_collect_missing_year_is_tolerated_but_all_missing_is_error(self):
        p = fr_validate_params(
            {"commune": "75101", "property_type": "apartment", "years": [2024, 2025]}, as_of=AS_OF
        )
        good = _dvf(
            *[dvf_row(f"m{i}", "2026-03-01", "300000", "Appartement", "50") for i in range(3)]
        )
        host = FakeHost(lambda url: ok(good) if "/2025/" in url else ok("", 404))
        sales, skipped, refs = fr_collect(Fetcher(host), p, as_of=AS_OF)
        assert len(sales) == 3 and skipped["year_file_unavailable"] == 1 and len(refs) == 2
        with pytest.raises(SourceUnavailable):
            fr_collect(Fetcher(FakeHost(lambda u: ok("", 404))), p, as_of=AS_OF)
        with pytest.raises(HttpError):  # a 500 is not "not published"
            fr_collect(Fetcher(FakeHost(lambda u: ok("", 500))), p, as_of=AS_OF)


# ---------------------------------------------------------------- Ireland
def _ie(*rows):
    return IE_HEADER + "\n" + "\n".join(rows) + "\n"


class TestIE:
    def test_validate(self):
        assert ie_validate_params({"county": "Dublin", "csv_text": "x"})["county"] == "dublin"
        for bad in [
            {"county": "Atlantis", "csv_text": "x"},
            {"county": "Cork"},
            {"county": "Cork", "csv_text": "  "},
            {"county": "Cork", "csv_text": "x", "dwelling": "castle"},
            {"county": "Cork", "csv_text": "x", "months": 0},
        ]:
            with pytest.raises(ValidationError):
                ie_validate_params(bad)

    def test_parse_formats_and_filters(self):
        text = _ie(
            ie_row("15/03/2026", "€450,000.00"),
            ie_row("16/03/2026", "â‚¬350,000.00"),  # mojibake euro sign
            ie_row("17/03/2026", "300000"),
            ie_row("18/03/2026", "€1,000.00", nfmp="Yes"),
            ie_row("19/03/2026", "€400,000.00", county="Cork"),
            ie_row("20/03/2026", "€410,000.00", desc="New Dwelling house /Apartment"),
            ie_row("21/03/2019", "€410,000.00"),
            ie_row("not-a-date", "€410,000.00"),
            ie_row("22/03/2026", "free"),
            ie_row("23/03/2026", "€12,34.00"),
        )
        sales, skipped = parse_ppr_csv(text, county="dublin", since=SINCE)
        assert sorted(s.price_minor for s in sales) == [
            30_000_000,
            35_000_000,
            41_000_000,
            45_000_000,
        ]
        assert skipped == {
            "not_full_market_price": 1,
            "outside_window": 1,
            "bad_date": 1,
            "bad_price": 2,
        }
        new, _ = parse_ppr_csv(text, county="dublin", since=SINCE, dwelling="new")
        old, _ = parse_ppr_csv(text, county="dublin", since=SINCE, dwelling="second_hand")
        assert len(new) == 1 and len(old) == 3

    def test_bom_header_and_short_rows(self):
        text = "﻿" + _ie(ie_row("15/03/2026", "€450,000.00"), "15/03/2026,x")
        sales, skipped = parse_ppr_csv(text, county="dublin", since=SINCE)
        assert len(sales) == 1 and skipped == {"short_row": 1}

    def test_missing_required_columns_is_an_error(self):
        with pytest.raises(ParseError) as ei:
            parse_ppr_csv("Address,County\nx,Dublin\n", county="dublin", since=SINCE)
        assert "Date of Sale" in str(ei.value) and "Price" in str(ei.value)
        with pytest.raises(ParseError):
            parse_ppr_csv("", county="dublin", since=SINCE)
        assert parse_ppr_csv(IE_HEADER, county="dublin", since=SINCE) == ([], {})
        with pytest.raises(InputTooLarge):
            parse_ppr_csv(_ie("x" * 200), county="dublin", since=SINCE, max_chars=100)

    def test_decode_bytes(self):
        raw = "Price (€)".encode("cp1252")
        assert decode_csv_bytes(raw) == "Price (€)"
        assert decode_csv_bytes("﻿abc".encode()) == "abc"

    def test_address_is_not_retained(self):
        sales, _ = parse_ppr_csv(
            _ie(ie_row("15/03/2026", "€450,000.00")), county="dublin", since=SINCE
        )
        assert "Testtown" not in repr(sales)
