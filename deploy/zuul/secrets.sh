#!/usr/bin/env bash
# secrets.sh: Zuul's Secrets (README.md, "Secrets"), printed as JSON for `kubectl apply -f -`.
# Recovery uses age ciphertext and the owner's 1Password identity, without AID.
#
#   deploy/zuul/secrets.sh restore [NAME] --secrets-dir DIR --identity FILE
#       decrypts NAME.age (default zuul-bootstrap-secrets) and prints a Kubernetes Secret List.
#       Preserves the saved values and namespaces; never generates replacements or applies anything.
#   deploy/zuul/secrets.sh decrypt NAME --secrets-dir DIR --identity FILE
#       decrypts an individual backup, byte for byte, to stdout for its intended consumer.
#       Both commands authenticate the complete ciphertext before emitting anything. Plaintext scratch
#       is private, RAM-backed /dev/shm, removed on exit. Pipe stdout directly to the consumer, never a log.
#
#   deploy/zuul/secrets.sh ca DIR
#       makes ZooKeeper's CA: DIR/ca.key (mode 600) and DIR/ca.crt, in a new directory DIR. Move the
#       key into a secret store and delete the file; ca.crt is public.
#   deploy/zuul/secrets.sh zuul --ca-cert FILE --ca-key FILE --keystore-password FILE
#       for the namespace zuul: zookeeper-tls (ZooKeeper's key pair), zuul-zookeeper-client (the
#       services' client key pair) and zuul-keystore.
#   deploy/zuul/secrets.sh executor --ca-cert FILE --ca-key FILE --keystore-password FILE
#       for the namespace zuul-executor: zuul-zookeeper-client (the executor's own client key pair)
#       and zuul-keystore.
#   deploy/zuul/secrets.sh github --app-id ID --app-key FILE --webhook-token FILE
#       for both namespaces, the same: zuul-github, the GitHub App's ID, its private key (the PEM GitHub made) and
#       the webhook secret set in the App's settings (github.md, "The GitHub App"). Renewing the key is the same
#       command with the new one, then a restart of every Zuul pod; GitHub keeps both keys valid until the old one is
#       deleted in the App's settings.
#   deploy/zuul/secrets.sh executor-ssh
#       for the namespace zuul-executor: node-ssh, a new ed25519 key as id_rsa, the key Zuul's executor adds to its
#       ssh-agent for every job (it fails a job, a nodeless one too, without one). Its public half goes to stderr.
#       #1345's deploy/zuul/node/secrets.sh makes the same Secret with the pair its build nodes accept, and replaces it.
#   deploy/zuul/secrets.sh web --root URL
#       for the namespace zuul: zuul-web, where report links point (zuul.conf, [web] root), the address AID's Zuul UI
#       has, https://<AID's host>:<the UI's port>/ (web.md). A Secret, though it is no secret, because the host is the
#       tailnet's, which this repository does not name. A new address is the same command, then a restart of the
#       scheduler and web.
#   deploy/zuul/secrets.sh web-admin
#       for the namespace zuul: zuul-web-admin, a new random secret that signs the tokens of zuul-web's admin API
#       (zuul.conf, [auth admin]; web.md, "The admin API"). Nothing outside the cluster needs a copy: admin-api.sh makes
#       each token in web's pod. A new secret is the same command, then a restart of web; tokens signed with the old one
#       stop working.
#   deploy/zuul/secrets.sh db
#       for the namespace zuul: zuul-db, new random passwords for the database's users zuul and
#       root. Only before the database's first start: MariaDB sets them once, into an empty
#       directory, and a new Secret afterwards locks Zuul out (README.md, "Secrets").
# Options:
#   --days N   validity in days (default: 3650 for the CA, 1825 for a key pair, and never past the CA)
#
# The keystore password encrypts the project keys Zuul keeps in ZooKeeper: the same one in both
# namespaces, kept in a secret store, never changed (Zuul cannot re-encrypt its keys with another).
# Make it once, for example with `openssl rand -hex 32`. Every FILE may be a pipe:
# --ca-key <(secret-store get zuul-ca-key).
# Needs openssl and jq.
set -euo pipefail

