# Session Search shard bundle transport

## Purpose

`session-search-shard-bundle.v1` is the portable data-plane artifact between accepted Session Search corpus authority and a derived Neon shard materializer.

It exists to keep corpus bytes out of the MarcoPolo request path and to separate source authority, transport, and target database runtime.

```text
accepted/frozen corpus authority
        ->
deterministic shard bundle
        ->
private Google Drive staging
        ->
Arcade Hosted materializer
        ->
Neon BUILDING generation
        ->
independent verify -> READY
```

Google Drive is durable transport/staging, not authority. Neon remains disposable/rebuildable projection state. Repository migrations remain the schema/role/function IaC.

## Bundle contract

The ZIP contains exactly three members:

- `manifest.json`
- `sessions.csv`
- `messages.csv`

The ZIP uses deterministic member ordering, fixed timestamps/permissions, and stored members so the same verified source snapshot produces byte-identical bundle bytes.

The manifest binds:

- provider and shard ID;
- materializer/projection schema versions;
- accepted artifact count and accepted-set digest;
- source projection digest;
- source adapter set;
- session/message/text-byte counts;
- deterministic generation ID;
- per-file SHA-256, size, row count, and column contract.

The bundle contains private session/message data. Public receipts expose only aggregate counts and digests.

## Verification

`scripts/shard_bundle.py verify` checks:

- exact member set;
- file hashes/sizes/columns/row counts;
- session/message counts;
- searchable text bytes;
- source projection digest reconstructed from CSV rows;
- source manifest digest;
- generation ID.

A bundle is not authoritative merely because it is internally valid. Promotion also requires an externally recorded/expected bundle digest derived from accepted authority.

## Drive staging

The first real bundle acceptance used the verified frozen xAI corpus and produced:

- provider/shard: `xai/xai-0001`;
- sessions: 60;
- searchable messages: 17,790;
- bundle bytes: 40,649,490;
- bundle SHA-256: `7ccf184964a3243f99c7d7baebf44f3906886ca70d3a450842291e0e63a9ae63`;
- generation: `6095e643b3fc4f133c94c04db24565dce6bbd456a308860820e94e70f8047835`.

The exact bundle was uploaded privately to Google Drive and then downloaded/read back through the native ChatGPT Google Drive connector. Native readback preserved MIME type, byte size, destination parent, and the locally recomputed bundle SHA-256.

The Drive file ID is operational/private metadata and is intentionally omitted from the public repository receipt.

## Runtime boundary

The existing direct `SESSION_SEARCH_DATABASE_URL -> psycopg2` materializer path is retained only as transitional/historical acceptance machinery until the Arcade consumer has equivalent bundle->BUILDING verification. It is not the desired long-term promotion interface.

The target long-term execution path is one generic Arcade Hosted materializer that:

1. authenticates to Google Drive through Arcade Google OAuth;
2. downloads one exact bundle by Drive file identity;
3. verifies the bundle before database mutation;
4. obtains target Neon control/data-plane access through Arcade-managed secrets/authorization rather than exposing credentials to the model;
5. applies repository-versioned migrations to a fresh target;
6. loads only a BUILDING generation;
7. reports aggregate verification evidence;
8. never activates/registers a production catalog generation in the same operation.

Activation/cutover remains a separate protected operation.
