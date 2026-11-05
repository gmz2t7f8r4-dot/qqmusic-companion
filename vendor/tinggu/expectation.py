"""计算和声与循环期待层。

数据契约：``analyze`` 只读主缓存的 ``chordAnalysis`` 与
``harmonyMotion``，返回由深听入口写回主缓存并渲染到“和声运动”节的
``expectation`` 字段；证据不足时返回 ``None``。
"""

import re
import unicodedata


PITCHES = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5,
           "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
MAJOR_DEGREES = {0: "I", 2: "ii", 4: "iii", 5: "IV", 7: "V", 9: "vi", 11: "vii"}
MINOR_DEGREES = {0: "i", 2: "ii", 3: "III", 5: "iv", 8: "VI", 10: "VII"}

# 通用功能和声与流行音乐常见进行。
PROGRESSIONS = {
    "4536 王道": ("IV", "V", "iii", "vi"),
    "1564 流行四和弦": ("I", "V", "vi", "IV"),
    "6415": ("vi", "IV", "I", "V"),
    "6451 小室": ("vi", "IV", "V", "I"),
    "1645 五十年代": ("I", "vi", "IV", "V"),
    "4156": ("IV", "I", "V", "vi"),
    "251 二五一": ("ii", "V", "I"),
    "小调 4536": ("iv", "V", "III", "VI"),
    "小调 6451": ("VI", "iv", "V", "i"),
}


def _degree(chord, tonic, mode):
    match = re.match(r"^([A-G](?:#)?)", chord or "")
    if not match:
        return "X"
    offset = (PITCHES[match.group(1)] - tonic) % 12
    if mode == "major":
        return MAJOR_DEGREES.get(offset, "X")
    if offset == 7:
        suffix = chord[match.end():]
        return "v" if suffix.startswith("m") and not suffix.startswith("maj") else "V"
    return MINOR_DEGREES.get(offset, "X")


def _is_rotation(window, ring):
    return len(window) == len(ring) and tuple(window) in tuple(
        tuple(ring[index:] + ring[:index]) for index in range(len(ring)))


def _annotate(spans, tonic, mode):
    annotated = []
    for span in spans:
        chord = span.get("chord") or "?"
        annotated.append({
            "start": float(span.get("start", 0)),
            "end": float(span.get("end", 0)),
            "degree": "?" if chord == "?" else _degree(chord, tonic, mode),
        })
    return annotated


def _runs(annotated):
    runs = []
    current = []
    for item in annotated:
        if item["degree"] == "?":
            if current:
                runs.append(current)
                current = []
            continue
        if current and current[-1]["degree"] == item["degree"]:
            current[-1]["end"] = item["end"]
        else:
            current.append(dict(item))
    if current:
        runs.append(current)
    return runs


def _matches(runs):
    matches = []
    windows = []
    for run in runs:
        for name, ring_tuple in PROGRESSIONS.items():
            ring = list(ring_tuple)
            for index in range(len(run) - len(ring) + 1):
                items = run[index:index + len(ring)]
                if _is_rotation([item["degree"] for item in items], ring):
                    match = {"name": name, "start": items[0]["start"],
                             "end": items[-1]["end"], "items": items}
                    matches.append(match)
                    windows.append((run, index, len(ring), name))
    return matches, windows


def _merged_intervals(matches, name=None):
    intervals = sorted((item["start"], item["end"]) for item in matches
                       if name is None or item["name"] == name)
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _duration(intervals):
    return sum(max(0.0, end - start) for start, end in intervals)


def _habit(runs, matches, valid_duration):
    names = list(PROGRESSIONS)
    totals = {name: _duration(_merged_intervals(matches, name)) for name in names}
    dominant = max(names, key=lambda name: totals[name]) if matches else None
    if dominant and totals[dominant] <= 0:
        dominant = None
    coverage = _duration(_merged_intervals(matches))
    departures = []
    if dominant:
        ring_length = len(PROGRESSIONS[dominant])
        for run in runs:
            previous_hit = False
            for index in range(len(run) - ring_length + 1):
                items = run[index:index + ring_length]
                hit = _is_rotation([item["degree"] for item in items],
                                   list(PROGRESSIONS[dominant]))
                if previous_hit and not hit:
                    previous = items[-2]["degree"]
                    current = items[-1]["degree"]
                    suffix = " 调外" if current == "X" else ""
                    departures.append({"t": items[-1]["start"],
                                       "toDegrees": f"{previous}→{current}{suffix}"})
                previous_hit = hit
    return {"coveragePct": round(coverage / valid_duration * 100) if valid_duration else 0,
            "dominant": dominant, "departures": departures[:10]}


