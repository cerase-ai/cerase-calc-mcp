"""Put the server module on the path so `import server` works when pytest is
run from the repository root or from tests/."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
