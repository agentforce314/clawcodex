#!/usr/bin/env python3
content = open('/mnt/c/WorkSpace/clawcodex/src/repl/core.py').read()

# Fix invalid escape sequences in help_text string:
# Line 2091: `- `/` - ...`  -> backslash-backtick before /
# Line 2112: `\` + Enter`     -> backslash-backtick before \ + Enter
# Python interprets \` as invalid escape, but we want literal \`
content_fixed = content.replace('\`/\`', '\\\\`/\\\\`').replace('\`\\\\\`', '\\\\`\\\\`')

open('/mnt/c/WorkSpace/clawcodex/src/repl/core.py', 'w').write(content_fixed)
print("Fixed")
