# CAD 與 Revit 的資料提取差異

## 情境

以下是一段典型的 AutoCAD API pseudo-code，目的是提取當前圖面中所有牆的 ID 屬性並輸出成 JSON。這種寫法在實際執行時**有時候會漏掉部分元素，有時候會拋出異常**：

```python
function ExtractWallID():
    entities = document.GetAllEntities()
    result = []

    for each entity in entities:
        if entity.layer == "WALL":
            attr = entity.GetAttribute("ID")
            value = attr.Value
            result.append({ "handle": entity.Handle, "id": value })

    return ToJson(result)
```

這段程式碼看起來沒問題，但它有四個缺陷，而且**其中最危險的那個不會報錯**。

---

## 四個缺陷

### 【問題一】attr 未做 null 檢查 —— 異常的主要來源

```python
attr = entity.GetAttribute("ID")
value = attr.Value
```

在 AutoCAD 中，並非所有位於 `"WALL"` 圖層上的物件都帶有 `"ID"` 屬性。例如標註線、輔助線、或未附加屬性的 POLYLINE，也可能繪製於 WALL 圖層。當 `GetAttribute("ID")` 找不到對應屬性時會回傳 `None`，接著對 `None` 呼叫 `.Value` 就會直接拋出例外。這是造成「有時候會拋出異常」的根本原因。

**需區分兩種識別碼的性質：**

| | `entity.Handle` | `entity.GetAttribute("ID")` |
|---|---|---|
| 性質 | AutoCAD 資料庫給每個物件的**系統識別碼** | **自訂業務屬性** |
| 命名 | 系統決定 | 由繪圖端決定（可以是 "ID"、"ABC"、"編號" 等） |
| 存在性 | 每個物件（含標註線）**一定存在** | **可能不存在**，必須做 null 檢查 |
| 對應概念 | 等同於 Revit 的 ElementId | 等同於 Revit 的自訂參數 |

### 【問題二】圖層名稱比對為大小寫敏感 —— 元素被靜默略過的原因

```python
if entity.layer == "WALL":
```

AutoCAD 圖層名稱在實務中大小寫常有混用（如 `"Wall"`、`"wall"`），字串完全比對會導致部分元素被跳過**而不報錯**，形成「靜默漏掉」。

**這比拋出例外更難察覺** —— 程式不會報錯，資料就這樣少了。

> **附帶說明**：若本程式是針對特定廠商圖面所撰寫，且該廠商已透過 BEP（BIM 執行計畫書）或圖層對照表明確規範圖層命名為全大寫 `"WALL"`，則此處的大小寫完全比對屬於已知前提下的合理設計，並非缺陷。但若要提升程式的通用性，仍建議改為大小寫不敏感的比對。

### 【問題三】GetAllEntities() 取不到圖塊參考／外部參考內的元素

`GetAllEntities()` 通常只遍歷當前空間（Model Space）的頂層物件。若牆體繪製於圖塊參考（Block Reference）或外部參考（Xref）之中，這些元素不會被列舉，導致漏掉。

**在有外部參考的建築圖中，此問題幾乎必然發生。**

### 【問題四】缺少全域例外保護，單一元素失敗會中斷整個流程

原始 `for` 迴圈內完全沒有 try / except 保護。只要任何一個 entity 在處理過程中出問題（例如問題一的 `attr.Value`、物件已被釋放、屬性讀取超時等），整個函式就會直接 crash，**前面已經成功處理的資料也會一併遺失**。對於批次資料提取程式而言，這種「全有或全無」的設計極不健壯。

**與問題一的差異**：問題一指出的是「特定欄位」會拋例外的根因；問題四是更上層的設計問題 —— 即使把 attr 的 null 檢查補好了，仍然可能因為其他未預期的例外（網路斷線、記憶體不足、第三方套件 bug 等）而中斷整個流程。**兩者必須一起修**才能達到生產環境可用的健壯性。

---

## 改寫後的 pseudo-code

同時修正前述四個問題，並將「圖塊參考遞迴展開」獨立成輔助函式以保持主流程的可讀性：

