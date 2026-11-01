"""EPC codec wrapper types for the HEMS Echonet Lite integration."""

from dataclasses import dataclass
from typing import Protocol

from pyhems import (
    BinaryCodec,
    EntityDefinition,
    EnumCodec,
    NodeState,
    NumericCodec,
    Property,
    get_codec,
    get_codec_for_epc,
)

from homeassistant.const import PRECISION_HALVES, PRECISION_TENTHS, PRECISION_WHOLE

from .const import camel_to_snake


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
