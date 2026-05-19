# ClawCodex patch metadata index
# Each patch has a corresponding .json file in this directory.
# Schema:
# {
#   "id": "<patch-name-without-ext>",
#   "description": "What this patch does",
#   "affected_modules": ["list of module prefixes this patch touches"],
#   "applied_at": "ISO date string",
#   "upstream_version_introduced": "upstream/vYYYY_MM tag when patch was created"
# }