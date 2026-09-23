# Security Policy

## Reporting a vulnerability

Report suspected vulnerabilities privately to the maintainers (repository
issues marked confidential, or direct contact). Please do not open public
issues containing exploit details. Reports are acknowledged within 48 hours.

## Design commitments

- **No PII at rest**: mapping records store fingerprints, entity mappings and
  occurrence spans only — never the original or transformed document.
- **No PII in logs**: request logs carry a salted hash of `payload_id`,
  direction, sizes and latency; exception classes are logged without their
  messages.
- **Fail closed**: if the authoritative store cannot guarantee a mapping is
  durable, the request is refused (`503` + `Retry-After`) rather than served
  from partial state.
- **No secrets in the repository**: credentials come exclusively from the
  environment (`.env`, secrets manager); compose files refuse to start
  without them and never pass passwords on a command line.
- **Deterministic surrogates from a cryptographic primitive**: synthetic
  values are derived from SHA-256 in counter mode, not from a
  general-purpose PRNG.

## Supported versions

The latest tagged release / default branch is supported.