days=""
ca_cert=""
ca_key=""
keystore=""
app_id=""
app_key=""
webhook_token=""
root=""
secrets_dir=""
identity=""
args=()

usage() { awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --days) days=${2:?--days needs a value}; shift 2 ;;
    --ca-cert) ca_cert=${2:?--ca-cert needs a value}; shift 2 ;;
    --ca-key) ca_key=${2:?--ca-key needs a value}; shift 2 ;;
    --keystore-password) keystore=${2:?--keystore-password needs a value}; shift 2 ;;
    --app-id) app_id=${2:?--app-id needs a value}; shift 2 ;;
    --app-key) app_key=${2:?--app-key needs a value}; shift 2 ;;
    --webhook-token) webhook_token=${2:?--webhook-token needs a value}; shift 2 ;;
    --root) root=${2:?--root needs a value}; shift 2 ;;
    --secrets-dir) secrets_dir=${2:?--secrets-dir needs a value}; shift 2 ;;
    --identity) identity=${2:?--identity needs a value}; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
    *) args+=("$1"); shift ;;
  esac
done
[ "${#args[@]}" -ge 1 ] || { usage >&2; exit 2; }
command=${args[0]}
args=("${args[@]:1}")
if [ -n "$days" ]; then
  [[ "$days" =~ ^[0-9]+$ ]] && [ "$days" -ge 1 ] || { echo "--days must be a positive number" >&2; exit 2; }
fi

umask 077
if [ "$command" = restore ] || [ "$command" = decrypt ]; then
  # Recovery must never spill plaintext to a disk-backed TMPDIR.
  [ -d /dev/shm ] && [ -w /dev/shm ] || { echo "recovery needs writable /dev/shm" >&2; exit 1; }
  tmp=$(mktemp -d /dev/shm/ci-secrets.XXXXXXXX)
else
  tmp=$(mktemp -d)
fi
trap 'rm -rf "$tmp"' EXIT

# secret <name> <type> <key=file>...: a Secret as JSON, each key's value the file's contents.
secret() {
  local name=$1 type=$2 i=0 kv
  shift 2
  local jq_args=()
  for kv in "$@"; do
    jq_args+=(--arg "k$i" "${kv%%=*}" --rawfile "v$i" "${kv#*=}")
    i=$((i + 1))
  done
  jq -n --arg name "$name" --arg type "$type" --argjson n "$i" "${jq_args[@]}" '
    {apiVersion: "v1", kind: "Secret", type: $type,
     metadata: {name: $name, labels: {"app.kubernetes.io/part-of": "zuul"}},
     data: ([range($n) as $i | {($ARGS.named["k\($i)"]): ($ARGS.named["v\($i)"] | @base64)}] | add)}'
}

# read_ca: the CA's certificate and key, checked to belong together, the key copied once (it may
# be a pipe) into $tmp/ca.key.
read_ca() {
  [ -n "$ca_cert" ] && [ -n "$ca_key" ] || { echo "$command needs --ca-cert and --ca-key" >&2; exit 2; }
  (umask 077 && cat "$ca_key" > "$tmp/ca.key")
  [ "$(openssl x509 -in "$ca_cert" -noout -pubkey)" = "$(openssl pkey -in "$tmp/ca.key" -pubout)" ] \
    || { echo "the CA key is not $ca_cert's" >&2; exit 1; }
  cp "$ca_cert" "$tmp/ca.crt"
}

# read_keystore: the keystore password, once, into $tmp/keystore, without a trailing newline.
read_keystore() {
  [ -n "$keystore" ] || { echo "$command needs --keystore-password" >&2; exit 2; }
  (umask 077 && tr -d '\n' < "$keystore" > "$tmp/keystore")
  [ "$(wc -c < "$tmp/keystore")" -ge 16 ] || { echo "the keystore password is shorter than 16 characters" >&2; exit 1; }
  # Zuul reads zuul.conf with Python's configparser, for which % starts an interpolation.
  ! grep -q '%' "$tmp/keystore" || { echo "the keystore password must not contain %" >&2; exit 1; }
}

