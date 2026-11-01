# Recorded producer interoperability fixtures

`frames.json` contains native executions of the actual ESPHome
`series_telemetry.h` collector and the exact MQTT publication lambda returned by
`generate-series-config.py`. Inventories cover `[16]`, `[4,8,4]`, four 32-cell
members, and the schema-1 four-by-four boundary.

The host adapter supplies only the small JSON API used by that lambda. Its
serialization is not ArduinoJson, so these fixtures qualify field values, array
inventories and evidence masks, not firmware serialization, BLE hardware or MQTT
transport. The native collector executes with AddressSanitizer and
UndefinedBehaviorSanitizer during fixture generation.

Consumer CI reads the committed files without a compiler or producer checkout.
`provenance.json` records the producer commit, source hashes, publication-lambda
hashes and the recorded-frame hash.

To regenerate, use the pinned producer commit recorded in that file, install its
hash-locked `.github/requirements-workflow-contracts.txt` into a scratch Python
environment, and run:

```bash
python tests/fixtures/series_producer/generate.py --producer /path/to/esphome-jbd-bms-mqtt
```

The generator refuses uncommitted changes to the producer inputs. Temporary
native sources and executables are removed after generation. Review fixture and
provenance changes together whenever updating the pinned producer.
