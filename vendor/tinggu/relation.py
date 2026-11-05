"""跨层关系层：只读既有分析缓存，产出克制的关系候选。"""


RELATION_VERSION = 1
ALIGN_S = 2.0
RESOLUTIONS = ("解决", "强解决")
STRETCHES = ("张力上升", "延宕")


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mmss(value):
    seconds = max(0, round(float(value)))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _near_score(t, target):
    distance = abs(t - target)
    return round(max(0.0, 1.0 - distance / ALIGN_S), 2) if distance <= ALIGN_S else None


def _arc_score(t, arc):
    start, end = _number(arc.get("start")), _number(arc.get("end"))
    if start is None or end is None:
        return None
    if start <= t <= end:
        return 1.0
    return _near_score(t, start if t < start else end)


def _support(data, t):
    notes = []
    for event in (data.get("arrangement") or {}).get("events") or []:
        event_t = _number(event.get("t"))
        label = event.get("label")
        if event_t is not None and label and abs(event_t - t) <= ALIGN_S:
            if label in ("人声进", "人声退"):
                notes.append(label)
    for track, segments in (data.get("stemTimeline") or {}).items():
        if track == "vocals" or not segments:
            continue
        first = segments[0]
        if isinstance(first, (list, tuple)) and first:
            start = _number(first[0])
            if start is not None and abs(start - t) <= ALIGN_S:
                notes.append(f"{track}首进")
    return list(dict.fromkeys(notes))


def _relation(harmony, dynamic):
    if harmony in RESOLUTIONS and dynamic in ("渐弱", "力度回落"):
        return "AGREE"
    if harmony in STRETCHES and dynamic == "渐强":
        return "AGREE"
    if harmony in RESOLUTIONS and dynamic == "渐强":
        return "CONTRAST"
    if harmony == "虚假解决" and dynamic == "渐强":
        return "CONTRAST"
    if harmony in STRETCHES and dynamic == "渐弱":
        return "CONTRAST"
    return None


def _sync(data):
    harmonic_events = []
    for item in (data.get("harmonyMotion") or {}).get("states") or []:
        state, t = item.get("state"), _number(item.get("start"))
        if state in RESOLUTIONS + STRETCHES and t is not None:
            harmonic_events.append((t, state))
    for item in (data.get("expectation") or {}).get("deceptive") or []:
        t = _number(item.get("t"))
        if t is not None:
            harmonic_events.append((t, "虚假解决"))

    dynamic_events = []
    for arc in (data.get("tension") or {}).get("arcs") or []:
        if arc.get("type") in ("渐强", "渐弱"):
            dynamic_events.append((arc.get("type"), arc))
    release = _number((data.get("tension") or {}).get("release"))
    if release is not None:
        dynamic_events.append(("力度回落", release))

    found = []
    for t, harmony in harmonic_events:
        for dynamic, source in dynamic_events:
            score = (_arc_score(t, source) if isinstance(source, dict)
                     else _near_score(t, source))
            relation = _relation(harmony, dynamic) if score is not None else None
            if relation:
                item = {"t": t, "harmony": harmony, "dynamic": dynamic,
                        "relation": relation, "score": score}
                support = _support(data, t)
                if support:
                    item["support"] = support
                found.append(item)
    return sorted(found, key=lambda item: (item["t"], item["harmony"], item["dynamic"]))


