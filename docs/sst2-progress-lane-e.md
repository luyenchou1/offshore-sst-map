# SST sprint 2, lane E (engine and data): progress log

Branch `sst2-lane-e`, worktree `/Users/lchou/Code/sst-sprint2/lane-e`, dev port 8081. Newest entries at the bottom.

## 2026-10-16

- Read BRIEF.md, SPRINT2-DECISIONS.md, design-notes.md + design/src, CLAUDE.md, docs/sst-review.md (E1, R1, T4, T6, R4,
  D3) and the SST code paths (app.py load/serve/cache, data/*, map/overlay.py, assets/sst.js, the seeder, the tests).
- `cache` is a symlink into the main checkout: every dev server and test here runs with `SST_CACHE_DIR` pointing at a
  temp copy, never at `./cache`.
- Real data for the extended box (37.8 to 44.0 N, 76.0 to 64.8 W), week of Oct 9 to 15, 2026, from this Mac:
  one MUR day is 1121 x 621 cells, 5.6 MB as .nc, 1.18 MB on the wire, 1.3 to 1.6 s. 28.9% of the box is land in MUR.
- Encoding measured on that week (0.1 F codes, uint16 base 28 F):
  plain gzip 1002 KB/week; byte planes of deltas 855 to 939 KB; zigzag bytes of a 2D residual 575 to 601 KB
  (left neighbour, gradient a+b-c, MED and (a+b)/2 predictors all within 4%: the 0.1 F quantization noise is the floor).
  Chosen: left-neighbour residual, zigzag in one byte, escapes for big jumps, one mask per week (masks are identical
  across the days), gzip -9. Temporal deltas made it worse (1080 to 1216 KB).
- MUR `analysis_error` (the haze source): checked 12 dates 2005 to 2026. Over the whole box it is 0.36 to 0.40 C on
  every date, 4 or 5 distinct values (ERDDAP gives 0.01 C), never above 0.40. It has smooth spatial structure (higher
  near the coasts and in the northeast, lower offshore) but almost no range, so the haze can only be relative (the top
  of that range). A strided request (every 5th cell, 0.05 deg) is 7 KB on the wire and 0.5 s.
- Lead's additions (mid-task): one cache entry per week (the color scale is a browser choice now), a disk-based cap
  sized from the real disk with least-recently-viewed eviction, old two-variant entries still load, and plain retention
  rules in CLAUDE.md.
- NOTE: pyflakes 4.0.3 got installed into the shared venv at 22:09 by a lint fallback; uninstall before finishing.
