# ClawCodex patch metadata index

Metadata files are stored under `upstream/` using the upstream commit hash as prefix.

## File naming

```
patches/metadata/upstream/{commit}_{patch-name}.json
```

Example: `b125e16_0001-port-to-python.json`

## Schema

```json
{
  "id": "0001-port-to-python",
  "description": "What this patch does",
  "affected_modules": ["list of module prefixes this patch touches"],
  "applied_at": "ISO date string",
  "upstream_version_introduced": "git commit hash (e.g. b125e16)"
}
```

## Notes

- One metadata file per patch
- Multiple patches per upstream commit are supported
- `upstream_version_introduced` records the exact upstream commit this patch was generated against