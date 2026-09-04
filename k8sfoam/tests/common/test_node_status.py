from k8sfoam.src.common.node_status import node_warnings


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
