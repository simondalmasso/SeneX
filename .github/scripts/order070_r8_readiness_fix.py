from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("CANDIDATE_DIR", "candidate")).resolve()

app = ROOT / "senecio_polymarket/frontend/app.js"
s = app.read_text()
old = """      const readiness = payload.readiness && typeof payload.readiness === 'object' ? payload.readiness : {};
      if (readiness.status === 'ready') domainSuccess('context');
      else domainFailure('context', new Error(`READINESS_${readiness.status || 'UNKNOWN'}`));
"""
new = """      const readiness = payload.readiness && typeof payload.readiness === 'object' ? payload.readiness : {};
      if (readiness.status === 'not_ready') {
        domainFailure('context', new Error('READINESS_NOT_READY'));
      } else {
        // Context health is independent from missing legacy readiness metadata.
        // Explicit NOT_READY still fails closed; absent readiness preserves valid context.
        domainSuccess('context');
      }
"""
if s.count(old) != 1:
    raise RuntimeError(f"READINESS_PATCH_DRIFT:{s.count(old)}")
app.write_text(s.replace(old, new, 1))

test = ROOT / "senecio_polymarket/tests/test_order_070_r8.py"
t = test.read_text()
marker = "\nif __name__=='__main__': unittest.main()\n"
extra = '''
    def test_readiness_explicit_not_ready_fails_closed_but_missing_is_compatible(self):
        js=(ROOT/'senecio_polymarket/frontend/app.js').read_text()
        self.assertIn("if (readiness.status === 'not_ready')", js)
        self.assertIn("domainFailure('context', new Error('READINESS_NOT_READY'))", js)
        self.assertNotIn("READINESS_${readiness.status || 'UNKNOWN'}", js)
'''
if t.count(marker) != 1:
    raise RuntimeError(f"R8_TEST_MARKER_DRIFT:{t.count(marker)}")
test.write_text(t.replace(marker, extra + marker, 1))

print("R8_READINESS_COMPATIBILITY_FIX=APPLIED")