```python
function ExtractWallID():
    result  = []
    skipped = []   # 記錄被跳過的元素，供後續 debug 追查

    # 取得當前文件 ModelSpace 的頂層物件
    entities = document.GetAllEntities()

    for each entity in entities:
        ProcessEntity(entity, result, skipped)

    # 【延伸設計】輸出時附上略過清單，讓下游能追查資料品質
    return ToJson({
        "walls":   result,
        "skipped": skipped
    })


# ── 核心處理邏輯（抽出獨立函式，以便圖塊內遞迴呼叫）──
function ProcessEntity(entity, result, skipped):
    # 【修正問題四】整個處理邏輯包在 try / except 內
    # 確保單一物件出錯不影響其他物件繼續被處理
    try:
        # ── 圖塊與外參遞迴展開（修正問題三）──
        if entity is BlockReference or entity is XrefReference:
            ExpandContainer(entity, result, skipped)
            return

        # 【修正問題二】大小寫不敏感的圖層比對
        if entity.layer.ToUpper() != "WALL":
            return

        # 【修正問題一】先確認屬性是否存在，再取值
        attr = entity.GetAttribute("ID")
        if attr is None:
            skipped.append({
                "handle": SafeGetHandle(entity),
                "reason": "缺少 ID 屬性"
            })
            return
        if attr.Value is None or attr.Value == "":
            skipped.append({
                "handle": SafeGetHandle(entity),
                "reason": "ID 屬性值為空"
            })
            return

        result.append({
            "handle": entity.Handle,
            "id":     attr.Value
        })

    except 任何例外 as e:
        # 【修正問題四】個別物件的例外不擴散，並記錄錯誤原因以利追查
        skipped.append({
            "handle": SafeGetHandle(entity),
            "reason": str(e)
        })


# ── 圖塊參考 / 外部參考遞迴展開（修正問題三）──
function ExpandContainer(container, result, skipped):
    # 取得圖塊的定義內容（BlockTableRecord 內所有實體）
    block_def = container.GetBlockTableRecord()

    for each sub_entity in block_def.GetEntities():
        # 遞迴呼叫：圖塊內可能還有圖塊（巢狀圖塊）
        # 同樣的篩選與例外保護邏輯會自動套用到每一層
        ProcessEntity(sub_entity, result, skipped)


# ── 防禦性工具函式：連 Handle 都讀不到時也不要 crash ──
function SafeGetHandle(entity):
    try:
        return entity.Handle
    except:
        return "UNKNOWN"
```

### 設計重點

- **`ProcessEntity` 抽成獨立函式** —— 讓主流程與圖塊遞迴共用同一套篩選邏輯，避免複製貼上造成維護負擔
- **try / except 包在最內層** —— 確保單一物件出錯不影響其他物件繼續被處理（修正問題四）
- **`ExpandContainer` 遞迴** —— 圖塊內可能還有圖塊（巢狀圖塊），遞迴呼叫天然支援任意深度，不需事先知道層數（修正問題三）
- **`SafeGetHandle` 工具函式** —— 防止 `entity.Handle` 本身在物件已釋放時也拋例外，讓 skipped 清單一定能寫入紀錄
- **skipped 清單同步輸出** —— 下游能直接得知哪些元素有問題，不需要另外比對

最後一點特別重要：**沉默的資料遺失比明確的錯誤更難處理**。把略過的元素連同原因一起輸出，等於把「不知道自己漏了什麼」變成「知道漏了什麼、為什麼漏」。

---

## 換成 Revit API，最主要的差異是什麼

### AutoCAD：意義必須由人來賦予

AutoCAD 儲存資料的形式基本上就是「點對（Dotted Pair）」。無論是點、線、面或是牆、柱、門，對 AutoCAD 來說本質上沒有差別。

**「這條線是牆還是管線」，CAD 本身並不知道，也不在乎**；因此必須靠人類事先制定規則 —— 圖層命名、顏色、線型等等 —— 程式再根據這些規則做人工翻譯。

這正是上面那段程式碼需要靠圖層名稱 `"WALL"` 來篩選的根本原因，也是它麻煩的地方：**規則只要執行不一致，程式就會漏掉或誤判**。如果設計單位的繪圖習慣不良就更是災難，例如所有物件都繪製在同一圖層、顏色全部用一樣的、圖塊全部炸開等等。

### Revit：每個東西生來就知道自己是什麼

Revit 的元素與族群，本身就是物件導向概念的具現化。「牆」在 Revit 的資料庫中從一開始就是一個獨立的物件類型，帶著自己的屬性與行為，**不需要靠命名規則來辨識身份**。

想要牆的資料，直接去讀「牆」這個品類的物件就好，不需要人工翻譯的過程。程式只需告訴 Revit「給我所有品類是牆的物件」，Revit 自己就能正確回傳，**不依賴任何繪圖規範**。

### 對照

| | AutoCAD | Revit |
|---|---|---|
| 資料本質 | 幾何 + 人為附加的意義 | 帶語意的物件 |
| 如何識別「牆」 | 靠圖層／顏色／線型等外部約定 | 直接讀 Category = Walls |
| 主要失敗模式 | 繪圖規範執行不一致 → 漏抓、誤判 | 幾乎不會誤判品類 |
| 程式的角色 | **翻譯者**（把人為約定轉成結構化資料） | **查詢者**（直接向資料庫要） |
| 前處理成本 | 高，且高度依賴上游圖面品質 | 低 |

這個差異也解釋了為什麼 [02 的資料結構](02-element-data-structure.md)必須把「設計前提假設」寫得那麼明確 —— **從 AutoCAD 提取時，那些假設就是程式正確性的全部基礎**。