def _chains(data):
    states = (data.get("harmonyMotion") or {}).get("states") or []
    delays = sorted(_number(item.get("start")) for item in states
                    if item.get("state") == "延宕" and _number(item.get("start")) is not None)
    resolutions = sorted((_number(item.get("start")), item.get("state")) for item in states
                         if item.get("state") in RESOLUTIONS
                         and _number(item.get("start")) is not None)
    deceptive = sorted(_number(item.get("t")) for item in
                       (data.get("expectation") or {}).get("deceptive") or []
                       if _number(item.get("t")) is not None)
    waits = (data.get("expectation") or {}).get("waits") or []
    paired = {}
    for wait in waits:
        start, span = _number(wait.get("start")), _number(wait.get("waitS"))
        resolved = wait.get("resolved")
        if start is not None and span is not None and resolved in RESOLUTIONS:
            paired[start] = (start + max(0.0, span), resolved)
            if start not in delays:
                delays.append(start)
    delays.sort()

    found = []
    for start in delays:
        is_paired = start in paired
        endpoint = paired.get(start)
        if endpoint is None:
            endpoint = next(((t, state) for t, state in resolutions if t > start), None)
        if endpoint is None:
            continue
        end, resolved = endpoint
        middles = [t for t in deceptive if start < t < end]
        times = [start, *middles, end]
        events = ["延宕", *(["虚假解决"] * len(middles)), resolved]
        span = round(max(0.0, end - start), 2)
        found.append({"times": times, "events": events, "spanS": span,
                      "far": span >= 8.0, "paired": is_paired,
                      "score": 1.0 if is_paired else 0.5})
        # waits 记的是局部相邻配对；若它之后还有虚假解决，保留同一起点的
        # 较长候选，交由报告使用者判断，不把局部配对冒充长距离结论。
        later_deceptive = next((t for t in deceptive if t > end), None)
        if is_paired and later_deceptive is not None:
            later_endpoint = next(((t, state) for t, state in resolutions
                                   if t > later_deceptive), None)
            if later_endpoint is not None:
                later_end, later_resolved = later_endpoint
                later_middles = [t for t in deceptive if start < t < later_end]
                later_span = round(later_end - start, 2)
                found.append({
                    "times": [start, *later_middles, later_end],
                    "events": ["延宕", *(["虚假解决"] * len(later_middles)), later_resolved],
                    "spanS": later_span, "far": later_span >= 8.0,
                    "paired": False, "score": 0.5,
                })
    return found


def analyze(data):
    """从 preanalysis 字典生成 crossLayer；不修改输入。"""
    source = data if isinstance(data, dict) else {}
    return {"relationVersion": RELATION_VERSION,
            "sync": _sync(source), "chains": _chains(source)}


def _with_omitted_note(lines, omitted):
    if omitted and lines:
        lines[-1] += f" ｜ 另 {omitted} 条入 json"
    return lines


def report_lines(cross_layer):
    """生成和声运动节尾的有限行；AGREE 与 score 只留在 JSON。"""
    payload = cross_layer if isinstance(cross_layer, dict) else {}
    contrasts = [item for item in payload.get("sync") or []
                 if item.get("relation") == "CONTRAST"]
    contrasts.sort(key=lambda item: (-float(item.get("score") or 0), float(item.get("t") or 0)))
    sync_lines = []
    for item in contrasts[:3]:
        dynamic = "力度仍上升" if item.get("dynamic") == "渐强" else "力度渐弱"
        support = item.get("support") or []
        suffix = f"（{'、'.join(support)}）" if support else ""
        sync_lines.append(f"{_mmss(item.get('t') or 0)} {item.get('harmony')} + {dynamic}{suffix} ｜ 关系：对抗")
    _with_omitted_note(sync_lines, max(0, len(contrasts) - 3))

    chains = list(payload.get("chains") or [])
    # 含虚假解决的三连链戏剧价值最高（被骗的证据），优先上报告——8.26 验收案例：
    # 纯 score 排序会把一条 1:35→2:15→2:25 的三连链挤进 json，排序补一级。
    chains.sort(key=lambda item: (-int("虚假解决" in (item.get("events") or [])),
                                  -float(item.get("score") or 0),
                                  float((item.get("times") or [0])[0])))
    chain_lines = []
    for item in chains[:2]:
        times = " → ".join(_mmss(t) for t in item.get("times") or [])
        events = " → ".join(item.get("events") or [])
        span = f"{float(item.get('spanS') or 0):g}s"
        distance = "·远距" if item.get("far") else ""
        chain_lines.append(f"{times} {events} ｜ 候选链（跨 {span}{distance}）")
    _with_omitted_note(chain_lines, max(0, len(chains) - 2))
    return sync_lines + chain_lines
