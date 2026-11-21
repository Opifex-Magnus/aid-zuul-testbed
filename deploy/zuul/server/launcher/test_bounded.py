"""Run with the pinned Zuul 14.3 runtime; no cluster or credentials required."""
import unittest
import collections
import json
import logging
import time
from contextlib import nullcontext
from types import SimpleNamespace as S
from unittest.mock import Mock, patch

from kubernetes.client import ApiClient
from kubernetes.client.exceptions import ApiException
from zuul.driver.kubernetes import KubernetesDriver
from zuul.launcher.server import Launcher
from zuul.model import ProviderNode, QuotaInformation
from zuul.zk.launcher import NodeCache
from check_ssh import check_labels
from zuul.driver.kubernetes.kubernetesmodel import KubernetesProviderNode
import bounded


def pod(name='np0123456789abc', ready=True, phase='Running', labels=None):
    return S(metadata=S(name=name, labels=labels or {}, deletion_timestamp=None),
             status=S(pod_ip='10.42.0.5', phase=phase,
                      conditions=[S(type='Ready', status='True' if ready else 'False')]))


def api_error(status, body):
    # The pinned Kubernetes client's ApiException accepts an HTTP response,
    # not a body keyword. Match the response interface used by its REST client.
    response = S(status=status, reason='test response',
                 data=body if isinstance(body, str) else json.dumps(body),
                 getheaders=lambda: {})
    return ApiException(http_resp=response)


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.api.api_client = ApiClient()
        self.template = {'metadata': {'labels': {'app.kubernetes.io/name': 'zuul-node'}},
                         'spec': {'restartPolicy': 'Never', 'containers': [
                             {'name': 'node', 'image': 'registry/node@sha256:' + 'a' * 64}]}}
        self.api.read_namespaced_pod_template.return_value = S(template=self.template)
        self.endpoint = S(connection=S(connection_config={'namespace': 'zuul-nodes'}), core_client=self.api)
        self.node = S(uuid='0123456789abcdef', create_state={}, delete_state={}, kubernetes_namespace_id=None,
                      tags={'zuul_node_uuid': '0123456789abcdef'}, updateFromInstance=Mock())
        self.label, self.image = S(kind='pod'), S(connection_type='ssh')
        self.api.read_namespaced_pod.return_value = pod(labels=self.node.tags)

    def create(self):
        return bounded.Create(self.endpoint, self.node, 'np0123456789abc', self.label, None, self.image, None)

    def test_ready_node_uses_only_admin_template_and_no_namespace_or_rbac_calls(self):
        for ns in ('zuul-nodes', 'zuul-gate', 'zuul-post', 'zuul-gate-compute'):
            with self.subTest(namespace=ns):
                self.api.reset_mock()
                self.endpoint.connection.connection_config['namespace'] = ns
                machine = self.create()
                machine.advance()
                self.assertTrue(machine.complete)
                self.assertEqual(self.node.kubernetes_namespace_id, 'np0123456789abc')
                self.api.read_namespaced_pod_template.assert_called_once_with('job-node', ns)
                args = self.api.create_namespaced_pod.call_args.args
                self.assertEqual(args[0], ns)
                self.assertEqual(args[1]['spec'], self.template['spec'])
                self.assertEqual(args[1]['metadata']['labels']['zuul_node_uuid'], self.node.tags['zuul_node_uuid'])
                self.assertEqual([c[0] for c in self.api.mock_calls], [
                    'read_namespaced_pod_template', 'create_namespaced_pod', 'read_namespaced_pod'])
                self.assertEqual(self.node.updateFromInstance.call_args.args[0].interface_ip, '10.42.0.5')

    def test_waits_for_ssh_readiness_without_recreating(self):
        self.api.read_namespaced_pod.return_value = pod(ready=False)
        machine = self.create()
        machine.advance()
        self.assertFalse(machine.complete)
        self.node.updateFromInstance.assert_not_called()
        self.api.read_namespaced_pod.return_value = pod()
        machine.advance()
        self.assertTrue(machine.complete)
        self.api.create_namespaced_pod.assert_called_once()

    def test_completed_or_failed_pod_is_not_a_usable_node(self):
        for phase in ('Succeeded', 'Failed'):
            self.api.read_namespaced_pod.return_value = pod(phase=phase)
            with self.assertRaisesRegex(RuntimeError, 'ended before readiness'):
                self.create().advance()

    def test_launch_retry_adopts_only_its_own_pod(self):
        self.api.create_namespaced_pod.side_effect = ApiException(status=409)
        machine = self.create()
        machine.advance()
        self.assertTrue(machine.complete)
        self.api.read_namespaced_pod.return_value = pod(labels={'zuul_node_uuid': 'another'})
        with self.assertRaisesRegex(ValueError, 'collision'):
            self.create().advance()

    def test_only_resource_quota_admission_waits(self):
        for status, body in (
                (403, {'reason': 'Forbidden', 'message': 'RBAC denied'}),
                (403, {'reason': 'Forbidden', 'message': 'violates PodSecurity'}),
                (403, 'not JSON'), (403, []),
                (403, {'reason': 'Forbidden', 'message': None}), (503, None)):
            with self.subTest(status=status, body=body):
                self.api.create_namespaced_pod.side_effect = api_error(status, body)
                with self.assertRaises(ApiException):
                    self.create().advance()

    def test_upstream_launcher_waits_for_n_slots_and_confirmed_deletion(self):
        # Real launcher create loop, quota arithmetic and NodeCache accounting;
        # only ZK persistence, Kubernetes and nodescan are stand-ins.
        for capacity in (1, 2, 4):
            with self.subTest(capacity=capacity):
                self.setUp()
                log = Mock()
                cache = object.__new__(NodeCache)
                cache._cached_objects = {}
                cache._provider_quota_used = collections.defaultdict(QuotaInformation)
                cached_quota = {}
                provider = S(resource_limits={'pods': capacity}, canonical_name='bounded',
                             name='bounded', endpoint=self.endpoint, launch_timeout=60)

                def account(node):
                    NodeCache._handleQuota(cache, ProviderNode.ALLOCATED_STATES,
                        cached_quota, cache._provider_quota_used,
                        None, None, None, node.uuid, node)

                launcher = S(log=log, wake_event=Mock(),
                    createZKContext=lambda *args: nullcontext(None),
                    _getProviderForNode=lambda node: provider,
                    getImageExternalId=lambda *args: None,
                    _emitMessages=lambda *args: None,
                    _setSubnodeStates=lambda *args: None,
                    _checkNodescanRequest=lambda *args: True,
                    getProviderQuota=lambda p: QuotaInformation(default=float('inf')),
                    _getUniqueProviders=lambda: [provider], api=S(nodes_cache=cache))
                launcher.getProviderQuotaAvailable = lambda *a, **kw: Launcher.getProviderQuotaAvailable(launcher, *a, **kw)
                launcher.doesProviderHaveQuotaForNode = lambda *a: Launcher.doesProviderHaveQuotaForNode(launcher, *a)
                provider.getCreateStateMachine = lambda node, *a: bounded.Create(
                    self.endpoint, node, 'np' + node.uuid, self.label, None, self.image, log)
                self.node._lock = None
                self.node.activeContext = lambda ctx: nullcontext(None)
                self.node.create_state_machine = None
                self.node.State = ProviderNode.State
                self.node.state = ProviderNode.State.REQUESTED
                self.node.quota = QuotaInformation(pods=1)
                self.node.provider = provider.canonical_name
                self.node.subnodes = []
                self.node.releaseLock = Mock()
                def set_state(state):
                    self.node.state = state
                    account(self.node)
                self.node.setState = set_state
                cache._cached_objects[self.node.uuid] = self.node
                predecessors = []
                for index in range(capacity):
                    predecessor = S(uuid=str(index), provider=provider.canonical_name,
                        # USED is retained during cleanup in Zuul 14.3;
                        # it has no separate DELETING provider-node state.
                        state=ProviderNode.State.USED, quota=QuotaInformation(pods=1),
                        delete_state={}, kubernetes_namespace_id='old' + str(index))
                    cache._cached_objects[predecessor.uuid] = predecessor
                    account(predecessor)
                    predecessors.append(predecessor)
                Launcher._checkNodeCreate(launcher, self.node, log)
                self.api.create_namespaced_pod.assert_not_called()
                deletion = bounded.Delete(self.endpoint, predecessors[0], log)
                deletion.advance()  # DELETE accepted, pod still present
                self.assertFalse(deletion.complete)
                Launcher._checkNodeCreate(launcher, self.node, log)
                self.api.create_namespaced_pod.assert_not_called()
                self.api.read_namespaced_pod.side_effect = ApiException(status=404)
                deletion.advance()
                self.assertTrue(deletion.complete)
                del cache._cached_objects[predecessors[0].uuid]
                account(predecessors[0])

                # Cached slot is free, but API admission still sees full quota
                # (unmanaged pod or delayed quota reconciliation).
                self.api.create_namespaced_pod.side_effect = api_error(403,
                    {'reason': 'Forbidden', 'message':
                     'pods "np": forbidden: exceeded quota: budget, requested: pods=1'})
                self.api.read_namespaced_pod.side_effect = None
                with patch('time.time', return_value=time.time() + provider.launch_timeout * 10), \
                     patch('bounded.time.monotonic', return_value=100):
                    Launcher._checkNodeCreate(launcher, self.node, log)
                    machine = self.node.create_state_machine
                    self.assertEqual(machine.state, machine.START)
                    self.assertEqual(self.node.state, ProviderNode.State.REQUESTED)
                    self.assertFalse(machine.complete)
                    # No hammering the API during the retry delay.
                    Launcher._checkNodeCreate(launcher, self.node, log)
                    self.api.create_namespaced_pod.assert_called_once()
                self.node.create_state = machine.toDict()
                self.node.create_state_machine = None  # launcher restart
                self.api.create_namespaced_pod.side_effect = None
                Launcher._checkNodeCreate(launcher, self.node, log)
                self.assertEqual(self.node.state, ProviderNode.State.READY)
                self.assertEqual(cache.getQuota(provider).getResources()['pods'], capacity)
                self.assertEqual(self.api.create_namespaced_pod.call_count, 2)
                self.node.updateFromInstance.assert_called_once()

    def test_delete_waits_until_pod_is_gone_and_survives_restart(self):
        self.node.kubernetes_namespace_id = 'np0123456789abc'
        machine = bounded.Delete(self.endpoint, self.node, None)
        machine.advance()
        self.assertFalse(machine.complete)
        self.api.delete_namespaced_pod.assert_called_once_with('np0123456789abc', 'zuul-nodes')
        self.node.delete_state = machine.toDict()
        resumed = bounded.Delete(self.endpoint, self.node, None)
        self.api.read_namespaced_pod.side_effect = ApiException(status=404)
        resumed.advance()
        self.assertTrue(resumed.complete)
        self.api.delete_namespaced_pod.assert_called_once()

    def test_failed_launch_still_has_a_deletable_identity(self):
        self.api.create_namespaced_pod.side_effect = ApiException(status=503)
        with self.assertRaises(ApiException):
            self.create().advance()
        self.assertEqual(self.node.kubernetes_namespace_id, 'np0123456789abc')
        self.api.delete_namespaced_pod.side_effect = ApiException(status=404)
        self.api.read_namespaced_pod.side_effect = ApiException(status=404)
        machine = bounded.Delete(self.endpoint, self.node, None)
        machine.advance()
        self.assertTrue(machine.complete)

    def test_delete_after_uncertain_create_reconstructs_identity(self):
        self.api.read_namespaced_pod.side_effect = ApiException(status=404)
        machine = bounded.Delete(self.endpoint, self.node, None)
        machine.advance()
        self.api.delete_namespaced_pod.assert_called_once_with('np0123456789abcdef', 'zuul-nodes')
        self.assertTrue(machine.complete)

    def test_api_failures_are_not_deletion_success(self):
        self.node.kubernetes_namespace_id = 'np0123456789abc'
        self.api.delete_namespaced_pod.side_effect = ApiException(status=403)
        with self.assertRaises(ApiException):
            bounded.Delete(self.endpoint, self.node, None).advance()

    def test_upstream_cleanup_retains_failed_timed_out_and_restarted_deletes(self):
        # Exercise the real caller: it calls fail() on both exceptions and
        # DELETE_TIMEOUT. Its ZK/tracing boundary is the only stand-in.
        log = Mock()
        launcher = S(log=log, createZKContext=lambda *args: nullcontext(None),
                     _getProviderForNode=lambda *args, **kwargs: S(
                         driver=S(name='kubernetes'),
                         getDeleteStateMachine=lambda node, log: bounded.Delete(self.endpoint, node, log)),
                     nodescan_worker=Mock(), wake_event=Mock(), api=Mock(),
                     tracer=S(start_as_current_span=lambda *args, **kwargs: nullcontext(None)),
                     DELETE_TIMEOUT=Launcher.DELETE_TIMEOUT)
        self.node._lock = None
        self.node.activeContext = lambda ctx: nullcontext(None)
        self.node.nodescan_request = None
        self.node.delete_state_machine = None
        self.node.state = ProviderNode.State.USED
        self.node.State = ProviderNode.State
        self.node.request_id = 'request'
        self.node.delete = Mock()
        self.node.releaseLock = Mock()
        self.node.span_info = None
        self.node.getSpanAttributes = lambda: {}
        launcher.api.getNodesetRequest.return_value = None
        for status in (503, 403):
            self.api.delete_namespaced_pod.side_effect = ApiException(status=status)
            Launcher._cleanupNode(launcher, self.node, log)
            self.assertFalse(self.node.delete_state_machine.complete)
            self.node.delete.assert_not_called()
        self.api.delete_namespaced_pod.side_effect = None
        self.api.read_namespaced_pod.side_effect = ApiException(status=503)
        Launcher._cleanupNode(launcher, self.node, log)
        self.node.delete.assert_not_called()
        machine = self.node.delete_state_machine
        machine.start_time = time.time() - Launcher.DELETE_TIMEOUT - 1
        Launcher._cleanupNode(launcher, self.node, log)
        self.assertFalse(machine.complete, 'the upstream timeout must retain the allocation')
        self.node.delete.assert_not_called()
        self.node.delete_state = machine.toDict()
        self.node.delete_state_machine = None  # launcher restart
        self.api.read_namespaced_pod.side_effect = None
        Launcher._cleanupNode(launcher, self.node, log)
        self.node.delete.assert_not_called()  # still terminating
        self.api.read_namespaced_pod.side_effect = ApiException(status=404)
        with patch('zuul.launcher.server.tracing.restoreSpan'), \
             patch('zuul.launcher.server.trace.use_span', return_value=nullcontext(None)):
            Launcher._cleanupNode(launcher, self.node, log)
        self.assertTrue(self.node.delete_state_machine.complete)
        self.node.delete.assert_called_once()
        self.node.releaseLock.assert_called_once()

    def test_real_provider_labels_scan_ssh_and_populate_executor_known_hosts(self):
        check_labels([dict(name=name, kind='pod', image=name, flavor=name, spec={},
                           project_canonical_name='github.com/kristofk/aid-zuul-config')
                      for name in ('aid-node', 'aid-gate-node', 'aid-post-node')])

    def test_namespace_kind_connection_and_unpinned_images_fail_closed(self):
        self.endpoint.connection.connection_config['namespace'] = 'zuul'
        with self.assertRaises(ValueError):
            self.create()
        self.endpoint.connection.connection_config['namespace'] = 'zuul-nodes'
        self.label.kind = 'namespace'
        with self.assertRaises(ValueError):
            self.create()
        self.label.kind = 'pod'
        self.image.connection_type = 'kubectl'
        with self.assertRaises(ValueError):
            self.create()
        self.image.connection_type = 'ssh'
        self.template['spec']['containers'][0]['image'] = 'registry/node:latest'
        with self.assertRaisesRegex(ValueError, 'pinned by digest'):
            self.create().advance()
        self.api.create_namespaced_pod.assert_not_called()

    def test_leaks_are_pods_and_collection_never_deletes_a_namespace(self):
        self.api.list_namespaced_pod.return_value = S(items=[pod(labels=self.node.tags)])
        resources = list(bounded.Endpoint.listResources(self.endpoint, []))
        self.assertEqual(resources[0].metadata, self.node.tags)
        self.assertEqual(resources[0].type, 'pod')
        bounded.Endpoint.deleteResource(self.endpoint, resources[0])
        self.api.delete_namespaced_pod.assert_called_once_with('np0123456789abc', 'zuul-nodes')
        instances = list(bounded.Endpoint.listInstances(self.endpoint))
        self.assertEqual(instances[0].external_id, 'pod=np0123456789abc')
        self.assertEqual(instances[0].getQuotaInformation().getResources()['pods'], 1)

    def test_additive_install_keeps_static_driver_factories_and_reuse(self):
        from zuul.driver.static import StaticDriver
        from zuul.driver.static.staticprovider import StaticProvider
        before = (StaticDriver._endpoint_class, StaticProvider.parseLabel,
                  StaticProvider.getCreateStateMachine, StaticProvider.getDeleteStateMachine)
        bounded.install()
        self.assertEqual(before, (StaticDriver._endpoint_class, StaticProvider.parseLabel,
                                  StaticProvider.getCreateStateMachine, StaticProvider.getDeleteStateMachine))
        for name in ('aid-node', 'aid-gate-node', 'aid-post-node', 'aid-staging-node'):
            label = StaticProvider.parseLabel(None, dict(name=name, image=name, flavor=name, config_hash='regression',
                project_canonical_name='github.com/kristofk/aid-zuul-config'), {}, None)
            self.assertTrue(label.reuse)
            self.assertTrue(label.host_key_checking)
        driver = KubernetesDriver()
        for part, ns in [('check', 'zuul-nodes'), ('gate', 'zuul-gate'), ('post', 'zuul-post'), ('gate-compute', 'zuul-gate-compute')]:
            connection = driver.getConnection(f'aid-{part}-pods', dict(driver='kubernetes', namespace=ns))
            self.assertEqual(bounded.namespace(S(connection=connection)), ns)

    def test_factory_is_compatible_with_pinned_upstream_and_node_driver_data(self):
        bounded.install()
        self.assertIs(KubernetesDriver._endpoint_class, bounded.Endpoint)
        node = KubernetesProviderNode()
        node._set(kubernetes_namespace_id='np0123456789abc')
        self.assertEqual(node.getDriverData(), {'kubernetes_namespace_id': 'np0123456789abc'})
        driver = KubernetesDriver()
        with patch('zuul.provider.QuotaCache'):
            endpoint = driver._getEndpoint(None, S(connection_name='bounded', connection_config={'namespace': 'zuul-nodes'}), 'system')
        self.assertIsInstance(endpoint, bounded.Endpoint)
        self.assertIs(endpoint.driver, driver)


if __name__ == '__main__':
    unittest.main(verbosity=2)
