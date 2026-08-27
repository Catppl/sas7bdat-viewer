"""Bounded-memory sequential reader for SAS XPORT V5 and V8 files."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, Self

from .domain import VariableMetadata


def _decode_xpt_text(value: object) -> str:
    """Decode XPORT metadata losslessly without assuming UTF-8."""
    if isinstance(value, bytes):
        return value.decode("latin-1", errors="replace").strip()
    return str(value or "").strip()


def _import_xport_reader():
    try:
        from pandas.io.sas import sas_xport
    except ImportError as error:
        raise RuntimeError(
            "pandas is not installed. Install desktop/Windows dependencies "
            "before opening an XPT dataset."
        ) from error
    return sas_xport


def _compatible_reader_type(sas_xport: Any):
    """Return pandas' sequential reader with the small V8 header extension.

    pandas' public ``read_sas(..., iterator=True)`` path is backed by this
    reader, but its header validation currently accepts only the V5 markers.
    V8 uses the same observation layout for the fields needed here; keeping
    this compatibility shim in this module prevents pandas internals leaking
    into the application reader.
    """

    class CompatibleXportReader(sas_xport.XportReader):
        def _read_header(self) -> None:
            self.filepath_or_buffer.seek(0)
            line1 = self._get_row()
            if not line1.startswith("HEADER RECORD*******LIBV8"):
                super()._read_header()
                return

            line2 = self._get_row()
            file_fields = [
                ["prefix", 24],
                ["version", 8],
                ["OS", 8],
                ["_", 24],
                ["created", 16],
            ]
            file_info = sas_xport._split_line(line2, file_fields)
            if file_info["prefix"] != "SAS     SAS     SASLIB":
                raise ValueError("Header record has invalid prefix.")
            file_info["created"] = sas_xport._parse_date(file_info["created"])
            file_info["modified"] = sas_xport._parse_date(self._get_row()[:16])
            self.file_info = file_info

            header1 = self._get_row()
            header2 = self._get_row()
            if not (
                header1.startswith("HEADER RECORD*******MEMBV8")
                and header2.startswith("HEADER RECORD*******DSCPTV8")
            ):
                raise ValueError("V8 member header not found.")
            field_name_length = int(header1[-5:-2])

            member_line = self._get_row()
            member_detail = self._get_row()
            self.member_info = {
                "set_name": member_line[8:40].strip(),
                "label": member_detail[32:72].strip(),
            }

            field_count = int(self._get_row()[54:58])
            data_length = field_name_length * field_count
            if data_length % 80:
                data_length += 80 - data_length % 80
            field_data = self.filepath_or_buffer.read(data_length)
            types = {1: "numeric", 2: "char"}
            fields = []
            observation_length = 0
            while len(field_data) >= field_name_length:
                field_bytes, field_data = (
                    field_data[:field_name_length],
                    field_data[field_name_length:],
                )
                field_bytes = field_bytes.ljust(140)
                unpacked = struct.unpack(">hhhh8s40s8shhh2s8shhl52s", field_bytes)
                field = dict(zip(sas_xport._fieldkeys, unpacked, strict=True))
                del field["_"]
                try:
                    field["ntype"] = types[field["ntype"]]
                except KeyError as error:
                    raise ValueError("V8 XPT field has an unsupported type.") from error
                length = field["field_length"]
                if field["ntype"] == "numeric" and not 2 <= length <= 8:
                    raise TypeError(
                        f"Floating field width {length} is not between 2 and 8."
                    )
                for key, value in field.items():
                    if isinstance(value, bytes):
                        field[key] = value.strip()
                observation_length += length
                fields.append(field)

            header = self._get_row()
            if not header.startswith("HEADER RECORD*******OBSV8"):
                raise ValueError("V8 observation header not found.")
            self.fields = fields
            self.record_length = observation_length
            self.record_start = self.filepath_or_buffer.tell()
            self.nobs = self._record_count()
            self.columns = [_decode_xpt_text(field["name"]) for field in fields]
            self._dtype = sas_xport.np.dtype(
                [
                    (f"s{index}", f"S{field['field_length']}")
                    for index, field in enumerate(fields)
                ]
            )

    return CompatibleXportReader


class XptSequentialReader:
    """Read one XPT file from start to finish without random row offsets."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._reader: Any | None = None
        self._variables: tuple[VariableMetadata, ...] = ()
        self._total_rows: int | None = None

    def __enter__(self) -> Self:
        sas_xport = _import_xport_reader()
        reader_type = _compatible_reader_type(sas_xport)
        try:
            # Latin-1 is a lossless one-byte decoding for XPORT character
            # fields. It avoids exposing pandas' raw bytes in the Viewer.
            self._reader = reader_type(
                str(self.path), encoding="latin-1", chunksize=None
            )
        except Exception as error:
            raise ValueError(f"Could not read XPT file: {error}") from error
        self._variables = self._metadata_from_reader(self._reader)
        # pandas uses trailing spaces to estimate observations for short XPT
        # records. Character data commonly ends with spaces, so calculate a
        # more accurate count from the fixed-width body without materializing
        # the file. This only scans the small trailing-padding region.
        if self._reader.record_length < 80:
            self._reader.nobs = self._short_record_count(self._reader)
        self._total_rows = int(self._reader.nobs)
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()

    @property
    def variables(self) -> tuple[VariableMetadata, ...]:
        self._require_open()
        return self._variables

    @property
    def total_rows(self) -> int | None:
        self._require_open()
        return self._total_rows

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(variable.name for variable in self.variables)

    def read_chunk(self, rows: int):
        self._require_open()
        if rows < 1:
            raise ValueError("XPT chunk size must be positive.")
        try:
            frame = self._reader.read(rows)
        except StopIteration:
            return None
        return frame.reindex(columns=list(self.column_names))

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    @staticmethod
    def _metadata_from_reader(reader: Any) -> tuple[VariableMetadata, ...]:
        variables: list[VariableMetadata] = []
        for field in reader.fields:
            name = _decode_xpt_text(field.get("name"))
            if not name:
                raise ValueError("The XPT file contains a variable without a name.")
            raw_format = _decode_xpt_text(field.get("nform"))
            format_width = field.get("nfl")
            decimals = field.get("nfj")
            if raw_format and format_width:
                raw_format = f"{raw_format}{format_width}.{decimals or 0}"
            variables.append(
                VariableMetadata(
                    name=name,
                    label=_decode_xpt_text(field.get("label")),
                    kind=("character" if field.get("ntype") == "char" else "numeric"),
                    length=int(field.get("field_length") or 0) or None,
                    format=raw_format,
                )
            )
        if not variables:
            raise ValueError("The dataset does not contain any variables.")
        return tuple(variables)

    def _short_record_count(self, reader: Any) -> int:
        body_length = self.path.stat().st_size - reader.record_start
        if body_length <= 0:
            return 0
        trailing_spaces = 0
        position = self.path.stat().st_size
        with self.path.open("rb") as source:
            while position > reader.record_start:
                size = min(64 * 1024, position - reader.record_start)
                position -= size
                source.seek(position)
                block = source.read(size)
                stripped = block.rstrip(b" ")
                trailing_spaces += len(block) - len(stripped)
                if stripped:
                    break
        data_length = body_length - trailing_spaces
        return (data_length + reader.record_length - 1) // reader.record_length

    def _require_open(self) -> None:
        if self._reader is None:
            raise RuntimeError("The XPT reader is closed.")
