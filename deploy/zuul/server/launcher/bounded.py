"""Fixed-namespace pods for Zuul 14.3's Kubernetes driver (AIDepartment#1698).

The upstream driver creates namespaces/RBAC. This adapter keeps its provider,
quota accounting, request assignment and leak collection, but provisions SSH pods
from admin-owned PodTemplates in existing bounded namespaces. No job may supply a
pod spec. Only the launcher loads this module; scheduler validation stays upstream.
"""
import copy
import json
import re
import time
from enum import StrEnum

from kubernetes.client.exceptions import ApiException
from zuul.driver.kubernetes import KubernetesDriver
from zuul.driver.kubernetes.kubernetesendpoint import KubernetesProviderEndpoint
from zuul.driver.kubernetes.kubernetesmodel import KubernetesInstance
from zuul.driver.kubernetes.kubernetesprovider import KubernetesProvider, KubernetesProviderLabel
from zuul.model import QuotaInformation
from zuul.provider import statemachine


class Resource(statemachine.Resource):
    class Type(StrEnum):
        POD = 'pod'

    def __init__(self, pod):
        super().__init__(pod.metadata.labels or {}, self.Type.POD)
        self.id = pod.metadata.name

    @property
    def unique_id(self):
        return self.id


def namespace(endpoint):
    value = endpoint.connection.connection_config.get('namespace')
    if value not in ('zuul-nodes', 'zuul-gate', 'zuul-post', 'zuul-gate-compute'):
        raise ValueError('bounded Kubernetes connection requires an AID node namespace')
    return value


def instance(pod):
    result = KubernetesInstance('pod', pod.metadata.name,
                                pod.metadata.labels or {}, QuotaInformation(pods=1))
    result.interface_ip = pod.status.pod_ip
    result.private_ipv4 = pod.status.pod_ip
    return result


def quota_exceeded(error):
    """Recognize ResourceQuota admission, never an RBAC/policy denial."""
    if error.status != 403:
        return False
    try:
        status = json.loads(error.body)
    except (TypeError, ValueError):
        return False
    return (isinstance(status, dict) and status.get('reason') == 'Forbidden'
            and isinstance(status.get('message'), str)
            and 'exceeded quota:' in status['message'])


class Create(statemachine.StateMachine):
    def __init__(self, endpoint, node, hostname, label, flavor, image, log):
        super().__init__(node.create_state)
        self.endpoint, self.node, self.hostname = endpoint, node, hostname
        self.label = label
        self.retry_after = 0
        namespace(endpoint)
        if label.kind != 'pod' or image.connection_type != 'ssh':
            raise ValueError('bounded Kubernetes nodes require kind pod and SSH')

    def advance(self):
        ep, node = self.endpoint, self.node
        ns = namespace(ep)
        if self.state == self.START:
            if time.monotonic() < self.retry_after:
                return
            # Persist the identity before create: an interrupted/failed launch must
            # still be deletable. A retry after an uncertain API answer is idempotent.
            node.kubernetes_namespace_id = self.hostname
            template = ep.core_client.read_namespaced_pod_template('job-node', ns)
            pod = ep.core_client.api_client.sanitize_for_serialization(template.template)
            pod = copy.deepcopy(pod)
            pod['apiVersion'], pod['kind'] = 'v1', 'Pod'
            pod['metadata'] = {'name': self.hostname, 'labels': {
                **pod.get('metadata', {}).get('labels', {}), **node.tags}}
            if pod['spec'].get('restartPolicy') != 'Never':
                raise ValueError('job-node PodTemplate must use restartPolicy Never')
            for container in pod['spec']['containers']:
                if not re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', container['image']):
                    raise ValueError('job-node PodTemplate image must be pinned by digest')
            try:
                ep.core_client.create_namespaced_pod(ns, pod)
            except ApiException as error:
                if quota_exceeded(error):
                    # Stay REQUESTED: upstream rechecks N-slot provider quota
                    # and resets the launch clock while in START. Admission can
                    # race its cache, or unmanaged/terminating pods can fill the
                    # namespace. Only API admission confirms a slot is free.
                    self.retry_after = time.monotonic() + 5
                    return
                if error.status != 409:
                    raise
                existing = ep.core_client.read_namespaced_pod(self.hostname, ns)
                if any((existing.metadata.labels or {}).get(k) != v for k, v in node.tags.items()):
                    raise ValueError('pod name collision with another node') from error
            self.state = 'waiting for pod'
        if self.state == 'waiting for pod':
            pod = ep.core_client.read_namespaced_pod(self.hostname, ns)
            if pod.status.phase in ('Failed', 'Succeeded'):
                raise RuntimeError(f'job pod ended before readiness: {pod.status.phase}')
            if pod.metadata.deletion_timestamp or not pod.status.pod_ip:
                return
            if not any(c.type == 'Ready' and c.status == 'True' for c in pod.status.conditions or []):
                return
            node.updateFromInstance(instance(pod))
            self.state = self.COMPLETE
        if self.state == self.COMPLETE:
            self.complete = True


