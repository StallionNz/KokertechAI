import os
import sys
import base64

b64 = os.environ.get("B64_CONFIG", "")

with open('test_config.py', 'w', encoding='utf-8') as f:
    f.write(base64.b64decode(b64).decode('utf-8'))
print('Written test_config.py from base64')
print('Size:', os.path.getsize('test_config.py'))
