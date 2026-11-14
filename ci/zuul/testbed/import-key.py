"""The test bed's project key for the config copy, as zuul-admin import-keys takes it (run.sh).

Run in the scheduler's image, before the scheduler starts: reads the key sync.sh made (unencrypted, a fixture),
encrypts it with the keystore password as Zuul stores its own (zuul/lib/keystorage.py, a project's "secrets" key),
and writes the import file. Zuul then takes this key for the project instead of making one, so the fixture secret
sync.sh encrypted for it decrypts. Reads ZUUL_KEYSTORE_PASSWORD and TESTBED_CONFIG_REPOSITORY from the environment.

    python3 import-key.py KEY.pem OUT.json
"""

import json
import os
import sys
import time
import urllib.parse

from zuul.lib import encryption

key, _ = encryption.deserialize_rsa_keypair(open(sys.argv[1], "rb").read())
pem = encryption.serialize_rsa_private_key(key, os.environ["ZUUL_KEYSTORE_PASSWORD"].encode()).decode()
project = os.environ["TESTBED_CONFIG_REPOSITORY"]
# The connection is the test bed's github (zuul.conf).
path = "/keystorage/github/%s/%s/secrets" % (project.split("/")[0], urllib.parse.quote_plus(project))
entry = {"schema": 1, "keys": [{"version": 0, "created": int(time.time()), "private_key": pem}]}
with open(sys.argv[2], "w") as out:
    json.dump({"keys": {path: entry}}, out)
print(f"import-key.py: the test bed's key for {project}, at {path}")
