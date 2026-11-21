"""Build 500 ft dwelling buffers for Bourne, MA, clipped to deer Wildlife Management Zone 12."""
import json
import requests
from pyproj import Transformer
from shapely.geometry import shape, mapping
from shapely.ops import unary_union, transform
from shapely.strtree import STRtree

SVC = "https://services1.arcgis.com/hGdibHYSPO59RG1h/ArcGIS/rest/services"
PARCELS = f"{SVC}/Massachusetts_Property_Tax_Parcels/FeatureServer/0/query"
BLDGS = f"{SVC}/Building_Structures/FeatureServer/0/query"
WMZ = "https://gis.eea.mass.gov/server/rest/services/DFG/MA_DFW_Wildlife_Management_Zones/MapServer/1/query"
BOURNE_ID = 36
BUFFER_M = 500 * 0.3048

to_ma = Transformer.from_crs(4326, 26986, always_xy=True).transform
to_ll = Transformer.from_crs(26986, 4326, always_xy=True).transform


def fetch(url, where, fields, sr):
    out, offset = [], 0
    while True:
        r = requests.get(url, params=dict(where=where, outFields=fields, outSR=sr, f="geojson",
                                          resultOffset=offset, resultRecordCount=2000), timeout=120)
        r.raise_for_status()
        feats = r.json()["features"]
        out += feats
        if len(feats) < 2000 and not r.json().get("exceededTransferLimit"):
            return out
        offset += len(feats)
        print(" ", len(out))


def is_dwelling(code):
    # MA DOR use codes: 101-109 single/condo/multi-family/misc residential, 111-112 apartments,
    # 013/014 and 031-043 style mixed-use start with these codes too (013 = mixed residential)
    c = (code or "").strip()
    return c[:3] in {"101", "102", "103", "104", "105", "106", "109", "111", "112", "013", "014"}


zone = shape(requests.get(WMZ, params=dict(where="DMZ='12'", outFields="DMZ", outSR=4326, f="geojson"),
                          timeout=120).json()["features"][0]["geometry"])
zone_ma = transform(to_ma, zone)

print("parcels...")
parcels = fetch(PARCELS, f"TOWN_ID={BOURNE_ID}", "USE_CODE,SITE_ADDR,BLDG_VAL", 4326)
res = [shape(f["geometry"]) for f in parcels if is_dwelling(f["properties"]["USE_CODE"]) and f["geometry"]]
print(len(parcels), "parcels,", len(res), "residential")
res_ma = [transform(to_ma, g.buffer(0)) for g in res]
tree = STRtree(res_ma)

print("buildings...")
bld = fetch(BLDGS, f"TOWN_ID={BOURNE_ID}", "AREA_SQ_FT,ARCHIVED", 4326)
print(len(bld), "buildings")
by_parcel = {}
for f in bld:
    if f["properties"].get("ARCHIVED") == "Y":
        continue
    g = transform(to_ma, shape(f["geometry"]).buffer(0))
    hits = tree.query(g.representative_point(), predicate="intersects")
    if len(hits):  # stacked/duplicate parcel polygons: count each building once
        by_parcel.setdefault(int(min(hits)), []).append((f["properties"]["AREA_SQ_FT"] or 0, g))

dwellings = []
for blds in by_parcel.values():
    blds.sort(key=lambda t: -t[0])
    dwellings.append(blds[0][1])  # main building on the lot
    dwellings += [g for a, g in blds[1:] if a >= 800]  # other sizeable buildings (ADUs, 2nd homes)
print(len(dwellings), "dwelling footprints")

# one 500 ft circle per dwelling (centroid), kept only where the dwelling is in Zone 12
circles = []
for g in dwellings:
    c = g.centroid
    if zone_ma.contains(c):
        circles.append({"type": "Feature", "properties": {},
                        "geometry": mapping(transform(to_ll, c.buffer(BUFFER_M, 4)))})
def rnd(o):
    return [rnd(x) for x in o] if isinstance(o, (list, tuple)) else round(o, 5)
for f in circles:
    f["geometry"]["coordinates"] = rnd(f["geometry"]["coordinates"])
json.dump({"type": "FeatureCollection", "features": circles}, open("bourne_dwelling_circles_500ft.geojson", "w"))
json.dump({"type": "Feature", "properties": {"name": "WMZ 12"}, "geometry": mapping(zone)}, open("wmz12.geojson", "w"))
print(len(circles), "circles in Zone 12")
