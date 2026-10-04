"""ECB Data Portal (SDMX 2.1 RESTful API, ``format=jsondata``).

Documented / specified shapes relied upon:

* Query: ``https://data-api.ecb.europa.eu/service/data/{flowRef}/{key}`` with
  ``lastNObservations`` and ``format=jsondata`` -
  https://data.ecb.europa.eu/help/api/data (and the SDMX 2.1 REST spec).
  No API key; the ECB publishes no numeric rate limit.
* Response: SDMX-JSON 1.0 data message -
  https://github.com/sdmx-twg/sdmx-json/tree/master/data-message/docs :
  ``dataSets[0].series["<idx>:<idx>:..."].observations{"<i>": [value, ...]}``,
  ``structure.dimensions.observation[0]`` (``TIME_PERIOD``) giving the period
  id for each ``<i>``, and ``structure.attributes.series[]`` with the series'
  ``attributes`` array holding an index into each attribute's ``values``.

Strictness: exactly one series must come back; ``UNIT`` is required;
observations with a null value are skipped (and counted nowhere else - the
series simply has no point for that period); an all-null series is
``no_data``. Unknown fields are ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from macro_context.errors import ParseError
from macro_context.money import loads_decimal, require_int, to_decimal_string
from macro_context.series import Observation

ECB_HOST = "data-api.ecb.europa.eu"
_KEY = re.compile(r"^[A-Za-z0-9_.+-]{1,120}$")
_FLOW = re.compile(r"^[A-Za-z0-9_]{1,20}$")


@dataclass(frozen=True)
class EcbSeriesSpec:
    id: str  # our id, e.g. "ecb.policy.mro"
    flow: str
    key: str
    title: str
    category: str
    last_n: int
    unit_note: str | None = None


# Keys below were checked to resolve against the live API when this plugin was
# written (2026-10-04); they follow the ECB dataflow dimension order
# documented on the portal (FM, EXR, ICP, YC).
CATALOG: tuple[EcbSeriesSpec, ...] = (
    EcbSeriesSpec(
        "ecb.policy.mro",
        "FM",
        "D.U2.EUR.4F.KR.MRR_FR.LEV",
        "ECB main refinancing operations rate (fixed rate)",
        "policy_rate",
        1,
    ),
    EcbSeriesSpec("ecb.policy.dfr", "FM", "D.U2.EUR.4F.KR.DFR.LEV", "ECB deposit facility rate", "policy_rate", 1),
    EcbSeriesSpec(
        "ecb.fx.usd",
        "EXR",
        "D.USD.EUR.SP00.A",
        "US dollar per euro (ECB reference rate)",
        "fx",
        5,
        "units of USD per 1 EUR",
    ),
    EcbSeriesSpec(
        "ecb.fx.jpy",
        "EXR",
        "D.JPY.EUR.SP00.A",
        "Japanese yen per euro (ECB reference rate)",
        "fx",
        5,
        "units of JPY per 1 EUR",
    ),
    EcbSeriesSpec(
        "ecb.fx.sgd",
        "EXR",
        "D.SGD.EUR.SP00.A",
        "Singapore dollar per euro (ECB reference rate)",
        "fx",
        5,
        "units of SGD per 1 EUR",
    ),
    EcbSeriesSpec(
        "ecb.fx.gbp",
        "EXR",
        "D.GBP.EUR.SP00.A",
        "Pound sterling per euro (ECB reference rate)",
        "fx",
        5,
        "units of GBP per 1 EUR",
    ),
    EcbSeriesSpec(
        "ecb.fx.chf",
        "EXR",
        "D.CHF.EUR.SP00.A",
        "Swiss franc per euro (ECB reference rate)",
        "fx",
        5,
        "units of CHF per 1 EUR",
    ),
    EcbSeriesSpec(
        "ecb.inflation.hicp",
        "ICP",
        "M.U2.N.000000.4.ANR",
        "Euro area HICP, overall index, annual rate of change",
        "inflation",
        12,
    ),
    EcbSeriesSpec(
        "ecb.yield.aaa10y",
        "YC",
        "B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y",
        "Euro area AAA government 10-year spot rate (yield curve)",
        "yield",
        5,
    ),
)
CATALOG_BY_ID = {s.id: s for s in CATALOG}


def request_path(spec: EcbSeriesSpec) -> str:
    if not _FLOW.match(spec.flow) or not _KEY.match(spec.key):
        raise ValueError("invalid ECB flow/key")
    return f"/service/data/{spec.flow}/{spec.key}?lastNObservations={spec.last_n}&format=jsondata"


def url_for(spec: EcbSeriesSpec) -> str:
    return f"https://{ECB_HOST}{request_path(spec)}"


@dataclass(frozen=True)
class ParsedEcb:
    observations: tuple[Observation, ...]
    unit_code: str
    unit_name: str | None
    unit_multiplier: int
    frequency: str | None
    source_title: str | None


def _attr_value(attrs_def: list[Any], series_attrs: list[Any], attr_id: str) -> dict[str, Any] | None:
    for position, definition in enumerate(attrs_def):
        if isinstance(definition, dict) and definition.get("id") == attr_id:
            if position >= len(series_attrs):
                return None
            index = series_attrs[position]
            values = definition.get("values")
            if index is None or not isinstance(values, list):
                return None
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(values):
                raise ParseError(f"attribute {attr_id}: index out of range")
            value = values[index]
            return value if isinstance(value, dict) else None
    return None


def parse_response(text: str) -> ParsedEcb:
    data = loads_decimal(text)
    if not isinstance(data, dict):
        raise ParseError("ecb response: expected an object")
    datasets = data.get("dataSets")
    if not isinstance(datasets, list) or len(datasets) != 1 or not isinstance(datasets[0], dict):
        raise ParseError("ecb response: expected exactly one dataSet")
    series_map = datasets[0].get("series")
    if not isinstance(series_map, dict):
        raise ParseError("ecb response: missing series")
    if not series_map:
        raise ParseError("ecb response: the query matched no series", code="no_data")
    if len(series_map) != 1:
        raise ParseError("ecb response: expected exactly one series")
    (series,) = series_map.values()
    if not isinstance(series, dict):
        raise ParseError("ecb response: malformed series")

    structure = data.get("structure")
    if not isinstance(structure, dict):
        raise ParseError("ecb response: missing structure")
    dims = structure.get("dimensions")
    obs_dims = dims.get("observation") if isinstance(dims, dict) else None
    if not isinstance(obs_dims, list) or not obs_dims or not isinstance(obs_dims[0], dict):
        raise ParseError("ecb response: missing observation dimension")
    if obs_dims[0].get("id") != "TIME_PERIOD":
        raise ParseError("ecb response: first observation dimension is not TIME_PERIOD")
    periods = obs_dims[0].get("values")
    if not isinstance(periods, list):
        raise ParseError("ecb response: missing time periods")

    observations = series.get("observations")
    if not isinstance(observations, dict):
        raise ParseError("ecb response: missing observations")
    points: list[tuple[int, Observation]] = []
    for key, row in observations.items():
        if not key.isdigit() or not isinstance(row, list) or not row:
            raise ParseError("ecb response: malformed observation")
        index = int(key)
        if (
            index >= len(periods)
            or not isinstance(periods[index], dict)
            or not isinstance(periods[index].get("id"), str)
        ):
            raise ParseError("ecb response: observation without a time period")
        if row[0] is None:
            continue
        points.append(
            (index, Observation(periods[index]["id"], to_decimal_string(row[0], "observation value", allow_str=False)))
        )
    if not points:
        raise ParseError("ecb response: all observations are empty", code="no_data")
    points.sort(key=lambda p: p[1].period)

    attrs_def = structure.get("attributes", {}).get("series") if isinstance(structure.get("attributes"), dict) else None
    series_attrs = series.get("attributes")
    if not isinstance(attrs_def, list) or not isinstance(series_attrs, list):
        raise ParseError("ecb response: missing series attributes")
    unit = _attr_value(attrs_def, series_attrs, "UNIT")
    if unit is None or not isinstance(unit.get("id"), str):
        raise ParseError("ecb response: series has no UNIT attribute")
    mult = _attr_value(attrs_def, series_attrs, "UNIT_MULT")
    multiplier = 1
    if mult is not None:
        exponent = require_int(
            int(mult["id"]) if isinstance(mult.get("id"), str) and mult["id"].isdigit() else None, "UNIT_MULT"
        )
        if exponent > 12:
            raise ParseError("ecb response: unit multiplier out of range")
        multiplier = 10**exponent
    title = _attr_value(attrs_def, series_attrs, "TITLE")
    freq = None
    series_dims = dims.get("series") if isinstance(dims, dict) else None
    if isinstance(series_dims, list):
        for dim in series_dims:
            if (
                isinstance(dim, dict)
                and dim.get("id") == "FREQ"
                and isinstance(dim.get("values"), list)
                and dim["values"]
            ):
                first = dim["values"][0]
                freq = first.get("name") if isinstance(first, dict) and isinstance(first.get("name"), str) else None
    return ParsedEcb(
        observations=tuple(o for _, o in points),
        unit_code=unit["id"],
        unit_name=unit.get("name") if isinstance(unit.get("name"), str) else None,
        unit_multiplier=multiplier,
        frequency=freq,
        source_title=title.get("name") if title and isinstance(title.get("name"), str) else None,
    )
