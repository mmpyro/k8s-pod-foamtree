import logging
import threading
from typing import Any, Dict, List, Optional
from k8sfoam.src.common.dtos import DisruptionBudget, NodeResources, PodResources
from k8sfoam.src.synthetic.model import ClusterModel
from k8sfoam.src.synthetic.scenarios import DEFAULT_SEED, build_all
from k8sfoam.src.utils.extractors import ResourcesExtractor

logger = logging.getLogger(__name__)

DEFAULT_CHURN_INTERVAL = 10.0


class SyntheticCluster():
    """One synthetic context, read like a ClusterCache: the same DTOs, version and change wait."""

    def __init__(self, model: ClusterModel):
        self.__model = model
        self.__extractor = ResourcesExtractor()
        self.__changed = threading.Condition()
        self.__version = 0
        self.__nodes, self.__pods, self.__budgets = self.__extract()

    @property
    def context(self) -> str:
        return self.__model.context

    @property
    def version(self) -> int:
        with self.__changed:
            return self.__version

    def wait_for_change(self, since: int, timeout: float) -> int:
        """Block until the version moves past `since` or `timeout` passes; return the current version."""
        with self.__changed:
            self.__changed.wait_for(lambda: self.__version != since, timeout)
            return self.__version

    def get_node_resources(self) -> List[NodeResources]:
        with self.__changed:
            return list(self.__nodes)

    def get_pod_resources(self) -> List[PodResources]:
        with self.__changed:
            return list(self.__pods)

    def get_disruption_budgets(self) -> List[DisruptionBudget]:
        with self.__changed:
            return list(self.__budgets)

    def churn(self) -> str:
        """Apply one random change to the cluster and tell the watchers."""
        with self.__changed:
            what = self.__model.churn()
            self.__nodes, self.__pods, self.__budgets = self.__extract()
            self.__version += 1
            self.__changed.notify_all()
            return what

    def __extract(self) -> tuple:
        nodes = [self.__extractor.extract_node_resources(n) for n in self.__model.nodes]
        pods = [self.__extractor.extract_pod_requested_resources(p) for p in self.__model.pods]
        budgets = [self.__extractor.extract_disruption_budget(b) for b in self.__model.disruption_budgets()]
        return nodes, pods, budgets


class SyntheticRegistry():
    """Every synthetic context, read like a CacheRegistry, with an optional thread that keeps them changing."""

    def __init__(self, seed: int = DEFAULT_SEED, churn_interval: float = DEFAULT_CHURN_INTERVAL):
        self.__clusters: Dict[str, SyntheticCluster] = {m.context: SyntheticCluster(m) for m in build_all(seed)}
        self.__default = next(iter(self.__clusters))
        self.__closed = threading.Event()
        self.__churn_interval = churn_interval
        if churn_interval > 0:
            threading.Thread(target=self.__churn_until_closed, daemon=True).start()

    def contexts(self) -> List[Dict[str, Any]]:
        return [{'context': name, 'active': name == self.__default} for name in self.__clusters]

    def get(self, context: Optional[str]) -> SyntheticCluster:
        """The cluster for `context`; None means the active one, like a kubeconfig's current-context."""
        cluster = self.__clusters.get(context or self.__default)
        if cluster is None:
            raise LookupError(f'Unknown synthetic context: {context}')
        return cluster

    def churn(self) -> None:
        for cluster in self.__clusters.values():
            logger.debug('%s: %s', cluster.context, cluster.churn())

    def close(self) -> None:
        self.__closed.set()

    def __churn_until_closed(self) -> None:
        while not self.__closed.wait(self.__churn_interval):
            self.churn()
