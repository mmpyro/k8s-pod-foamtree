import gzip
import json
import threading
import pytest
from unittest.mock import MagicMock, patch
from k8sfoam.src.pricing import PriceBook, provider_of
from k8sfoam.src.pricing.fetchers import (aws_product_price, fetch_azure, gcp_lookup, gcp_rates, gcp_shape)


@pytest.fixture
def snapshot(tmp_path):
    path = tmp_path / 'prices.json.gz'
    with gzip.open(path, 'wt') as f:
        json.dump({'generated': '2026-11-01', 'prices': {
            'aws': {'eu-west-1': {'m5.xlarge': 0.214}},
            'gcp': {'us-central1': {'e2/core': 0.02, 'e2/ram': 0.003}},
        }}, f)
    return path


def wait_for_fetch(book: PriceBook) -> None:
    for t in threading.enumerate():
        if t.name == 'price-fetch':
            t.join(5)


@pytest.mark.parametrize('provider_id, expected', [
    ('aws:///eu-west-1a/i-0abc', 'aws'),
    ('azure:///subscriptions/x/resourceGroups/y/providers/Microsoft.Compute/virtualMachineScaleSets/z/0', 'azure'),
    ('gce://project/us-central1-a/gke-node', 'gcp'),
    ('kind://docker/kind/kind-control-plane', None),
    ('', None),
    (None, None),
])
def test_provider_of(provider_id, expected):
    assert provider_of(provider_id) == expected


def test_snapshot_price(snapshot):
    book = PriceBook(live=False, snapshot_path=snapshot)

    assert book.price('aws', 'eu-west-1', 'm5.xlarge') == {'hourly': 0.214, 'source': 'snapshot'}
    assert book.price('aws', 'eu-west-1', 'x9.huge') is None
    assert book.price(None, 'eu-west-1', 'm5.xlarge') is None
    assert book.status() == {'generated': '2026-11-01', 'liveErrors': {}}


def test_gcp_snapshot_prices_machine_type_from_family_rates(snapshot):
    book = PriceBook(live=False, snapshot_path=snapshot)

    # 4 vCPU * 0.02 + 16 GiB * 0.003
    assert book.price('gcp', 'us-central1', 'e2-standard-4')['hourly'] == pytest.approx(0.128)


def test_missing_snapshot_still_answers(tmp_path):
    book = PriceBook(live=False, snapshot_path=tmp_path / 'nope.json.gz')

    assert book.price('aws', 'eu-west-1', 'm5.xlarge') is None
    assert book.status()['generated'] is None


def test_live_price_overrides_snapshot(snapshot):
    book = PriceBook(snapshot_path=snapshot, fetchers={'aws': lambda region, types: {'m5.xlarge': 0.2}})

    book.price('aws', 'eu-west-1', 'm5.xlarge')
    wait_for_fetch(book)

    assert book.price('aws', 'eu-west-1', 'm5.xlarge') == {'hourly': 0.2, 'source': 'live'}


def test_failing_fetch_falls_back_to_snapshot_and_reports(snapshot):
    calls = []

    def broken(region, types):
        calls.append(types)
        raise RuntimeError('token expired')

    book = PriceBook(snapshot_path=snapshot, fetchers={'aws': broken})
    assert book.price('aws', 'eu-west-1', 'm5.xlarge')['source'] == 'snapshot'
    wait_for_fetch(book)

    assert book.price('aws', 'eu-west-1', 'm5.xlarge') == {'hourly': 0.214, 'source': 'snapshot'}
    assert book.status()['liveErrors'] == {'aws': 'token expired'}
    # Not retried on every request.
    assert calls == [['m5.xlarge']]


def test_unsafe_label_values_never_reach_a_fetcher(snapshot):
    fetcher = MagicMock()
    book = PriceBook(snapshot_path=snapshot, fetchers={'azure': fetcher})

    assert book.price('azure', "eastus' or 1 eq 1", 'Standard_D4s_v5') is None
    fetcher.assert_not_called()


@pytest.mark.parametrize('machine_type, shape', [
    ('e2-standard-4', ('e2', 4, 16)),
    ('n2-highmem-8', ('n2', 8, 64)),
    ('n1-standard-2', ('n1', 2, 7.5)),
    ('e2-medium', ('e2', 1, 4)),
    ('a2-highgpu-2g', None),
    ('x9-standard-4', None),
])
def test_gcp_shape(machine_type, shape):
    assert gcp_shape(machine_type) == shape


def test_gcp_rates_from_catalog_skus():
    def sku(description, price, usage='OnDemand'):
        return {'description': description, 'category': {'usageType': usage}, 'serviceRegions': ['us-central1'],
                'pricingInfo': [{'pricingExpression': {'tieredRates': [{'unitPrice': {'units': '0', 'nanos': price}}]}}]}

    rates = gcp_rates(iter([
        sku('N2 Instance Core running in Americas', 31611000),
        sku('N2 Instance Ram running in Americas', 4237000),
        sku('N2D AMD Instance Core running in Americas', 27502000),
        sku('Spot Preemptible N2 Instance Core running in Americas', 7000000),
        sku('Commitment v1: N2 Cpu in Americas for 1 Year', 1, usage='Commit1Yr'),
    ]))

    assert rates == {'us-central1': {'n2/core': 0.031611, 'n2/ram': 0.004237, 'n2d/core': 0.027502}}
    assert gcp_lookup(rates['us-central1'], 'n2-standard-2') == pytest.approx(2 * 0.031611 + 8 * 0.004237)
    assert gcp_lookup(rates['us-central1'], 'n2d-standard-2') is None


def test_aws_product_price():
    product = {'product': {'attributes': {'instanceType': 'm5.xlarge'}},
               'terms': {'OnDemand': {'X': {'priceDimensions': {'Y': {'unit': 'Hrs', 'pricePerUnit': {'USD': '0.2140000000'}}}}}}}

    assert aws_product_price(product) == ('m5.xlarge', 0.214)


def test_fetch_azure_keeps_linux_on_demand_rows():
    def row(product, meter, price):
        return {'armSkuName': 'Standard_D4s_v5', 'retailPrice': price, 'type': 'Consumption',
                'unitOfMeasure': '1 Hour', 'productName': product, 'meterName': meter}

    page = MagicMock()
    page.json.return_value = {'Items': [row('Virtual Machines Dv5 Series Windows', 'D4s v5', 0.376),
                                        row('Virtual Machines Dv5 Series', 'D4s v5 Spot', 0.02),
                                        row('Virtual Machines Dv5 Series', 'D4s v5', 0.192)], 'NextPageLink': None}
    with patch('k8sfoam.src.pricing.fetchers.requests.get', return_value=page) as get:
        assert fetch_azure('eastus', ['Standard_D4s_v5']) == {'Standard_D4s_v5': 0.192}
    assert "armSkuName eq 'Standard_D4s_v5'" in get.call_args.kwargs['params']['$filter']
