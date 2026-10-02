from k8sfoam.src.common.node_topology import node_topology


def test_should_read_well_known_topology_labels():
    # Given
    labels = {
        'topology.kubernetes.io/zone': 'us-east-1a',
        'topology.kubernetes.io/region': 'us-east-1',
        'node.kubernetes.io/instance-type': 'm5.large',
        'karpenter.sh/nodepool': 'general',
        'karpenter.sh/capacity-type': 'spot',
    }

    # When
    topology = node_topology(labels)

    # Then
    assert topology == {
        'zone': 'us-east-1a',
        'region': 'us-east-1',
        'instanceType': 'm5.large',
        'nodePool': 'general',
        'capacityType': 'spot',
    }


def test_should_return_none_for_every_fact_when_node_has_no_labels():
    assert node_topology(None) == node_topology({}) == {
        'zone': None, 'region': None, 'instanceType': None, 'nodePool': None, 'capacityType': None,
    }


def test_should_fall_back_to_deprecated_beta_labels():
    # Given — a cluster upgraded in place still carries only the beta spellings
    labels = {
        'failure-domain.beta.kubernetes.io/zone': 'eu-west-1b',
        'failure-domain.beta.kubernetes.io/region': 'eu-west-1',
        'beta.kubernetes.io/instance-type': 'c5.xlarge',
    }

    # When
    topology = node_topology(labels)

    # Then
    assert topology['zone'] == 'eu-west-1b'
    assert topology['region'] == 'eu-west-1'
    assert topology['instanceType'] == 'c5.xlarge'


def test_should_prefer_well_known_label_over_beta_one():
    labels = {'topology.kubernetes.io/zone': 'new', 'failure-domain.beta.kubernetes.io/zone': 'old'}
    assert node_topology(labels)['zone'] == 'new'


def test_should_read_node_pool_from_each_provider():
    assert node_topology({'eks.amazonaws.com/nodegroup': 'ng-1'})['nodePool'] == 'ng-1'
    assert node_topology({'cloud.google.com/gke-nodepool': 'default-pool'})['nodePool'] == 'default-pool'
    assert node_topology({'kubernetes.azure.com/agentpool': 'sys'})['nodePool'] == 'sys'
    assert node_topology({'agentpool': 'legacy'})['nodePool'] == 'legacy'


def test_should_prefer_karpenter_node_pool_over_eks_node_group():
    labels = {'eks.amazonaws.com/nodegroup': 'ng-1', 'karpenter.sh/nodepool': 'general'}
    assert node_topology(labels)['nodePool'] == 'general'


def test_should_normalise_capacity_type_across_providers():
    assert node_topology({'karpenter.sh/capacity-type': 'on-demand'})['capacityType'] == 'on-demand'
    assert node_topology({'eks.amazonaws.com/capacityType': 'SPOT'})['capacityType'] == 'spot'
    assert node_topology({'eks.amazonaws.com/capacityType': 'ON_DEMAND'})['capacityType'] == 'on-demand'
    assert node_topology({'cloud.google.com/gke-spot': 'true'})['capacityType'] == 'spot'
    assert node_topology({'kubernetes.azure.com/scalesetpriority': 'spot'})['capacityType'] == 'spot'


def test_should_leave_unrecognised_capacity_type_unknown():
    # Karpenter also has 'reserved'; it is neither spot nor on-demand, so it is not guessed.
    assert node_topology({'karpenter.sh/capacity-type': 'reserved'})['capacityType'] is None
    assert node_topology({'cloud.google.com/gke-spot': 'false'})['capacityType'] is None
