"""On-demand node prices: a bundled snapshot, refreshed live from the clouds' pricing APIs where reachable.

Pricing is decoration. Nothing here may fail a request: a lookup that cannot be
answered returns None and the frontend warns about the node instead.
"""
import gzip
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional, Set, Tuple
from k8sfoam.src.pricing.fetchers import FETCHERS, LOOKUPS

log = logging.getLogger(__name__)

SNAPSHOT = Path(__file__).parent / 'prices.json.gz'
# A live table is trusted this long, and a failed fetch is not retried sooner than RETRY_AFTER.
LIVE_TTL = 24 * 3600.0
RETRY_AFTER = 3600.0
# spec.providerID scheme -> provider. GKE nodes say gce://.
_SCHEMES = {'aws': 'aws', 'azure': 'azure', 'gce': 'gcp'}
# Label values are [A-Za-z0-9._-]; anything else never reaches a pricing API query.
_SAFE = re.compile(r'^[A-Za-z0-9._-]{1,63}$')

Key = Tuple[str, str]


def provider_of(provider_id: Optional[str]) -> Optional[str]:
    """aws, azure or gcp from a node's spec.providerID; None for kind, bare metal and the rest."""
    if not provider_id or '://' not in provider_id:
        return None
    return _SCHEMES.get(provider_id.split('://', 1)[0])


def load_snapshot(path: Path) -> dict:
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            return json.load(f)
    except Exception as ex:
        log.warning('No price snapshot at %s, live prices only: %s', path, ex)
        return {}


class PriceBook():
    """Hourly USD on-demand price per (provider, region, instance type).

    `live` False (synthetic clusters, tests) answers from the snapshot alone and never touches the network.
    """

    def __init__(self, live: bool = True, snapshot_path: Path = SNAPSHOT,
                 fetchers: Optional[Dict[str, Callable]] = None):
        self.__snapshot = load_snapshot(snapshot_path)
        self.__fetchers = fetchers if fetchers is not None else (FETCHERS if live else {})
        self.__lock = threading.Lock()
        # Live tables per (provider, region), merged across fetches, and when each type was last tried.
        self.__live: Dict[Key, dict] = {}
        self.__tried: Dict[Tuple[str, str, str], Tuple[float, bool]] = {}
        self.__pending: Dict[Key, Set[str]] = {}
        self.__errors: Dict[str, str] = {}
        self.__worker: Optional[threading.Thread] = None

    def price(self, provider: Optional[str], region: Optional[str], instance_type: Optional[str]) -> Optional[dict]:
        """{hourly, source} with source 'live' or 'snapshot'; None when no price is known."""
        try:
            if not provider or not region or not instance_type:
                return None
            if not _SAFE.match(region) or not _SAFE.match(instance_type):
                return None
            self.__want(provider, region, instance_type)
            lookup = LOOKUPS[provider]
            with self.__lock:
                live = self.__live.get((provider, region))
            hourly = lookup(live, instance_type) if live else None
            if hourly is not None:
                return {'hourly': hourly, 'source': 'live'}
            table = self.__snapshot.get('prices', {}).get(provider, {}).get(region)
            hourly = lookup(table, instance_type) if table else None
            return {'hourly': hourly, 'source': 'snapshot'} if hourly is not None else None
        except Exception as ex:
            log.warning('Price lookup failed for %s/%s/%s: %s', provider, region, instance_type, ex)
            return None

    def status(self) -> dict:
        with self.__lock:
            errors = dict(self.__errors)
        return {'generated': self.__snapshot.get('generated'), 'liveErrors': errors}

    def __want(self, provider: str, region: str, instance_type: str) -> None:
        """Queue a background fetch unless this type was fetched lately; never blocks."""
        if provider not in self.__fetchers:
            return
        now = time.monotonic()
        with self.__lock:
            tried = self.__tried.get((provider, region, instance_type))
            if tried is not None and now - tried[0] < (LIVE_TTL if tried[1] else RETRY_AFTER):
                return
            # Marked now so the next request does not queue it again while the fetch runs.
            self.__tried[(provider, region, instance_type)] = (now, False)
            self.__pending.setdefault((provider, region), set()).add(instance_type)
            if self.__worker is None or not self.__worker.is_alive():
                self.__worker = threading.Thread(target=self.__drain, name='price-fetch', daemon=True)
                self.__worker.start()

    def __drain(self) -> None:
        while True:
            with self.__lock:
                if not self.__pending:
                    return
                (provider, region), types = self.__pending.popitem()
            try:
                table = self.__fetchers[provider](region, sorted(types))
            except Exception as ex:
                log.warning('Live %s prices for %s unavailable, using the snapshot: %s', provider, region, ex)
                with self.__lock:
                    self.__errors[provider] = str(ex) or type(ex).__name__
                continue
            now = time.monotonic()
            with self.__lock:
                self.__live.setdefault((provider, region), {}).update(table)
                for t in types:
                    self.__tried[(provider, region, t)] = (now, True)
                self.__errors.pop(provider, None)
