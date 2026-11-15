# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately via
[GitHub private vulnerability reporting](../../security/advisories/new), not in a public issue.
You can expect a first answer within a week.

## Supported versions

Only the latest release receives fixes.

## Threat model

- The integration talks to Moonraker over plain HTTP/websocket unless *SSL* is enabled in the
  setup. Anyone on the LAN can read the API key and the printer status in that case. Use
  `trusted_clients` or TLS if that matters to you.
- Moonraker's API key, when used, is stored in the integration's configuration on the remote
  (or the Docker host's `/config` volume).
- The API key is only sent to the configured Moonraker host. Webcam snapshots on other hosts
  are fetched without it.
- Cancel, emergency stop, firmware restart and macros require a second press within
  3 seconds. This guards against accidental presses, not against someone holding the remote.
