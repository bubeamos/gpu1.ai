#!/usr/bin/env python3
"""gpu1.ai price fetcher — writes prices.json next to index.html.
Proven working 2026-10-18. Sources (both public, no auth):
  Vast.ai  GET  https://cloud.vast.ai/api/v0/bundles/?q=<urlencoded JSON>
           - gpu_name uses SPACES ("RTX 4090", not RTX_4090)
           - q-only params; extra params (limit/order) can 400
  RunPod   POST https://api.runpod.io/graphql
           - minimal headers ONLY: Content-Type. Custom UA / apollo-preflight
             headers get the connection dropped by their edge.
Output schema matches what index.html renders:
  {"fetchedAt": iso, "rows": [{gpu,vram,vastMin,vastMedian,vastSingle,
   vastOffers,vastLoc,vastReliable,runpod}], "tops": {gpu: [{ppg,total,n,
   loc,rel,cpu,ram,disk,down,up}]}}
"""
import json
import statistics
import urllib.request
import urllib.parse
from datetime import datetime, timezone

VAST = "https://cloud.vast.ai/api/v0/bundles/"
RUNPOD = "https://api.runpod.io/graphql"

# page GPU -> Vast.ai gpu_name
VAST_NAME = {
    "B200": "B200", "H200": "H200", "H100 SXM": "H100 SXM",
    "RTX 5090": "RTX 5090", "L40S": "L40S", "RTX A6000": "RTX A6000",
    "A40": "A40", "RTX 4090": "RTX 4090", "RTX 3090 Ti": "RTX 3090 Ti",
    "RTX 3090": "RTX 3090", "L4": "L4",
}
VRAM = {"B200": 180, "H200": 141, "H100 SXM": 80, "A100 80GB": 80,
        "A100 40GB": 40, "RTX 5090": 32, "L40S": 48, "RTX A6000": 48,
        "A40": 48, "RTX 4090": 24, "RTX 3090 Ti": 24, "RTX 3090": 24,
        "L4": 24}
GPUS = list(VRAM.keys())
TAB_GPUS = ["H100 SXM", "A100 80GB", "RTX 5090", "RTX 4090", "L40S"]

# page GPU -> RunPod displayName
RUNPOD_NAME = {"H200": "H200 SXM", "A100 80GB": "A100 SXM",
               "A100 40GB": "A100 SXM 40GB"}


def vast_offers(name):
    q = {"gpu_name": {"eq": name}, "rentable": {"eq": True},
         "rented": {"eq": False}}
    url = VAST + "?" + urllib.parse.urlencode({"q": json.dumps(q)})
    req = urllib.request.Request(url, headers={"User-Agent": "gpu1.ai/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get("offers", [])


def ppg(o):
    return o["dph_total"] / max(o.get("num_gpus", 1), 1)


def summarize(offers):
    per = sorted(ppg(o) for o in offers if o.get("dph_total"))
    if not per:
        return None
    best = min(offers, key=ppg)
    singles = [ppg(o) for o in offers
               if o.get("num_gpus", 1) == 1 and o.get("dph_total")]
    rel = [ppg(o) for o in offers if (o.get("reliability2") or 0) >= 0.98
           and o.get("dph_total")]
    return {
        "min": round(per[0], 3),
        "median": round(statistics.median(per), 3),
        "single": round(min(singles), 3) if singles else round(per[0], 3),
        "offers": len(offers),
        "loc": best.get("geolocation"),
        "reliable": round(min(rel), 3) if rel else round(per[0], 3),
    }


def top_offers(name, offers, n=8):
    out = []
    for o in sorted(offers, key=ppg)[:n]:
        if not o.get("dph_total"):
            continue
        out.append({
            "ppg": round(ppg(o), 3),
            "total": round(o["dph_total"], 3),
            "n": o.get("num_gpus", 1),
            "loc": o.get("geolocation"),
            "rel": round(o.get("reliability2") or o.get("reliability") or 0, 3),
            "cpu": o.get("cpu_name"),
            "ram": o.get("cpu_ram"),
            "disk": round(o.get("disk_space") or 0, 1),
            "down": round(o.get("inet_down") or 0, 1),
            "up": round(o.get("inet_up") or 0, 1),
        })
    return out


def runpod_lowest():
    body = json.dumps({
        "operationName": "GpuTypes",
        "query": "query GpuTypes { gpuTypes { displayName "
                 "lowestPrice(input:{gpuCount:1}) { uninterruptablePrice } } }",
    }).encode()
    # minimal headers only — extra headers get dropped by their edge
    req = urllib.request.Request(
        RUNPOD, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        types = json.load(r)["data"]["gpuTypes"]
    return {t["displayName"]: (t.get("lowestPrice") or {}).get("uninterruptablePrice")
            for t in types}


def a100_offers(vram):
    offers = vast_offers("A100 SXM4")
    return [o for o in offers
            if abs(round((o.get("gpu_ram") or 0) / 1024) - vram) <= 4]


def main():
    rp = runpod_lowest()
    rows, tops = [], {}
    for name in GPUS:
        if name in ("A100 80GB", "A100 40GB"):
            offers = a100_offers(VRAM[name])
        else:
            offers = vast_offers(VAST_NAME[name])
        s = summarize(offers)
        if not s:
            print(f"WARN: no Vast.ai offers for {name}")
            continue
        rows.append({
            "gpu": name, "vram": VRAM[name],
            "vastMin": s["min"], "vastMedian": s["median"],
            "vastSingle": s["single"], "vastOffers": s["offers"],
            "vastLoc": s["loc"], "vastReliable": s["reliable"],
            "runpod": rp.get(RUNPOD_NAME.get(name, name)),
        })
        if name in TAB_GPUS:
            tops[name] = top_offers(name, offers)
    out = {"fetchedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "rows": rows, "tops": tops}
    with open("prices.json", "w") as f:
        json.dump(out, f, indent=1)
    print(f"prices.json: {len(rows)} GPUs, tops for {len(tops)}, at {out['fetchedAt']}")


if __name__ == "__main__":
    main()
