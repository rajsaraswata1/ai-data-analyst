"""Robust CSV ingestion: size/type validation, encoding fallback, delimiter sniffing, header repair,
duplicate-column handling and per-column type inference (recording invalid values for quality checks)."""
from __future__ import annotations

import csv
import io
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ..core.errors import ValidationError

NA_TOKENS = {"", "na", "n/a", "nan", "null", "none", "-", "--", "#n/a", "nil"}
BOOL_TOKENS = {"true", "false", "yes", "no", "t", "f"}
CURRENCY_RE = re.compile(r"[₹$€£,\s]")
CLEAN_RE = re.compile(r"[^0-9a-zA-Z]+")


@dataclass
class IngestReport:
    """Everything notable that happened while reading a file."""
    encoding: str = "utf-8"
    delimiter: str = ","
    warnings: list[str] = field(default_factory=list)
    renamed_columns: dict[str, str] = field(default_factory=dict)
    kinds: dict[str, str] = field(default_factory=dict)          # column -> inferred kind
    invalid_counts: dict[str, int] = field(default_factory=dict)  # column -> values that failed the inferred type
    skipped_rows: int = 0
    raw_duplicate_headers: list[str] = field(default_factory=list)


def clean_name(raw: object, idx: int) -> str:
    s = CLEAN_RE.sub("_", str(raw).strip()).strip("_").lower()
    if not s:
        return f"unnamed_{idx}"
    return f"c_{s}" if s[0].isdigit() else s


def table_name_from_filename(filename: str, taken: set[str]) -> str:
    stem = Path(filename).stem
    base = clean_name(stem, 0) if stem else "dataset"
    name, i = base, 2
    while name in taken:
        name, i = f"{base}_{i}", i + 1
    return name


def validate_upload(filename: str, raw: bytes, max_mb: int) -> None:
    if not filename or not filename.lower().endswith(".csv"):
        raise ValidationError("Only .csv files are supported.")
    if len(raw) == 0 or not raw.strip():
        raise ValidationError(f"'{filename}' is empty.")
    if len(raw) > max_mb * 1024 * 1024:
        raise ValidationError(f"'{filename}' exceeds the {max_mb} MB upload limit.")
    if b"\x00" in raw[:4096]:
        raise ValidationError(f"'{filename}' looks like a binary file, not a CSV.")


def _decode(raw: bytes, rep: IngestReport) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        rep.encoding = "cp1252"
        rep.warnings.append("File is not valid UTF-8; decoded as Windows-1252 (some characters may differ).")
        return raw.decode("cp1252", errors="replace")


def _sniff_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:20])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _is_numeric_like(v: str) -> bool:
    try:
        float(CURRENCY_RE.sub("", v))
        return True
    except ValueError:
        return False


def _read_table(text: str, delim: str, rep: IngestReport) -> pd.DataFrame:
    kwargs = dict(sep=delim, header=None, dtype=str, keep_default_na=False, skipinitialspace=True)
    try:
        return pd.read_csv(io.StringIO(text), **kwargs)
    except pd.errors.ParserError:
        skipped = []
        df = pd.read_csv(io.StringIO(text), engine="python",
                         on_bad_lines=lambda row: skipped.append(1) or None, **kwargs)
        rep.skipped_rows = len(skipped)
        rep.warnings.append(f"{len(skipped)} malformed row(s) with the wrong number of fields were skipped.")
        return df
    except pd.errors.EmptyDataError:
        raise ValidationError("The file has no readable data.")


def _fix_header(df: pd.DataFrame, rep: IngestReport) -> pd.DataFrame:
    first = [str(v).strip() for v in df.iloc[0]]
    non_empty = [v for v in first if v]
    headerless = bool(non_empty) and all(_is_numeric_like(v) for v in non_empty)
    if headerless:
        rep.warnings.append("No header row detected; generated column names col_1, col_2, ...")
        df.columns = [f"col_{i + 1}" for i in range(df.shape[1])]
        return df.reset_index(drop=True)
    seen: dict[str, int] = {}
    names: list[str] = []
    for i, h in enumerate(first):
        base = clean_name(h, i + 1)
        if h == "":
            rep.warnings.append(f"Column {i + 1} had no header; named '{base}'.")
        if base in seen:
            rep.raw_duplicate_headers.append(h)
            seen[base] += 1
            new = f"{base}_{seen[base]}"
        else:
            seen[base], new = 1, base
        if new != h:
            rep.renamed_columns[h] = new
        names.append(new)
    if rep.raw_duplicate_headers:
        rep.warnings.append(f"Duplicate column names were de-duplicated: {sorted(set(rep.raw_duplicate_headers))}.")
    out = df.iloc[1:].reset_index(drop=True)
    out.columns = names
    return out


