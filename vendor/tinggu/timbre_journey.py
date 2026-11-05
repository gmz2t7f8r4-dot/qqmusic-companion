"""同轨自比的音色旅程。"""
import pathlib

import librosa
import numpy as np


SR = 22050
WINDOW_S = 2.0
HOP_S = 1.0
TRACKS = ("guitar", "piano", "other")
FEATURES = ("centroidHz", "flatness", "onsetPerS")


def _window_starts(segments):
    starts = []
    for start, end in segments:
        if end - start >= WINDOW_S:
            starts.extend(np.arange(float(start), float(end) - WINDOW_S + 1e-9, HOP_S))
    return np.asarray(starts, dtype=float)


def _extract_events(track, feature, starts, values):
    if not len(values):
        return []
    med = float(np.median(values))
    q25, q75 = np.percentile(values, [25, 75])
    iqr = float(q75 - q25)
    if iqr <= 0:
        return []
    directions = np.where(values > med + 1.5 * iqr, 1,
                          np.where(values < med - 1.5 * iqr, -1, 0))
    groups = []
    beginning = None
    for index, direction in enumerate(directions):
        continues = (beginning is not None and direction == directions[index - 1]
                     and starts[index] - starts[index - 1] <= HOP_S + 1e-6)
        if direction and beginning is None:
            beginning = index
        elif direction and not continues:
            groups.append((beginning, index))
            beginning = index
        elif not direction and beginning is not None:
            groups.append((beginning, index))
            beginning = None
    if beginning is not None:
        groups.append((beginning, len(values)))

    events = []
    for first, after in groups:
        end = float(starts[after - 1] + WINDOW_S)
        if end - float(starts[first]) + 1e-6 < 8.0:
            continue
        deviation = float(np.median(np.abs(values[first:after] - med)))
        events.append({"track": track, "feature": feature,
                       "start": round(float(starts[first]), 1), "end": round(end, 1),
                       "dir": "+" if directions[first] > 0 else "-",
                       "magnitude": round(deviation / iqr, 1)})
    return sorted(events, key=lambda event: event["magnitude"], reverse=True)


def _track_result(y, segments):
    starts = _window_starts(segments)
    length = round(WINDOW_S * SR)
    windows = [y[round(start * SR):round(start * SR) + length] for start in starts]
    energies = np.asarray([float(np.mean(window * window)) for window in windows])
    if not len(energies):
        return {"med": {feature: 0.0 for feature in FEATURES}, "events": [], "steady": True}
    threshold = float(np.median(energies)) * 0.05
    kept = [(start, window) for start, window, energy in zip(starts, windows, energies)
            if energy >= threshold]
    if not kept:
        return {"med": {feature: 0.0 for feature in FEATURES}, "events": [], "steady": True}
    kept_starts = np.asarray([item[0] for item in kept])
    metrics = {feature: [] for feature in FEATURES}
    for _, window in kept:
        metrics["centroidHz"].append(float(np.mean(librosa.feature.spectral_centroid(
            y=window, sr=SR))))
        metrics["flatness"].append(float(np.mean(librosa.feature.spectral_flatness(y=window))))
        metrics["onsetPerS"].append(float(len(librosa.onset.onset_detect(
            y=window, sr=SR))) / WINDOW_S)
    med = {}
    events = []
    for feature, raw_values in metrics.items():
        values = np.asarray(raw_values)
        middle = float(np.median(values))
        q25, q75 = np.percentile(values, [25, 75])
        med[feature] = {"med": middle, "iqr": float(q75 - q25)}
        events.extend(_extract_events("", feature, kept_starts, values))
    events.sort(key=lambda event: event["magnitude"], reverse=True)
    return {"med": med, "events": events[:3], "steady": not events}


def analyze(stems_dir, stem_timeline):
    stems_dir = pathlib.Path(stems_dir)
    results = {}
    for track in TRACKS:
        path = stems_dir / f"{track}.mp3"
        segments = (stem_timeline or {}).get(track) or []
        active_duration = sum(max(0.0, float(end) - float(start)) for start, end in segments)
        if not path.exists() or active_duration < 20.0:
            continue
        y, _ = librosa.load(path, sr=SR, mono=True)
        result = _track_result(y, segments)
        for event in result["events"]:
            event["track"] = track
        results[track] = result
        del result
        del y
    return {"timbreVersion": 1, "tracks": results} if results else None
