# Fluent textures (legacy placeholder)

**Do not commit decoded JPGs or `manifest.json` here.**

Host/ZEIA texture rebuilds go under the sibling gitignored folder:

`../local/textures/` → asset URLs `/models/fluent/local/textures/<guid>.jpg`

```bash
python3 source/tools/simulator/extract_fluent_textures.py --install /path/to/DataStoreOrHostDb
# optional selective rebuild:
#   --texture-guid <guid> | --texture-guids-from preserve-texture-guids.json
```

This `textures/` directory stays as a tracked stub only (`README.md` / `.gitkeep`).
Any leftover `*.jpg` / `manifest.json` here are local orphans — delete or ignore; never re-add to git.
