from typing import Dict, List
from ..config import APPROACH_ROIS, APPROACHES, settings


def point_in_roi(cx, cy, roi, width, height):
    x1,y1=roi["x1"]*width,roi["y1"]*height
    x2,y2=roi["x2"]*width,roi["y2"]*height
    return x1 <= cx <= x2 and y1 <= cy <= y2


def assign_approach(cx, cy, width, height):
    """Assign by nearest approach corridor center, avoiding overlapping ROI tie errors."""
    nx, ny = cx/max(1,width), cy/max(1,height)
    candidates=[]
    for name, roi in APPROACH_ROIS.items():
        if point_in_roi(cx,cy,roi,width,height):
            rx=(roi["x1"]+roi["x2"])/2
            ry=(roi["y1"]+roi["y2"])/2
            # Distance is weighted toward the approach's entry axis.
            if name in ("NORTH","SOUTH"):
                score=abs(nx-0.5)*0.35 + abs(ny-ry)*0.65
            else:
                score=abs(ny-0.5)*0.35 + abs(nx-rx)*0.65
            candidates.append((score,name))
    if candidates:
        return min(candidates)[1]
    distances={"NORTH":abs(ny-0.10),"SOUTH":abs(ny-0.90),"WEST":abs(nx-0.10),"EAST":abs(nx-0.90)}
    return min(distances,key=distances.get)


def density_ratio(count):
    return min(1.0, count/max(1,settings.traffic_capacity))


def density_from_count(count):
    ratio=density_ratio(count)
    if ratio < settings.low_threshold:
        return "LOW"
    if ratio <= settings.medium_threshold:
        return "MEDIUM"
    return "HIGH"


def queue_proxy_for_detections(detections,width,height):
    counts={a:0 for a in APPROACHES}
    for d in detections:
        if d.get("near_queue_zone"):
            counts[d["approach"]]+=1
    denominator=max(1, round(settings.traffic_capacity*0.6))
    return {a:min(1.0,counts[a]/denominator) for a in APPROACHES}


def is_near_queue_zone(cx,cy,approach,width,height):
    nx,ny=cx/max(1,width),cy/max(1,height)
    if approach=="NORTH": return ny >= 0.55 and ny <= 0.40 + 0.55*(1-0.40)
    if approach=="SOUTH": return ny <= 0.45 and ny >= 0.60 - 0.55*0.60
    if approach=="WEST": return nx >= 0.55 and nx <= 0.40 + 0.55*(1-0.40)
    if approach=="EAST": return nx <= 0.45 and nx >= 0.60 - 0.55*0.60
    return False


def summarize(detections:List[dict],width,height):
    types={"car":0,"motorcycle":0,"bus":0,"truck":0}
    approaches={a:0 for a in APPROACHES}
    for d in detections:
        if d["class_name"] in types: types[d["class_name"]]+=1
        approaches[d["approach"]]+=1
    total=sum(types.values())
    return {
        "types":types,
        "approaches":approaches,
        "total":total,
        "density":density_from_count(total),
        "approach_density":{a:density_from_count(approaches[a]) for a in APPROACHES},
        "density_ratio":{a:round(density_ratio(approaches[a]),4) for a in APPROACHES},
    }
