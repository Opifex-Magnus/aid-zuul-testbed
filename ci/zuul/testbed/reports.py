"""Check native 14.3.0 MQTT reports from the standard test-bed's real gate runs, never forge events."""
import json
import sys
import urllib.request

reports = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
gate = [r for r in reports if r["pipeline"] == "gate"]
for action in ["enqueue", "start", "failure", "success", "dequeue"]:
    assert any(r["action"] == action for r in gate), f"no gate {action} report"
for action in ["failure", "success"]:
    report = next(r for r in gate if r["action"] == action)
    assert report["changes"] and report["changes"][0]["change"], report
    assert report["uuid"] and report["buildset"]["uuid"], report
    url = "http://127.0.0.1:9000/api/tenant/aid/buildset/" + report["buildset"]["uuid"]
    with urllib.request.urlopen(url, timeout=10) as response:
        final = json.load(response)
    assert final["uuid"] == report["buildset"]["uuid"] and final["pipeline"] == "gate"
    assert final["message"] is not None and final["result"] == action.upper(), final
    print("MQTT gate", action, "change", report["changes"][0]["change"], "buildset", final["uuid"], "final", final["result"], flush=True)
print("MQTT: native enqueue/start/dequeue/failure/success, change identities and finalized SQL results checked", flush=True)
