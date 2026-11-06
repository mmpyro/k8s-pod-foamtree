"""The clouds' official pricing APIs, Linux on-demand list prices in USD per hour.

Every fetcher is `(region, types) -> table` and raises on failure; `types` None
means the whole region (the snapshot generator). A table is read through the
provider's LOOKUPS entry: AWS and Azure price each instance type, GCP prices
vCPU and memory per machine family, so its machine type is priced from its shape.
"""
import json
import re
from typing import Dict, Iterator, List, Optional, Tuple
import requests  # type: ignore

TIMEOUT = 10


# ---- Azure: Retail Prices API, public, no auth -------------------------------

AZURE_URL = 'https://prices.azure.com/api/retail/prices'


def azure_items(odata_filter: str) -> Iterator[dict]:
    url: Optional[str] = AZURE_URL
    params: Optional[dict] = {'$filter': odata_filter}
    while url:
        response = requests.get(url, params=params, timeout=TIMEOUT)
        response.raise_for_status()
        body = response.json()
        yield from body.get('Items') or []
        # NextPageLink already carries the filter.
        url, params = body.get('NextPageLink'), None


def azure_linux_on_demand(item: dict) -> bool:
    meter = item.get('meterName') or ''
    linux = 'Windows' not in (item.get('productName') or '')
    on_demand = 'Spot' not in meter and 'Low Priority' not in meter
    return item.get('type') == 'Consumption' and item.get('unitOfMeasure') == '1 Hour' and linux and on_demand


def fetch_azure(region: str, types: Optional[List[str]]) -> Dict[str, float]:
    base = f"serviceName eq 'Virtual Machines' and priceType eq 'Consumption' and armRegionName eq '{region}'"
    filters = [f"{base} and armSkuName eq '{t}'" for t in types] if types is not None else [base]
    table: Dict[str, float] = {}
    for odata_filter in filters:
        for item in azure_items(odata_filter):
            sku, price = item.get('armSkuName'), item.get('retailPrice')
            if sku and price and azure_linux_on_demand(item):
                table[sku] = min(price, table.get(sku, price))
    return table


# ---- AWS: Price List GetProducts, needs AWS credentials and boto3 ------------

AWS_FILTERS = {'operatingSystem': 'Linux', 'tenancy': 'Shared', 'preInstalledSw': 'NA',
               'capacitystatus': 'Used', 'licenseModel': 'No License required'}


def aws_product_price(product: dict) -> Tuple[Optional[str], Optional[float]]:
    """(instanceType, hourly USD) of one GetProducts PriceList entry."""
    instance_type = product.get('product', {}).get('attributes', {}).get('instanceType')
    for term in product.get('terms', {}).get('OnDemand', {}).values():
        for dimension in term.get('priceDimensions', {}).values():
            usd = float(dimension.get('pricePerUnit', {}).get('USD') or 0)
            if usd > 0 and dimension.get('unit') == 'Hrs':
                return instance_type, usd
    return instance_type, None


def _aws_products(region: str, instance_type: Optional[str]) -> Iterator[dict]:
    import boto3  # type: ignore  # optional extra: pip install 'k8sfoams[aws]'
    # The Price List API is served from us-east-1 for every region.
    client = boto3.client('pricing', region_name='us-east-1')
    terms = {**AWS_FILTERS, 'regionCode': region, **({'instanceType': instance_type} if instance_type else {})}
    filters = [{'Type': 'TERM_MATCH', 'Field': k, 'Value': v} for k, v in terms.items()]
    for page in client.get_paginator('get_products').paginate(ServiceCode='AmazonEC2', Filters=filters):
        for entry in page['PriceList']:
            yield json.loads(entry)


def fetch_aws(region: str, types: Optional[List[str]]) -> Dict[str, float]:
    table: Dict[str, float] = {}
    wanted: List[Optional[str]] = list(types) if types is not None else [None]
    for instance_type in wanted:
        for product in _aws_products(region, instance_type):
            name, price = aws_product_price(product)
            if name and price:
                table[name] = price
    return table


