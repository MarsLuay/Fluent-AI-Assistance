import sys
sys.path.insert(0, 'source/03-protocol-builder/libs/fluentcoder')
import importlib
import types

sys.modules['tecanlab'] = importlib.import_module('fluentcoder')
try:
    from examples.lm_authoring_attempt1 import build_worktable
    wt = build_worktable()
    print("Success")
except Exception as e:
    import traceback
    traceback.print_exc()
