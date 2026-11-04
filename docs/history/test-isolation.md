# Why the test suite isolates HOME, Anki and AnkiConnect

Moved here from `tests/conftest.py` and the justfile on 2026-11-04. The conftest keeps the
rules; this keeps the incidents that produced them.

## Real config wiped (2026-09-07)

Arete's defaults (`cache.db`, `logs/`, `config.toml`) hang off `Path.home()`. On
2026-09-07 a pytest run wiped the real cache and wrote run logs into
`~/.config/arete/logs`. The autouse `_isolated_home` fixture and the session check
`_real_config_untouched` followed.

## Fixture notes in a real collection (2026-03-03, 2026-03-05)

An integration run wrote four fixture notes ("Hello Integration"/"World", "Healing
Candidate"/"Same Back") into a real collection under a deck named `IntegrationTest`. The
dates come from the Arete ULIDs those notes carried. Nothing caught it for six months.
The session check `_real_collection_untouched` followed.

## Hardcoded integration fixtures

The root conftest once defined `anki_url` and `anki_media_dir` with a hardcoded port
8766 and a bind-mount under `docker/anki_data`, left over from the fixed-port compose
setup, and both shadowed the integration conftest's versions for every test under
`tests/integration/`. They were removed; the integration conftest reads the port off a
throwaway container on a random free port.

## The removed `test-anki` recipes

`just test-anki` and `just mac-test-anki` started a compose container on a fixed port,
then ran pytest. Neither set `ANKI_CONNECT_URL`, so the integration conftest ignored that
container and started a second one on a random port: two containers, one wasted. Both
recipes were removed; locally `just test-integration` runs on its own.
