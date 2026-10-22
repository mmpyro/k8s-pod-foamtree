import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional
from kubernetes import watch  # type: ignore
from kubernetes.client.rest import ApiException  # type: ignore
from urllib3.exceptions import HTTPError
from k8sfoam.src.k8s.k8s_client import core_v1, list_all, ACTIVE_POD_SELECTOR
from k8sfoam.src.utils.extractors import ResourcesExtractor
from k8sfoam.src.common.dtos import NodeResources, PodResources

log = logging.getLogger(__name__)

# The API server ends each WATCH after this long; the informer then re-watches
# from the last resourceVersion, which is also when a stopped informer exits.
WATCH_TIMEOUT = 300
CONNECT_TIMEOUT = 10
# How long the first request for a context waits for the initial LIST.
SYNC_TIMEOUT = 60
# Longer than the 600 s maximum of the UI's refresh slider.
IDLE_TIMEOUT = 15 * 60
SWEEP_INTERVAL = 30
MIN_BACKOFF = 1.0
MAX_BACKOFF = 30.0
HTTP_GONE = 410
TERMINAL_PHASES = ('Succeeded', 'Failed')


class Informer():
    """The extracted state of one resource kind, kept current by LIST + WATCH.

    Values are immutable DTOs, so a snapshot is a shallow copy taken under the lock.
    """

    def __init__(self, list_func: Callable[..., Any], extract: Callable[[Any], Any], key: Callable[[Any], str],
                 page_size: int, keep: Callable[[Any], bool] = lambda obj: True, **selectors):
        self.__list_func = list_func
        self.__extract = extract
        self.__key = key
        self.__keep = keep
        self.__page_size = page_size
        self.__selectors = selectors
        self.__lock = threading.Lock()
        self.__store: Dict[str, Any] = {}
        self.__error: Optional[Exception] = None
        # Set once the first LIST has either succeeded or failed.
        self.__settled = threading.Event()
        self.__synced = threading.Event()
        self.__stopped = threading.Event()
        self.__watch: Optional[Any] = None
        self.resource_version: Optional[str] = None

    @property
    def synced(self) -> bool:
        return self.__synced.is_set()

    def start(self) -> None:
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self) -> None:
        self.__stopped.set()
        if self.__watch is not None:
            self.__watch.stop()

    def snapshot(self, timeout: float) -> List[Any]:
        if not self.__settled.wait(timeout):
            raise TimeoutError(f'Listing from the API server took longer than {timeout}s')
        with self.__lock:
            if self.__error is not None:
                raise self.__error
            return list(self.__store.values())

    def relist(self) -> None:
        try:
            items, resource_version = list_all(self.__list_func, self.__page_size, **self.__selectors)
            store = {self.__key(obj): self.__extract(obj) for obj in items if self.__keep(obj)}
        except Exception as ex:
            # Serving the last good state would show a dead cluster as healthy.
            with self.__lock:
                self.__error = ex
            self.__settled.set()
            raise
        with self.__lock:
            self.__store = store
            self.__error = None
        self.resource_version = resource_version
        self.__synced.set()
        self.__settled.set()

    def watch(self) -> None:
        """One WATCH request, from the last resourceVersion until the server ends it.

        timeout_seconds also turns off the client's own retry after 410, so
        `run` alone decides when to relist.
        """
        self.__watch = watch.Watch()
        stream = self.__watch.stream(self.__list_func, resource_version=self.resource_version,
                                     allow_watch_bookmarks=True, timeout_seconds=WATCH_TIMEOUT,
                                     _request_timeout=(CONNECT_TIMEOUT, WATCH_TIMEOUT + 30), **self.__selectors)
        for event in stream:
            self.apply(event)
            if self.__stopped.is_set():
                break

    def apply(self, event: Dict[str, Any]) -> None:
        kind = event['type']
        if kind in ('ADDED', 'MODIFIED', 'DELETED'):
            obj = event['object']
            key = self.__key(obj)
            value = self.__extract(obj) if kind != 'DELETED' and self.__keep(obj) else None
            with self.__lock:
                if value is None:
                    self.__store.pop(key, None)
                else:
                    self.__store[key] = value
        # The client ignores the resourceVersion of a BOOKMARK, so read it from the raw object.
        resource_version = (event.get('raw_object') or {}).get('metadata', {}).get('resourceVersion')
        if resource_version:
            self.resource_version = resource_version

    def run(self) -> None:
        backoff = MIN_BACKOFF
        relist = True
        while not self.__stopped.is_set():
            if relist:
                try:
                    self.relist()
                    relist = False
                except Exception as ex:
                    log.warning('LIST failed, retrying in %ss: %s', backoff, ex)
                    self.__stopped.wait(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF)
                    continue
            if self.__stopped.is_set():
                break
            try:
                self.watch()
                backoff = MIN_BACKOFF
                continue
            except ApiException as ex:
                relist = True
                if ex.status == HTTP_GONE:
                    continue
                log.warning('WATCH failed, relisting in %ss: %s', backoff, ex)
            except (HTTPError, OSError) as ex:
                log.warning('WATCH connection dropped, re-watching in %ss: %s', backoff, ex)
            except Exception as ex:
                # Re-watching would replay the event that broke; a relist reports it instead.
                relist = True
                log.warning('WATCH event failed, relisting in %ss: %s', backoff, ex)
            self.__stopped.wait(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)


