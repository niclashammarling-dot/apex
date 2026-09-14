"""
Atomic, numpy-tolerant JSON writes for files under data/.

Two failure classes this closes, both found on the first scheduled
weekly_research run (2026-09-14):

- numpy scalars reaching json.dump. numpy.float64 subclasses float and
  serialises; numpy.bool_ and numpy.int64 do not subclass their Python
  counterparts and raise. Any value derived from a numpy comparison or
  reduction carries this, and it fails at the write — after the work is
  done. `_default` converts every numpy scalar via .item().

- open()-then-dump. A serialisation failure partway leaves a truncated
  file that a reader cannot distinguish from a short one
  (data/optimizer_results.json, cut mid-array). Serialise fully first,
  write to a sibling .tmp, rename — the target is either the old file or
  the complete new one.
"""
import json
import os
from pathlib import Path


def _default(obj):
    item = getattr(obj, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:
            pass
    tolist = getattr(obj, "tolist", None)
    if callable(tolist):
        return tolist()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def dumps(payload, **kwargs) -> str:
    return json.dumps(payload, default=_default, **kwargs)


def write_json_atomic(path: Path, payload, **kwargs) -> None:
    path = Path(path)
    text = dumps(payload, **kwargs)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
