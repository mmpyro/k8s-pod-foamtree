"""Regenerate the bundled price snapshot: python -m k8sfoam.src.pricing.snapshot [aws azure gcp]

Azure needs nothing; AWS needs credentials allowed pricing:GetProducts and boto3
(pip install 'k8sfoams[aws]'); GCP needs `gcloud auth application-default login`.
A provider that fails keeps its previous table, so partial credentials never wipe prices.
"""
import argparse
import datetime
import gzip
import json
import sys
from pathlib import Path
from typing import Callable, Dict, Set
from k8sfoam.src.pricing import SNAPSHOT, load_snapshot
from k8sfoam.src.pricing.fetchers import azure_items, gcp_skus, fetch_aws, fetch_azure, gcp_rates

# SKUs sold in practically every Azure region; their price rows name the regions to fetch.
_AZURE_PROBE_SKUS = ('Standard_D2s_v3', 'Standard_D2s_v5')


def azure_regions() -> Set[str]:
    regions = set()
    for sku in _AZURE_PROBE_SKUS:
        odata_filter = f"serviceName eq 'Virtual Machines' and priceType eq 'Consumption' and armSkuName eq '{sku}'"
        regions |= {item['armRegionName'] for item in azure_items(odata_filter) if item.get('armRegionName')}
    return regions


def build_azure() -> dict:
    return {r: t for r in sorted(azure_regions()) if (t := fetch_azure(r, None))}


def build_aws() -> dict:
    import boto3  # type: ignore
    return {r: t for r in sorted(boto3.session.Session().get_available_regions('ec2')) if (t := fetch_aws(r, None))}


def build_gcp() -> dict:
    return gcp_rates(gcp_skus())


BUILDERS: Dict[str, Callable[[], dict]] = {'aws': build_aws, 'azure': build_azure, 'gcp': build_gcp}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('providers', nargs='*', choices=sorted(BUILDERS), default=sorted(BUILDERS))
    parser.add_argument('--out', type=Path, default=SNAPSHOT)
    args = parser.parse_args()

    prices = load_snapshot(args.out).get('prices', {})
    failed = []
    for provider in args.providers:
        try:
            table = BUILDERS[provider]()
            if not table:
                raise LookupError('no prices returned')
            prices[provider] = table
            print(f'{provider}: {len(table)} regions')
        except Exception as ex:
            failed.append(provider)
            print(f'{provider}: kept the previous table, fetch failed: {ex}', file=sys.stderr)
    snapshot = {'generated': datetime.date.today().isoformat(), 'prices': prices}
    with gzip.open(args.out, 'wt', encoding='utf-8') as f:
        json.dump(snapshot, f, separators=(',', ':'), sort_keys=True)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
