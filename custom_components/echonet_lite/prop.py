"""EPC codec wrapper types for the HEMS Echonet Lite integration."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
import logging
import math
from typing import Protocol

from pyhems import (
    BinaryCodec,
    EntityDefinition,
    EnumCodec,
    NodeState,
    NumericCodec,
    Property,
    TemporalCodec,
    get_codec,
    get_codec_for_epc,
)

from homeassistant.const import (
    PRECISION_HALVES,
    PRECISION_TENTHS,
    PRECISION_WHOLE,
    UnitOfTime,
)
from homeassistant.util import dt as dt_util

from .const import camel_to_snake

_LOGGER = logging.getLogger(__name__)


def _with_state_keys(codec: EnumCodec) -> EnumCodec:
    """Return ``codec`` with its pyhems keys converted to state keys."""
    by_key = {camel_to_snake(key): edt for key, edt in codec.by_key.items()}
    return EnumCodec(
        by_key=by_key,
        by_edt={edt: key for key, edt in by_key.items()},
        byte_offset=codec.byte_offset,
    )


class Prop[ValueT](Protocol):
    """Protocol for a single EPC and its codec, with helper methods to get the value from a node and create a Property for setting."""

    def get(self, node: NodeState) -> ValueT | None:
        """Return decoded value from coordinator state, or None if unavailable."""

    def make_property(self, value: ValueT) -> Property:
        """Create a Property instance for this EPC with the encoded value."""


@dataclass(frozen=True)
class BinaryProp:
    """EPC + BinaryCodec pair."""

    epc: int
    codec: BinaryCodec

    def get(self, node: NodeState) -> bool | None:
        """Return decoded value from coordinator state, or None if unavailable."""
        edt = node.properties.get(self.epc)
        return self.codec.decode(edt, node.properties) if edt is not None else None

    def make_property(self, value: bool) -> Property:
        """Create a Property instance for this EPC with the encoded value."""
        return Property(epc=self.epc, edt=self.codec.encode(value))

    @classmethod
    def from_registry(
        cls,
        class_code: int,
        epc: int,
    ) -> BinaryProp:
        """Build from pyhems definitions by EPC lookup, raising TypeError on type mismatch."""
        codec = get_codec_for_epc(class_code, epc)
        if not isinstance(codec, BinaryCodec):
            raise TypeError(
                f"EPC 0x{epc:02X} on class 0x{class_code:04X} "
                f"is not a BinaryCodec (got {type(codec).__name__})"
            )
        return cls(epc, codec)

    @classmethod
    def from_entity_def(
        cls,
        entity_def: EntityDefinition,
    ) -> BinaryProp:
        """Build from an EntityDefinition, raising TypeError if codec is not BinaryCodec."""
        codec = get_codec(entity_def)
        if not isinstance(codec, BinaryCodec):
            raise TypeError(
                f"EPC 0x{entity_def.epc:02X}: "
                f"expected BinaryCodec, got {type(codec).__name__}"
            )
        return cls(entity_def.epc, codec)


@dataclass(frozen=True)
class NumericProp:
    """EPC + NumericCodec pair."""

    epc: int
    codec: NumericCodec

    def get(self, node: NodeState) -> int | float | None:
        """Return decoded value from coordinator state, or None if unavailable."""
        edt = node.properties.get(self.epc)
        return self.codec.decode(edt, node.properties) if edt is not None else None

    def make_property(self, value: float) -> Property:
        """Create a Property instance for this EPC with the encoded value."""
        return Property(epc=self.epc, edt=self.codec.encode(value))

    @property
    def min_value(self) -> float | None:
        """Return minimum scaled value, or None if unbounded."""
        return (
            None
            if self.codec.minimum is None
            else self.codec.minimum * self.codec.scale
        )

    @property
    def max_value(self) -> float | None:
        """Return maximum scaled value, or None if unbounded."""
        return (
            None
            if self.codec.maximum is None
            else self.codec.maximum * self.codec.scale
        )

    @property
    def step(self) -> float:
        """Return the codec scale as the step size."""
        return self.codec.scale

    @property
    def precision(self) -> float:
        """Return the HA precision constant closest to the codec scale."""
        if self.codec.scale <= PRECISION_TENTHS:
            return PRECISION_TENTHS
        if self.codec.scale <= PRECISION_HALVES:
            return PRECISION_HALVES
        return PRECISION_WHOLE

    @classmethod
    def from_registry(
        cls,
        class_code: int,
        epc: int,
    ) -> NumericProp:
        """Build from pyhems definitions by EPC lookup, raising TypeError on type mismatch."""
        codec = get_codec_for_epc(class_code, epc)
        if not isinstance(codec, NumericCodec):
            raise TypeError(
                f"EPC 0x{epc:02X} on class 0x{class_code:04X} "
                f"is not a NumericCodec (got {type(codec).__name__})"
            )
        return cls(epc, codec)

    @classmethod
    def from_entity_def(
        cls,
        entity_def: EntityDefinition,
        *,
        class_code: int,
    ) -> NumericProp:
        """Build from an EntityDefinition, raising TypeError if codec is not NumericCodec.

        ``class_code`` resolves the codecs of the definition's coefficient EPCs.
        """
        codec = get_codec(entity_def, class_code=class_code)
        if not isinstance(codec, NumericCodec):
            raise TypeError(
                f"EPC 0x{entity_def.epc:02X}: "
                f"expected NumericCodec, got {type(codec).__name__}"
            )
        return cls(entity_def.epc, codec)


@dataclass(frozen=True)
class EnumProp:
    """EPC + EnumCodec pair."""

    epc: int
    codec: EnumCodec

    def get(self, node: NodeState) -> str | None:
        """Return decoded value from coordinator state, or None if unavailable."""
        edt = node.properties.get(self.epc)
        return self.codec.decode(edt, node.properties) if edt is not None else None

    def make_property(self, value: str) -> Property:
        """Create a Property instance for this EPC with the encoded value."""
        return Property(epc=self.epc, edt=self.codec.encode(value))

    @classmethod
    def from_registry(
        cls,
        class_code: int,
        epc: int,
    ) -> EnumProp:
        """Build from pyhems definitions by EPC lookup, raising TypeError on type mismatch."""
        codec = get_codec_for_epc(class_code, epc)
        if not isinstance(codec, EnumCodec):
            raise TypeError(
                f"EPC 0x{epc:02X} on class 0x{class_code:04X} "
                f"is not an EnumCodec (got {type(codec).__name__})"
            )
        return cls(epc, _with_state_keys(codec))

    @classmethod
    def from_entity_def(
        cls,
        entity_def: EntityDefinition,
    ) -> EnumProp:
        """Build from an EntityDefinition, raising TypeError if codec is not EnumCodec."""
        codec = get_codec(entity_def)
        if not isinstance(codec, EnumCodec):
            raise TypeError(
                f"EPC 0x{entity_def.epc:02X}: "
                f"expected EnumCodec, got {type(codec).__name__}"
            )
        return cls(entity_def.epc, _with_state_keys(codec))

    @classmethod
    def from_mapping(cls, epc: int, mapping: dict[str, int]) -> EnumProp:
        """Build from an explicit key→EDT mapping (e.g. HA mode names → raw bytes)."""
        if not mapping:
            raise ValueError(
                f"EnumProp.from_mapping called with empty mapping for EPC 0x{epc:02X}"
            )
        return cls(epc, EnumCodec.from_mapping(mapping))

    @property
    def options(self) -> list[str]:
        """Return option keys in mapping insertion order.

        Registry-built properties use the pyhems definition order.
        """
        return list(self.codec.by_key)


def local_datetime(value: datetime) -> datetime:
    """Resolve a local time only if the wall time identifies a unique instant."""
    timezone = dt_util.get_default_time_zone()
    naive = value.replace(tzinfo=None, fold=0)
    first = naive.replace(tzinfo=timezone)
    second = first.replace(fold=1)
    try:
        round_trip = first.astimezone(UTC).astimezone(timezone).replace(tzinfo=None)
    except OverflowError as err:
        raise ValueError("Datetime is outside the supported timezone range") from err
    if round_trip != naive:
        raise ValueError(f"Nonexistent local datetime in {timezone}: {naive}")
    if first.utcoffset() != second.utcoffset():
        raise ValueError(f"Ambiguous local datetime in {timezone}: {naive}")
    return first


@dataclass(frozen=True)
class TemporalProp[ValueT: (date, datetime, time)]:
    """A typed temporal EPC with explicit special states represented as unknown."""

    epc: int
    codec: TemporalCodec
    value_type: type[ValueT]

    @classmethod
    def from_entity_def(
        cls, entity_def: EntityDefinition, value_type: type[ValueT]
    ) -> TemporalProp[ValueT]:
        """Build from a scalar temporal entity definition."""
        codec = get_codec(entity_def)
        if not isinstance(codec, TemporalCodec):
            raise TypeError(f"Expected temporal codec for {entity_def.id}")
        return cls(entity_def.epc, codec, value_type)

    def get(self, node: NodeState) -> ValueT | None:
        """Decode a normal value, retaining state keys in debug diagnostics."""
        edt = node.properties.get(self.epc)
        if edt is None:
            return None
        value = self.codec.decode(edt)
        if isinstance(value, self.value_type):
            return value
        _LOGGER.debug("Temporal EPC 0x%02X on %s: %r", self.epc, node.device_key, value)
        return None

    def make_property(self, value: ValueT) -> Property:
        """Encode without precision loss."""
        return Property(self.epc, self.codec.encode(value))


@dataclass(frozen=True)
class DatetimeProp:
    """Interpret a device's local datetime using HA's timezone."""

    prop: TemporalProp[datetime]

    def get(self, node: NodeState) -> datetime | None:
        """Return a unique aware datetime or diagnosed unknown."""
        if (value := self.prop.get(node)) is None:
            return None
        try:
            return local_datetime(value)
        except ValueError as err:
            _LOGGER.warning("EPC 0x%02X on %s: %s", self.prop.epc, node.device_key, err)
            return None

    def make_property(self, value: datetime) -> Property:
        """Convert an aware datetime to the device's exact local representation."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Expected timezone-aware datetime")
        timezone = dt_util.get_default_time_zone()
        try:
            local = value.astimezone(timezone).replace(tzinfo=None, fold=0)
        except OverflowError as err:
            raise ValueError(
                "Datetime is outside the supported timezone range"
            ) from err
        resolved = local_datetime(local)
        if resolved.astimezone(UTC) != value.astimezone(UTC):
            raise ValueError("Datetime cannot be represented unambiguously")
        return self.prop.make_property(local)


