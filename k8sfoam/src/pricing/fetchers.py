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
# shortcut: Custom, Spot, sole-tenant, compute-/memory-optimized and GPU SKUs are not matched, so
# c2, m1-m3, a2/a3/g2 machine types get no price (frontend warns); add their SKU wording when needed.
_GCP_SKU = re.compile(r'^(?P<family>[A-Z][A-Z0-9]*) (?:Predefined |AMD )?Instance (?P<kind>Core|Ram) running in ')
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


def gcp_shape(machine_type: str) -> Optional[Tuple[str, float, float]]:
    """(family, vCPU, GiB) of a predefined machine type; None for one the table does not know."""
    if machine_type in GCP_SHARED_CORE:
        return GCP_SHARED_CORE[machine_type]
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
    """{region: {'n2/core': usd per vCPU-hour, 'n2/ram': usd per GiB-hour}} from Compute SKUs."""
    rates: Dict[str, Dict[str, float]] = {}
    for sku in skus:
        category = sku.get('category', {})
        m = _GCP_SKU.match(sku.get('description', ''))
        if not m or category.get('usageType') != 'OnDemand' or not sku.get('pricingInfo'):
            continue
        key = f"{m['family'].lower()}/{m['kind'].lower()}"
        price = _unit_price(sku)
        for region in sku.get('serviceRegions') or []:
            rates.setdefault(region, {})[key] = price
    return rates


def gcp_skus() -> Iterator[dict]:
    import google.auth  # type: ignore  # installed with the kubernetes client
    from google.auth.transport.requests import AuthorizedSession  # type: ignore
    credentials, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-billing.readonly'])
    session = AuthorizedSession(credentials)
    params = {'pageSize': 5000, 'currencyCode': 'USD'}
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
    return vcpu * core + gib * ram if core is not None and ram is not None else None


def _per_type(table: dict, instance_type: str) -> Optional[float]:
    return table.get(instance_type)


FETCHERS = {'aws': fetch_aws, 'azure': fetch_azure, 'gcp': fetch_gcp}
LOOKUPS = {'aws': _per_type, 'azure': _per_type, 'gcp': gcp_lookup}
