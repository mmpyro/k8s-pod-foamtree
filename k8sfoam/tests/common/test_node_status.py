import pytest
from k8sfoam.src.common.dtos import NodeResources
from k8sfoam.src.common.node_status import node_warnings, stranded_devices


def taint(key: str, effect: str = 'NoSchedule') -> dict:
    return {'key': key, 'value': None, 'effect': effect}


def healthy_conditions(**overrides) -> dict:
    conditions = {'MemoryPressure': False, 'DiskPressure': False, 'PIDPressure': False, 'Ready': True}
    conditions.update(overrides)
    return conditions


def test_healthy_node_has_no_warnings():
    # When
    warnings = node_warnings(False, [], healthy_conditions())

    # Then
    assert warnings == []


def test_node_with_no_metadata_at_all_has_no_warnings():
    # Given — what an older backend or a positionally-built DTO reports
    # When
    warnings = node_warnings(False, None, None)

    # Then
    assert warnings == []


def test_cordoned_node_is_flagged():
    # When
    warnings = node_warnings(True, [], healthy_conditions())

    # Then
    assert warnings == ['cordoned']


def test_each_pressure_condition_is_flagged():
    # When / Then
    assert node_warnings(False, [], healthy_conditions(MemoryPressure=True)) == ['memory-pressure']
    assert node_warnings(False, [], healthy_conditions(DiskPressure=True)) == ['disk-pressure']
    assert node_warnings(False, [], healthy_conditions(PIDPressure=True)) == ['pid-pressure']


def test_not_ready_node_is_flagged():
    # When
    warnings = node_warnings(False, [], healthy_conditions(Ready=False))

    # Then
    assert warnings == ['not-ready']


def test_missing_ready_key_is_not_reported_as_not_ready():
    # Given — absent is not the same as False; never invent an outage
    # When
    warnings = node_warnings(False, [], {'MemoryPressure': False})

    # Then
    assert warnings == []


def test_blocking_taints_are_flagged():
    # When / Then
    assert node_warnings(False, [taint('gpu-only', 'NoSchedule')], healthy_conditions()) == ['tainted']
    assert node_warnings(False, [taint('evict-me', 'NoExecute')], healthy_conditions()) == ['tainted']


def test_prefer_no_schedule_taint_alone_does_not_flag_the_node():
    # Given — a soft hint, still listed in the detail view but never a marker
    # When
    warnings = node_warnings(False, [taint('spot', 'PreferNoSchedule')], healthy_conditions())

    # Then
    assert warnings == []


def test_prefer_no_schedule_alongside_a_blocking_taint_still_flags():
    # Given
    taints = [taint('spot', 'PreferNoSchedule'), taint('gpu-only', 'NoSchedule')]

    # When
    warnings = node_warnings(False, taints, healthy_conditions())

    # Then
    assert warnings == ['tainted']


def test_every_reason_is_reported_worst_first():
    # Given — a node that is cordoned, down, out of memory and tainted
    conditions = healthy_conditions(Ready=False, MemoryPressure=True, DiskPressure=True)

    # When
    warnings = node_warnings(True, [taint('gpu-only')], conditions)

    # Then
    assert warnings == ['cordoned', 'not-ready', 'memory-pressure', 'disk-pressure', 'tainted']


@pytest.mark.parametrize("used, expected", [
    ({'cpu': 7200, 'nvidia.com/gpu': 1}, True),    # exactly 90% CPU, GPUs free
    ({'cpu': 7199, 'nvidia.com/gpu': 1}, False),   # just under the edge
    ({'memory': 900, 'nvidia.com/gpu': 3}, True),  # memory full, 1 GPU free
    ({'cpu': 8000, 'nvidia.com/gpu': 4}, False),   # every GPU in use: nothing stranded
])
def test_stranded_devices(used, expected):
    # Given
    node = NodeResources('gpu', 8000, 1000, extended={'nvidia.com/gpu': 4, 'ephemeral-storage': 5000})

    # When / Then
    assert stranded_devices(node, used) is expected


def test_node_without_devices_is_never_stranded():
    # Given — ephemeral storage is not a device
    node = NodeResources('plain', 8000, 1000, extended={'ephemeral-storage': 5000})

    # When / Then
    assert stranded_devices(node, {'cpu': 8000}) is False