@dataclass(frozen=True)
class DurationProp:
    """Convert timedelta to integral native minutes or seconds."""

    epc: int
    codec: TemporalCodec

    @classmethod
    def from_entity_def(cls, entity_def: EntityDefinition) -> DurationProp:
        """Build a duration adapter from a temporal codec."""
        codec = get_codec(entity_def)
        if not isinstance(codec, TemporalCodec):
            raise TypeError(f"Expected temporal codec for {entity_def.id}")
        return cls(entity_def.epc, codec)

    @property
    def unit(self) -> UnitOfTime:
        """Return the native unit matching wire precision."""
        return (
            UnitOfTime.MINUTES
            if self.codec.resolution == timedelta(minutes=1)
            else UnitOfTime.SECONDS
        )

    @property
    def maximum(self) -> int:
        """Return the exact largest representable value in native units."""
        return self.codec.maximum_duration // self.codec.resolution

    def get(self, node: NodeState) -> int | None:
        """Return a numeric duration, not a clock or special state."""
        edt = node.properties.get(self.epc)
        if edt is None:
            return None
        value = self.codec.decode(edt)
        if isinstance(value, timedelta):
            return value // self.codec.resolution
        _LOGGER.debug("Duration EPC 0x%02X on %s: %r", self.epc, node.device_key, value)
        return None

    def make_property(self, value: float) -> Property:
        """Reject fractional steps and nonfinite durations before encoding."""
        if not math.isfinite(value) or value != int(value):
            raise ValueError("Duration must be a finite whole number of native units")
        try:
            duration = int(value) * self.codec.resolution
        except OverflowError as err:
            raise ValueError("Duration out of range") from err
        return Property(self.epc, self.codec.encode(duration))