def node_informer(v1_client: Any, page_size: int) -> Informer:
    return Informer(v1_client.list_node, ResourcesExtractor().extract_node_resources,
                    lambda node: node.metadata.name, page_size)


def _is_active(pod: Any) -> bool:
    return pod.status is None or pod.status.phase not in TERMINAL_PHASES


def pod_informer(v1_client: Any, page_size: int) -> Informer:
    # The field selector makes the server send DELETED once a pod terminates;
    # _is_active also drops a terminal pod that still arrives as MODIFIED.
    return Informer(v1_client.list_pod_for_all_namespaces, ResourcesExtractor().extract_pod_requested_resources,
                    lambda pod: f'{pod.metadata.namespace}/{pod.metadata.name}', page_size,
                    keep=_is_active, field_selector=ACTIVE_POD_SELECTOR)


class ClusterCache():
    """Nodes and pods of one kubeconfig context, read like a K8sClient."""

    def __init__(self, context: Optional[str], page_size: int):
        v1_client = core_v1(context)
        self.__nodes = node_informer(v1_client, page_size)
        self.__pods = pod_informer(v1_client, page_size)
        self.__stopped = False
        self.last_used = 0.0

    @property
    def stopped(self) -> bool:
        return self.__stopped

    def start(self) -> None:
        self.__nodes.start()
        self.__pods.start()

    def stop(self) -> None:
        self.__stopped = True
        self.__nodes.stop()
        self.__pods.stop()

    def get_node_resources(self) -> List[NodeResources]:
        return self.__read(self.__nodes)

    def get_pod_resources(self) -> List[PodResources]:
        return self.__read(self.__pods)

    def __read(self, informer: Informer) -> List[Any]:
        try:
            return informer.snapshot(SYNC_TIMEOUT)
        except Exception:
            # Never synced: drop it so the next request starts over, as without the cache.
            if not informer.synced:
                self.stop()
            raise


class CacheRegistry():
    """One ClusterCache per context, started on first use and stopped when idle."""

    def __init__(self, page_size: int, idle_timeout: float = IDLE_TIMEOUT, clock: Callable[[], float] = time.monotonic):
        self.__page_size = page_size
        self.__idle_timeout = idle_timeout
        self.__clock = clock
        self.__lock = threading.Lock()
        self.__caches: Dict[Optional[str], Any] = {}
        self.__closed = threading.Event()
        self.__janitor: Optional[threading.Thread] = None

    def get(self, context: Optional[str]) -> ClusterCache:
        with self.__lock:
            cache = self.__caches.get(context)
            if cache is None or cache.stopped:
                cache = ClusterCache(context, self.__page_size)
                cache.start()
                self.__caches[context] = cache
                self.__start_janitor()
            cache.last_used = self.__clock()
            return cache

    def sweep(self) -> None:
        now = self.__clock()
        with self.__lock:
            idle = [context for context, cache in self.__caches.items()
                    if cache.stopped or now - cache.last_used > self.__idle_timeout]
            for context in idle:
                self.__caches.pop(context).stop()

    def close(self) -> None:
        self.__closed.set()
        with self.__lock:
            for cache in self.__caches.values():
                cache.stop()
            self.__caches.clear()

    def __start_janitor(self) -> None:
        if self.__janitor is None:
            self.__janitor = threading.Thread(target=self.__sweep_until_closed, daemon=True)
            self.__janitor.start()

    def __sweep_until_closed(self) -> None:
        while not self.__closed.wait(SWEEP_INTERVAL):
            self.sweep()
