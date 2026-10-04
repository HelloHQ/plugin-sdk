"""Hand-written response fixtures, built from the vendors' DOCUMENTED shapes.

* ECB Data Portal (SDMX-JSON 1.0 data message)
    https://data.ecb.europa.eu/help/api/data
    https://github.com/sdmx-twg/sdmx-json/tree/master/data-message/docs
* World Bank Indicators API v2
    https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation
  (the docs describe the two-element array; the per-observation field list and
  the error body are NOT on that page - see the README "Unverified response
  shapes"; those parts mirror what was observed once during development.)
* US Treasury Fiscal Data   https://fiscaldata.treasury.gov/api-documentation/
* data.gov.sg datastore     https://data.gov.sg/datasets/d_cdd73fd4341b345fa4307e44d6f82175/view
  (wide layout per the dataset page; the JSON envelope is the CKAN-style
  datastore_search reply, UNVERIFIED against documentation - see README).

All numbers are synthetic round values chosen for tests, not market data.
"""

from __future__ import annotations

import json


def ecb_message(
    periods: list[str],
    values: list[float | None],
    *,
    unit_id: str = "PCPA",
    unit_name: str | None = "Percent per annum",
    mult_id: str | None = "0",
    title: str = "Test series",
    freq_name: str = "Daily",
    extra_series: bool = False,
    drop_unit: bool = False,
) -> str:
    attrs_def = []
    series_attrs = []
    if not drop_unit:
        unit = {"id": unit_id}
        if unit_name is not None:
            unit["name"] = unit_name
        attrs_def.append({"id": "UNIT", "name": "Unit", "values": [unit]})
        series_attrs.append(0)
    if mult_id is not None:
        attrs_def.append({"id": "UNIT_MULT", "name": "Unit multiplier", "values": [{"id": mult_id, "name": "Units"}]})
        series_attrs.append(0)
    attrs_def.append({"id": "TITLE", "name": "Title", "values": [{"name": title}]})
    series_attrs.append(0)
    series = {
        "0:0:0:0:0": {
            "attributes": series_attrs,
            "observations": {str(i): [v, 0, 0, None, None] for i, v in enumerate(values)},
        }
    }
    if extra_series:
        series["0:0:0:0:1"] = series["0:0:0:0:0"]
    message = {
        "header": {"id": "test", "test": True, "prepared": "2026-10-02T15:58:15.133+02:00", "sender": {"id": "ECB"}},
        "dataSets": [{"action": "Replace", "validFrom": "2026-10-02T15:58:15.133+02:00", "series": series}],
        "structure": {
            "name": "Test",
            "dimensions": {
                "series": [{"id": "FREQ", "name": "Frequency", "values": [{"id": "D", "name": freq_name}]}],
                "observation": [
                    {
                        "id": "TIME_PERIOD",
                        "name": "Time period or range",
                        "role": "time",
                        "values": [{"id": p, "name": p} for p in periods],
                    }
                ],
            },
            "attributes": {
                "series": attrs_def,
                "observation": [
                    {"id": "OBS_STATUS", "name": "Observation status", "values": [{"id": "A", "name": "Normal value"}]}
                ],
            },
        },
    }
    return json.dumps(message)


ECB_EMPTY = json.dumps({"dataSets": [{"series": {}}], "structure": {}})


def wb_row(date: str, value, *, indicator="FP.CPI.TOTL.ZG", title="Inflation, consumer prices (annual %)", iso3="SGP"):
    return {
        "indicator": {"id": indicator, "value": title},
        "country": {"id": "XX", "value": "Testland"},
        "countryiso3code": iso3,
        "date": date,
        "value": value,
        "unit": "",
        "obs_status": "",
        "decimal": 1,
    }


def wb_message(rows, *, pages=1) -> str:
    return json.dumps(
        [
            {
                "page": 1,
                "pages": pages,
                "per_page": 50,
                "total": len(rows),
                "sourceid": "2",
                "lastupdated": "2026-07-13",
            },
            rows,
        ]
    )


WB_ERROR = '[{"message":[{"id":"120","key":"Invalid value","value":"The provided parameter value is not valid"}]}]'


def treasury_message(rows, *, data_type="PERCENTAGE", pages=1) -> str:
    return json.dumps(
        {
            "data": rows,
            "meta": {
                "count": len(rows),
                "labels": {"avg_interest_rate_amt": "Average Interest Rate Amount"},
                "dataTypes": {"record_date": "DATE", "avg_interest_rate_amt": data_type},
                "dataFormats": {"record_date": "YYYY-MM-DD", "avg_interest_rate_amt": "10.2%"},
                "total-count": len(rows),
                "total-pages": pages,
            },
            "links": {"self": "&page%5Bnumber%5D=1&page%5Bsize%5D=100", "next": None},
        }
    )


def treasury_row(date, desc, rate, kind="Marketable"):
    return {"record_date": date, "security_type_desc": kind, "security_desc": desc, "avg_interest_rate_amt": rate}


def sgfx_message(rows, *, success=True, total=None) -> str:
    return json.dumps(
        {
            "success": success,
            "result": {
                "resource_id": "d_cdd73fd4341b345fa4307e44d6f82175",
                "fields": [{"type": "text", "id": "DataSeries"}],
                "records": rows,
                "total": len(rows) if total is None else total,
                "limit": 100,
            },
        }
    )


def sgfx_row(name, **months):
    return {"_id": 1, "DataSeries": name, **months}
