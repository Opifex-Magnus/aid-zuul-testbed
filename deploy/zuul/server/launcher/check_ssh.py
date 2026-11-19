"""Real Zuul label -> TCP/SSH nodescan -> executor known_hosts regression.

Used by test_bounded.py and the trusted-config provider checker. The only SSH
server is a local fixture with a new public host key; no cluster is contacted.
"""
import logging
import socket
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace as S

import paramiko
from zuul.driver.kubernetes.kubernetesmodel import KubernetesProviderNode
from zuul.driver.kubernetes.kubernetesprovider import KubernetesProvider
from zuul.executor.server import AnsibleJob
from zuul.launcher.server import Launcher, NodescanRequest, NodescanWorker
import bounded


@contextmanager
def ssh_server():
    key = paramiko.RSAKey.generate(2048)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(20)
    listener.settimeout(0.1)
    stopped = threading.Event()
    transports = []
    logger = logging.getLogger('paramiko.transport')
    level = logger.level
    logger.setLevel(logging.CRITICAL)

    def serve():
        while not stopped.is_set():
            try:
                client, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            transport = paramiko.Transport(client)
            transports.append(transport)
            transport.add_server_key(key)
            try:
                transport.start_server(server=paramiko.ServerInterface())
            except (paramiko.SSHException, EOFError, OSError):
                transport.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1], f'{key.get_name()} {key.get_base64()}'
    finally:
        stopped.set()
        listener.close()
        for transport in transports:
            transport.close()
        thread.join(timeout=5)
        logger.setLevel(level)


def check_labels(configs):
    """Construct actual provider labels from public config, then scan each."""
    bounded.install()
    log = logging.getLogger('bounded-ssh-test')
    with ssh_server() as (port, expected_key):
        worker = NodescanWorker()
        worker.start()
        try:
            for config in configs:
                # This is the launcher's patched parseLabel, not a mock label or
                # a direct constructor that bypasses the production factory.
                label = KubernetesProvider.parseLabel(None, {**config, 'config_hash': 'regression'}, {}, None)
                assert label.host_key_checking, label.name
                provider_node = KubernetesProviderNode()
                provider_node._set(
                    name=['node'], label=label.name, interface_ip='127.0.0.1',
                    connection_type='ssh', connection_port=port,
                    host_key_checking=label.host_key_checking, boot_timeout=20,
                    username='zuul', nodescan_request=None)
                scan = NodescanRequest(provider_node, log)
                provider_node._set(nodescan_request=scan)
                worker.addRequest(scan)
                deadline = time.monotonic() + 25
                while not scan.complete and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert scan.complete, f'{label.name}: scan timed out'
                assert scan.init_connection_attempts > 0, 'scan skipped TCP'
                assert expected_key in scan.result(), f'{label.name}: host key missing'
                # Use the actual caller that copies the scan result to the node.
                node_data = provider_node.getNodeData()
                node_data.update(name=['node'], label=label.name, provider='bounded',
                                 slot=0, reuse=False, node_properties={}, nodescan_request=scan)
                node = S(**node_data)
                assert Launcher._checkNodescanRequest(S(), node, log)
                job = S(job=S(host_variables={}, group_variables={}), normal_vars={},
                        nodeset=S(getGroups=lambda: []), executor_server=S(default_username='zuul'))
                hosts = AnsibleJob.getHostList(job, {}, [node])
                assert hosts[0]['host_keys'] == [f'[127.0.0.1]:{port} {key}' for key in scan.result()]
                assert 'StrictHostKeyChecking=false' not in hosts[0]['host_vars'].get('ansible_ssh_common_args', '')
                worker.removeRequest(scan)
        finally:
            worker.stop()
            worker.join()