# ---- GCP: Cloud Billing Catalog, needs Application Default Credentials --------

GCP_URL = 'https://cloudbilling.googleapis.com/v1/services/6F81-5844-456A/skus'  # Compute Engine
# "N2 Instance Core running in Americas", "N1 Predefined Instance Ram running in Paris", "N2D AMD Instance Core …".
# shortcut: Custom, Spot, sole-tenant and compute-/memory-optimized SKUs are not matched, so c2,
# m1-m3 and custom machine types get no price (frontend warns); add their SKU wording when needed.
_GCP_SKU = re.compile(r'^(?P<family>[A-Z][A-Z0-9]*) (?:Predefined |AMD )?Instance (?P<kind>Core|Ram) running in ')
# "Nvidia Tesla A100 GPU running in Americas" (40GB), "Nvidia Tesla A100 80GB GPU …", "Nvidia L4 GPU …".
_GCP_GPU_SKU = re.compile(r'^Nvidia (?:Tesla )?(?P<gpu>[A-Z0-9]+(?: \d+GB)?) GPU running in ')
# GiB of memory per vCPU by family and class, from the machine type docs.
GCP_GIB_PER_VCPU: Dict[str, Dict[str, float]] = {
    'e2': {'standard': 4, 'highmem': 8, 'highcpu': 1},
    'n1': {'standard': 3.75, 'highmem': 6.5, 'highcpu': 0.9},
    'n2': {'standard': 4, 'highmem': 8, 'highcpu': 1},
    'n2d': {'standard': 4, 'highmem': 8, 'highcpu': 1},
    'n4': {'standard': 4, 'highmem': 8, 'highcpu': 2},
    'c3': {'standard': 4, 'highmem': 8, 'highcpu': 2},
    't2d': {'standard': 4},
}
# Shared-core E2: billed as a fraction of a vCPU.
GCP_SHARED_CORE = {'e2-micro': ('e2', 0.25, 1), 'e2-small': ('e2', 0.5, 2), 'e2-medium': ('e2', 1, 4)}
_GCP_TYPE = re.compile(r'^(?P<family>[a-z0-9]+)-(?P<cls>standard|highmem|highcpu)-(?P<vcpu>\d+)$')
# Accelerator-optimized types: (vCPU, GiB, GPU rate key, GPU count), from the machine type docs. They bill
# vCPU and memory at their family's rate plus every attached GPU.
GCP_GPU_TYPES: Dict[str, Tuple[int, int, str, int]] = {
    'a2-highgpu-1g': (12, 85, 'a100', 1), 'a2-highgpu-2g': (24, 170, 'a100', 2),
    'a2-highgpu-4g': (48, 340, 'a100', 4), 'a2-highgpu-8g': (96, 680, 'a100', 8),
    'a2-megagpu-16g': (96, 1360, 'a100', 16),
    'a2-ultragpu-1g': (12, 170, 'a100-80gb', 1), 'a2-ultragpu-2g': (24, 340, 'a100-80gb', 2),
    'a2-ultragpu-4g': (48, 680, 'a100-80gb', 4), 'a2-ultragpu-8g': (96, 1360, 'a100-80gb', 8),
    'a3-highgpu-1g': (26, 234, 'h100-80gb', 1), 'a3-highgpu-2g': (52, 468, 'h100-80gb', 2),
    'a3-highgpu-4g': (104, 936, 'h100-80gb', 4), 'a3-highgpu-8g': (208, 1872, 'h100-80gb', 8),
    'g2-standard-4': (4, 16, 'l4', 1), 'g2-standard-8': (8, 32, 'l4', 1), 'g2-standard-12': (12, 48, 'l4', 1),
    'g2-standard-16': (16, 64, 'l4', 1), 'g2-standard-24': (24, 96, 'l4', 2), 'g2-standard-32': (32, 128, 'l4', 1),
    'g2-standard-48': (48, 192, 'l4', 4), 'g2-standard-96': (96, 384, 'l4', 8),
}


