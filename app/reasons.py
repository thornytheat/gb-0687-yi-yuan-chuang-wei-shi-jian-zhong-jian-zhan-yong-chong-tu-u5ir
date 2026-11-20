REASONS = {
  "COLUMN_COUNT":"字段数不符", "MISSING":"缺少必填值", "NUMBER":"数值格式错误",
  "RANGE":"数值越界", "UNIT":"单位不支持", "TIME":"时间缺少偏移或格式无效",
  "DUPLICATE":"相同记录重复", "CONFLICT":"同键记录冲突", "EMPTY":"文件没有数据",
  "HEADER_ONLY":"只有字段名", "BLANK_ONLY":"仅含空行", "STALE":"复核版本已更新",
  "STATE":"事件状态不符", "GAP":"采样缺口超限", "UNMAPPED":"未登记的业务编码",
  "EXPIRED":"已过有效期", "ORPHAN":"缺少关联记录", "OVERLAP":"有效区间重叠",
  "LIMIT":"业务阈值超限", "NEGATIVE":"出现负数", "CURVE":"曲线不满足单调条件",
  "REVIEW":"存在待复核差异", "INCOMPLETE":"关键记录不完整",
  "OCCUPIED":"班次时点已有患者占用", "ISOLATION_DEDICATED":"专用隔离床不纳入普通收治",
  "ISOLATION_RESERVED":"已为途中疑似患者保留", "GENERAL_FREE":"普通空床可收治",
  "ISOLATION_READY":"隔离空床可接疑似患者", "ISOLATION_SHORTAGE":"隔离保留余量不足",
  "SUPERSEDED":"晚到隔离记录使旧结论失效"
}
def rejected(row, code, detail=""):
    return {"line":row.get("_line", ""), "key":row.get("id", row.get("event_id", "")),
            "code":code, "reason":REASONS.get(code,code), "detail":detail,
            "raw":{k:v for k,v in row.items() if not k.startswith("_")}}
