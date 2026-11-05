"""声线坐标锚点尺（9.25）：固定锚点集替代现算参照池。

纯函数 voice_axes / voice_axes_lines 不读文件；读 JSON 只走 load_anchors 一处。
铁律：输出只有歌手名、歌名、数值、机制词，气质词零出现。
9.25「报告只印能信的」：自检不过、锚点不足、本歌缺值的轴一行不印，
轴名交给调用方写进报告末尾的脚注；一根都没过就连表头也不印。
"""
import json
import math
import numbers
import re
import sys

# (字段, 轴名, 数值格式, 左端字, 右端字)
AXES = (
    ("airRatio", "气声", "{:.0%}", "实", "气"),
    ("centroidHz", "亮暗", "{:.0f}Hz", "暗", "亮"),
    ("hnrDb", "噪比", "{:.1f}dB", "毛", "净"),
)


def load_anchors(path):
    """读锚点集；缺失/损坏返回 None（调用方留白），不抛异常。
    单条锚点写坏只跳过那一条并报 stderr，其余照用（9.25 审计修补）。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload.get("anchors"), list):
            raise ValueError("anchors 字段缺失")
    except Exception as error:
        print(f"声线坐标：锚点集读取失败（{type(error).__name__}）", file=sys.stderr)
        return None
    kept = []
    for index, anchor in enumerate(payload["anchors"], 1):
        problem = _anchor_problem(anchor)
        if problem:
            print(f"声线坐标：第 {index} 条锚点格式不对，跳过（{problem}）", file=sys.stderr)
            continue
        kept.append(anchor)
    return {**payload, "anchors": kept}


def _is_number(value):
    return isinstance(value, numbers.Real) and not isinstance(value, bool) and math.isfinite(value)


def _anchor_problem(anchor):
    """一条锚点能不能上尺：values 是 null 或「数值/null」的 dict，ends 是 dict（缺省当没提名）。"""
    if not isinstance(anchor, dict):
        return "不是对象"
    values = anchor.get("values")
    if values is not None:
        if not isinstance(values, dict):
            return "values 不是对象"
        if any(value is not None and not _is_number(value) for value in values.values()):
            return "values 里有非数值"
    ends = anchor.get("ends")
    if ends is not None and not isinstance(ends, dict):
        return "ends 不是对象"
    return None


def _units(fmt, value):
    """按显示精度对齐成整数刻度——fmt 印出什么就比什么：14% → 14，2202Hz → 2202，12.6dB → 126。
    比较全走整数，印出来一样的两个数就是相等，不拿浮点做 ==（9.25 审计修补）。"""
    return int(re.match(r"-?\d+(?:\.\d+)?", fmt.format(value)).group().replace(".", ""))


def _name(anchor):
    return f"{anchor.get('artist') or ''}《{anchor.get('name') or ''}》"


def _axis_line(key, label, fmt, left, right, value, valid):
    """一根轴的坐标行；锚点不足或自检不过返回 None（不印）。"""
    high = [a for a in valid if (a.get("ends") or {}).get(key) == "high"]
    low = [a for a in valid if (a.get("ends") or {}).get(key) == "low"]
    shown = fmt.format(value)
    if not high or not low:
        return None
    # 轴资格自检：high 端锚值必须全部大于 low 端锚值（每次渲染现算）
    if min(a["values"][key] for a in high) <= max(a["values"][key] for a in low):
        return None
    caution = "（跨年代慎读）" if key == "hnrDb" else ""
    # 落位全按显示精度对齐后的整数比：读者看到的数字说了算
    unit = lambda anchor: _units(fmt, anchor["values"][key])
    mine = _units(fmt, value)
    ranked = sorted(valid, key=lambda a: (unit(a), a["values"][key]))
    level = [a for a in ranked if unit(a) == mine]
    if level:
        names = "、".join(_name(a) for a in level)
        return f"{label} {shown}：与 {names} 持平{caution}"
    if mine < unit(ranked[0]):
        edge = ranked[0]
        return (f"{label} {shown}：比最{left}的锚点 {_name(edge)}"
                f"{fmt.format(edge['values'][key])} 还靠{left}{caution}")
    if mine > unit(ranked[-1]):
        edge = ranked[-1]
        return (f"{label} {shown}：比最{right}的锚点 {_name(edge)}"
                f"{fmt.format(edge['values'][key])} 还靠{right}{caution}")
    under = [a for a in ranked if unit(a) < mine][-1]
    over = [a for a in ranked if unit(a) > mine][0]
    gap_under, gap_over = mine - unit(under), unit(over) - mine
    near = "居中" if gap_under == gap_over else ("靠前者" if gap_under < gap_over else "靠后者")
    return (f"{label} {shown}：介于 {_name(under)}{fmt.format(under['values'][key])} 与 "
            f"{_name(over)}{fmt.format(over['values'][key])} 之间，{near}{caution}")


def _nearest_overall(texture, valid, axes):
    """嗓音最接近的锚点：只用自检通过的轴，按各自锚点集 min–max 归一后的欧氏距离。
    （9.25：一根轴降级不连坐，通过的轴 ≥2 就印。）"""
    spans = {}
    for key, *_ in axes:
        values = [a["values"][key] for a in valid]
        spans[key] = max(values) - min(values)

    def gaps(anchor):
        return {key: (0.0 if spans[key] == 0
                      else abs(texture[key] - anchor["values"][key]) / spans[key])
                for key, *_ in axes}

    best = min(valid, key=lambda a: sum(g * g for g in gaps(a).values()), default=None)
    if best is None:
        return None
    worst_label = max(axes, key=lambda axis: gaps(best)[axis[0]])[1]
    return best, worst_label


def _same_song(anchor, song_name):
    """本歌就是这条锚点时不拿自己当刻度。开源版没有曲库 ID，按歌名比（忽略大小写与首尾空白）；
    本地文件名和锚点歌名对不上时照常上尺。"""
    if not song_name:
        return False
    return str(anchor.get("name") or "").strip().casefold() == str(song_name).strip().casefold()


def _same_artist(anchor, artist):
    """最接近的锚点就是本歌歌手时不印那行：「X 最像 X」对读报告的一方是零信息。
    多名歌手按 , ， & 、 / 拆开，任一重合就算。"""
    if not artist:
        return False
    split = lambda text: {part.strip().casefold() for part in re.split(r"[,，&、/]", str(text or "")) if part.strip()}
    return bool(split(anchor.get("artist")) & split(artist))


def voice_axes(texture, anchors_payload, song_name=None, artist=None):
    """一首歌的 texture 中位 → (坐标行, 没印的轴名)。
    自检不过、锚点不足、本歌缺值的轴不出行，轴名进第二个返回值；
    锚点集为 None（未就绪）时三根轴全算没印。通过的轴一根没有 → 行为空，表头也不印。"""
    if anchors_payload is None:
        return [], [label for _, label, *_ in AXES]
    version = anchors_payload.get("anchorVersion", "?")
    all_valid = [a for a in anchors_payload["anchors"]
                 if a.get("values") and not _same_song(a, song_name)]
    rows, omitted, qualified_axes = [], [], []
    for key, label, fmt, left, right in AXES:
        value = texture.get(key)
        valid = [a for a in all_valid if a["values"].get(key) is not None]
        row = None if value is None else _axis_line(key, label, fmt, left, right, value, valid)
        if row is None:
            omitted.append(label)
            continue
        rows.append(row)
        qualified_axes.append((key, label, fmt, left, right))
    if not rows:
        return [], omitted
    lines = [f"声线坐标（锚点 v{version}）："] + rows
    if len(qualified_axes) >= 2:
        valid = [a for a in all_valid
                 if all(a["values"].get(key) is not None for key, *_ in qualified_axes)]
        result = _nearest_overall(texture, valid, qualified_axes)
        if result and not _same_artist(result[0], artist):
            best, worst_label = result
            if len(qualified_axes) == len(AXES):
                lines.append(f"嗓音最接近的锚点：{_name(best)}（只比嗓音；差得最多的是{worst_label}）")
            else:
                used = "、".join(axis[1] for axis in qualified_axes)
                lines.append(f"嗓音最接近的锚点：{_name(best)}（只比嗓音的{used}两项；差得最多的是{worst_label}）")
    return lines, omitted


def voice_axes_lines(texture, anchors_payload, song_name=None, artist=None):
    """只要坐标行的调用方用这个；没印的轴名见 voice_axes。"""
    return voice_axes(texture, anchors_payload, song_name, artist)[0]