def gcp_shape(machine_type: str) -> Optional[Tuple[str, float, float]]:
    """(family, vCPU, GiB) of a predefined machine type; None for one the table does not know."""
    if machine_type in GCP_SHARED_CORE:
        return GCP_SHARED_CORE[machine_type]
    if machine_type in GCP_GPU_TYPES:
        vcpu, gib, _, _ = GCP_GPU_TYPES[machine_type]
        return machine_type.split('-')[0], vcpu, gib
    m = _GCP_TYPE.match(machine_type)
    ratio = GCP_GIB_PER_VCPU.get(m['family'], {}).get(m['cls']) if m else None
    if m is None or ratio is None:
        return None
    vcpu = int(m['vcpu'])
    return m['family'], vcpu, vcpu * ratio


def _unit_price(sku: dict) -> float:
    rate = sku['pricingInfo'][0]['pricingExpression']['tieredRates'][-1]['unitPrice']
    return int(rate.get('units') or 0) + rate.get('nanos', 0) / 1e9


def gcp_rates(skus: Iterator[dict]) -> Dict[str, Dict[str, float]]:
    """{region: {'n2/core': usd per vCPU-hour, 'n2/ram': usd per GiB-hour, 'gpu/l4': usd per GPU-hour}}."""
    rates: Dict[str, Dict[str, float]] = {}
    for sku in skus:
        category, description = sku.get('category', {}), sku.get('description', '')
        if category.get('usageType') != 'OnDemand' or not sku.get('pricingInfo'):
            continue
        m, gpu = _GCP_SKU.match(description), _GCP_GPU_SKU.match(description)
        if m:
            key = f"{m['family'].lower()}/{m['kind'].lower()}"
        elif gpu:
            key = f"gpu/{gpu['gpu'].lower().replace(' ', '-')}"
        else:
            continue
        price = _unit_price(sku)
        for region in sku.get('serviceRegions') or []:
            rates.setdefault(region, {})[key] = price
    return rates


def gcp_skus() -> Iterator[dict]:
    import google.auth  # type: ignore  # installed with the kubernetes client
    from google.auth.transport.requests import AuthorizedSession  # type: ignore
    credentials, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-billing.readonly'])
    session = AuthorizedSession(credentials)
    params = {'pageSize': '5000', 'currencyCode': 'USD'}
    while True:
        response = session.get(GCP_URL, params=params, timeout=TIMEOUT)
        response.raise_for_status()
        body = response.json()
        yield from body.get('skus') or []
        if not body.get('nextPageToken'):
            return
        params['pageToken'] = body['nextPageToken']


def fetch_gcp(region: str, types: Optional[List[str]]) -> Dict[str, float]:
    rates = gcp_rates(gcp_skus()).get(region)
    if not rates:
        raise LookupError(f'No Compute Engine rates for region {region}')
    return rates


def gcp_lookup(rates: dict, machine_type: str) -> Optional[float]:
    shape = gcp_shape(machine_type)
    if shape is None:
        return None
    family, vcpu, gib = shape
    core, ram = rates.get(f'{family}/core'), rates.get(f'{family}/ram')
    if core is None or ram is None:
        return None
    _, _, gpu, count = GCP_GPU_TYPES.get(machine_type, (0, 0, '', 0))
    gpu_rate = rates.get(f'gpu/{gpu}') if count else 0.0
    return vcpu * core + gib * ram + count * gpu_rate if gpu_rate is not None else None


def _per_type(table: dict, instance_type: str) -> Optional[float]:
    return table.get(instance_type)


FETCHERS = {'aws': fetch_aws, 'azure': fetch_azure, 'gcp': fetch_gcp}
LOOKUPS = {'aws': _per_type, 'azure': _per_type, 'gcp': gcp_lookup}
