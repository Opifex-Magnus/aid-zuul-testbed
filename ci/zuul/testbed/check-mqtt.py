"""Exercise the real Zuul publish method without network or AID (#1506).

Default: import the scheduler image's installed driver. --source FILE also checks a
release's downloaded mqttconnection.py using only the standard library.
"""
import ast
import json
import logging
import signal
import sys
from types import SimpleNamespace
from unittest.mock import Mock

# A regression that waits indefinitely must fail the probe rather than hang the test bed.
signal.signal(signal.SIGALRM, lambda *args: sys.exit("MQTT publish did not return within 5 seconds"))
signal.alarm(5)

if len(sys.argv) == 3 and sys.argv[1] == "--source":
    with open(sys.argv[2]) as source:
        tree = ast.parse(source.read())
    connection = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MQTTConnection")
    publish = next(n for n in connection.body if isinstance(n, ast.FunctionDef) and n.name == "publish")
    namespace = {"json": json, "get_annotated_logger": lambda log, event: log}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[publish], type_ignores=[])), "mqttconnection.py", "exec"), namespace)
    method = namespace["publish"]
else:
    from zuul.driver.mqtt.mqttconnection import MQTTConnection
    method = MQTTConnection.publish

log = logging.getLogger("mqtt-probe")
log.addHandler(logging.NullHandler())
log.propagate = False
client = Mock()
connection = SimpleNamespace(connected=False, client=client, log=log)
method(connection, "aid/zuul/reports", {"result": "FAILURE"}, 1, None)
client.publish.assert_not_called()

connection.connected = True
client.publish.side_effect = OSError("broker unavailable")
method(connection, "aid/zuul/reports", {"result": "FAILURE"}, 1, None)
client.publish.assert_called_once()

client.reset_mock(side_effect=True)

class Unacknowledged:
    def __getattribute__(self, name):
        accessed.append(name)
        raise AssertionError("publish must not inspect or wait for broker acknowledgment: " + name)

accessed = []
client.publish.side_effect = lambda *args, **kwargs: Unacknowledged()
method(connection, "aid/zuul/reports", {"result": "SUCCESS"}, 1, None)
client.publish.assert_called_once_with("aid/zuul/reports", payload='{"result": "SUCCESS"}', qos=1)
assert not accessed, "publish inspected its acknowledgment handle: " + repr(accessed)
signal.alarm(0)
print("MQTT publish: disconnected, publish error and unacknowledged QoS 1 return without failing or waiting")
