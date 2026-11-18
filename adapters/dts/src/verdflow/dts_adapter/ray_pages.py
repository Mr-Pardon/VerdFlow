"""Pack large live sampling commands in camera traversal order."""
from array import array
from dataclasses import dataclass, field
from math import floor


@dataclass(slots=True)
class _Window:
    bbox: list[float]
    groups: dict[int, array] = field(default_factory=dict)


class RayPages:
    def __init__(self, lines, batches, normalize, page_size: int):
        self._coords = array("d")
        cells = {}
        self._batches = batches
        for b, batch in enumerate(batches):
            for i in range(batch.start, batch.start + batch.count):
                line = normalize(lines[i])
                start, end = line["start"], line["end"]
                self._coords.extend(start)
                self._coords.extend(end)
                xyz = (start[0], start[1], start[2] / 2 + end[2] / 2)
                key = (floor(xyz[0] / 1024), floor(xyz[1] / 1024))
                cell = cells.get(key)
                if cell is None:
                    cell = cells[key] = _Window(list(xyz) * 2)
                else:
                    for k, v in enumerate(xyz):
                        if v < cell.bbox[k]: cell.bbox[k] = v
                        if v > cell.bbox[k+3]: cell.bbox[k+3] = v
                if b not in cell.groups:
                    cell.groups[b] = array("Q")
                cell.groups[b].append(i)
        self.window_count = len(cells)
        self._pages = []
        remaining = 0
        for number, key in enumerate(sorted(cells, key=lambda k: (k[1], -k[0] if k[1] % 2 else k[0]))):
            cell = cells[key]
            for b, indices in cell.groups.items():
                offset = 0
                while offset < len(indices):
                    if not remaining:
                        self._pages.append([])
                        remaining = page_size
                    take = min(remaining, len(indices)-offset)
                    self._pages[-1].append((number, cell.bbox, b, indices, offset, take))
                    offset += take
                    remaining -= take

    def __len__(self):
        return len(self._pages)

    def page(self, index):
        if type(index) is not int or not 0 <= index < len(self):
            raise ValueError("Invalid elevation page")
        windows = []
        for number, bbox, b, indices, offset, take in self._pages[index]:
            if not windows or windows[-1]["number"] != number:
                windows.append({"number": number, "bbox": bbox, "groups": []})
            ids = indices[offset:offset+take].tolist()
            batch = self._batches[b]
            windows[-1]["groups"].append({
                "selection_id": batch.selection_id, "path_id": batch.path_id, "name": batch.name, "indices": ids,
                "lines": [{"start": self._coords[i*6:i*6+3].tolist(), "end": self._coords[i*6+3:i*6+6].tolist()}
                          for i in ids],
            })
        return windows