def _waits(harmony_motion):
    states = (harmony_motion or {}).get("states") or []
    found = []
    for current, following in zip(states, states[1:]):
        if (current.get("state") == "延宕"
                and following.get("state") in ("解决", "强解决")
                and float(current.get("end", 0)) == float(following.get("start", 0))):
            start, end = float(current.get("start", 0)), float(current.get("end", 0))
            found.append({"start": start, "waitS": round(max(0.0, end - start), 2),
                          "resolved": following["state"]})
    return found


def _deceptive(runs, mode):
    found = []
    wanted = ("V", "vi") if mode == "major" else ("V", "VI")
    for run in runs:
        for previous, current in zip(run, run[1:]):
            if (previous["degree"], current["degree"]) == wanted:
                found.append({"t": current["start"], "pair": "→".join(wanted)})
    return found


def _loop_key(text):
    """折叠大小写并去掉所有标点、空白。"""
    return "".join(character for character in str(text or "").strip().casefold()
                   if not character.isspace()
                   and not unicodedata.category(character).startswith("P"))


def _tension_at(curve, t):
    """张力曲线上取离 t 最近的采样值；曲线空返回 None。"""
    if not curve:
        return None
    best = min(curve, key=lambda point: abs(float(point[0]) - float(t)))
    return float(best[1])


def loops(rows, tension_curve=None):
    """从行级表归纳完全同文的重复轨迹。

    「推上去」三路判据（8.24 维护者耳朵定的方向）：伴奏加件、人声抬声、全曲张力上行——
    件数不涨但弦乐在重复上越堆越厚时（Together 案），前两路瞎，第三路才看得见。
    """
    groups = {}
    for row in rows or []:
        key = _loop_key(row.get("text"))
        if key:
            groups.setdefault(key, []).append(row)
    result = []
    for group in groups.values():
        if len(group) < 3:
            continue
        ordered = sorted(group, key=lambda row: float(row.get("t") or 0))
        dress = [row.get("accompStems") for row in ordered]
        loud = [row.get("rmsDb") for row in ordered]
        if any(value is None for value in dress + loud):
            continue
        dress_delta = dress[-1] - dress[0]
        loud_delta = round(float(loud[-1]) - float(loud[0]), 1)
        item = {
            "text": ordered[0].get("text") or "",
            "n": len(ordered),
            "spanS": [ordered[0].get("t"), ordered[-1].get("t")],
            "dress": dress,
            "loudDb": loud,
            "dressDelta": dress_delta,
            "loudDelta": loud_delta,
        }
        tension_vals = [_tension_at(tension_curve, row.get("t") or 0) for row in ordered]
        if all(value is not None for value in tension_vals):
            # 推上去常发生在重复途中而非末遍（Together 案第 4 遍冲顶后回落），
            # 所以量「途中峰 − 首遍」，不量首末差。
            peak_index = max(range(len(tension_vals)), key=lambda i: tension_vals[i])
            item["tensionVals"] = [round(value, 2) for value in tension_vals]
            item["tensionClimb"] = round(tension_vals[peak_index] - tension_vals[0], 2)
            item["tensionPeakT"] = ordered[peak_index].get("t")
        result.append(item)
    result.sort(key=lambda item: item["n"], reverse=True)
    return result[:8]


def analyze(chord_analysis, harmony_motion):
    """生成期待层三笔账；输入证据未过门槛时留白。"""
    if not chord_analysis or not chord_analysis.get("key"):
        return None
    if float(chord_analysis.get("keyConfidence") or 0) < 0.2:
        return None
    spans = chord_analysis.get("spans") or []
    total_duration = sum(max(0.0, float(item.get("end", 0)) - float(item.get("start", 0)))
                         for item in spans)
    valid_duration = sum(max(0.0, float(item.get("end", 0)) - float(item.get("start", 0)))
                         for item in spans if (item.get("chord") or "?") != "?")
    if not total_duration or valid_duration / total_duration < 0.4:
        return None
    try:
        tonic_name, mode = chord_analysis["key"].split()
        tonic = PITCHES[tonic_name]
        if mode not in ("major", "minor"):
            return None
    except (KeyError, ValueError):
        return None
    runs = _runs(_annotate(spans, tonic, mode))
    matches, _ = _matches(runs)
    return {"expectVersion": 3, "habit": _habit(runs, matches, valid_duration),
            "waits": _waits(harmony_motion), "deceptive": _deceptive(runs, mode)}