# leaf <name> <CN> <serverAuth|clientAuth> [DNS name...]: a key pair signed by the CA, as
# $tmp/<name>.key and $tmp/<name>.crt.
leaf() {
  local name=$1 cn=$2 usage=$3; shift 3
  local leaf_days=${days:-1825} ca_days san=""
  # Never past the CA's own end.
  ca_days=$(( ($(date -d "$(openssl x509 -in "$tmp/ca.crt" -noout -enddate | cut -d= -f2)" +%s) - $(date +%s)) / 86400 ))
  [ "$ca_days" -gt 30 ] || { echo "the CA expires within 30 days: make a new CA first (README.md)" >&2; exit 1; }
  [ "$leaf_days" -le "$ca_days" ] || leaf_days=$ca_days
  (umask 077 && openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$tmp/$name.key")
  for n in "$@"; do san+=",DNS:$n"; done
  {
    echo "basicConstraints = critical,CA:false"
    echo "keyUsage = critical,digitalSignature"
    echo "extendedKeyUsage = $usage"
    echo "subjectKeyIdentifier = hash"
    echo "authorityKeyIdentifier = keyid"
    [ -z "$san" ] || echo "subjectAltName = ${san#,}"
  } > "$tmp/$name.cnf"
  openssl req -new -key "$tmp/$name.key" -subj "/O=AIDepartment/CN=$cn" -out "$tmp/$name.csr"
  # A random positive serial of 120 bits.
  openssl x509 -req -in "$tmp/$name.csr" -CA "$tmp/ca.crt" -CAkey "$tmp/ca.key" -sha256 \
    -set_serial "0x7$(openssl rand -hex 15)" -days "$leaf_days" -extfile "$tmp/$name.cnf" \
    -out "$tmp/$name.crt" 2> /dev/null
  local purpose=sslserver; [ "$usage" = clientAuth ] && purpose=sslclient
  openssl verify -CAfile "$tmp/ca.crt" -purpose "$purpose" "$tmp/$name.crt" > /dev/null
  echo "$name: $(openssl x509 -in "$tmp/$name.crt" -noout -subject -enddate | paste -sd ' ' -)" >&2
}

# The services' and the executor's client key pair, and the keystore password, for one namespace.
client_secrets() {
  leaf client "zuul client $1" clientAuth
  secret zuul-zookeeper-client kubernetes.io/tls \
    "tls.crt=$tmp/client.crt" "tls.key=$tmp/client.key" "ca.crt=$tmp/ca.crt"
  secret zuul-keystore Opaque "password=$tmp/keystore"
}

case "$command" in
  restore|decrypt)
    [ -n "$secrets_dir" ] && [ -n "$identity" ] \
      || { echo "$command needs --secrets-dir and --identity" >&2; exit 2; }
    if [ "$command" = restore ] && [ "${#args[@]}" -eq 0 ]; then args=(zuul-bootstrap-secrets); fi
    [ "${#args[@]}" -eq 1 ] || { usage >&2; exit 2; }
    name=${args[0]}
    [[ "$name" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "invalid backup name" >&2; exit 2; }
    command -v age >/dev/null || { echo "recovery needs age on PATH" >&2; exit 1; }
    # age can write plaintext before detecting a damaged final chunk. Buffer until it succeeds.
    age --decrypt --identity "$identity" "$secrets_dir/$name.age" > "$tmp/plain" 2>/dev/null \
      || { echo "cannot decrypt $name.age (missing file, wrong identity or damaged ciphertext)" >&2; exit 1; }
    [ -s "$tmp/plain" ] || { echo "empty backup: $name.age" >&2; exit 1; }
    if [ "$command" = decrypt ]; then
      cat "$tmp/plain"
    else
      # Rebuild manifests, not live object metadata (UIDs, resourceVersions, owner references or
      # kubectl's last-applied annotation). A malformed/foreign object aborts the entire restore.
      jq -es '
        def valid:
          . as $s |
          .apiVersion == "v1" and .kind == "Secret" and
          (["zuul","zuul-executor","zuul-nodes","zuul-gate-compute","zuul-gate","zuul-post","build-farm"] | index($s.metadata.namespace)) != null and
          (.metadata.name | type == "string" and test("^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")) and
          (.type == "Opaque" or .type == "kubernetes.io/tls") and
          (.data | type == "object" and length > 0 and all(.[]; type == "string" and test("^([A-Za-z0-9+/]{4})*([A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$")));
        if length != 1 then error("expected one document") else .[0] end |
        (if .apiVersion == "v1" and (.kind == "List" or .kind == "SecretList") then .items
         elif .kind == "Secret" then [.] else error("expected Secrets") end) |
        if type != "array" or length == 0 then error("empty backup") else . end |
        # Kubernetes defaults an omitted, null or empty Secret type to Opaque. The node
        # generators save that pre-admission format directly into the keychain.
        map(if .type == null or .type == "" then .type = "Opaque" else . end) |
        if all(.[]; valid) and
           (map([.metadata.namespace,.metadata.name]) | length == (unique | length))
        then {apiVersion:"v1",kind:"List",items:map({apiVersion,kind,type,data,
          metadata:{name:.metadata.name,namespace:.metadata.namespace}})}
        else error("invalid Secrets") end
      ' "$tmp/plain" > "$tmp/manifests" 2>/dev/null \
        || { echo "invalid CI Secret backup: $name.age" >&2; exit 1; }
      cat "$tmp/manifests"
    fi
    ;;

  ca)
    [ "${#args[@]}" -eq 1 ] || { usage >&2; exit 2; }
    dir=${args[0]}
    [ ! -e "$dir" ] || { echo "$dir exists: the CA goes into a new directory" >&2; exit 2; }
    (umask 077 && mkdir -p "$dir")
    (umask 077 && openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$dir/ca.key")
    # A CA for ZooKeeper's server and its clients only, with no CA below it.
    openssl req -new -x509 -key "$dir/ca.key" -sha256 -days "${days:-3650}" \
      -subj "/O=AIDepartment/CN=Zuul ZooKeeper CA $(date -u +%Y-%m-%d)" \
      -addext "basicConstraints=critical,CA:true,pathlen:0" \
      -addext "keyUsage=critical,keyCertSign,cRLSign" \
      -addext "subjectKeyIdentifier=hash" \
      -out "$dir/ca.crt"
    openssl x509 -in "$dir/ca.crt" -noout -subject -enddate -fingerprint -sha256 >&2
    ;;

  zuul)
    [ "${#args[@]}" -eq 0 ] || { usage >&2; exit 2; }
    read_ca
    read_keystore
    # The names the services reach ZooKeeper by (zuul.conf), and the shorter ones.
    leaf server zookeeper.zuul serverAuth zookeeper zookeeper.zuul zookeeper.zuul.svc \
      zookeeper.zuul.svc.cluster.local
    # ZooKeeper reads its key pair as one PEM file, the certificate first.
    cat "$tmp/server.crt" "$tmp/server.key" > "$tmp/keystore.pem"
    {
      secret zookeeper-tls Opaque "keystore.pem=$tmp/keystore.pem" "ca.crt=$tmp/ca.crt"
      client_secrets zuul
    } | jq -s '{apiVersion: "v1", kind: "List", items: .}'
    ;;

  executor)
    [ "${#args[@]}" -eq 0 ] || { usage >&2; exit 2; }
    read_ca
    read_keystore
    client_secrets zuul-executor | jq -s '{apiVersion: "v1", kind: "List", items: .}'
    ;;

  github)
    [ "${#args[@]}" -eq 0 ] || { usage >&2; exit 2; }
    [ -n "$app_id" ] && [ -n "$app_key" ] && [ -n "$webhook_token" ] \
      || { echo "github needs --app-id, --app-key and --webhook-token" >&2; exit 2; }
    [[ "$app_id" =~ ^[1-9][0-9]{0,11}$ ]] || { echo "--app-id must be the App's number, as its settings page shows it" >&2; exit 2; }
    printf '%s' "$app_id" > "$tmp/app-id"
    # Each file is read once: it may be a pipe.
    (umask 077 && cat "$app_key" > "$tmp/app-key.pem")
    if ! openssl pkey -in "$tmp/app-key.pem" -noout 2> /dev/null \
       || ! grep -q -- '-----BEGIN RSA PRIVATE KEY-----\|-----BEGIN PRIVATE KEY-----' "$tmp/app-key.pem"; then
      echo "--app-key is not a private key in PEM, as GitHub makes it" >&2; exit 1
    fi
    openssl rsa -in "$tmp/app-key.pem" -check -noout > /dev/null 2>&1 \
      || { echo "--app-key is not an RSA key: GitHub's App keys are RSA" >&2; exit 1; }
    (umask 077 && tr -d '\n' < "$webhook_token" > "$tmp/webhook-token")
    [ "$(wc -c < "$tmp/webhook-token")" -ge 32 ] || { echo "the webhook secret is shorter than 32 characters" >&2; exit 1; }
    # zuul.conf takes it through configparser, for which % starts an interpolation; GitHub takes no line break.
    ! grep -q '[%[:space:]]' "$tmp/webhook-token" || { echo "the webhook secret must not contain % or a space" >&2; exit 1; }
    secret zuul-github Opaque "app-id=$tmp/app-id" "app-key.pem=$tmp/app-key.pem" "webhook-token=$tmp/webhook-token"
    echo "zuul-github: App $app_id, key $(openssl pkey -in "$tmp/app-key.pem" -pubout 2> /dev/null | openssl sha256 -r | cut -c1-16)…" >&2
    ;;

  executor-ssh)
    [ "${#args[@]}" -eq 0 ] || { usage >&2; exit 2; }
    command -v ssh-keygen > /dev/null || { echo "executor-ssh needs ssh-keygen, OpenSSH's, which is not on PATH" >&2; exit 1; }
    ssh-keygen -q -t ed25519 -N '' -C zuul-executor -f "$tmp/id_rsa"
    secret node-ssh Opaque "id_rsa=$tmp/id_rsa"
    echo "node-ssh: $(cut -d' ' -f1,2 "$tmp/id_rsa.pub") zuul-executor" >&2
    ;;

  web)
    [ "${#args[@]}" -eq 0 ] || { usage >&2; exit 2; }
    [ -n "$root" ] || { echo "web needs --root" >&2; exit 2; }
    # An origin and the path /, as AID serves an app's UI: Zuul joins t/<tenant>/... to it. No % (configparser).
    [[ "$root" =~ ^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?/$ ]] \
      || { echo "--root must be http(s)://<host>[:<port>]/, the UI's address, with the closing /" >&2; exit 2; }
    printf '%s' "$root" > "$tmp/root"
    secret zuul-web Opaque "root=$tmp/root"
    echo "zuul-web: report links to $root" >&2
    ;;

  web-admin)
    [ "${#args[@]}" -eq 0 ] || { usage >&2; exit 2; }
    # 256 bits, as HS256's key. Hex: nothing configparser would read as an interpolation.
    (umask 077 && openssl rand -hex 32 | tr -d '\n' > "$tmp/secret")
    secret zuul-web-admin Opaque "secret=$tmp/secret"
    echo "zuul-web-admin: a new signing secret for the admin API" >&2
    ;;

  db)
    [ "${#args[@]}" -eq 0 ] || { usage >&2; exit 2; }
    # Hex: nothing that needs quoting in the database's URL or configparser.
    (umask 077 && openssl rand -hex 24 | tr -d '\n' > "$tmp/password" && openssl rand -hex 24 | tr -d '\n' > "$tmp/root")
    secret zuul-db Opaque "password=$tmp/password" "root-password=$tmp/root"
    echo "zuul-db: new passwords for zuul and root" >&2
    ;;

  *) usage >&2; exit 2 ;;
esac
