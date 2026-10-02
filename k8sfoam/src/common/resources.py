import math
from kubernetes.utils import parse_quantity  # type: ignore


# Allocatable keys that are not requested by containers, so they can never be
# drawn as a treemap: `pods` is a slot count and attachable volumes are a CSI limit.
NON_REQUESTABLE_PREFIXES = ('attachable-volumes-',)
NON_REQUESTABLE = ('pods',)

# Byte-sized resources are converted to decimal kB, the same unit as memory.
BYTE_RESOURCES = ('ephemeral-storage',)
BYTE_PREFIXES = ('hugepages-',)


def convert_cpu(cpu: str) -> int:
    # Millicores, rounded up like the scheduler's MilliValue().
    return math.ceil(parse_quantity(cpu) * 1000)


def convert_memory(memory: str) -> float:
    # Decimal kB (1000 bytes), the unit the frontend expects.
    return float(parse_quantity(memory) / 1000)


def is_extended(name: str) -> bool:
    """Every requestable resource other than cpu and memory."""
    return name not in ('cpu', 'memory') and name not in NON_REQUESTABLE \
        and not name.startswith(NON_REQUESTABLE_PREFIXES)


def is_bytes(name: str) -> bool:
    return name in BYTE_RESOURCES or name.startswith(BYTE_PREFIXES)


def is_device(name: str) -> bool:
    # Device plugins always advertise vendor-prefixed names (nvidia.com/gpu, amd.com/gpu).
    return '/' in name


def convert_extended(name: str, quantity: str) -> float:
    """kB for byte-sized resources, a plain count for everything else."""
    if is_bytes(name):
        return convert_memory(quantity)
    value = parse_quantity(quantity)
    return int(value) if value == int(value) else float(value)


def extract_extended(resources: dict) -> dict:
    return {name: convert_extended(name, q) for name, q in (resources or {}).items() if is_extended(name)}
