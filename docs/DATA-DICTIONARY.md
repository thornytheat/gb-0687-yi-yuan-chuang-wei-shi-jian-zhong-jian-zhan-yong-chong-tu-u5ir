# 病床事件定义

id为事件号、patient为患者号、bed为床号，全部保留字符串及前导零。ward支持W1/W2或中文病区名；床号001–004属于一病区，101–104属于二病区。
time需带偏移，统一UTC。type支持admit、transfer、discharge。同一患者按事件时间排序，同刻先出院、再转床、再入院。
占床段[start,end)，终点不重复计数。缺出院延长至observation_end并标记open。转床关闭前床并打开后床。没有入院的出院进入隔离。
同事件最高version采信；同版相互矛盾则全部隔离。完全重复和旧版单列。双占通过区间交集计算分钟，不直接删除来源事件。
当地日历固定+08:00，午夜切分，分钟为Decimal。分段合计应等于日历合计，双占另外列明。迟到基于文件原始行序比较既有最大事件时间，晚到仍完整回放。
复核版本递增，旧版更新409。输入文件与参数纳入运行ID，内容不变重跑不增行。空值不可作为零时长。

## M8 班次收治

班次时点 `shift_time` 对全部8床逐张判定，占床沿用半开区间 `[start,end)`：`start<=shift_time<end` 计占用，同刻出院的床为空床。
`isolation_beds` 声明专用隔离床，**专用隔离床不计入普通可收治床数**，修复隔离床被当普通空床排床的问题。
隔离余量：先从空闲隔离床中按床号升序锁定 `suspect_reserve` 张，标记 `ISOLATION_RESERVED`，仅供途中疑似患者，普通重症不得占用；未锁定的空闲隔离床为 `ISOLATION_READY`。
班次结论给出 `decision`、普通可收治床数 `general_admissible`、隔离余量 `reserve_margin` 与余量缺口；待收重症 `critical_pending` 仅能使用普通空床，无普通空床时结论为暂缓且余量不下调。
原因码：`OCCUPIED` 占用、`ISOLATION_DEDICATED` 专用隔离不纳入普通收治、`ISOLATION_RESERVED` 疑似保留、`ISOLATION_READY` 隔离空床可接疑似、`GENERAL_FREE` 普通空床、`ISOLATION_SHORTAGE` 余量不足。每张床返回 `admissible` 布尔、原因码与 `basis`（占床患者、区间、来源事件）。
晚到隔离记录：采信行沿用 M7 迟到审计，隔离行按同一 `late_minutes` 阈值对照全文件水位判定；命中隔离床即把同班次（`隔离床集合@班次时点`）现行结论置「已失效」，旧 payload 保留原床位去向与依据，新结论记录触发行并回链 `superseded_by`。
收治结论ID由运行ID、班次时点、隔离床集合、保留数、重症数哈希得到；同一记录重试返回同一结论，不新增占用行。导出 `GET /api/admission.csv` 直接读取已存储结论，与页面共用同一 payload。
