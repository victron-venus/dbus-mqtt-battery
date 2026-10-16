# Protected local broker profile

`dbus-mqtt-battery` reads MQTT telemetry and exports a battery on the system
D-Bus. Charging decisions can depend on those values. It does not authenticate
MQTT publishers, encrypt MQTT, or replace a physical BMS. A private IP address
alone is not an authentication boundary.

## Supported local configuration

Run a broker on the same trusted Venus host and select the local-only guard:

```ini
[mqtt]
broker = 127.0.0.1
port = 1883
topic_prefix = battery
local_only = true
telemetry_mode = atomic
```

The equivalent command-line option is `--local-only`. It rejects DNS names and
non-loopback IP addresses before constructing the MQTT client. `localhost` is
converted to the numeric IPv4 loopback address, without resolving DNS. Numeric
IPv6 loopback is also accepted. Paho reconnects to that same numeric destination.
This guard does not configure the broker or attest to its access controls.

Configure a dedicated loopback listener on the broker for this trusted process.
Do not expose that anonymous listener on the LAN. Allow only authenticated
producers to publish the configured battery topics on any other listener, and
use verified TLS or a separately authenticated encrypted tunnel for those
producers. Follow the broker and producer documentation for credentials, trust
roots and rotation. Restrict accounts capable of executing code on the Venus
host: another local process can reach a loopback TCP listener. D-Bus and host
administration are also trusted boundaries.

The bridge sends no password or client certificate. Its MQTT client identifier
is a routing identifier, not a secret. Atomic sequence/boot metadata prevents
accidental stale or incomplete samples; it is not a cryptographic signature and
does not protect against an authorized malicious publisher.

## Existing installations

The guard is opt-in so upgrades do not silently disconnect existing remote
brokers. When it is disabled, the legacy remote-broker connection is plaintext
and unauthenticated. It is unsuitable for untrusted networks and does not meet
the protected local profile. Arrange a local broker or externally secured local
forwarder before enabling the guard; test telemetry freshness and retain the
old configuration for rollback. No live broker configuration is changed by an
upgrade of this repository.

## Cryptography and release tools

The runtime implements no cryptographic primitive or password database. Python
release tooling uses `hashlib` SHA-256 for artifacts and invokes maintained Git,
GitHub CLI and signature-verification tools; Git object SHA-1 identity is not a
replacement for artifact authenticity. Follow [release verification](../RELEASING.md)
and validate the expected repository, source and workflow identity. These tools'
HTTPS connections and operator SSH tunnels retain their own verified transport
configuration. Runtime local-only tests do not validate those external tools or
an operator's TLS setup.
