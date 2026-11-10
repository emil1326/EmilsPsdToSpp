"""Dev check: the plugin's extraction vs the manifests of the original Uncia tool."""
import json, os, sys, tempfile, time
import numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from EmilsPsdToSpp import extract

OLD, PSD = sys.argv[1], sys.argv[2]


def flat(nodes, out, path=""):
    for n in nodes:
        p = path + "/" + n["name"]
        out.append((p, n["type"], n["visible"], n["opacity"], n["blend"], json.dumps(n.get("fill")),
                    bool(n.get("mask")), bool(n.get("mask2"))))
        if n["type"] == "group":
            flat(n["children"], out, p)
    return out


def masks(nodes, out):
    for n in nodes:
        if n["type"] == "group":
            masks(n["children"], out)
        else:
            out += [n.get("mask"), n.get("mask2")]
    return out


t = time.time(); bad = 0
for key in sorted(os.listdir(OLD)):
    old = json.load(open(os.path.join(OLD, key, "manifest.json"), encoding="utf-8"))
    tmp = tempfile.mkdtemp()
    new = extract.extract(os.path.join(PSD, old["psd"]), tmp)
    a, b = flat(old["layers"], []), flat(new["layers"], [])
    diff = [(x, y) for x, y in zip(a, b) if x != y]
    pix = 0
    for x, y in zip(masks(old["layers"], []), masks(new["layers"], [])):
        if x and y:
            A = np.asarray(Image.open(os.path.join(OLD, key, x)), int)
            B = np.asarray(Image.open(os.path.join(tmp, y)), int)
            pix = max(pix, int(np.abs(A - B).max()))
    bad += len(diff) + (len(a) != len(b)) + (pix > 0)
    print(f"{key}: {len(a)}/{len(b)} nodes, {len(diff)} diffs, max mask diff {pix}", diff[:2])
print(f"{time.time() - t:.1f}s", "OK" if not bad else "DIFFERENCES")
sys.exit(1 if bad else 0)