def _infer_column(s: pd.Series) -> tuple[pd.Series, str, int]:
    """Return (typed series, kind, invalid_count). kind in empty|boolean|integer|float|datetime|text."""
    s = s.astype("object").where(s.notna(), None)
    stripped = s.map(lambda v: v.strip() if isinstance(v, str) else v)
    na_mask = stripped.map(lambda v: v is None or v.lower() in NA_TOKENS)
    values = stripped.mask(na_mask, None)
    present = values.dropna()
    if present.empty:
        return pd.Series(np.nan, index=s.index, dtype="float64"), "empty", 0

    lowered = set(present.str.lower().unique())
    if lowered <= BOOL_TOKENS:
        mapped = values.map(lambda v: None if v is None else v.lower() in {"true", "yes", "t"})
        return mapped.astype("object"), "boolean", 0

    leading_zero = present.str.match(r"^0\d+$").mean() > 0.05  # zip codes / padded ids stay text
    cleaned = present.str.replace(CURRENCY_RE, "", regex=True).str.replace(r"^\((.*)\)$", r"-\1", regex=True)
    nums = pd.to_numeric(cleaned, errors="coerce")
    if not leading_zero and nums.notna().mean() >= 0.9:
        full = pd.Series(np.nan, index=s.index, dtype="float64")
        full.loc[nums.index] = nums
        invalid = int(nums.isna().sum())
        valid = nums.dropna()
        if len(valid) and (valid % 1 == 0).all() and not full.isna().any():
            return full.astype("int64"), "integer", invalid
        return full, ("integer" if len(valid) and (valid % 1 == 0).all() else "float"), invalid

    looks_dateish = present.str.contains(r"[-/:]|[A-Za-z]{3}", regex=True).mean() > 0.8 and nums.notna().mean() < 0.5
    if looks_dateish:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            dt = pd.to_datetime(present, errors="coerce", format="ISO8601")
            if dt.notna().mean() < 0.9:
                dt = pd.to_datetime(present, errors="coerce")
        if dt.notna().mean() >= 0.9:
            full = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
            full.loc[dt.index] = dt.astype("datetime64[ns]")
            return full, "datetime", int(dt.isna().sum())

    return values.astype("object"), "text", 0


def parse_csv(raw: bytes, filename: str, max_rows: int) -> tuple[pd.DataFrame, IngestReport]:
    """Parse raw bytes into a typed DataFrame plus an IngestReport. Raises ValidationError for unusable files."""
    rep = IngestReport()
    text = _decode(raw, rep)
    rep.delimiter = _sniff_delimiter(text)
    try:
        table = _read_table(text, rep.delimiter, rep)
    except ValidationError:
        raise
    except Exception as exc:  # malformed beyond repair
        raise ValidationError(f"'{filename}' could not be parsed as CSV.", detail=repr(exc))
    table = table.dropna(how="all")
    if table.empty or table.shape[1] == 0:
        raise ValidationError(f"'{filename}' contains no data.")
    table = _fix_header(table, rep)
    if table.empty:
        raise ValidationError(f"'{filename}' has a header but no data rows.")
    if len(table) > max_rows:
        raise ValidationError(f"'{filename}' has {len(table):,} rows; the limit is {max_rows:,}.")
    out = {}
    for col in table.columns:
        typed, kind, invalid = _infer_column(table[col])
        out[col] = typed
        rep.kinds[col] = kind
        if invalid:
            rep.invalid_counts[col] = invalid
    df = pd.DataFrame(out)
    if df.shape[1] == 1 and rep.delimiter == ",":
        rep.warnings.append("Only one column detected - check that the file uses a standard delimiter.")
    return df, rep
