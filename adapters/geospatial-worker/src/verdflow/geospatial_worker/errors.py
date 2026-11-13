"""Stable data-error codes and safe, actionable messages for application edges."""

import errno


class GeospatialDataError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def is_host_memory_error(error: Exception) -> bool:
    return isinstance(error, MemoryError) or isinstance(error, OSError) and (
        error.errno == errno.ENOMEM or getattr(error, "winerror", None) in {8, 14, 1455}
    )


def invalid_polygon_message(geometry: object, feature_number: int) -> str:
    from shapely.validation import explain_validity

    reason = explain_validity(geometry).split("[", 1)[0]
    description = {
        "Self-intersection": "边界自交",
        "Ring Self-intersection": "环边界自交",
        "Hole lies outside shell": "孔洞超出外边界",
        "Holes are nested": "孔洞相互嵌套",
        "Nested shells": "外边界相互嵌套",
        "Too few points in geometry component": "有效顶点不足",
    }.get(reason, "空面、零面积或无效拓扑")
    return f"第 {feature_number} 条面要素存在{description}。请先在 GIS 软件中检查并修复；本次未导入任何面，未自动填洞或跳过要素。"
