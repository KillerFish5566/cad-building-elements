"""
Q2 - 程式設計與物件導向
CAD 建築構件解析 → JSON 輸出

資料流：CAD 圖面（.dwg）→ 本程式解析 → output.json → 下游 3D 模組
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
import sys

# Windows console 預設編碼為 cp1252，無法輸出中文，改為 UTF-8
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


# ── 幾何座標點資料結構 ─────────────────────────────────────────────

@dataclass
class Point3D:
    x: float
    y: float
    z: float

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "z": self.z}


# ══════════════════════════════════════════════════════════════════
# 父類別：BuildingElement
# 定義所有建築構件的共用屬性與輸出邏輯
# ══════════════════════════════════════════════════════════════════

class BuildingElement(ABC):

    def __init__(
        self,
        element_id: str,        # 圖元識別碼，對應 CAD Entity Handle
        layer: str,             # 所在圖層名稱
        level: str,             # 所在樓層，例如 "1F"
        material: str = None    # 材質（可選，無則為 None）
    ):
        self.element_id = element_id
        self.layer = layer
        self.level = level
        self.material = material

    def _key_mapping(self) -> dict:
        """
        預設標籤對照表（子類別可選擇性覆寫）
        定義輸出 JSON 時各屬性對應的 key 名稱
        """
        return {
            "element_id": "element_id",
            "layer":      "layer",
            "level":      "level",
            "material":   "material",
        }

    @abstractmethod
    def _geometry_payload(self) -> dict:
        """
        各子類別各自的幾何資料（強制實作）
        未實作則在執行前報錯
        """
        pass

    def to_dict(self) -> dict:
        """
        統一 JSON 輸出入口
        組合共用屬性（套用 key mapping）+ 幾何資料
        """
        # 輸出前檢查不能為空的欄位
        if not self.element_id:
            raise ValueError(f"element_id 不可為空，請確認 CAD 圖元的 Handle（layer={self.layer}）")

        km = self._key_mapping()

        # 共用屬性套用標籤對照表後組裝
        base = {
            km["element_id"]: self.element_id,
            km["layer"]:      self.layer,
            km["level"]:      self.level,
            km["material"]:   self.material,
        }

        # 合併幾何資料
        base.update(self._geometry_payload())
        return base


# ══════════════════════════════════════════════════════════════════
# 子類別：Column（柱）
# ══════════════════════════════════════════════════════════════════

class Column(BuildingElement):

    def __init__(self, element_id, layer, level, material=None,
                 origin=None, height=0.0, section_width=0.0,
                 section_depth=0.0, rotation=0.0):
        super().__init__(element_id, layer, level, material)
        self.origin        = origin or Point3D(0, 0, 0)
        self.height        = height
        self.section_width = section_width
        self.section_depth = section_depth
        self.rotation      = rotation

    def _key_mapping(self) -> dict:
        """
        覆寫標籤對照表
        下游模組要求 element_id 改名為 col_id，僅 Column 適用
        Wall 與 Slab 不受影響
        """
        return {
            "element_id": "col_id",   # ← 依下游要求覆寫，只改這一行
            "layer":      "layer",
            "level":      "level",
            "material":   "material",
        }

    def _geometry_payload(self) -> dict:
        return {
            "origin":   self.origin.to_dict(),
            "height":   self.height,
            "section":  {"width": self.section_width, "depth": self.section_depth},
            "rotation": self.rotation,
        }


# ══════════════════════════════════════════════════════════════════
# 子類別：Wall（牆）
# ══════════════════════════════════════════════════════════════════

class Wall(BuildingElement):

    def __init__(self, element_id, layer, level, material=None,
                 start_point=None, end_point=None,
                 height=0.0, thickness=0.0, base_elevation=0.0):
        super().__init__(element_id, layer, level, material)
        self.start_point    = start_point or Point3D(0, 0, 0)   # 起點座標
        self.end_point      = end_point   or Point3D(0, 0, 0)   # 終點座標
        self.height         = height                             # 牆高
        self.thickness      = thickness                          # 厚度
        self.base_elevation = base_elevation                     # 底部標高

    # 未覆寫 _key_mapping → 沿用父類別預設值

    def _geometry_payload(self) -> dict:
        return {
            "start_point":    self.start_point.to_dict(),
            "end_point":      self.end_point.to_dict(),
            "height":         self.height,
            "thickness":      self.thickness,
            "base_elevation": self.base_elevation,
        }


# ══════════════════════════════════════════════════════════════════
# 子類別：Slab（樓板）
# ══════════════════════════════════════════════════════════════════

class Slab(BuildingElement):

    def __init__(self, element_id, layer, level, material=None,
                 boundary_pts=None, thickness=0.0,
                 elevation=0.0, opening_ids=None, slope=0.0):
        super().__init__(element_id, layer, level, material)
        self.boundary_pts = boundary_pts or []   # 邊界頂點座標集合（封閉多邊形）
        self.thickness    = thickness            # 板厚
        self.elevation    = elevation            # 板頂標高
        self.opening_ids  = opening_ids  or []   # 開口圖元識別碼清單（無開口則為空）
        self.slope        = slope                # 坡度（無坡則為 0）

    # 未覆寫 _key_mapping → 沿用父類別預設值

    def _geometry_payload(self) -> dict:
        return {
            "boundary":    [p.to_dict() for p in self.boundary_pts],
            "thickness":   self.thickness,
            "elevation":   self.elevation,
            "opening_ids": self.opening_ids,
            "slope":       self.slope,
        }


# ══════════════════════════════════════════════════════════════════
# 主程式
# 實際執行時連接 AutoCAD API 逐一讀取圖元
# 此處以假資料模擬 CAD 讀取結果做示範
# ══════════════════════════════════════════════════════════════════

def main():
    results = []

    # 模擬從 CAD 讀取到的圖元資料
    # 實際執行時這些值來自 AutoCAD API：
    #   entity.Handle → element_id
    #   entity.Layer  → layer
    #   entity.GetAttribute("LEVEL") → level
    #   entity.InsertionPoint → origin / start_point 等

    raw_entities = [
        {
            "layer": "COLUMN",
            "handle": "3B1C",
            "level": "1F",
            "origin": Point3D(100, 200, 0),
            "height": 3000,
            "section_width": 600,
            "section_depth": 600,
            "rotation": 0,
        },
        {
            "layer": "WALL",
            "handle": "2A3F",
            "level": "1F",
            "start_point": Point3D(0, 0, 0),
            "end_point":   Point3D(5000, 0, 0),
            "height": 3000,
            "thickness": 200,
            "base_elevation": 0,
        },
        {
            "layer": "SLAB",
            "handle": "1C8E",
            "level": "1F",
            "boundary_pts": [
                Point3D(0, 0, 0),
                Point3D(5000, 0, 0),
                Point3D(5000, 4000, 0),
                Point3D(0, 4000, 0),
            ],
            "thickness": 150,
            "elevation": 3000,
            "opening_ids": [],
            "slope": 0,
        },
    ]

    # 逐一判斷圖層 → 建立對應物件 → 輸出 JSON
    for entity in raw_entities:
        layer = entity["layer"]

        if layer == "COLUMN":
            obj = Column(
                element_id    = entity["handle"],
                layer         = entity["layer"],
                level         = entity["level"],
                origin        = entity["origin"],
                height        = entity["height"],
                section_width = entity["section_width"],
                section_depth = entity["section_depth"],
                rotation      = entity["rotation"],
            )

        elif layer == "WALL":
            obj = Wall(
                element_id    = entity["handle"],
                layer         = entity["layer"],
                level         = entity["level"],
                start_point   = entity["start_point"],
                end_point     = entity["end_point"],
                height        = entity["height"],
                thickness     = entity["thickness"],
                base_elevation= entity["base_elevation"],
            )

        elif layer == "SLAB":
            obj = Slab(
                element_id   = entity["handle"],
                layer        = entity["layer"],
                level        = entity["level"],
                boundary_pts = entity["boundary_pts"],
                thickness    = entity["thickness"],
                elevation    = entity["elevation"],
                opening_ids  = entity["opening_ids"],
                slope        = entity["slope"],
            )

        else:
            # 不在已知圖層內的圖元，跳過
            continue

        results.append(obj.to_dict())

    # 輸出成 JSON 檔案
    output_path = "output.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"完成，共輸出 {len(results)} 個構件 → {output_path}")


if __name__ == "__main__":
    main()
