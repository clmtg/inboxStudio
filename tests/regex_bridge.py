"""Test-only adapter for the engine's PCRE-compatible pattern subset."""
import re
import sys
pattern = bytes.fromhex(sys.argv[1]).decode('utf-8').replace('(*UTF)', '')
text = bytes.fromhex(sys.argv[2]).decode('utf-8')
print('true' if re.search(pattern, text) else 'false')