class Delete(statemachine.StateMachine):
    def __init__(self, endpoint, node, log):
        super().__init__(node.delete_state)
        self.endpoint, self.node = endpoint, node

    def fail(self):
        # Launcher._cleanupNode invokes fail() on API errors AND its deletion
        # timeout, then drops a completed allocation. A pod's absence is the only
        # completion signal here. Retain the node/quota and start another attempt.
        self.state, self.complete = self.START, False
        self.start_time = time.time()

    def advance(self):
        # The deterministic name also covers an API create that succeeded just
        # before the launcher lost its ZooKeeper context and rolled back fields.
        name = self.node.kubernetes_namespace_id or f'np{self.node.uuid}'
        ns, api = namespace(self.endpoint), self.endpoint.core_client
        if self.state == self.START:
            try:
                api.delete_namespaced_pod(name, ns)
            except ApiException as error:
                if error.status != 404:
                    raise
            self.state = 'waiting for deletion'
        try:
            api.read_namespaced_pod(name, ns)
        except ApiException as error:
            if error.status != 404:
                raise
            self.state, self.complete = self.COMPLETE, True


class Endpoint(KubernetesProviderEndpoint):
    def startEndpoint(self):
        namespace(self)
        super().startEndpoint()

    def listInstances(self):
        for pod in self.core_client.list_namespaced_pod(namespace(self)).items:
            yield instance(pod)

    def listResources(self, providers):
        for pod in self.core_client.list_namespaced_pod(namespace(self)).items:
            yield Resource(pod)

    def getResourceTypes(self):
        return Resource.Type

    def deleteResource(self, resource):
        try:
            self.core_client.delete_namespaced_pod(resource.id, namespace(self))
        except ApiException as error:
            if error.status != 404:
                raise

    def getQuotaForLabel(self, label):
        return QuotaInformation(pods=1)


def install():
    # ProviderNode and Provider are serialized Zuul variable-base classes and may
    # not be subclassed. Keep their registered types and replace only lifecycle
    # factories in the launcher. The scheduler uses the upstream schema unchanged.
    def create(provider, node, image_external_id, log):
        label = provider.labels[node.label]
        return Create(provider.endpoint, node, f'np{node.uuid}', label,
                      provider.flavors[label.flavor], provider.images[label.image], log)

    def parse_label(provider, label_config, provider_config, connection):
        label = KubernetesProviderLabel(label_config, provider_config)
        # Upstream assumes kubectl and forcibly disables this in __init__.
        # These nodes use SSH: nodescan must populate known_hosts and inventory
        # must never select StrictHostKeyChecking=false, including for post.
        label.host_key_checking = True
        return label

    def delete(provider, node, log):
        return Delete(provider.endpoint, node, log)

    KubernetesProvider.parseLabel = parse_label
    KubernetesProvider.getCreateStateMachine = create
    KubernetesProvider.getDeleteStateMachine = delete
    KubernetesDriver._endpoint_class = Endpoint
